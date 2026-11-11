"""Status and profile endpoints for testing the feature on a preview.

`status` works for any authenticated caller and reveals only the caller's own id. `profile` is a
404 unless the caller passes `gate.override_allowed` (flag, non-production ENVIRONMENT, allowlist),
whatever the mode, so on a deployment without the override it does not exist.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from chat.serializers import IdentitySerializerMixin
from chat.V2.sentry import capture_exception

from . import gate, runtime

logger = logging.getLogger(__name__)

ACTIONS = ("get", "set", "release", "clear", "settings", "resolve_proposal", "run_batch")
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


def _run(action: str, data: dict, store_user_id: str, mode: str):
    now = timezone.now()
    store = runtime.get_store()
    if action == "get":
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

    subject = data.get("personalizationSubject")
    config = gate.resolve(settings, actor.user_id, gate.RequestedPersonalization(subject=subject))
    if config.override_refused:
        return _bad("invalid personalizationSubject")
    try:
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
