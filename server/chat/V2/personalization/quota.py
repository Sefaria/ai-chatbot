"""Cost limits and single-flight locks for the Profile job, kept in Postgres (`LabQuota`,
`LabJobLock`). Moved unchanged from the old lab API; the job and the settings API call them.
Lock and quota transactions are short and commit before any model call."""

from __future__ import annotations

import os
from datetime import timedelta

from django.db import transaction

JOB_PER_HOUR_DEFAULT = 10
JOB_PER_DAY_DEFAULT = 300
ALL_KEY = "__all__"
LOCK_STALE_SECONDS = 300


def limit(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, "")), 0)
    except ValueError:
        return default


def take_quota(scope: str, key: str, window_start, limit_: int) -> bool:
    """Count one call against a window; False (and no count) when the window is full."""
    from chat.models import LabQuota

    row, _ = LabQuota.objects.select_for_update().get_or_create(
        scope=scope, key=key, window_start=window_start
    )
    if row.count >= limit_:
        return False
    row.count += 1
    row.save(update_fields=["count"])
    return True


def take_quotas(scope: str, checks) -> bool:
    """All (key, window_start, limit) checks pass or none counts. One short transaction."""
    with transaction.atomic():
        sid = transaction.savepoint()
        for key, window_start, limit_ in checks:
            if not take_quota(scope, key, window_start, limit_):
                transaction.savepoint_rollback(sid)
                return False
        transaction.savepoint_commit(sid)
    return True


def acquire_job_lock(user_id: str, now):
    """Take the user's lock. Returns the lease (the `started_at` written, which only this holder
    carries) or None when a fresh lock is held. A lease is the proof of ownership for release."""
    from chat.models import LabJobLock

    with transaction.atomic():
        lock, _ = LabJobLock.objects.select_for_update().get_or_create(user_id=user_id)
        if (
            lock.running
            and lock.started_at is not None
            and lock.started_at > now - timedelta(seconds=LOCK_STALE_SECONDS)
        ):
            return None
        lock.running, lock.started_at = True, now
        lock.save(update_fields=["running", "started_at"])
    return now


def release_job_lock(user_id: str, lease) -> None:
    """Release only if the row still carries this holder's lease; a worker whose lock was taken
    over as stale leaves the new holder's lock alone."""
    from chat.models import LabJobLock

    LabJobLock.objects.filter(user_id=user_id, running=True, started_at=lease).update(running=False)
