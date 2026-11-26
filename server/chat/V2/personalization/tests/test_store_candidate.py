"""Candidates: a proposed profile held beside the real one. render never reads them."""

from chat.V2.personalization.render import render_profile
from chat.V2.personalization.service import PersonalizationService
from chat.V2.personalization.store import STALE_REVISION

from .conftest import NOW

SLOTS = {"hebrew_fluency": "basic"}
CHANGES = [{"slot": "hebrew_fluency", "old": None, "new": "basic"}]


def test_candidate_not_rendered(store, registry):
    store.set_stated("u1", "terminology_comfort", "new", NOW)
    before = render_profile(store.get_profile("u1"), registry, NOW)
    assert store.set_candidate("u1", SLOTS, CHANGES, None).applied
    assert store.get_candidate("u1")["slots"] == SLOTS
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert render_profile(store.get_profile("u1"), registry, NOW) == before
    service = PersonalizationService(store, registry, None, mode="hybrid")
    assert service.profile_context("u1", NOW) == before


def test_candidate_write_uses_revision(store):
    revision = store.revision("u1")
    stale = store.set_candidate("u1", SLOTS, CHANGES, revision + 5)
    assert not stale.applied and stale.reason == STALE_REVISION
    assert store.get_candidate("u1") is None
    ok = store.set_candidate("u1", SLOTS, CHANGES, revision)
    assert ok.applied and ok.revision == revision + 1
    candidate = store.get_candidate("u1")
    assert candidate["slots"] == SLOTS and candidate["changes"] == CHANGES
    assert candidate["base_revision"] == revision
    # Replacing keeps one candidate per user.
    assert store.set_candidate("u1", {"register": "academic"}, [], ok.revision).applied
    assert store.get_candidate("u1")["slots"] == {"register": "academic"}


def test_clear_removes_candidate(store):
    store.set_candidate("u1", SLOTS, CHANGES, None)
    store.set_candidate("u2", SLOTS, CHANGES, None)
    result = store.clear_candidate("u1")
    assert result.applied
    assert store.get_candidate("u1") is None
    assert store.get_candidate("u2") is not None  # another learner's is untouched
    store.set_candidate("u1", SLOTS, CHANGES, None)
    store.clear("u1")  # erasing a learner's data erases the candidate too
    assert store.get_candidate("u1") is None


def test_register_slot_in_v2(registry):
    assert registry.version == "v2"
    slot = registry.get("register")
    assert slot.allowed == ("traditional", "academic")
    assert slot.shelf_life_days == 60
    assert slot.deterministic  # no model consolidation fills it
    assert registry.validate("register", "academic") == "academic"
    assert registry.validate("register", "pirate") is None
