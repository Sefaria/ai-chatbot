"""Request-based listening API. All model work happens outside DB transactions."""

import copy
import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from chat.auth import AuthenticationError, authenticate_request
from chat.models import ListeningSession

from . import model_service
from .serializers import AnalyzeSerializer, SuggestionSerializer

logger = logging.getLogger("chat.listening")
IDLE_TTL = timedelta(minutes=30)
HARD_TTL = timedelta(hours=1)
LEASE_TTL = timedelta(seconds=40)


def _error(code, status):
    return Response({"error": code}, status=status)


def _actor(request):
    actor = authenticate_request(request, request.data if isinstance(request.data, dict) else {})
    # Ambient listening has its own cost budget; do not bypass anonymous chat limits.
    if actor.is_anonymous:
        return None
    return actor


def _session(request, session_id):
    actor = _actor(request)
    if actor is None:
        return None
    return ListeningSession.objects.filter(id=session_id, user_id=actor.user_id).first()


def _active(session):
    return not session.closed and session.expires_at > timezone.now()


def _snapshot(session):
    return {
        "session_id": str(session.id),
        "received_through_seq": session.state.get("received", 0),
        "analyzed_through_seq": session.state.get("analyzed", 0),
        "suggestions": session.state.get("suggestions", []),
    }


@api_view(["GET", "POST"])
def sessions(request):
    if request.method == "GET":
        return Response({"enabled": settings.LISTENING_ENABLED, "requires_login": True})
    if not settings.LISTENING_ENABLED:
        return _error("listening_disabled", 404)
    try:
        actor = _actor(request)
    except AuthenticationError:
        return _error("auth_required", 401)
    if actor is None:
        return _error("auth_required", 401)
    now = timezone.now()
    # Clear expired transcript state opportunistically; scheduled cleanup removes rows.
    ListeningSession.objects.filter(expires_at__lte=now).exclude(state={}).update(
        state={},
        closed=True,
        lease=None,
    )
    # Six starts per hour bounds reset-based model budget abuse. Closed rows remain
    # until cleanup so restarting doesn't reset this budget.
    if (
        ListeningSession.objects.filter(
            user_id=actor.user_id,
            created_at__gte=now - HARD_TTL,
        ).count()
        >= 6
    ):
        return _error("listening_limit", 429)
    session = ListeningSession.objects.create(
        user_id=actor.user_id,
        expires_at=now + IDLE_TTL,
        state={"received": 0, "analyzed": 0, "segments": [], "suggestions": [], "requests": []},
    )
    return Response(_snapshot(session), status=201)


@api_view(["POST"])
def analyze(request, session_id):
    if not settings.LISTENING_ENABLED:
        return _error("listening_disabled", 404)
    try:
        session = _session(request, session_id)
    except AuthenticationError:
        return _error("auth_required", 401)
    if session is None:
        return _error("not_found", 404)
    if not _active(session):
        return _error("session_closed", 410)
    serializer = AnalyzeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    request_id = str(data["request_id"])
    fingerprint = hashlib.sha256(json.dumps(data["segments"], sort_keys=True).encode()).hexdigest()
    state = copy.deepcopy(session.state)
    for previous in state["requests"]:
        if previous["id"] == request_id:
            if previous["hash"] != fingerprint:
                return _error("request_conflict", 409)
            return Response(_snapshot(session))
    if state.get("attempts", 0) >= 360:
        return _error("listening_limit", 429)
    now = timezone.now()
    if session.next_analysis_at and session.next_analysis_at > now:
        return _error("analysis_rate_limit", 429)
    lease = uuid.uuid4()
    claimed = (
        ListeningSession.objects.filter(
            id=session.id,
            revision=session.revision,
            closed=False,
            expires_at__gt=now,
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .update(
            lease=lease,
            lease_until=now + LEASE_TTL,
            revision=session.revision + 1,
            next_analysis_at=now + timedelta(seconds=5),
        )
    )
    if not claimed:
        return _error("analysis_busy", 409)
    state["attempts"] = state.get("attempts", 0) + 1
    owned = ListeningSession.objects.filter(id=session.id, lease=lease, closed=False)
    try:
        known = {s["seq"]: s for s in state["segments"]}
        for segment in data["segments"]:
            seq = segment["seq"]
            if seq <= state["received"]:
                if known.get(seq) != segment:
                    return _error("segment_conflict", 409)
                continue
            if seq != state["received"] + 1:
                return Response(
                    {"error": "sequence_gap", "expected_seq": state["received"] + 1}, status=409
                )
            state["segments"].append(segment)
            known[seq] = segment
            state["received"] = seq
        if sum(len(s["text"]) for s in state["segments"]) > 18000:
            return _error("transcript_buffer_full", 413)
        # Persist received speech before model work. Failed work is retried using
        # the same request; it never advances the analyzed cursor.
        if not owned.filter(expires_at__gt=timezone.now()).update(state=state):
            return _error("session_closed", 410)
        new = [s for s in state["segments"] if s["seq"] > state["analyzed"]]
        window, chars = [], 0
        for segment in new:
            if chars + len(segment["text"]) > 6000:
                break
            window.append(segment)
            chars += len(segment["text"])
        if not window:
            return Response(_snapshot(session))
        context = [s for s in state["segments"] if s["seq"] <= state["analyzed"]][-3:]
        payload = {
            "context_segments": context,
            "new_segments": window,
            "recent_suggestions": [s["question"] for s in state["suggestions"]],
        }
        # Checkpoint positive detection so a failed Sonnet request doesn't rerun Haiku.
        candidate = state.get("candidate")
        if candidate is None:
            evidence = model_service.detect(payload)
            candidate = {"evidence": evidence, "payload": payload}
            state["candidate"] = candidate
            if not owned.filter(expires_at__gt=timezone.now()).update(state=state):
                return _error("session_closed", 410)
        evidence = candidate["evidence"]
        payload = candidate["payload"]
        window = payload["new_segments"]
        context = payload["context_segments"]
        question = model_service.reword(payload, evidence) if evidence else None
        if question and question.casefold() not in {
            s["question"].casefold() for s in state["suggestions"]
        }:
            source = next(s for s in context + window if s["seq"] == evidence["end_seq"])
            state["suggestions"].append(
                {
                    "id": str(uuid.uuid4()),
                    "question": question,
                    "status": "pending",
                    "page_url": source.get("page_url", ""),
                    "evidence_start_seq": evidence["start_seq"],
                    "evidence_end_seq": evidence["end_seq"],
                }
            )
        state["analyzed"] = window[-1]["seq"]
        state.pop("candidate", None)
        state["segments"] = [s for s in state["segments"] if s["seq"] > state["analyzed"] - 3]
        state["requests"] = (state["requests"] + [{"id": request_id, "hash": fingerprint}])[-20:]
        state["suggestions"] = state["suggestions"][-30:]
        if not owned.filter(expires_at__gt=timezone.now()).update(
            state=state,
            expires_at=min(timezone.now() + IDLE_TTL, session.created_at + HARD_TTL),
        ):
            return _error("session_closed", 410)
        session.state = state
        return Response(_snapshot(session))
    except Exception as exc:
        # Log class only: exception messages can contain provider request data.
        logger.warning("Listening analysis failed: %s", type(exc).__name__)
        return _error("analysis_unavailable", 503)
    finally:
        owned.update(lease=None, lease_until=None)


@api_view(["POST"])
def stop(request, session_id):
    try:
        session = _session(request, session_id)
    except AuthenticationError:
        return _error("auth_required", 401)
    if session is None:
        return _error("not_found", 404)
    ListeningSession.objects.filter(id=session.id).update(
        closed=True,
        state={},
        lease=None,
        lease_until=None,
    )
    return Response({"status": "closed"})


@api_view(["PATCH"])
def suggestion(request, session_id, suggestion_id):
    try:
        session = _session(request, session_id)
    except AuthenticationError:
        return _error("auth_required", 401)
    if session is None:
        return _error("not_found", 404)
    if not _active(session):
        return _error("session_closed", 410)
    serializer = SuggestionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    state = copy.deepcopy(session.state)
    target = next((s for s in state["suggestions"] if s["id"] == str(suggestion_id)), None)
    if target is None:
        return _error("not_found", 404)
    if target["status"] != "pending":
        return Response({"status": target["status"]})
    target["status"] = serializer.validated_data["status"]
    # Don't overwrite an in-flight analysis. Client retries this independent update.
    now = timezone.now()
    changed = (
        ListeningSession.objects.filter(
            id=session.id,
            revision=session.revision,
            closed=False,
            expires_at__gt=now,
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .update(
            state=state,
            revision=session.revision + 1,
            lease=None,
            lease_until=None,
        )
    )
    return Response({"status": target["status"]}) if changed else _error("analysis_busy", 409)
