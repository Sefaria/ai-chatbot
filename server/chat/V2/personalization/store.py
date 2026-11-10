"""Profile storage: a Protocol and two implementations (in memory, Django).

Every method is scoped by ``user_id``. The only read across users is
``users_with_pending`` (which users have unconsumed observations).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from .types import (
    MAX_EVIDENCE,
    Evidence,
    Observation,
    ProfileSettingsData,
    Proposal,
    SlotValue,
    Value,
)


class ProfileStore(Protocol):
    def get_profile(self, user_id: str) -> dict[str, SlotValue]: ...
    def set_inferred(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        evidence: list[Evidence],
        now: datetime,
        expires_at: datetime | None = None,
    ) -> bool: ...
    def set_stated(self, user_id: str, slot_key: str, value: Value, now: datetime) -> None: ...
    def release(self, user_id: str, slot_key: str, now: datetime) -> None: ...
    def delete_slot(self, user_id: str, slot_key: str) -> None: ...
    def clear(self, user_id: str) -> None: ...
    def add_observation(self, observation: Observation) -> None: ...
    def observations(self, user_id: str, since: datetime | None = None) -> list[Observation]: ...
    def mark_consumed(self, user_id: str, now: datetime) -> None: ...
    def users_with_pending(self) -> list[str]: ...
    def add_proposal(
        self, user_id: str, slot_key: str, value: Value, quote: str, now: datetime
    ) -> Proposal: ...
    def proposals(self, user_id: str, status: str | None = None) -> list[Proposal]: ...
    def resolve_proposal(
        self, user_id: str, proposal_id: str, accept: bool, now: datetime
    ) -> Proposal | None: ...
    def move(self, from_user_id: str, to_user_id: str) -> None: ...
    def get_settings(self, user_id: str) -> ProfileSettingsData: ...
    def set_settings(
        self,
        user_id: str,
        personalize_enabled: bool | None = None,
        store_enabled: bool | None = None,
    ) -> ProfileSettingsData: ...
    def expire(self, now: datetime) -> int: ...


def _winner(source: SlotValue, target: SlotValue) -> SlotValue:
    """Merge rule for `move`: the target's stated slot wins, otherwise the newer value."""
    if target.status == "stated":
        return target
    return source if source.updated_at > target.updated_at else target


class InMemoryProfileStore:
    def __init__(self) -> None:
        self._slots: dict[str, dict[str, SlotValue]] = {}
        self._observations: dict[str, list[Observation]] = {}
        self._proposals: dict[str, list[Proposal]] = {}
        self._settings: dict[str, ProfileSettingsData] = {}

    def get_profile(self, user_id):
        return dict(self._slots.get(user_id, {}))

    def set_inferred(self, user_id, slot_key, value, evidence, now, expires_at=None):
        slots = self._slots.setdefault(user_id, {})
        current = slots.get(slot_key)
        if current and current.status == "stated":
            return False
        slots[slot_key] = SlotValue(
            slot_key, value, "inferred", list(evidence[-MAX_EVIDENCE:]), now, expires_at
        )
        return True

    def set_stated(self, user_id, slot_key, value, now):
        self._slots.setdefault(user_id, {})[slot_key] = SlotValue(
            slot_key, value, "stated", [], now
        )

    def release(self, user_id, slot_key, now):
        slot = self._slots.get(user_id, {}).get(slot_key)
        if slot:
            self._slots[user_id][slot_key] = replace(slot, status="inferred", updated_at=now)

    def delete_slot(self, user_id, slot_key):
        self._slots.get(user_id, {}).pop(slot_key, None)

    def clear(self, user_id):
        for table in (self._slots, self._observations, self._proposals):
            table.pop(user_id, None)

    def add_observation(self, observation):
        self._observations.setdefault(observation.user_id, []).append(observation)

    def observations(self, user_id, since=None):
        rows = self._observations.get(user_id, [])
        return sorted((o for o in rows if since is None or o.at >= since), key=lambda o: o.at)

    def mark_consumed(self, user_id, now):
        for obs in self._observations.get(user_id, []):
            if obs.consumed_at is None:
                obs.consumed_at = now

    def users_with_pending(self):
        return [u for u, rows in self._observations.items() if any(not o.consumed_at for o in rows)]

    def add_proposal(self, user_id, slot_key, value, quote, now):
        proposal = Proposal(uuid.uuid4().hex, user_id, slot_key, value, quote, "pending", now)
        self._proposals.setdefault(user_id, []).append(proposal)
        return proposal

    def proposals(self, user_id, status=None):
        return [p for p in self._proposals.get(user_id, []) if status in (None, p.status)]

    def resolve_proposal(self, user_id, proposal_id, accept, now):
        for proposal in self._proposals.get(user_id, []):
            if proposal.id == proposal_id and proposal.status == "pending":
                proposal.status = "accepted" if accept else "rejected"
                if accept:
                    self.set_stated(user_id, proposal.slot_key, proposal.value, now)
                return proposal
        return None

    def move(self, from_user_id, to_user_id):
        if from_user_id == to_user_id:
            return
        target = self._slots.setdefault(to_user_id, {})
        for key, slot in self._slots.pop(from_user_id, {}).items():
            target[key] = _winner(slot, target[key]) if key in target else slot
        for table in (self._observations, self._proposals):
            rows = table.pop(from_user_id, [])
            for row in rows:
                row.user_id = to_user_id
            table.setdefault(to_user_id, []).extend(rows)
        source_settings = self._settings.pop(from_user_id, None)
        if source_settings:
            self._settings[to_user_id] = _stricter(self.get_settings(to_user_id), source_settings)

    def get_settings(self, user_id):
        return replace(self._settings.get(user_id, ProfileSettingsData()))

    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        current = self.get_settings(user_id)
        if personalize_enabled is not None:
            current.personalize_enabled = personalize_enabled
        if store_enabled is not None:
            current.store_enabled = store_enabled
        self._settings[user_id] = current
        return replace(current)

    def expire(self, now):
        removed = 0
        for slots in self._slots.values():
            for key in [
                k
                for k, s in slots.items()
                if s.status == "inferred" and s.expires_at and s.expires_at <= now
            ]:
                del slots[key]
                removed += 1
        return removed


def _stricter(a: ProfileSettingsData, b: ProfileSettingsData) -> ProfileSettingsData:
    """When two identities merge, an opt-out on either side stays in force."""
    return ProfileSettingsData(
        a.personalize_enabled and b.personalize_enabled, a.store_enabled and b.store_enabled
    )


def _evidence_to_json(evidence: list[Evidence]) -> list[dict]:
    return [
        {
            "quote": e.quote,
            "session_id": e.session_id,
            "message_id": e.message_id,
            "at": e.at.isoformat(),
        }
        for e in evidence[-MAX_EVIDENCE:]
    ]


def _evidence_from_json(rows: list[dict]) -> list[Evidence]:
    return [
        Evidence(r["quote"], r["session_id"], r["message_id"], datetime.fromisoformat(r["at"]))
        for r in rows
    ]


class DjangoProfileStore:
    """Postgres-backed store over the Profile* models in chat.models."""

    def get_profile(self, user_id):
        from chat.models import ProfileSlot

        return {
            row.slot_key: _slot_from_row(row) for row in ProfileSlot.objects.filter(user_id=user_id)
        }

    def set_inferred(self, user_id, slot_key, value, evidence, now, expires_at=None):
        from django.db import transaction

        from chat.models import ProfileSlot

        with transaction.atomic():
            row = (
                ProfileSlot.objects.select_for_update()
                .filter(user_id=user_id, slot_key=slot_key)
                .first()
            )
            if row and row.status == ProfileSlot.Status.STATED:
                return False
            ProfileSlot.objects.update_or_create(
                user_id=user_id,
                slot_key=slot_key,
                defaults={
                    "value": value,
                    "status": ProfileSlot.Status.INFERRED,
                    "evidence": _evidence_to_json(evidence),
                    "updated_at": now,
                    "expires_at": expires_at,
                },
            )
        return True

    def set_stated(self, user_id, slot_key, value, now):
        from chat.models import ProfileSlot

        ProfileSlot.objects.update_or_create(
            user_id=user_id,
            slot_key=slot_key,
            defaults={
                "value": value,
                "status": ProfileSlot.Status.STATED,
                "evidence": [],
                "updated_at": now,
                "expires_at": None,
            },
        )

    def release(self, user_id, slot_key, now):
        from chat.models import ProfileSlot

        ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key).update(
            status=ProfileSlot.Status.INFERRED, updated_at=now
        )

    def delete_slot(self, user_id, slot_key):
        from chat.models import ProfileSlot

        ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key).delete()

    def clear(self, user_id):
        from chat.models import ProfileObservation, ProfileProposal, ProfileSlot

        for model in (ProfileSlot, ProfileObservation, ProfileProposal):
            model.objects.filter(user_id=user_id).delete()

    def add_observation(self, observation):
        from chat.models import ProfileObservation

        ProfileObservation.objects.create(
            user_id=observation.user_id,
            session_id=observation.session_id,
            message_id=observation.message_id,
            text=observation.text,
            signals=observation.signals,
            created_at=observation.at,
            consumed_at=observation.consumed_at,
        )

    def observations(self, user_id, since=None):
        from chat.models import ProfileObservation

        rows = ProfileObservation.objects.filter(user_id=user_id)
        if since is not None:
            rows = rows.filter(created_at__gte=since)
        return [_observation_from_row(r) for r in rows.order_by("created_at", "id")]

    def mark_consumed(self, user_id, now):
        from chat.models import ProfileObservation

        ProfileObservation.objects.filter(user_id=user_id, consumed_at__isnull=True).update(
            consumed_at=now
        )

    def users_with_pending(self):
        from chat.models import ProfileObservation

        return list(
            ProfileObservation.objects.filter(consumed_at__isnull=True)
            .order_by("user_id")
            .values_list("user_id", flat=True)
            .distinct()
        )

    def add_proposal(self, user_id, slot_key, value, quote, now):
        from chat.models import ProfileProposal

        row = ProfileProposal.objects.create(
            user_id=user_id, slot_key=slot_key, value=value, quote=quote, created_at=now
        )
        return _proposal_from_row(row)

    def proposals(self, user_id, status=None):
        from chat.models import ProfileProposal

        rows = ProfileProposal.objects.filter(user_id=user_id)
        if status:
            rows = rows.filter(status=status)
        return [_proposal_from_row(r) for r in rows]

    def resolve_proposal(self, user_id, proposal_id, accept, now):
        from django.db import transaction

        from chat.models import ProfileProposal

        with transaction.atomic():
            row = ProfileProposal.objects.filter(
                user_id=user_id, id=proposal_id, status=ProfileProposal.Status.PENDING
            ).first()
            if row is None:
                return None
            row.status = (
                ProfileProposal.Status.ACCEPTED if accept else ProfileProposal.Status.REJECTED
            )
            row.save(update_fields=["status"])
            if accept:
                self.set_stated(user_id, row.slot_key, row.value, now)
        return _proposal_from_row(row)

    def move(self, from_user_id, to_user_id):
        from django.db import transaction

        from chat.models import ProfileObservation, ProfileProposal, ProfileSettings, ProfileSlot

        if from_user_id == to_user_id:
            return
        with transaction.atomic():
            target = {s.slot_key: s for s in ProfileSlot.objects.filter(user_id=to_user_id)}
            for row in ProfileSlot.objects.filter(user_id=from_user_id):
                existing = target.get(row.slot_key)
                source_slot = _slot_from_row(row)
                if existing and _winner(source_slot, _slot_from_row(existing)) is not source_slot:
                    row.delete()
                    continue
                if existing:
                    existing.delete()
                row.user_id = to_user_id
                row.save(update_fields=["user_id"])
            ProfileObservation.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
            ProfileProposal.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
            source = ProfileSettings.objects.filter(user_id=from_user_id).first()
            if source:
                merged = _stricter(
                    self.get_settings(to_user_id),
                    ProfileSettingsData(source.personalize_enabled, source.store_enabled),
                )
                ProfileSettings.objects.update_or_create(
                    user_id=to_user_id,
                    defaults={
                        "personalize_enabled": merged.personalize_enabled,
                        "store_enabled": merged.store_enabled,
                    },
                )
                source.delete()

    def get_settings(self, user_id):
        from chat.models import ProfileSettings

        row = ProfileSettings.objects.filter(user_id=user_id).first()
        return (
            ProfileSettingsData(row.personalize_enabled, row.store_enabled)
            if row
            else ProfileSettingsData()
        )

    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        from chat.models import ProfileSettings

        current = self.get_settings(user_id)
        if personalize_enabled is not None:
            current.personalize_enabled = personalize_enabled
        if store_enabled is not None:
            current.store_enabled = store_enabled
        ProfileSettings.objects.update_or_create(
            user_id=user_id,
            defaults={
                "personalize_enabled": current.personalize_enabled,
                "store_enabled": current.store_enabled,
            },
        )
        return current

    def expire(self, now):
        from chat.models import ProfileSlot

        deleted, _ = ProfileSlot.objects.filter(
            status=ProfileSlot.Status.INFERRED, expires_at__isnull=False, expires_at__lte=now
        ).delete()
        return deleted


def _slot_from_row(row) -> SlotValue:
    return SlotValue(
        row.slot_key,
        row.value,
        row.status,
        _evidence_from_json(row.evidence or []),
        row.updated_at,
        row.expires_at,
        row.slot_set_version,
    )


def _observation_from_row(row) -> Observation:
    return Observation(
        row.user_id,
        row.session_id,
        row.message_id,
        row.text,
        row.created_at,
        row.signals or {},
        row.consumed_at,
    )


def _proposal_from_row(row) -> Proposal:
    return Proposal(
        row.id, row.user_id, row.slot_key, row.value, row.quote, row.status, row.created_at
    )
