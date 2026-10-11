"""Postgres-backed SessionStore for Claude Agent SDK transcripts.

Passed as ``ClaudeAgentOptions.session_store`` so the SDK mirrors each session
transcript into ``SdkTranscriptEntry`` rows and can ``resume`` a session on any
pod. Entries are opaque SDK-owned JSON blobs; only ``uuid`` is interpreted, as
an idempotency key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from asgiref.sync import sync_to_async
from claude_agent_sdk.types import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStoreEntry,
    SessionStoreListEntry,
)
from django.db import close_old_connections
from django.db.models import Max

from ...models import SdkTranscriptEntry

T = TypeVar("T")


async def _run_db(fn: Callable[[], T]) -> T:
    """Run ORM work off the event loop, releasing the connection afterwards.

    The SDK calls the store outside any request cycle, so Django never closes
    the executor thread's connection on its own. Not thread-sensitive: the
    default would funnel every concurrent turn's store calls through one
    process-wide thread.
    """

    def _wrapped() -> T:
        try:
            return fn()
        finally:
            close_old_connections()

    return await sync_to_async(_wrapped, thread_sensitive=False)()


def _key_filter(key: SessionKey) -> dict[str, str]:
    return {
        "project_key": key["project_key"],
        "session_id": key["session_id"],
        "subpath": key.get("subpath") or "",
    }


class PostgresSessionStore:
    """SessionStore adapter persisting transcript lines via the Django ORM."""

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        rows = [
            SdkTranscriptEntry(**_key_filter(key), uuid=entry.get("uuid"), data=entry)
            for entry in entries
        ]
        await _run_db(lambda: SdkTranscriptEntry.objects.bulk_create(rows, ignore_conflicts=True))

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        entries = await _run_db(
            lambda: list(
                SdkTranscriptEntry.objects.filter(**_key_filter(key))
                .order_by("id")
                .values_list("data", flat=True)
            )
        )
        return entries or None

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        rows = await _run_db(
            lambda: list(
                SdkTranscriptEntry.objects.filter(project_key=project_key, subpath="")
                .order_by()
                .values("session_id")
                .annotate(last=Max("created_at"))
            )
        )
        return [
            {"session_id": row["session_id"], "mtime": int(row["last"].timestamp() * 1000)}
            for row in rows
        ]

    async def delete(self, key: SessionKey) -> None:
        filters = _key_filter(key)
        if not key.get("subpath"):
            # Deleting a main transcript cascades to its subagent transcripts.
            del filters["subpath"]
        await _run_db(lambda: SdkTranscriptEntry.objects.filter(**filters).delete())

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        return await _run_db(
            lambda: list(
                SdkTranscriptEntry.objects.filter(
                    project_key=key["project_key"], session_id=key["session_id"]
                )
                .exclude(subpath="")
                .order_by()
                .values_list("subpath", flat=True)
                .distinct()
            )
        )
