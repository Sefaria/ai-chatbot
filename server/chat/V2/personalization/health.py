"""Job health: per job, when it last ran, when it last wrote, and how many users are parked.

A job that keeps running and never writes shows up here. Read-only; no user text is returned.
"""

from __future__ import annotations

from django.db.models import Max

from chat.models import ProfileWriterRun

from . import profile_store
from .profile_job import is_parked

JOBS = (("session", "session"), ("dream", "dream"))  # (name in the API, window on the run row)


def _iso(value):
    return value.isoformat() if value else None


def parked_users(window: str) -> int:
    """Users whose last PARK_AFTER runs in this window all failed or were rejected, with no prompt
    newer than the last of them."""
    candidates = (
        ProfileWriterRun.objects.filter(window=window, status__in=("failed", "rejected"))
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    count = 0
    for user_id in candidates:
        since = (
            profile_store.get_settings(user_id).processed_through if window == "session" else None
        )
        if is_parked(user_id, window, since):
            count += 1
    return count


def snapshot() -> dict:
    jobs = []
    for name, window in JOBS:
        runs = ProfileWriterRun.objects.filter(window=window)
        jobs.append(
            {
                "name": name,
                "lastRunAt": _iso(runs.aggregate(t=Max("started_at"))["t"]),
                "lastWriteAt": _iso(
                    runs.filter(status="applied").aggregate(t=Max("started_at"))["t"]
                ),
                "parked": parked_users(window),
            }
        )
    return {"jobs": jobs}
