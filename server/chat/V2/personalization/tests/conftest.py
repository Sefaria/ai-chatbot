"""Shared fixtures for personalization tests."""

from datetime import UTC, datetime, timedelta

import pytest

from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.store import DjangoProfileStore, InMemoryProfileStore
from chat.V2.personalization.types import Evidence

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def day(n: int) -> datetime:
    """A time `n` days after NOW (negative for the past)."""
    return NOW + timedelta(days=n)


def evidence(quote="q", session="s1", message="m1", at=NOW) -> Evidence:
    return Evidence(quote, session, message, at)


@pytest.fixture
def registry() -> SlotRegistry:
    return SlotRegistry.load()


@pytest.fixture(params=["memory", pytest.param("django", marks=pytest.mark.django_db)])
def store(request):
    """Every store test runs against both implementations."""
    if request.param == "memory":
        return InMemoryProfileStore()
    return DjangoProfileStore()


def make_turn(text, *, user="u1", session="s1", message="m1", at=NOW, reply="An answer."):
    from chat.V2.personalization.types import Turn

    return Turn(user, session, message, text, reply, at)


def make_strategy(name, store, registry, responder=lambda system, user: "{}", jev=None):
    from chat.V2.personalization.llm import FakeLLMClient, UsageLedger
    from chat.V2.personalization.strategies.base import build_strategy

    llm, ledger = FakeLLMClient(responder), UsageLedger()
    strategy = build_strategy(name, store, registry, llm, ledger, "claude-haiku-5-5", jev=jev)
    return strategy, llm, ledger


@pytest.fixture(autouse=True)
def drain_after_turn_jobs():
    """A background after-turn job must not outlive its test: it commits on its own connection and
    would leak rows into the next one."""
    from chat.V2.personalization import runtime

    runtime.wait_for_idle(5)
    yield
    runtime.wait_for_idle(5)
