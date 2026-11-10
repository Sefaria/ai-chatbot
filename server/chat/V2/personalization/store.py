"""Profile storage: a Protocol and two implementations (in memory, Django).

Every method is scoped by ``user_id``. The only read across users is
``users_with_pending`` (which users have unconsumed observations).

Writes that follow a model or Jev call take an ``expected_generation`` (read with
``generation(user_id)`` before the call). ``clear`` and ``move`` bump the generation, so a write
from a job that started earlier is refused. ``set_inferred``, ``add_proposal`` and
``add_observation`` also re-check ``store_enabled`` at commit. ``refusal_reason`` says why a
write returned False.
"""

from __future__ import annotations

import copy
import functools
import threading
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

STALE_GENERATION = "stale_generation"
STORE_DISABLED = "store_disabled_at_commit"


class ProfileStore(Protocol):
    def get_profile(self, user_id: str) -> dict[str, SlotValue]: ...
    def generation(self, user_id: str) -> int: ...
    def refusal_reason(self, user_id: str, expected_generation: int | None) -> str | None: ...
    def set_inferred(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        evidence: list[Evidence],
        now: datetime,
        expires_at: datetime | None = None,
        expected_generation: int | None = None,
    ) -> bool: ...
    def set_stated(self, user_id: str, slot_key: str, value: Value, now: datetime) -> None: ...
    def release(self, user_id: str, slot_key: str, now: datetime) -> None: ...
    def delete_slot(self, user_id: str, slot_key: str) -> None: ...
    def delete_slot_if_inferred(
        self, user_id: str, slot_key: str, expected_generation: int | None = None
    ) -> bool: ...
    def clear(self, user_id: str) -> None: ...
    def add_observation(
        self, observation: Observation, expected_generation: int | None = None
    ) -> bool: ...
    def observations(self, user_id: str, since: datetime | None = None) -> list[Observation]: ...
    def mark_consumed(
        self,
        user_id: str,
        observation_ids: list,
        now: datetime,
        expected_generation: int | None = None,
    ) -> bool: ...
    def users_with_pending(self) -> list[str]: ...
    def add_proposal(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        quote: str,
        now: datetime,
        expected_generation: int | None = None,
    ) -> Proposal | None: ...
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
    def expire_user(self, user_id: str, now: datetime) -> int: ...


def _winner(source: SlotValue, target: SlotValue) -> SlotValue:
    """Merge rule for `move`: the target's stated slot wins, otherwise the newer value."""
    if target.status == "stated":
        return target
    return source if source.updated_at > target.updated_at else target


def cap_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Keep MAX_EVIDENCE entries, newest first, taking one per distinct session before any repeat."""
    if len(evidence) <= MAX_EVIDENCE:
        return list(evidence)
    chosen: list[int] = []
    seen: set[str] = set()
    for i in range(len(evidence) - 1, -1, -1):
        if evidence[i].session_id not in seen:
            seen.add(evidence[i].session_id)
            chosen.append(i)
    chosen = chosen[:MAX_EVIDENCE]
    for i in range(len(evidence) - 1, -1, -1):
        if len(chosen) >= MAX_EVIDENCE:
            break
        if i not in chosen:
            chosen.append(i)
    return [evidence[i] for i in sorted(chosen)]


def _synchronized(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class InMemoryProfileStore:
    """Dict-backed store. Reads return deep copies, writes copy their inputs, one RLock guards all."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slots: dict[str, dict[str, SlotValue]] = {}
        self._observations: dict[str, list[Observation]] = {}
        self._proposals: dict[str, list[Proposal]] = {}
        self._settings: dict[str, ProfileSettingsData] = {}
        self._generations: dict[str, int] = {}
        self._next_observation_id = 1

    @_synchronized
    def get_profile(self, user_id):
        return copy.deepcopy(self._slots.get(user_id, {}))

    @_synchronized
    def generation(self, user_id):
        return self._generations.get(user_id, 0)

    def _refusal(self, user_id, expected_generation, require_store=True):
        if expected_generation is not None and self.generation(user_id) != expected_generation:
            return STALE_GENERATION
        if require_store and not self.get_settings(user_id).store_enabled:
            return STORE_DISABLED
        return None

    @_synchronized
    def refusal_reason(self, user_id, expected_generation):
        return self._refusal(user_id, expected_generation)

    @_synchronized
    def set_inferred(
        self, user_id, slot_key, value, evidence, now, expires_at=None, expected_generation=None
    ):
        if self._refusal(user_id, expected_generation):
            return False
        slots = self._slots.setdefault(user_id, {})
        current = slots.get(slot_key)
        if current and current.status == "stated":
            return False
        slots[slot_key] = SlotValue(
            slot_key,
            copy.deepcopy(value),
            "inferred",
            copy.deepcopy(cap_evidence(evidence)),
            now,
            expires_at,
        )
        return True

    @_synchronized
    def set_stated(self, user_id, slot_key, value, now):
        self._slots.setdefault(user_id, {})[slot_key] = SlotValue(
            slot_key, copy.deepcopy(value), "stated", [], now
        )

    @_synchronized
    def release(self, user_id, slot_key, now):
        slot = self._slots.get(user_id, {}).get(slot_key)
        if slot:
            self._slots[user_id][slot_key] = replace(slot, status="inferred", updated_at=now)

    @_synchronized
    def delete_slot(self, user_id, slot_key):
        self._slots.get(user_id, {}).pop(slot_key, None)

    @_synchronized
    def delete_slot_if_inferred(self, user_id, slot_key, expected_generation=None):
        if self._refusal(user_id, expected_generation, require_store=False):
            return False
        slot = self._slots.get(user_id, {}).get(slot_key)
        if slot is None or slot.status != "inferred":
            return False
        del self._slots[user_id][slot_key]
        return True

    @_synchronized
    def clear(self, user_id):
        for table in (self._slots, self._observations, self._proposals):
            table.pop(user_id, None)
        self._generations[user_id] = self.generation(user_id) + 1

    @_synchronized
    def add_observation(self, observation, expected_generation=None):
        if self._refusal(observation.user_id, expected_generation):
            return False
        stored = copy.deepcopy(observation)
        stored.id = self._next_observation_id
        self._next_observation_id += 1
        self._observations.setdefault(stored.user_id, []).append(stored)
        return True

    @_synchronized
    def observations(self, user_id, since=None):
        rows = self._observations.get(user_id, [])
        return copy.deepcopy(
            sorted((o for o in rows if since is None or o.at >= since), key=lambda o: o.at)
        )

    @_synchronized
    def mark_consumed(self, user_id, observation_ids, now, expected_generation=None):
        if self._refusal(user_id, expected_generation, require_store=False):
            return False
        wanted = set(observation_ids)
        for obs in self._observations.get(user_id, []):
            if obs.consumed_at is None and obs.id in wanted:
                obs.consumed_at = now
        return True

    @_synchronized
    def users_with_pending(self):
        return [u for u, rows in self._observations.items() if any(not o.consumed_at for o in rows)]

    @_synchronized
    def add_proposal(self, user_id, slot_key, value, quote, now, expected_generation=None):
        if self._refusal(user_id, expected_generation):
            return None
        proposal = Proposal(
            uuid.uuid4().hex, user_id, slot_key, copy.deepcopy(value), quote, "pending", now
        )
        self._proposals.setdefault(user_id, []).append(proposal)
        return copy.deepcopy(proposal)

    @_synchronized
    def proposals(self, user_id, status=None):
        return copy.deepcopy(
            [p for p in self._proposals.get(user_id, []) if status in (None, p.status)]
        )

    @_synchronized
    def resolve_proposal(self, user_id, proposal_id, accept, now):
        for proposal in self._proposals.get(user_id, []):
            if proposal.id == proposal_id and proposal.status == "pending":
                proposal.status = "accepted" if accept else "rejected"
                if accept:
                    self.set_stated(user_id, proposal.slot_key, proposal.value, now)
                return copy.deepcopy(proposal)
        return None

    @_synchronized
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
        for user_id in (from_user_id, to_user_id):
            self._generations[user_id] = self.generation(user_id) + 1

    @_synchronized
    def get_settings(self, user_id):
        return replace(self._settings.get(user_id, ProfileSettingsData()))

    @_synchronized
    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        current = self.get_settings(user_id)
        if personalize_enabled is not None:
            current.personalize_enabled = personalize_enabled
        if store_enabled is not None:
            current.store_enabled = store_enabled
        self._settings[user_id] = current
        return replace(current)

    @_synchronized
    def expire(self, now):
        return sum(self.expire_user(user_id, now) for user_id in list(self._slots))

    @_synchronized
    def expire_user(self, user_id, now):
        slots = self._slots.get(user_id, {})
        stale = [
            k
            for k, s in slots.items()
            if s.status == "inferred" and s.expires_at and s.expires_at <= now
        ]
        for key in stale:
            del slots[key]
        return len(stale)


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
        for e in cap_evidence(evidence)
    ]


def _evidence_from_json(rows: list[dict]) -> list[Evidence]:
    return [
        Evidence(r["quote"], r["session_id"], r["message_id"], datetime.fromisoformat(r["at"]))
        for r in rows
    ]


def _lock_settings(user_ids: list[str]) -> dict:
    """Lock (creating first when missing) the settings rows, in sorted user-id order."""
    from chat.models import ProfileSettings

    ids = sorted(set(user_ids))
    for user_id in ids:
        ProfileSettings.objects.get_or_create(user_id=user_id)
    rows = ProfileSettings.objects.select_for_update().filter(user_id__in=ids).order_by("user_id")
    return {row.user_id: row for row in rows}


def _refusal_of(row, expected_generation, require_store=True) -> str | None:
    if expected_generation is not None and row.generation != expected_generation:
        return STALE_GENERATION
    if require_store and not row.store_enabled:
        return STORE_DISABLED
    return None


class DjangoProfileStore:
    """Postgres-backed store over the Profile* models in chat.models.

    Writers that must honour consent or a generation lock the user's ProfileSettings row first;
    `move` locks both identities' rows in sorted user-id order.
    """

    def get_profile(self, user_id):
        from chat.models import ProfileSlot

        return {
            row.slot_key: _slot_from_row(row) for row in ProfileSlot.objects.filter(user_id=user_id)
        }

    def generation(self, user_id):
        from chat.models import ProfileSettings

        return (
            ProfileSettings.objects.filter(user_id=user_id)
            .values_list("generation", flat=True)
            .first()
            or 0
        )

    def refusal_reason(self, user_id, expected_generation):
        from chat.models import ProfileSettings

        row = ProfileSettings.objects.filter(user_id=user_id).first() or ProfileSettings(
            user_id=user_id
        )
        return _refusal_of(row, expected_generation)

    def set_inferred(
        self, user_id, slot_key, value, evidence, now, expires_at=None, expected_generation=None
    ):
        from django.db import transaction

        from chat.models import ProfileSlot

        fields = {
            "value": value,
            "status": ProfileSlot.Status.INFERRED,
            "evidence": _evidence_to_json(evidence),
            "updated_at": now,
            "expires_at": expires_at,
        }
        with transaction.atomic():
            if _refusal_of(_lock_settings([user_id])[user_id], expected_generation):
                return False
            row = (
                ProfileSlot.objects.select_for_update()
                .filter(user_id=user_id, slot_key=slot_key)
                .first()
            )
            if row is None:
                row, created = ProfileSlot.objects.get_or_create(
                    user_id=user_id, slot_key=slot_key, defaults=fields
                )
                if created:
                    return True
                # Someone else created it first: lock it and look at its status before writing.
                row = ProfileSlot.objects.select_for_update().get(pk=row.pk)
            if row.status == ProfileSlot.Status.STATED:
                return False
            for name, field_value in fields.items():
                setattr(row, name, field_value)
            row.save(update_fields=list(fields))
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

    def delete_slot_if_inferred(self, user_id, slot_key, expected_generation=None):
        from django.db import transaction

        from chat.models import ProfileSlot

        with transaction.atomic():
            row = _lock_settings([user_id])[user_id]
            if _refusal_of(row, expected_generation, require_store=False):
                return False
            deleted, _ = ProfileSlot.objects.filter(
                user_id=user_id, slot_key=slot_key, status=ProfileSlot.Status.INFERRED
            ).delete()
        return deleted > 0

    def clear(self, user_id):
        from django.db import transaction
        from django.db.models import F

        from chat.models import ProfileObservation, ProfileProposal, ProfileSettings, ProfileSlot

        with transaction.atomic():
            _lock_settings([user_id])
            for model in (ProfileSlot, ProfileObservation, ProfileProposal):
                model.objects.filter(user_id=user_id).delete()
            ProfileSettings.objects.filter(user_id=user_id).update(generation=F("generation") + 1)

    def add_observation(self, observation, expected_generation=None):
        from django.db import transaction

        from chat.models import ProfileObservation

        with transaction.atomic():
            row = _lock_settings([observation.user_id])[observation.user_id]
            if _refusal_of(row, expected_generation):
                return False
            ProfileObservation.objects.create(
                user_id=observation.user_id,
                session_id=observation.session_id,
                message_id=observation.message_id,
                text=observation.text,
                signals=observation.signals,
                created_at=observation.at,
                consumed_at=observation.consumed_at,
            )
        return True

    def observations(self, user_id, since=None):
        from chat.models import ProfileObservation

        rows = ProfileObservation.objects.filter(user_id=user_id)
        if since is not None:
            rows = rows.filter(created_at__gte=since)
        return [_observation_from_row(r) for r in rows.order_by("created_at", "id")]

    def mark_consumed(self, user_id, observation_ids, now, expected_generation=None):
        from django.db import transaction

        from chat.models import ProfileObservation

        with transaction.atomic():
            row = _lock_settings([user_id])[user_id]
            if _refusal_of(row, expected_generation, require_store=False):
                return False
            ProfileObservation.objects.filter(
                user_id=user_id, id__in=list(observation_ids), consumed_at__isnull=True
            ).update(consumed_at=now)
        return True

    def users_with_pending(self):
        from chat.models import ProfileObservation

        return list(
            ProfileObservation.objects.filter(consumed_at__isnull=True)
            .order_by("user_id")
            .values_list("user_id", flat=True)
            .distinct()
        )

    def add_proposal(self, user_id, slot_key, value, quote, now, expected_generation=None):
        from django.db import transaction

        from chat.models import ProfileProposal

        with transaction.atomic():
            if _refusal_of(_lock_settings([user_id])[user_id], expected_generation):
                return None
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
            # The row is locked before its status is looked at; a concurrent resolve waits here and
            # then no longer matches `status=pending`.
            row = (
                ProfileProposal.objects.select_for_update()
                .filter(user_id=user_id, id=proposal_id, status=ProfileProposal.Status.PENDING)
                .first()
            )
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
        from django.db.models import F

        from chat.models import ProfileObservation, ProfileProposal, ProfileSettings, ProfileSlot

        if from_user_id == to_user_id:
            return
        with transaction.atomic():
            locked = _lock_settings([from_user_id, to_user_id])
            ProfileObservation.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
            ProfileProposal.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
            # Slots are read only now, under the locks.
            target = {
                s.slot_key: s
                for s in ProfileSlot.objects.select_for_update().filter(user_id=to_user_id)
            }
            for row in ProfileSlot.objects.select_for_update().filter(user_id=from_user_id):
                existing = target.get(row.slot_key)
                if existing is None:
                    row.user_id = to_user_id
                    row.save(update_fields=["user_id"])
                    continue
                source_slot = _slot_from_row(row)
                if _winner(source_slot, _slot_from_row(existing)) is source_slot:
                    # Update the target's row in place (never insert: the key is unique per user).
                    for name in (
                        "value", "status", "evidence", "updated_at", "expires_at",
                        "slot_set_version",
                    ):  # fmt: skip
                        setattr(existing, name, getattr(row, name))
                    existing.save()
                row.delete()
            merged = _stricter(
                ProfileSettingsData(
                    locked[to_user_id].personalize_enabled, locked[to_user_id].store_enabled
                ),
                ProfileSettingsData(
                    locked[from_user_id].personalize_enabled, locked[from_user_id].store_enabled
                ),
            )
            ProfileSettings.objects.filter(user_id=to_user_id).update(
                personalize_enabled=merged.personalize_enabled,
                store_enabled=merged.store_enabled,
                generation=F("generation") + 1,
            )
            # The source identity reverts to defaults; its row stays so its generation survives.
            ProfileSettings.objects.filter(user_id=from_user_id).update(
                personalize_enabled=True, store_enabled=True, generation=F("generation") + 1
            )

    def get_settings(self, user_id):
        from chat.models import ProfileSettings

        row = ProfileSettings.objects.filter(user_id=user_id).first()
        return (
            ProfileSettingsData(row.personalize_enabled, row.store_enabled)
            if row
            else ProfileSettingsData()
        )

    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        from django.db import transaction

        from chat.models import ProfileSettings

        changes = {}
        if personalize_enabled is not None:
            changes["personalize_enabled"] = personalize_enabled
        if store_enabled is not None:
            changes["store_enabled"] = store_enabled
        with transaction.atomic():
            row, _ = ProfileSettings.objects.get_or_create(user_id=user_id, defaults=changes)
            if changes:
                # Only the fields passed: a concurrent change to the other toggle is not undone.
                ProfileSettings.objects.filter(pk=row.pk).update(**changes)
        return self.get_settings(user_id)

    def expire(self, now):
        from chat.models import ProfileSlot

        deleted, _ = ProfileSlot.objects.filter(
            status=ProfileSlot.Status.INFERRED, expires_at__isnull=False, expires_at__lte=now
        ).delete()
        return deleted

    def expire_user(self, user_id, now):
        from chat.models import ProfileSlot

        deleted, _ = ProfileSlot.objects.filter(
            user_id=user_id,
            status=ProfileSlot.Status.INFERRED,
            expires_at__isnull=False,
            expires_at__lte=now,
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
        row.id,
    )


def _proposal_from_row(row) -> Proposal:
    return Proposal(
        row.id, row.user_id, row.slot_key, row.value, row.quote, row.status, row.created_at
    )
