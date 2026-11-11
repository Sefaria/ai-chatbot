"""The update-rule arms of the `aggressiveness` aspect: how many signals change the profile.

Every turn the small model answers the same closed-set questions about the learner's latest prompt
(`HaikuTypedClient`); each confident answer is one supporting signal for one candidate value of one
slot. `SupportCounter` keeps the signals per slot; `decide` turns the counts into "set this value"
or "change nothing". The arms differ only in `min_support` and `ratio`:

- light: `min_support=3, ratio=2.0` (at least 3 signals and at least twice any rival's support);
- aggressive: `min_support=1, ratio=0.0` (a ratio of 0 means no rival test: only the latest signal
  is kept, so the latest signal wins).

The arms ignore the slots' `min_sessions` (that rule belongs to the nightly design). A turn the
sensitive screen drops is never stored, counted or sent to the typed client; a slot the learner set
is never asked about, counted or overwritten. `text_categories` is a list slot: a category is active
once it has `min_support` signals, the three best are kept, and a ratio of 0 replaces the list with
the latest turn's categories.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from .registry import SlotRegistry
from .signals import detect_language, is_sensitive
from .store import ProfileStore
from .strategies import typed_questions as q
from .typed import TypedClient
from .types import Evidence, Turn

LANGUAGE_SLOT = "preferred_language"
CATEGORIES_SLOT = "text_categories"
EVIDENCE_CHARS = 80  # the first characters of the prompt: a verbatim quote, as for hybrid_jev


def decide(
    counts: dict[str, int], current: str | None, *, min_support: int, ratio: float
) -> str | None:
    """The value to set, or None to change nothing.

    The leader is the candidate with the most signals; a tie for the lead decides nothing. It acts
    only with at least `min_support` signals and at least `ratio` times the support of the best
    rival (the incumbent `current` counts as a rival with its own count, 0 if it has none). The
    leader already being `current` is also "change nothing".
    """
    live = {value: n for value, n in counts.items() if n > 0}
    if not live:
        return None
    top = max(live.values())
    leaders = [value for value, n in live.items() if n == top]
    if len(leaders) != 1:
        return None
    leader = leaders[0]
    if top < min_support:
        return None
    rival = max((n for value, n in live.items() if value != leader), default=0)
    if current is not None and current != leader:
        rival = max(rival, live.get(current, 0))
    if top < ratio * rival:
        return None
    return None if leader == current else leader


class SupportCounter:
    """Supporting signals per slot and candidate value, for one learner.

    With `latest_only` (the aggressive arm) a new signal for a slot replaces what was counted:
    only the latest signal is kept.
    """

    def __init__(self, latest_only: bool = False):
        self.latest_only = latest_only
        self._counts: dict[str, Counter] = {}

    def add(self, slot: str, values: str | list[str]) -> None:
        """One turn's signal for `slot`: one value, or the set of values the turn pointed at."""
        values = [values] if isinstance(values, str) else list(dict.fromkeys(values))
        if not values:
            return
        counter = self._counts.setdefault(slot, Counter())
        if self.latest_only:
            counter.clear()
        for value in values:
            counter[value] += 1

    def counts(self, slot: str) -> dict[str, int]:
        return dict(self._counts.get(slot, {}))


class ArmUpdater:
    """One arm of `aggressiveness`: counts the turn's signals and writes the profile by `decide`."""

    def __init__(
        self,
        store: ProfileStore,
        typed: TypedClient,
        *,
        min_support: int,
        ratio: float,
        registry: SlotRegistry | None = None,
    ):
        self.store = store
        self.typed = typed
        self.min_support = min_support
        self.ratio = ratio
        self.registry = registry or SlotRegistry.load()
        self._counters: dict[str, SupportCounter] = {}
        self.stats = Counter()  # turns, skipped_sensitive, typed_calls, writes, flips, refused

    def counter(self, user_id: str) -> SupportCounter:
        if user_id not in self._counters:
            self._counters[user_id] = SupportCounter(latest_only=self.ratio <= 0)
        return self._counters[user_id]

    def _open(self, user_id: str) -> set[str]:
        """The slots this arm may fill: every one the learner has not set."""
        profile = self.store.get_profile(user_id)
        return {
            key
            for key in (LANGUAGE_SLOT, *q.CHOICE_SLOTS, CATEGORIES_SLOT)
            if not (key in profile and profile[key].status == "stated")
        }

    def _questions(self, open_slots: set[str]):
        questions = {}
        questions.update(
            {k: v for k, v in q.choice_questions(self.registry).items() if k in open_slots}
        )
        if CATEGORIES_SLOT in open_slots:
            questions.update(q.category_questions(self.registry))
        return q.to_typed(questions)

    def on_turn(self, user_id: str, turn: Turn) -> list[str]:
        """Handle one turn; returns the slots whose value changed."""
        self.stats["turns"] += 1
        if is_sensitive(turn.user_message):  # never stored, counted or sent to a model
            self.stats["skipped_sensitive"] += 1
            return []
        now = turn.at
        self.store.expire_user(user_id, now)
        open_slots = self._open(user_id)
        counter = self.counter(user_id)
        if LANGUAGE_SLOT in open_slots and (lang := detect_language(turn.user_message)):
            counter.add(LANGUAGE_SLOT, lang)
        questions = self._questions(open_slots - {LANGUAGE_SLOT})
        if questions:
            state = {
                "learner_prompts": [
                    {
                        "session": 1,
                        "date": f"{now:%Y-%m-%d}",
                        "text": turn.user_message[: q.PROMPT_CHARS],
                    }
                ]
            }
            self.stats["typed_calls"] += 1
            answers = {
                a.key: a
                for a in self.typed.answer(json.dumps(state, ensure_ascii=False), questions)
            }
            self._count_answers(counter, answers, open_slots)
        return self._apply(user_id, turn, counter, open_slots)

    def _count_answers(self, counter: SupportCounter, answers: dict, open_slots: set[str]) -> None:
        for key in q.CHOICE_SLOTS:
            answer = answers.get(key)
            if key not in open_slots or answer is None:
                continue
            if (
                answer.choice in (None, q.UNCLEAR)
                or answer.confidence is None
                or answer.confidence < q.SET_MIN_CONFIDENCE
            ):
                continue
            if self.registry.validate(key, answer.choice) is not None:
                counter.add(key, answer.choice)
        if CATEGORIES_SLOT in open_slots:
            named = [
                name
                for name in self.registry.get(CATEGORIES_SLOT).allowed
                if (a := answers.get(f"{q.CATEGORY_PREFIX}{name}")) is not None
                and a.noul is not None
                and a.noul >= q.CATEGORY_MIN_NOUL
            ]
            counter.add(CATEGORIES_SLOT, named)

    def _apply(self, user_id, turn, counter, open_slots) -> list[str]:
        profile = self.store.get_profile(user_id)
        evidence = _evidence(turn)
        changed = []
        for key in (LANGUAGE_SLOT, *q.CHOICE_SLOTS):
            if key not in open_slots:
                continue
            current = profile[key].value if key in profile else None
            value = decide(
                counter.counts(key), current, min_support=self.min_support, ratio=self.ratio
            )
            if value is not None and self._write(user_id, key, value, evidence, turn.at):
                changed.append(key)
                self.stats["flips" if current is not None else "fills"] += 1
        if CATEGORIES_SLOT in open_slots:
            current = profile[CATEGORIES_SLOT].value if CATEGORIES_SLOT in profile else None
            value = self._categories(counter.counts(CATEGORIES_SLOT))
            if (
                value
                and value != current
                and self._write(user_id, CATEGORIES_SLOT, value, evidence, turn.at)
            ):
                changed.append(CATEGORIES_SLOT)
                self.stats["flips" if current is not None else "fills"] += 1
        return changed

    def _categories(self, counts: dict[str, int]) -> list[str]:
        """The categories with `min_support` signals, most signals first (ties keep the registry's order), at most
        the slot's `max_items`. The order is deterministic."""
        slot = self.registry.get(CATEGORIES_SLOT)
        ranked = sorted(
            (name for name, n in counts.items() if n >= self.min_support),
            key=lambda name: -counts[name],
        )
        return ranked[: slot.max_items or len(ranked)]

    def _write(
        self, user_id: str, key: str, value, evidence: list[Evidence], now: datetime
    ) -> bool:
        slot = self.registry.get(key)
        expires = now + timedelta(days=slot.shelf_life_days) if slot.shelf_life_days else None
        result = self.store.set_inferred(user_id, key, value, evidence, now, expires)
        if result.applied:
            self.stats["writes"] += 1
        elif result.reason:
            self.stats[f"refused_{result.reason}"] += 1
        return result.applied


def _evidence(turn: Turn) -> list[Evidence]:
    quote = turn.user_message.strip()[:EVIDENCE_CHARS]
    return [Evidence(quote, turn.session_id, turn.message_id, turn.at)] if quote else []
