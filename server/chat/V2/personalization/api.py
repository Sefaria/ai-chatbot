"""Status and profile endpoints for testing the feature on a preview.

`status` works for any authenticated caller and reveals only the caller's own id. `profile` is a
404 unless the caller passes `gate.override_allowed` (flag, non-production ENVIRONMENT, allowlist),
whatever the mode, so on a deployment without the override it does not exist.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from chat.serializers import IdentitySerializerMixin
from chat.V2.sentry import capture_exception

from . import gate, job, runtime

logger = logging.getLogger(__name__)

ACTIONS = (
    "get",
    "set",
    "release",
    "clear",
    "settings",
    "resolve_proposal",
    "run_batch",
    "run_job",
    "apply_candidate",
    "discard_candidate",
    "seed_persona",
)
SEED_MAX_TURNS = 60
SEED_MAX_CHARS = 4000
SEED_ASSISTANT_MAX_CHARS = 2000
SEED_WINDOW_DAYS = 29  # inside the job's 30-day window, with a day of margin
# The job's model call must not hold the request's database transaction open.
UNATOMIC_ACTIONS = frozenset({"run_job"})
BATCH_MODES = sorted(gate.MODES - {"off"})


class StatusRequestSerializer(IdentitySerializerMixin):
    pass


class ProfileRequestSerializer(IdentitySerializerMixin):
    action = serializers.ChoiceField(choices=ACTIONS)
    personalizationSubject = serializers.CharField(max_length=100, required=False)
    slot = serializers.CharField(max_length=100, required=False)
    value = serializers.JSONField(required=False)
    personalizeEnabled = serializers.BooleanField(required=False)
    storeEnabled = serializers.BooleanField(required=False)
    proposalId = serializers.CharField(max_length=100, required=False)
    accept = serializers.BooleanField(required=False)
    mode = serializers.ChoiceField(choices=BATCH_MODES, required=False)
    turns = serializers.JSONField(required=False)


def _authenticate(request, data):
    from chat.V2.views import _authenticate_actor_or_response

    return _authenticate_actor_or_response(request, data)


def _identity(data) -> dict:
    return {k: str(data[k]) for k in ("userId", "anonId") if data.get(k)}


def _tables_exist() -> bool:
    from chat.models import ProfileSlot

    try:
        with transaction.atomic():
            ProfileSlot.objects.exists()
        return True
    except DatabaseError:
        return False


def _iso(moment):
    return moment.isoformat() if moment else None


def _slot_json(slot) -> dict:
    return {
        "slot": slot.slot_key,
        "value": slot.value,
        "status": slot.status,
        "evidence": [
            {
                "quote": e.quote,
                "sessionId": e.session_id,
                "messageId": e.message_id,
                "at": _iso(e.at),
            }
            for e in slot.evidence
        ],
        "updatedAt": _iso(slot.updated_at),
        "expiresAt": _iso(slot.expires_at),
        "slotSetVersion": slot.slot_set_version,
    }


def _proposal_json(proposal) -> dict:
    return {
        "id": proposal.id,
        "slot": proposal.slot_key,
        "value": proposal.value,
        "quote": proposal.quote,
        "status": proposal.status,
        "createdAt": _iso(proposal.created_at),
    }


def _write_json(result) -> dict:
    return {"applied": result.applied, "reason": result.reason, "revision": result.revision}


@api_view(["POST"])
def personalization_status(request):
    """POST /api/v2/personalization/status {userId}: how personalization is set up for you."""
    serializer = StatusRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    actor = _authenticate(request, _identity(serializer.validated_data))
    if isinstance(actor, Response):
        return actor
    config = gate.resolve(settings, actor.user_id, gate.RequestedPersonalization())
    return Response(
        {
            "persistentUserId": actor.user_id,
            "mode": config.mode,
            "injection": config.injection,
            "overrideEnabled": getattr(settings, "PERSONALIZATION_OVERRIDE_ENABLED", False) is True,
            "overrideAllowedForYou": gate.override_allowed(settings, actor.user_id),
            "environment": os.environ.get("ENVIRONMENT", ""),
            "braintrustProject": os.environ.get("BRAINTRUST_PROJECT", "On Site Agent"),
            "tables": _tables_exist(),
            "slotSetVersion": runtime.get_registry().version,
        }
    )


def _bad(message: str):
    return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


def _profile_json(store, store_user_id: str) -> dict:
    observations = store.observations(store_user_id, include_quarantined=True)
    quarantined = sum(1 for o in observations if o.quarantined)
    prefs = store.get_settings(store_user_id)
    return {
        "storeUserId": store_user_id,
        "revision": store.revision(store_user_id),
        "slots": [_slot_json(s) for s in store.get_profile(store_user_id).values()],
        "settings": {
            "personalizeEnabled": prefs.personalize_enabled,
            "storeEnabled": prefs.store_enabled,
        },
        "proposals": [_proposal_json(p) for p in store.proposals(store_user_id)],
        "observations": len(observations) - quarantined,
        "quarantinedObservations": quarantined,
    }


def _run_job(store, store_user_id: str, now) -> dict:
    """One candidate run: a model call (outside any request transaction) and a candidate write."""
    result = job.run_job(
        store,
        store_user_id,
        now=now,
        llm=runtime.get_llm(),
        mode="candidate",
        registry=runtime.get_registry(),
        model=settings.PERSONALIZATION_MODEL,
    )
    return {
        "status": "candidate" if result.candidate_written else "empty",
        "changes": result.changes,
        "conflicts": result.conflicts,
        "observationsRead": result.observations_read,
        "tokens": result.input_tokens + result.output_tokens,
        "costUsd": result.cost_usd,
        "revision": store.revision(store_user_id),
    }


def _parse_turn(raw, index: int, now) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"turn {index} must be an object"
    user = raw.get("user")
    if not isinstance(user, str) or not user.strip() or len(user) > SEED_MAX_CHARS:
        return None, f"turn {index}: user must be a non-empty string of at most {SEED_MAX_CHARS}"
    assistant = raw.get("assistant")
    if assistant is not None and not isinstance(assistant, str):
        return None, f"turn {index}: assistant must be a string or null"
    session_id = raw.get("sessionId")
    if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 100):
        return None, f"turn {index}: sessionId must be a string of at most 100"
    at = None
    if raw.get("ts") is not None:
        at = parse_datetime(raw["ts"]) if isinstance(raw["ts"], str) else None
        if at is None:
            return None, f"turn {index}: ts must be an ISO datetime"
        if timezone.is_naive(at):
            at = timezone.make_aware(at, UTC)
    return {"user": user, "assistant": assistant, "session": session_id, "at": at}, None


def _seed_persona(data: dict, store, store_user_id: str, now):
    """Write a synthetic persona's prompts as observations, as the hybrid mode records a real
    turn (same signals, same quarantine of a standing instruction). A turn time outside the job's
    window is moved into it; a turn with no time is spread over the days before `now`."""
    from .signals import (
        cited_categories,
        detect_language,
        find_standing_instructions,
        hebrew_ratio,
    )
    from .types import Observation

    raw_turns = data.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        return _bad("seed_persona needs turns")
    if len(raw_turns) > SEED_MAX_TURNS:
        return _bad(f"at most {SEED_MAX_TURNS} turns")
    parsed = []
    for index, raw in enumerate(raw_turns):
        turn, error = _parse_turn(raw, index, now)
        if error:
            return _bad(error)
        parsed.append(turn)
    earliest, run_id = now - timedelta(days=SEED_WINDOW_DAYS), uuid.uuid4().hex[:8]
    seeded = 0
    for index, turn in enumerate(parsed):
        at = turn["at"] or earliest + (now - earliest) * (index + 1) / (len(parsed) + 1)
        at = min(max(at, earliest), now)
        text = turn["user"]
        quarantined = bool(find_standing_instructions(text))
        signals = {} if quarantined else {
            "language": detect_language(text),
            "hebrew_ratio": round(hebrew_ratio(text), 3),
            "cited_categories": cited_categories(text),
        }  # fmt: skip
        if turn["assistant"] and not quarantined:
            signals["preceding_assistant"] = turn["assistant"][:SEED_ASSISTANT_MAX_CHARS]
        observation = Observation(
            user_id=store_user_id,
            session_id=turn["session"] or f"seed-{run_id}",
            message_id=f"seed-{run_id}-{index}",
            text="" if quarantined else text,
            at=at,
            signals=signals,
            consumed_at=now if quarantined else None,
            quarantined=quarantined,
        )
        if store.add_observation(observation).applied:
            seeded += 1
    return {"seeded": seeded}


def _run(action: str, data: dict, store_user_id: str, mode: str):
    now = timezone.now()
    store = runtime.get_store()
    if action == "get":
        return _profile_json(store, store_user_id)
    if action == "run_job":
        return _run_job(store, store_user_id, now)
    if action == "apply_candidate":
        result = job.apply_candidate(store, store_user_id, now=now, registry=runtime.get_registry())
        if result.reason == "stale_revision":
            return Response({"error": "stale_revision"}, status=status.HTTP_409_CONFLICT)
        if not result.applied:
            return Response({"error": "no_candidate"}, status=status.HTTP_404_NOT_FOUND)
        return {"applied": True, "profile": _profile_json(store, store_user_id)}
    if action == "discard_candidate":
        job.discard_candidate(store, store_user_id)
        return {"discarded": True}
    if action == "seed_persona":
        return _seed_persona(data, store, store_user_id, now)
    if action == "clear":
        return _write_json(store.clear(store_user_id))
    if action == "settings":
        if "personalizeEnabled" not in data and "storeEnabled" not in data:
            return _bad("settings needs personalizeEnabled and/or storeEnabled")
        result = store.set_settings(
            store_user_id, data.get("personalizeEnabled"), data.get("storeEnabled")
        )
        return _write_json(result)
    if action == "resolve_proposal":
        if not data.get("proposalId") or "accept" not in data:
            return _bad("resolve_proposal needs proposalId and accept")
        return _write_json(
            store.resolve_proposal(store_user_id, data["proposalId"], data["accept"], now)
        )
    if action in ("set", "release"):
        slot = data.get("slot")
        registry = runtime.get_registry()
        if not slot or registry.get(slot) is None:
            return _bad("unknown slot")
        if action == "release":
            return _write_json(store.release(store_user_id, slot, now))
        value = registry.validate(slot, data.get("value"))
        if value is None:
            return _bad("value not acceptable for this slot")
        service = runtime.get_service("explicit")
        return _write_json(service.set_stated(store_user_id, slot, value, now))
    # run_batch: the nightly consolidation for this one store user, synchronously.
    service = runtime.get_service(data.get("mode") or mode)
    if service is None or service.strategy is None:
        return _bad("run_batch needs a mode other than off")
    result = service.strategy.run_batch(now, [store_user_id])
    return {
        "mode": service.mode,
        "usersProcessed": result.users_processed,
        "updatedSlots": [slot for _user, slot in result.updated_slots],
        "llmCalls": result.llm_calls,
        "errors": result.errors,
        "inputTokens": result.input_tokens,
        "outputTokens": result.output_tokens,
        "costUsd": result.cost_usd,
        "writesApplied": result.writes_applied,
        "writesRefused": dict(result.writes_refused),
        "diagnostics": dict(result.diagnostics),
    }


@api_view(["POST"])
def personalization_profile(request):
    """POST /api/v2/personalization/profile {userId, action, ...}: read or change your own profile."""
    serializer = ProfileRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    data = serializer.validated_data
    actor = _authenticate(request, _identity(data))
    if isinstance(actor, Response):
        return actor
    if not gate.override_allowed(settings, actor.user_id):
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    if data["action"] == "seed_persona" and os.environ.get("PERSONALIZATION_LAB_SEED") != "true":
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    subject = data.get("personalizationSubject")
    config = gate.resolve(settings, actor.user_id, gate.RequestedPersonalization(subject=subject))
    if config.override_refused:
        return _bad("invalid personalizationSubject")
    try:
        if data["action"] in UNATOMIC_ACTIONS:
            body = _run(data["action"], data, config.store_user_id, config.mode)
        else:
            with transaction.atomic():
                body = _run(data["action"], data, config.store_user_id, config.mode)
    except DatabaseError as exc:
        logger.exception("Personalization profile API failed")
        capture_exception(exc, phase="personalization_api")
        return Response(
            {"error": "profile_tables_unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    if isinstance(body, Response):
        return body
    return Response(body)


LAB_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LA personalization lab</title>
  <style>body{font-family:system-ui,sans-serif;margin:0;padding:48px 24px;max-width:720px;margin:auto}</style>
</head>
<body>
  <h1>LA personalization lab</h1>
  <p>Synthetic personas only. Open the chat, then use the personalization panel.</p>
  <lc-chatbot api-base-url="/api" default-open="true" placement="right" personalization-lab="true"></lc-chatbot>
  <script>
    // The widget reads its personalization mode from sessionStorage (or ?la_personalization=).
    try { sessionStorage.setItem("la_personalization", "hybrid"); } catch (e) {}
    const params = new URLSearchParams(window.location.search);
    for (const attribute of ["user-id", "anon-free-answers"]) {
      if (params.get(attribute)) {
        document.querySelector("lc-chatbot").setAttribute(attribute, params.get(attribute));
      }
    }
  </script>
  <script src="/static/js/lc-chatbot.umd.cjs"></script>
</body>
</html>
"""


@api_view(["GET"])
def lab_page(request):
    """GET /lab/: the widget with personalization mode hybrid. A 404 unless PERSONALIZATION_LAB_SEED."""
    from django.http import Http404, HttpResponse

    if os.environ.get("PERSONALIZATION_LAB_SEED") != "true":
        raise Http404
    return HttpResponse(LAB_PAGE, content_type="text/html; charset=utf-8")
