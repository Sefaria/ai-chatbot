"""Job health: per job, when it last ran, and when it last wrote.

A job that keeps running and never writes shows up here. Read-only; no user text is returned.
"""

from __future__ import annotations

from django.db.models import Max

from chat.models import ProfileWriterRun

JOBS = (("session", "session"),)  # (name in the API, window on the run row)


def _iso(value):
    return value.isoformat() if value else None


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
            }
        )
    return {"jobs": jobs}
