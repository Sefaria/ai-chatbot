"""Tests for the Postgres-backed Claude Agent SDK SessionStore."""

import pytest
from claude_agent_sdk.testing import run_session_store_conformance

from chat.models import SdkTranscriptEntry
from chat.V2.agent.session_store import PostgresSessionStore, _run_db

# ORM calls run on asgiref's executor thread, so rows must be committed to be visible.
pytestmark = pytest.mark.django_db(transaction=True)

KEY = {"project_key": "proj", "session_id": "sess"}


async def _fresh_store():
    await _run_db(lambda: SdkTranscriptEntry.objects.all().delete())
    return PostgresSessionStore()


async def test_conformance():
    await run_session_store_conformance(_fresh_store)


async def test_append_dedups_by_uuid():
    store = await _fresh_store()
    entry = {"type": "user", "uuid": "u1", "message": {"content": "hi"}}
    await store.append(KEY, [entry])
    await store.append(KEY, [entry, {"type": "title", "title": "t"}])
    await store.append(KEY, [{"type": "title", "title": "t"}])

    assert await store.load(KEY) == [
        entry,
        {"type": "title", "title": "t"},
        {"type": "title", "title": "t"},
    ]
