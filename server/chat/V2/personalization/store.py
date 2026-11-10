"""Profile storage: a Protocol and two implementations (in memory, Django).

Every method is scoped by ``user_id``. The only read across users is
``users_with_pending`` (which users have unconsumed observations).

Every public mutating method goes through one private helper, ``_locked_write``: it takes the
user's lock once (the ``ProfileSettings`` row ``FOR UPDATE`` in Postgres, a per-user lock in
memory; several users are locked in sorted ``user_id`` order), refuses with ``store_disabled`` or
``stale_revision`` before anything is written, runs the write, and adds 1 to each locked user's
``revision`` when the write applied. Every mutating method returns a ``WriteResult``. A public
method never calls another public mutating method; shared work lives in unlocked ``_do_*``
helpers, so one public call bumps the revision exactly once.

Writes that follow a model or Jev call pass ``expected_revision`` (read with ``revision(user_id)``
before the call, then carried forward from each applied write's ``WriteResult.revision``), so a
write computed from an older profile is refused.
"""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
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
    WriteResult,
)

STALE_REVISION = "stale_revision"
STORE_DISABLED = "store_disabled"
DUPLICATE = "duplicate"
STATED_WINS = "stated_wins"
PENDING_PROPOSAL = "pending_proposal"


class ProfileStore(Protocol):
    def get_profile(self, user_id: str) -> dict[str, SlotValue]: ...
    def revision(self, user_id: str) -> int: ...
    def set_inferred(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        evidence: list[Evidence],
        now: datetime,
        expires_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> WriteResult: ...
    def set_stated(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        now: datetime,
        expected_revision: int | None = None,
    ) -> WriteResult: ...
    def release(self, user_id: str, slot_key: str, now: datetime) -> WriteResult: ...
    def delete_slot(self, user_id: str, slot_key: str) -> WriteResult: ...
    def delete_slot_if_inferred(
        self, user_id: str, slot_key: str, expected_revision: int | None = None
    ) -> WriteResult: ...
    def clear(self, user_id: str) -> WriteResult: ...
    def add_observation(
        self, observation: Observation, expected_revision: int | None = None
    ) -> WriteResult: ...
    def observations(
        self, user_id: str, since: datetime | None = None, include_quarantined: bool = False
    ) -> list[Observation]: ...
    def release_observation(
        self,
        user_id: str,
        message_id: str,
        text: str,
        signals: dict,
        expected_revision: int | None = None,
    ) -> WriteResult: ...
    def mark_consumed(
        self,
        user_id: str,
        observation_ids: list,
        now: datetime,
        expected_revision: int | None = None,
    ) -> WriteResult: ...
    def users_with_pending(self) -> list[str]: ...
    def add_proposal(
        self,
        user_id: str,
        slot_key: str,
        value: Value,
        quote: str,
        now: datetime,
        expected_revision: int | None = None,
        source_message_id: str = "",
    ) -> WriteResult: ...
    def proposals(self, user_id: str, status: str | None = None) -> list[Proposal]: ...
    def resolve_proposal(
        self, user_id: str, proposal_id: str, accept: bool, now: datetime
    ) -> WriteResult: ...
    def move(self, from_user_id: str, to_user_id: str) -> WriteResult: ...
    def get_settings(self, user_id: str) -> ProfileSettingsData: ...
    def set_settings(
        self,
        user_id: str,
        personalize_enabled: bool | None = None,
        store_enabled: bool | None = None,
    ) -> WriteResult: ...
    def expire(self, now: datetime) -> int: ...
    def expire_user(self, user_id: str, now: datetime) -> WriteResult: ...


@dataclass(frozen=True)
class _Outcome:
    """What a write function did inside `_locked_write`: applied (bump), refused, or nothing."""

    applied: bool = True
    reason: str | None = None
    proposal: Proposal | None = None


APPLIED = _Outcome()
NOTHING = _Outcome(applied=False)  # nothing to change: not applied, no reason, no bump


def _refused(reason: str, proposal: Proposal | None = None) -> _Outcome:
    return _Outcome(False, reason, proposal)


def _refusal(
    store_enabled: bool, revision: int, expected_revision: int | None, needs_store_enabled: bool
) -> str | None:
    """Why `_locked_write` refuses before running the write, or None. Shared by both stores."""
    if needs_store_enabled and not store_enabled:
        return STORE_DISABLED
    if expected_revision is not None and revision != expected_revision:
        return STALE_REVISION
    return None


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


def _stricter(a: ProfileSettingsData, b: ProfileSettingsData) -> ProfileSettingsData:
    """When two identities merge, an opt-out on either side stays in force."""
    return ProfileSettingsData(
        a.personalize_enabled and b.personalize_enabled, a.store_enabled and b.store_enabled
    )


class InMemoryProfileStore:
    """Dict-backed store. Reads return deep copies, writes copy their inputs.

    Writers hold the user's `threading.Lock` (several users: sorted order), then `_data`, a short
    lock that also guards every read. A per-user lock is not re-entrant: a public method that
    called another public mutating method would deadlock, which the tests use as a guard.
    """

    def __init__(self) -> None:
        self._data = threading.RLock()
        self._user_locks: dict[str, threading.Lock] = {}
        self._slots: dict[str, dict[str, SlotValue]] = {}
        self._observations: dict[str, list[Observation]] = {}
        self._proposals: dict[str, list[Proposal]] = {}
        self._settings: dict[str, ProfileSettingsData] = {}
        self._revisions: dict[str, int] = {}
        self._next_observation_id = 1

    # -- the one write path ---------------------------------------------------------------

    def _locked_write(
        self,
        user_ids: tuple[str, ...],
        *,
        expected_revision: int | None,
        needs_store_enabled: bool,
        fn: Callable[[], _Outcome | None],
    ) -> WriteResult:
        with self._data:
            locks = [
                self._user_locks.setdefault(u, threading.Lock()) for u in sorted(set(user_ids))
            ]
        for lock in locks:
            lock.acquire()
        try:
            with self._data:
                first = user_ids[0]
                reason = _refusal(
                    self._settings_of(first).store_enabled,
                    self._revisions.get(first, 0),
                    expected_revision,
                    needs_store_enabled,
                )
                if reason:
                    return WriteResult(False, reason, self._revisions.get(first, 0))  # type: ignore[arg-type]
                outcome = fn() or APPLIED
                if outcome.applied:
                    for user_id in set(user_ids):
                        self._revisions[user_id] = self._revisions.get(user_id, 0) + 1
                return WriteResult(
                    outcome.applied,
                    outcome.reason,  # type: ignore[arg-type]
                    self._revisions.get(first, 0),
                    copy.deepcopy(outcome.proposal),
                )
        finally:
            for lock in reversed(locks):
                lock.release()

    # -- reads ----------------------------------------------------------------------------

    def _settings_of(self, user_id: str) -> ProfileSettingsData:
        return self._settings.get(user_id, ProfileSettingsData())

    def get_profile(self, user_id):
        with self._data:
            return copy.deepcopy(self._slots.get(user_id, {}))

    def revision(self, user_id):
        with self._data:
            return self._revisions.get(user_id, 0)

    def observations(self, user_id, since=None, include_quarantined=False):
        with self._data:
            rows = self._observations.get(user_id, [])
            return copy.deepcopy(
                sorted(
                    (
                        o
                        for o in rows
                        if (since is None or o.at >= since)
                        and (include_quarantined or not o.quarantined)
                    ),
                    key=lambda o: o.at,
                )
            )

    def users_with_pending(self):
        with self._data:
            return [
                u for u, rows in self._observations.items() if any(not o.consumed_at for o in rows)
            ]

    def proposals(self, user_id, status=None):
        with self._data:
            return copy.deepcopy(
                [p for p in self._proposals.get(user_id, []) if status in (None, p.status)]
            )

    def get_settings(self, user_id):
        with self._data:
            return replace(self._settings_of(user_id))

    # -- unlocked helpers (called only inside `fn`) -----------------------------------------

    def _do_set_stated(self, user_id, slot_key, value, now) -> None:
        self._slots.setdefault(user_id, {})[slot_key] = SlotValue(
            slot_key, copy.deepcopy(value), "stated", [], now
        )

    def _pending(self, user_id, slot_key) -> Proposal | None:
        return next(
            (
                p
                for p in self._proposals.get(user_id, [])
                if p.slot_key == slot_key and p.status == "pending"
            ),
            None,
        )

    def _do_expire_user(self, user_id, now) -> int:
        slots = self._slots.get(user_id, {})
        stale = [
            k
            for k, s in slots.items()
            if s.status == "inferred" and s.expires_at and s.expires_at <= now
        ]
        for key in stale:
            del slots[key]
        return len(stale)

    # -- writes ---------------------------------------------------------------------------

    def set_inferred(
        self, user_id, slot_key, value, evidence, now, expires_at=None, expected_revision=None
    ):
        def fn():
            current = self._slots.get(user_id, {}).get(slot_key)
            if current and current.status == "stated":
                return _refused(STATED_WINS)
            if self._pending(user_id, slot_key):
                return _refused(PENDING_PROPOSAL)
            self._slots.setdefault(user_id, {})[slot_key] = SlotValue(
                slot_key,
                copy.deepcopy(value),
                "inferred",
                copy.deepcopy(cap_evidence(evidence)),
                now,
                expires_at,
            )
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    def set_stated(self, user_id, slot_key, value, now, expected_revision=None):
        return self._locked_write(
            (user_id,),
            expected_revision=expected_revision,
            needs_store_enabled=True,
            fn=lambda: self._do_set_stated(user_id, slot_key, value, now),
        )

    def release(self, user_id, slot_key, now):
        def fn():
            slot = self._slots.get(user_id, {}).get(slot_key)
            if slot is None:
                return NOTHING
            self._slots[user_id][slot_key] = replace(slot, status="inferred", updated_at=now)
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def delete_slot(self, user_id, slot_key):
        def fn():
            removed = self._slots.get(user_id, {}).pop(slot_key, None)
            return APPLIED if removed else NOTHING

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def delete_slot_if_inferred(self, user_id, slot_key, expected_revision=None):
        def fn():
            slot = self._slots.get(user_id, {}).get(slot_key)
            if slot is None:
                return NOTHING
            if slot.status != "inferred":
                return _refused(STATED_WINS)
            del self._slots[user_id][slot_key]
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=False, fn=fn
        )

    def clear(self, user_id):
        def fn():
            for table in (self._slots, self._observations, self._proposals):
                table.pop(user_id, None)

        # Always applied: the bump is what stops a job that started before the clear.
        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def add_observation(self, observation, expected_revision=None):
        def fn():
            rows = self._observations.setdefault(observation.user_id, [])
            if any(o.message_id == observation.message_id for o in rows):
                return _refused(DUPLICATE)
            stored = copy.deepcopy(observation)
            stored.id = self._next_observation_id
            self._next_observation_id += 1
            rows.append(stored)
            return APPLIED

        return self._locked_write(
            (observation.user_id,),
            expected_revision=expected_revision,
            needs_store_enabled=True,
            fn=fn,
        )

    def release_observation(self, user_id, message_id, text, signals, expected_revision=None):
        def fn():
            for obs in self._observations.get(user_id, []):
                if obs.message_id == message_id and obs.quarantined:
                    obs.quarantined, obs.text, obs.signals = False, text, copy.deepcopy(signals)
                    obs.consumed_at = None  # a kept turn is consolidated like any other
                    return APPLIED
            return NOTHING

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    def _do_quarantine(self, user_id, message_id) -> None:
        for obs in self._observations.get(user_id, []):
            if obs.message_id == message_id:
                obs.quarantined, obs.text, obs.signals = True, "", {}

    def mark_consumed(self, user_id, observation_ids, now, expected_revision=None):
        def fn():
            wanted = set(observation_ids)
            for obs in self._observations.get(user_id, []):
                if obs.consumed_at is None and obs.id in wanted:
                    obs.consumed_at = now

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=False, fn=fn
        )

    def add_proposal(
        self, user_id, slot_key, value, quote, now, expected_revision=None, source_message_id=""
    ):
        def fn():
            pending = self._pending(user_id, slot_key)
            if pending is not None and pending.value == value:
                return _refused(DUPLICATE, pending)
            if pending is not None:
                # One confirmation per slot, about the latest value. The old row stops being
                # pending, so the id the learner saw can no longer be resolved, and its words
                # were never accepted.
                pending.status = "rejected"
                if pending.source_message_id and pending.source_message_id != source_message_id:
                    self._do_quarantine(user_id, pending.source_message_id)
            proposal = Proposal(
                uuid.uuid4().hex,
                user_id,
                slot_key,
                copy.deepcopy(value),
                quote,
                "pending",
                now,
                source_message_id,
            )
            self._proposals.setdefault(user_id, []).append(proposal)
            return _Outcome(proposal=proposal)

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    def resolve_proposal(self, user_id, proposal_id, accept, now):
        def fn():
            for proposal in self._proposals.get(user_id, []):
                if proposal.id == proposal_id and proposal.status == "pending":
                    proposal.status = "accepted" if accept else "rejected"
                    if accept:
                        self._do_set_stated(user_id, proposal.slot_key, proposal.value, now)
                    elif proposal.source_message_id:
                        self._do_quarantine(user_id, proposal.source_message_id)
                    return _Outcome(proposal=proposal)
            return NOTHING

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=accept, fn=fn
        )

    def move(self, from_user_id, to_user_id):
        if from_user_id == to_user_id:
            return WriteResult(False, None, self.revision(to_user_id))

        def fn():
            target = self._slots.setdefault(to_user_id, {})
            for key, slot in self._slots.pop(from_user_id, {}).items():
                target[key] = _winner(slot, target[key]) if key in target else slot
            seen = {o.message_id for o in self._observations.get(to_user_id, [])}
            pending = {
                p.slot_key for p in self._proposals.get(to_user_id, []) if p.status == "pending"
            }
            for table, keep in (
                (self._observations, lambda o: o.message_id not in seen),
                (self._proposals, lambda p: p.status != "pending" or p.slot_key not in pending),
            ):
                rows = [row for row in table.pop(from_user_id, []) if keep(row)]
                for row in rows:
                    row.user_id = to_user_id
                table.setdefault(to_user_id, []).extend(rows)
            source_settings = self._settings.pop(from_user_id, None)
            if source_settings:
                self._settings[to_user_id] = _stricter(
                    self._settings_of(to_user_id), source_settings
                )

        return self._locked_write(
            (from_user_id, to_user_id), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        def fn():
            if personalize_enabled is None and store_enabled is None:
                return NOTHING
            current = replace(self._settings_of(user_id))
            if personalize_enabled is not None:
                current.personalize_enabled = personalize_enabled
            if store_enabled is not None:
                current.store_enabled = store_enabled
            self._settings[user_id] = current
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def expire(self, now):
        with self._data:
            users = list(self._slots)
        return sum(self._expire_one(user_id, now)[1] for user_id in users)

    def expire_user(self, user_id, now):
        return self._expire_one(user_id, now)[0]

    def _expire_one(self, user_id, now) -> tuple[WriteResult, int]:
        deleted = [0]

        def fn():
            deleted[0] = self._do_expire_user(user_id, now)
            return APPLIED if deleted[0] else NOTHING

        result = self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )
        return result, deleted[0]


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


def _lock_settings(user_ids) -> dict:
    """Lock (creating first when missing) the settings rows, in sorted user-id order."""
    from chat.models import ProfileSettings

    ids = sorted(set(user_ids))
    for user_id in ids:
        ProfileSettings.objects.get_or_create(user_id=user_id)
    rows = ProfileSettings.objects.select_for_update().filter(user_id__in=ids).order_by("user_id")
    return {row.user_id: row for row in rows}


class DjangoProfileStore:
    """Postgres-backed store over the Profile* models in chat.models.

    Every write locks the user's ProfileSettings row (`_locked_write`); `move` locks both
    identities' rows in sorted user-id order.
    """

    # -- the one write path ---------------------------------------------------------------

    def _locked_write(
        self,
        user_ids: tuple[str, ...],
        *,
        expected_revision: int | None,
        needs_store_enabled: bool,
        fn: Callable[[dict], _Outcome | None],
    ) -> WriteResult:
        from django.db import transaction
        from django.db.models import F

        from chat.models import ProfileSettings

        with transaction.atomic():
            locked = _lock_settings(user_ids)
            first = locked[user_ids[0]]
            reason = _refusal(
                first.store_enabled, first.revision, expected_revision, needs_store_enabled
            )
            if reason:
                return WriteResult(False, reason, first.revision)  # type: ignore[arg-type]
            outcome = fn(locked) or APPLIED
            revision = first.revision
            if outcome.applied:
                ProfileSettings.objects.filter(user_id__in=list(locked)).update(
                    revision=F("revision") + 1
                )
                revision += 1
        return WriteResult(outcome.applied, outcome.reason, revision, outcome.proposal)  # type: ignore[arg-type]

    # -- reads ----------------------------------------------------------------------------

    def get_profile(self, user_id):
        from chat.models import ProfileSlot

        return {
            row.slot_key: _slot_from_row(row) for row in ProfileSlot.objects.filter(user_id=user_id)
        }

    def revision(self, user_id):
        from chat.models import ProfileSettings

        return (
            ProfileSettings.objects.filter(user_id=user_id)
            .values_list("revision", flat=True)
            .first()
            or 0
        )

    def observations(self, user_id, since=None, include_quarantined=False):
        from chat.models import ProfileObservation

        rows = ProfileObservation.objects.filter(user_id=user_id)
        if not include_quarantined:
            rows = rows.filter(quarantined=False)
        if since is not None:
            rows = rows.filter(created_at__gte=since)
        return [_observation_from_row(r) for r in rows.order_by("created_at", "id")]

    def users_with_pending(self):
        from chat.models import ProfileObservation

        return list(
            ProfileObservation.objects.filter(consumed_at__isnull=True)
            .order_by("user_id")
            .values_list("user_id", flat=True)
            .distinct()
        )

    def proposals(self, user_id, status=None):
        from chat.models import ProfileProposal

        rows = ProfileProposal.objects.filter(user_id=user_id)
        if status:
            rows = rows.filter(status=status)
        return [_proposal_from_row(r) for r in rows]

    def get_settings(self, user_id):
        from chat.models import ProfileSettings

        row = ProfileSettings.objects.filter(user_id=user_id).first()
        return (
            ProfileSettingsData(row.personalize_enabled, row.store_enabled)
            if row
            else ProfileSettingsData()
        )

    # -- unlocked helpers (called only inside `fn`) -----------------------------------------

    @staticmethod
    def _do_set_stated(user_id, slot_key, value, now) -> None:
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

    @staticmethod
    def _do_expire(queryset) -> int:
        from chat.models import ProfileSlot

        deleted, _ = queryset.filter(
            status=ProfileSlot.Status.INFERRED, expires_at__isnull=False
        ).delete()
        return deleted

    # -- writes ---------------------------------------------------------------------------

    def set_inferred(
        self, user_id, slot_key, value, evidence, now, expires_at=None, expected_revision=None
    ):
        from chat.models import ProfileProposal, ProfileSlot

        fields = {
            "value": value,
            "status": ProfileSlot.Status.INFERRED,
            "evidence": _evidence_to_json(evidence),
            "updated_at": now,
            "expires_at": expires_at,
        }

        def fn(_locked):
            row = (
                ProfileSlot.objects.select_for_update()
                .filter(user_id=user_id, slot_key=slot_key)
                .first()
            )
            if row is not None and row.status == ProfileSlot.Status.STATED:
                return _refused(STATED_WINS)  # checked before a pending proposal, as in memory
            if ProfileProposal.objects.filter(
                user_id=user_id, slot_key=slot_key, status=ProfileProposal.Status.PENDING
            ).exists():
                return _refused(PENDING_PROPOSAL)
            if row is None:
                row, created = ProfileSlot.objects.get_or_create(
                    user_id=user_id, slot_key=slot_key, defaults=fields
                )
                if created:
                    return APPLIED
                row = ProfileSlot.objects.select_for_update().get(pk=row.pk)
                if row.status == ProfileSlot.Status.STATED:
                    return _refused(STATED_WINS)
            for name, field_value in fields.items():
                setattr(row, name, field_value)
            row.save(update_fields=list(fields))
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    def set_stated(self, user_id, slot_key, value, now, expected_revision=None):
        return self._locked_write(
            (user_id,),
            expected_revision=expected_revision,
            needs_store_enabled=True,
            fn=lambda _locked: self._do_set_stated(user_id, slot_key, value, now),
        )

    def release(self, user_id, slot_key, now):
        from chat.models import ProfileSlot

        def fn(_locked):
            updated = ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key).update(
                status=ProfileSlot.Status.INFERRED, updated_at=now
            )
            return APPLIED if updated else NOTHING

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def delete_slot(self, user_id, slot_key):
        from chat.models import ProfileSlot

        def fn(_locked):
            deleted, _ = ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key).delete()
            return APPLIED if deleted else NOTHING

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def delete_slot_if_inferred(self, user_id, slot_key, expected_revision=None):
        from chat.models import ProfileSlot

        def fn(_locked):
            status = (
                ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key)
                .values_list("status", flat=True)
                .first()
            )
            if status is None:
                return NOTHING
            if status != ProfileSlot.Status.INFERRED:
                return _refused(STATED_WINS)
            ProfileSlot.objects.filter(user_id=user_id, slot_key=slot_key).delete()
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=False, fn=fn
        )

    def clear(self, user_id):
        from chat.models import ProfileObservation, ProfileProposal, ProfileSlot

        def fn(_locked):
            for model in (ProfileSlot, ProfileObservation, ProfileProposal):
                model.objects.filter(user_id=user_id).delete()

        # Always applied: the bump is what stops a job that started before the clear.
        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def add_observation(self, observation, expected_revision=None):
        from django.db import IntegrityError, transaction

        from chat.models import ProfileObservation

        def fn(_locked):
            if ProfileObservation.objects.filter(
                user_id=observation.user_id, message_id=observation.message_id
            ).exists():
                return _refused(DUPLICATE)
            try:
                with transaction.atomic():  # the unique constraint backs the check above
                    ProfileObservation.objects.create(
                        user_id=observation.user_id,
                        session_id=observation.session_id,
                        message_id=observation.message_id,
                        text=observation.text,
                        signals=observation.signals,
                        created_at=observation.at,
                        consumed_at=observation.consumed_at,
                        quarantined=observation.quarantined,
                    )
            except IntegrityError:
                return _refused(DUPLICATE)
            return APPLIED

        return self._locked_write(
            (observation.user_id,),
            expected_revision=expected_revision,
            needs_store_enabled=True,
            fn=fn,
        )

    def release_observation(self, user_id, message_id, text, signals, expected_revision=None):
        from chat.models import ProfileObservation

        def fn(_locked):
            kept = ProfileObservation.objects.filter(
                user_id=user_id, message_id=message_id, quarantined=True
            ).update(quarantined=False, text=text, signals=signals, consumed_at=None)
            return APPLIED if kept else NOTHING

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    @staticmethod
    def _do_quarantine(user_id, message_id) -> None:
        from chat.models import ProfileObservation

        ProfileObservation.objects.filter(user_id=user_id, message_id=message_id).update(
            quarantined=True, text="", signals={}
        )

    def mark_consumed(self, user_id, observation_ids, now, expected_revision=None):
        from chat.models import ProfileObservation

        def fn(_locked):
            ProfileObservation.objects.filter(
                user_id=user_id, id__in=list(observation_ids), consumed_at__isnull=True
            ).update(consumed_at=now)

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=False, fn=fn
        )

    def add_proposal(
        self, user_id, slot_key, value, quote, now, expected_revision=None, source_message_id=""
    ):
        from chat.models import ProfileProposal

        def fn(_locked):
            pending = ProfileProposal.objects.filter(
                user_id=user_id, slot_key=slot_key, status=ProfileProposal.Status.PENDING
            ).first()
            if pending is not None and pending.value == value:
                return _refused(DUPLICATE, _proposal_from_row(pending))
            if pending is not None:
                # One confirmation per slot, about the latest value. The old row stops being
                # pending, so the id the learner saw can no longer be resolved.
                pending.status = ProfileProposal.Status.REJECTED
                pending.save(update_fields=["status"])
                if pending.source_message_id and pending.source_message_id != source_message_id:
                    self._do_quarantine(user_id, pending.source_message_id)
            row = ProfileProposal.objects.create(
                user_id=user_id,
                slot_key=slot_key,
                value=value,
                quote=quote,
                created_at=now,
                source_message_id=source_message_id,
            )
            return _Outcome(proposal=_proposal_from_row(row))

        return self._locked_write(
            (user_id,), expected_revision=expected_revision, needs_store_enabled=True, fn=fn
        )

    def resolve_proposal(self, user_id, proposal_id, accept, now):
        from chat.models import ProfileProposal

        def fn(_locked):
            row = (
                ProfileProposal.objects.select_for_update()
                .filter(user_id=user_id, id=proposal_id, status=ProfileProposal.Status.PENDING)
                .first()
            )
            if row is None:
                return NOTHING
            row.status = (
                ProfileProposal.Status.ACCEPTED if accept else ProfileProposal.Status.REJECTED
            )
            row.save(update_fields=["status"])
            if accept:
                self._do_set_stated(user_id, row.slot_key, row.value, now)
            elif row.source_message_id:
                self._do_quarantine(user_id, row.source_message_id)
            return _Outcome(proposal=_proposal_from_row(row))

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=accept, fn=fn
        )

    def move(self, from_user_id, to_user_id):
        from chat.models import ProfileObservation, ProfileProposal, ProfileSettings, ProfileSlot

        if from_user_id == to_user_id:
            return WriteResult(False, None, self.revision(to_user_id))

        def fn(locked):
            # A message or a pending slot the target already has stays the target's.
            ProfileObservation.objects.filter(
                user_id=from_user_id,
                message_id__in=ProfileObservation.objects.filter(user_id=to_user_id).values(
                    "message_id"
                ),
            ).delete()
            ProfileProposal.objects.filter(
                user_id=from_user_id,
                status=ProfileProposal.Status.PENDING,
                slot_key__in=ProfileProposal.objects.filter(
                    user_id=to_user_id, status=ProfileProposal.Status.PENDING
                ).values("slot_key"),
            ).delete()
            ProfileObservation.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
            ProfileProposal.objects.filter(user_id=from_user_id).update(user_id=to_user_id)
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
            )
            # The source identity reverts to defaults; its row stays so its revision survives.
            ProfileSettings.objects.filter(user_id=from_user_id).update(
                personalize_enabled=True, store_enabled=True
            )

        return self._locked_write(
            (from_user_id, to_user_id), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def set_settings(self, user_id, personalize_enabled=None, store_enabled=None):
        from chat.models import ProfileSettings

        changes = {}
        if personalize_enabled is not None:
            changes["personalize_enabled"] = personalize_enabled
        if store_enabled is not None:
            changes["store_enabled"] = store_enabled

        def fn(_locked):
            if not changes:
                return NOTHING
            # Only the fields passed: a change to the other toggle is not undone.
            ProfileSettings.objects.filter(user_id=user_id).update(**changes)
            return APPLIED

        return self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )

    def expire(self, now):
        from chat.models import ProfileSlot

        users = (
            ProfileSlot.objects.filter(
                status=ProfileSlot.Status.INFERRED, expires_at__isnull=False, expires_at__lte=now
            )
            .order_by("user_id")
            .values_list("user_id", flat=True)
            .distinct()
        )
        return sum(self._expire_one(user_id, now)[1] for user_id in list(users))

    def expire_user(self, user_id, now):
        return self._expire_one(user_id, now)[0]

    def _expire_one(self, user_id, now) -> tuple[WriteResult, int]:
        from chat.models import ProfileSlot

        deleted = [0]

        def fn(_locked):
            deleted[0] = self._do_expire(
                ProfileSlot.objects.filter(user_id=user_id, expires_at__lte=now)
            )
            return APPLIED if deleted[0] else NOTHING

        result = self._locked_write(
            (user_id,), expected_revision=None, needs_store_enabled=False, fn=fn
        )
        return result, deleted[0]


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
        row.quarantined,
    )


def _proposal_from_row(row) -> Proposal:
    return Proposal(
        row.id,
        row.user_id,
        row.slot_key,
        row.value,
        row.quote,
        row.status,
        row.created_at,
        row.source_message_id,
    )
