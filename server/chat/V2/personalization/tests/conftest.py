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
