"""E. hybrid_jev: Jev judges the closed-set slots, an LLM consolidates only the free-text slots.

On every turn the code sets `preferred_language` (as `hybrid` does), then makes ONE Jev request
over the recent prompts for `hebrew_fluency`, `terminology_comfort` and one yes/no per text
category, and applies the verdicts with thresholds from jev_questions.py. Jev cannot quote, so the
evidence for a Jev-set slot is the first 80 characters of the latest prompt (a verbatim substring
of it). A standing instruction gets a second, small Jev request; a lasting style preference
becomes a pending proposal holding the learner's own sentence, a request to exclude sources is
never stored. The nightly batch is the nightly consolidation limited to the free-text slots.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..jev import JevClient, JevError, JevQuestion, JevResponse
from ..llm import LLMResult, estimate_cost
from ..registry import SlotRegistry
from ..signals import find_standing_instruction, is_sensitive
from ..store import ProfileStore
from ..types import Observation, SlotValue, Turn
from . import jev_questions as q
from .base import TurnResult, truncate_prompt, verbatim_evidence
from .hybrid import handle_propose_memory_update
from .nightly import WINDOW_DAYS, NightlyStrategy

logger = logging.getLogger(__name__)

TEXT_SLOTS = frozenset({"answer_style", "learning_habits"})


class HybridJevStrategy(NightlyStrategy):
    name = "hybrid_jev"
    batch_slots = TEXT_SLOTS

    def __init__(
        self, store: ProfileStore, registry: SlotRegistry, llm, ledger, model, jev: JevClient
    ):
        super().__init__(store, registry, llm, ledger, model)
        self.jev = jev

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.log_observation(turn)
        result = TurnResult()
        if self.set_language_now(turn, now):
            result.updated_slots.append("preferred_language")
        try:
            result.updated_slots += self.judge_closed_slots(turn, now)
        except JevError as exc:
            logger.warning("Jev request failed on the hot path: %s", exc)
            result.errors += 1
        instruction = find_standing_instruction(truncate_prompt(turn.user_message))
        if instruction is not None:
            try:
                self.judge_instruction(turn, instruction, now, result)
            except JevError as exc:
                logger.warning("Jev instruction request failed: %s", exc)
                result.errors += 1
        return result

    def ask_jev(self, user_id: str, state: dict, questions: dict[str, JevQuestion]) -> JevResponse:
        response = self.jev.ask(state, questions)
        cost = estimate_cost(q.JEV_MODEL, response.input_tokens, 0)
        self.ledger.record(
            self.name, "turn", user_id, q.JEV_MODEL,
            LLMResult("", response.input_tokens, 0, cost, response.latency_ms),
        )  # fmt: skip
        return response

    # -- closed-set slots ---------------------------------------------------------------

    def learner_prompts(self, user_id: str, now: datetime) -> list[dict]:
        recent: list[Observation] = [
            o
            for o in self.store.observations(user_id, since=now - timedelta(days=WINDOW_DAYS))
            if not is_sensitive(o.text)
        ][-q.WINDOW_PROMPTS :]
        sessions: dict[str, int] = {}
        return [
            {
                "session": sessions.setdefault(o.session_id, len(sessions) + 1),
                "date": f"{o.at:%Y-%m-%d}",
                "text": o.text[: q.PROMPT_CHARS],
            }
            for o in recent
        ]

    def judge_closed_slots(self, turn: Turn, now: datetime) -> list[str]:
        questions = {**q.choice_questions(self.registry), **q.category_questions(self.registry)}
        state = {"learner_prompts": self.learner_prompts(turn.user_id, now)}
        answers = self.ask_jev(turn.user_id, state, questions).answers
        message = truncate_prompt(turn.user_message)
        proof = verbatim_evidence(
            message.strip()[:80], message, turn.session_id, turn.message_id, now
        )
        evidence = [proof] if proof else []
        profile = self.store.get_profile(turn.user_id)
        updated = []

        for key in q.CHOICE_SLOTS:
            value = self.decide_choice(key, answers.get(key), profile.get(key))
            if value and self.apply_update(turn.user_id, key, value, evidence, now):
                updated.append(key)

        scored = [
            (answers[k].noul, name)
            for name in self.registry.get("text_categories").allowed
            if (k := f"{q.CATEGORY_PREFIX}{name}") in answers and answers[k].noul is not None
        ]
        chosen = [
            n for score, n in sorted(scored, key=lambda s: -s[0]) if score >= q.CATEGORY_MIN_NOUL
        ]
        if chosen and self.apply_update(turn.user_id, "text_categories", chosen[:3], evidence, now):
            updated.append("text_categories")
        return updated

    def decide_choice(self, key: str, answer, current: SlotValue | None) -> str | None:
        """Set an empty slot at SET_MIN_CONFIDENCE; replace a different value only at CHANGE_MIN."""
        if answer is None or answer.choice in (None, q.UNCLEAR) or answer.confidence is None:
            return None
        if answer.confidence < q.SET_MIN_CONFIDENCE:
            return None
        value = self.registry.validate(key, answer.choice)
        if value is None or (current and current.value == value):
            return None
        if current and answer.confidence < q.CHANGE_MIN_CONFIDENCE:
            return None
        return value

    # -- standing instructions ----------------------------------------------------------

    def judge_instruction(self, turn: Turn, instruction: str, now: datetime, result: TurnResult):
        answers = self.ask_jev(
            turn.user_id, {"message": instruction}, q.instruction_questions()
        ).answers

        def score(key: str) -> float:
            answer = answers.get(key)
            return answer.noul if answer and answer.noul is not None else 0.0

        if score(q.EXCLUDE_KEY) >= q.INSTRUCTION_MIN_NOUL:
            result.skipped_reason = "instruction_excludes_sources"
            return
        proposal = None
        if score(q.STYLE_KEY) >= q.INSTRUCTION_MIN_NOUL:
            proposal = handle_propose_memory_update(
                self.store,
                self.registry,
                turn.user_id,
                "answer_style",
                instruction,
                instruction,
                now,
            )
        if proposal is None:
            result.skipped_reason = "instruction_needs_settings"
        else:
            result.proposals.append(proposal)

    def blocked_slots(self, user_id: str) -> set[str]:
        return {p.slot_key for p in self.store.proposals(user_id, "pending")}
