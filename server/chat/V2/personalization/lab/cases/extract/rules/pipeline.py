"""extract/rules: how far code alone gets. No model, no typed client: `P/signals.py` only.

Two slots are filled and nothing else (the hypothesis: exact on language, useful on text
categories, empty on the rest):

- `preferred_language`: each session votes by the script of its prompts (`session_vote`), and the
  profile follows `profile_language_v2` over the sessions' votes, oldest first.
- `text_categories`: the corpora a learner names (`cited_categories`) in at least two different
  prompts, the most often named first, at most the slot's `max_items` (3). The slot's own
  instruction says "only recurring interests, not a single question", so one mention is no fill.

A turn that the sensitive screen or the standing-instruction finder flags is dropped before it
counts (a standing instruction such as "never show me Rashi" must not read as interest in Rashi).
The two rules are fixed here, before any live run, and are not tuned on the data.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.signals import (
    cited_categories,
    find_standing_instructions,
    is_sensitive,
    profile_language_v2,
    session_vote,
)

MIN_TURNS_PER_CATEGORY = 2
STEPS = ["screen_sensitive", "detect_language", "write_profile"]


class RulesPipeline:
    """The `Pipeline` of the case: it keeps, per learner, the counted prompts and the profile."""

    def __init__(self):
        self.registry = SlotRegistry.load()
        self._texts: dict[str, dict[str, list[str]]] = {}  # user -> session -> prompts, in order
        self._category_turns: dict[str, Counter] = {}
        self._profile: dict[str, dict] = {}
        self._language: dict[str, str | None] = {}
        self._turns = 0
        self._skips: Counter = Counter()
        self._written = 0

    def on_turn(self, user_id, turn) -> None:
        self._turns += 1
        text = turn.user_message
        if is_sensitive(text):
            self._skips["sensitive"] += 1
            return
        if find_standing_instructions(text):
            self._skips["instruction"] += 1
            return
        self._texts.setdefault(user_id, {}).setdefault(turn.session_id, []).append(text)
        counter = self._category_turns.setdefault(user_id, Counter())
        counter.update(set(cited_categories(text)))
        self._write(user_id)

    def run_job(self, user_id, now) -> None:
        """Nothing to consolidate: the rules read the prompts as they come."""

    def _write(self, user_id) -> None:
        profile: dict = {}
        votes = [session_vote(texts) for texts in self._texts.get(user_id, {}).values()]
        language = profile_language_v2(votes, self._language.get(user_id))
        self._language[user_id] = language
        if language:
            profile["preferred_language"] = language
        counter = self._category_turns.get(user_id, Counter())
        ranked = [c for c, n in counter.most_common() if n >= MIN_TURNS_PER_CATEGORY]
        slot = self.registry.get("text_categories")
        categories = self.registry.validate("text_categories", ranked[: slot.max_items])
        if categories:
            profile["text_categories"] = categories
        self._written += len(profile) - len(self._profile.get(user_id, {}))
        self._profile[user_id] = profile

    def profile(self, user_id) -> dict:
        return dict(self._profile.get(user_id, {}))

    def steps(self) -> list[str]:
        return list(STEPS)

    def calls(self) -> list[dict]:
        return []  # no model, no typed client

    def sent_texts(self) -> list[str]:
        return []

    def stats(self) -> dict:
        return {"skips": dict(self._skips), "diagnostics": {}, "auto_accepted": 0}

    def step_counts(self) -> dict:
        kept = self._turns - self._skips["sensitive"]
        return {
            "screen_sensitive": (self._turns, kept),
            "detect_language": (
                kept,
                sum("preferred_language" in p for p in self._profile.values()),
            ),
        }


def build(config, llm=None, typed=None) -> RulesPipeline:
    return RulesPipeline()


# -- the mock: five invented personas, each probing one rule ----------------------------------


def mock_items():
    return json.loads((Path(__file__).parent / "mock_fixture.json").read_text(encoding="utf-8"))[
        "personas"
    ]
