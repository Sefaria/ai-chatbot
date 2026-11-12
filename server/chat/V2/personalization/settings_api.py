"""The personalization settings endpoint: `POST /api/v2/personalization/settings`.

This is the access-control surface of personalization, so the rules are few and strict:

- The user id comes only from the signed token in the JSON body field `userId` (through
  `IdentitySerializerMixin` and `authenticate_request`). No header, path or other body field names a
  user; `personalizationSubject`, `user_id`, `revision` and every other unknown field are ignored.
- A logged-out visitor (`anonId`), a gate that is off and a production or beta deployment all get 404,
  as do the lab-only actions anywhere else than on a lab deployment.
- Writes go through a fixed list of fields per action, validated here, never by echoing the body.
- Responses are never cached. The token is never logged; the three write actions that change what
  the assistant knows each log one audit line with a hash of the user id and no text.
"""

from __future__ import annotations

import hashlib
import logging

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ...auth import AuthenticationError, UserTokenExpired, authenticate_request
from ...serializers import IdentitySerializerMixin
from . import gate, health, profile_edit, profile_job, profile_store, quota

logger = logging.getLogger("chat")
audit_logger = logging.getLogger("chat.personalization.audit")

MAX_REQUEST = profile_store.MAX_INSTRUCTIONS
NO_STORE = "no-store, private"

ACTIONS = (
    "get",
    "set_instructions",
    "toggle",
    "clear_profile",
    "edit_profile",
    "run_now",
    "set_testing",
    "health",
)
LAB_ONLY_ACTIONS = frozenset({"run_now", "set_testing", "health"})
RUN_KINDS = {"session": "session", "dream": "dream"}


class SettingsRequestSerializer(IdentitySerializerMixin):
    """The envelope: who (token or anonId, exactly one) and what. Action fields are checked per
    action below; anything else in the body is not read."""

    action = serializers.ChoiceField(choices=ACTIONS)


def _respond(data, code=status.HTTP_200_OK):
    response = Response(data, status=code)
    response["Cache-Control"] = NO_STORE
    return response


def _not_found():
    return _respond({"error": "not_found"}, status.HTTP_404_NOT_FOUND)


def _bad_request(error="bad_request", **extra):
    return _respond({"error": error, **extra}, status.HTTP_400_BAD_REQUEST)


def _iso(value):
    return value.isoformat() if value else None


def _state(user_id: str) -> dict:
    settings_ = profile_store.get_settings(user_id)
    sections = profile_store.get_sections(user_id)
    return {
        "instructions": {
            "text": settings_.instructions_text,
            "enabled": settings_.instructions_enabled,
            "updatedAt": _iso(settings_.instructions_updated_at),
        },
        "profile": {
            "text": profile_store.render_profile(sections),
            "sections": [{"heading": s.heading, "body": s.body, "kind": s.kind} for s in sections],
            "enabled": settings_.profile_enabled,
            "updatedAt": _iso(settings_.profile_updated_at),
            "updatedBy": settings_.profile_updated_by,
            "revision": settings_.profile_revision,
        },
        "labEnabled": gate.lab_enabled(),
        "testing": {
            "showPlainResponse": settings_.show_plain_response,
            "updateEveryMessage": settings_.update_every_message,
        },
    }


def _audit(action: str, user_id: str) -> None:
    """One line: the action and a hash of the user id. Never the text."""
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    audit_logger.info("personalization_audit action=%s user=%s", action, digest)


def _take_edit_quota(user_id: str) -> bool:
    now = timezone.now()
    hour = now.replace(minute=0, second=0, microsecond=0)
    day = hour.replace(hour=0)
    per_hour = quota.limit("PERSONALIZATION_EDIT_PER_HOUR", quota.JOB_PER_HOUR_DEFAULT)
    per_day = quota.limit("PERSONALIZATION_EDIT_PER_DAY", quota.JOB_PER_DAY_DEFAULT)
    return quota.take_quotas(
        "edit",
        [(user_id, hour, per_hour), (quota.ALL_KEY, day, per_day)],
    )


def _is_str(value) -> bool:
    return isinstance(value, str)


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _handle(action: str, user_id: str, data: dict):
    if action == "get":
        return _respond(_state(user_id))

    if action == "set_instructions":
        text = data.get("text")
        if not _is_str(text):
            return _bad_request()
        text = text.strip()
        if len(text) > profile_store.MAX_INSTRUCTIONS:
            return _bad_request("too_long", max=profile_store.MAX_INSTRUCTIONS)
        profile_store.set_instructions(user_id, text)
        _audit(action, user_id)
        return _respond(_state(user_id))

    if action == "toggle":
        target, enabled = data.get("target"), data.get("enabled")
        if target not in profile_store.TARGETS or not _is_bool(enabled):
            return _bad_request()
        profile_store.set_enabled(user_id, target, enabled)
        _audit(action, user_id)
        return _respond(_state(user_id))

    if action == "clear_profile":
        profile_store.clear_profile(user_id)
        _audit(action, user_id)
        return _respond(_state(user_id))

    if action == "set_testing":
        fields = {}
        for wire, name in (
            ("showPlainResponse", "show_plain_response"),
            ("updateEveryMessage", "update_every_message"),
        ):
            if wire in data:
                if not _is_bool(data[wire]):
                    return _bad_request()
                fields[name] = data[wire]
        profile_store.set_testing(user_id, **fields)
        return _respond(_state(user_id))

    if action == "edit_profile":
        request_text = data.get("request")
        if not _is_str(request_text) or not request_text.strip():
            return _bad_request()
        request_text = request_text.strip()
        if len(request_text) > MAX_REQUEST:
            return _bad_request("too_long", max=MAX_REQUEST)
        if not _take_edit_quota(user_id):
            return _respond({"error": "quota"}, status.HTTP_429_TOO_MANY_REQUESTS)
        edit_status, reason = profile_edit.edit_profile(user_id, request_text)
        return _respond({**_state(user_id), "edit": {"status": edit_status, "reason": reason}})

    if action == "run_now":
        kind = data.get("kind")
        if kind not in RUN_KINDS:
            return _bad_request()
        result = profile_job.run_profile_job(user_id, "run_now", window=RUN_KINDS[kind])
        if result.status == "quota":
            return _respond({"error": "quota"}, status.HTTP_429_TOO_MANY_REQUESTS)
        return _respond(
            {**_state(user_id), "run": {"status": result.status, "reason": result.reason}}
        )

    if action == "health":
        return _respond(health.snapshot())

    return _bad_request()  # unreachable: the serializer limits the action


@api_view(["POST"])
def personalization_settings(request):
    # Gate first, before reading the body: a deployment that is not a lab one looks like it has no
    # such endpoint. A placeholder id is enough here; the real id is checked again below.
    if not gate.enabled_for("gate-probe"):
        return _not_found()

    data = request.data
    if not isinstance(data, dict):
        return _bad_request()
    envelope = SettingsRequestSerializer(data=data)
    if not envelope.is_valid():
        return _bad_request()
    action = envelope.validated_data["action"]

    if envelope.validated_data.get("anonId"):
        return _not_found()

    try:
        actor = authenticate_request(request, data, body_only=True)
    except UserTokenExpired:
        return _respond({"error": "userId_expired"}, status.HTTP_401_UNAUTHORIZED)
    except AuthenticationError:
        return _respond({"error": "invalid_userId"}, status.HTTP_401_UNAUTHORIZED)

    if actor.is_anonymous or not gate.enabled_for(actor.user_id):
        return _not_found()
    if action in LAB_ONLY_ACTIONS and not gate.lab_enabled():
        return _not_found()

    return _handle(action, actor.user_id, data)
