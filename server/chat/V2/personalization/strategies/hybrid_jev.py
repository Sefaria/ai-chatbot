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
from datetime import datetime

from ..jev import JevClient, JevError, JevQuestion, JevResponse
from ..llm import LLMResult, estimate_cost
from ..registry import SlotRegistry
from ..signals import find_standing_instructions
from ..store import ProfileStore
from ..types import Evidence, InstructionVerdict, Observation, SlotValue, Turn
from . import jev_questions as q
from .base import TurnResult, classify_turn, is_clean, verbatim_evidence
from .hybrid import handle_propose_memory_update
from .nightly import NightlyStrategy

logger = logging.getLogger(__name__)

UNCERTAIN_LOW, UNCERTAIN_HIGH = 0.35, 0.65  # a noul in this band is uncertain and must not act
TEXT_SLOTS = frozenset({"answer_style", "learning_habits"})


class HybridJevStrategy(NightlyStrategy):
    name = "hybrid_jev"
    batch_slots = TEXT_SLOTS

    def __init__(
        self, store: ProfileStore, registry: SlotRegistry, llm, ledger, model, jev: JevClient
    ):
        super().__init__(store, registry, llm, ledger, model)
        self.jev = jev

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.store.expire_user(turn.user_id, now)
        instructions = find_standing_instructions(turn.user_message)
        # A turn holding a standing instruction is quarantined from the start; it keeps its text
        # only once Jev has cleared every instruction in it as a safe style preference.
        if reason := self.start_turn(turn, now, quarantined=bool(instructions)):
            return TurnResult(skipped_reason=reason)
        result = TurnResult()
        if instructions:
            styled: set[str] = set()
            verdicts = classify_turn(
                instructions, lambda items: self.judge_instructions(turn, items, styled, result)
            )
            if not is_clean(verdicts):
                # Never stored beyond the quarantined row. An instruction Jev could not classify
                # is held back the same way.
                if InstructionVerdict.UNKNOWN in verdicts:
                    self.diagnostics["instruction_unclassified"] += 1
                result.skipped_reason = (
                    "instruction_excludes_sources"
                    if InstructionVerdict.EXCLUSION in verdicts
                    else "instruction_unclassified"
                )
                return result
            if not self.keep_turn(turn).applied:
                result.skipped_reason = "observation_not_kept"
                return result
            for instruction in instructions:
                if instruction not in styled:
                    continue
                proposal = handle_propose_memory_update(
                    self.store, self.registry, turn.user_id, "answer_style", instruction,
                    instruction, now, source_message_id=turn.message_id,
                    submit=lambda call: self.write(turn.user_id, call),
                )  # fmt: skip
                if proposal is not None:
                    result.proposals.append(proposal)
            if not result.proposals:
                result.skipped_reason = "instruction_needs_settings"
        if self.is_stale(turn.user_id):
            return result
        window = self.window_observations(turn.user_id, now)
        if self.update_language(turn.user_id, window, now):
            result.updated_slots.append("preferred_language")
        if not window or self.is_stale(turn.user_id):
            return result  # nothing new for Jev to read
        try:
            result.updated_slots += self.judge_closed_slots(turn, window, now)
        except JevError as exc:
            logger.warning("Jev request failed on the hot path: %s", exc)
            self.diagnostics["jev_error"] += 1
            result.errors += 1
        return result

    def ask_jev(self, user_id: str, state: dict, questions: dict[str, JevQuestion]) -> JevResponse:
        response = self.jev.ask(state, questions)
        cost = estimate_cost(q.JEV_MODEL, response.input_tokens, 0)
        self.ledger.record(
            self.name, "turn", user_id, q.JEV_MODEL,
            LLMResult("", response.input_tokens, 0, cost, response.latency_ms),
        )  # fmt: skip
        self.run.add_usage(response.input_tokens, 0, cost)
        return response

    # -- closed-set slots ---------------------------------------------------------------

    @staticmethod
    def learner_prompts(window: list[Observation]) -> list[dict]:
        sessions: dict[str, int] = {}
        return [
            {
                "session": sessions.setdefault(o.session_id, len(sessions) + 1),
                "date": f"{o.at:%Y-%m-%d}",
                "text": o.text[: q.PROMPT_CHARS],
            }
            for o in window[-q.WINDOW_PROMPTS :]
        ]

    def evidence_for_jev(self, window: list[Observation], now: datetime) -> list[Evidence]:
        """Jev cannot quote, so the evidence is the first 80 characters of the most recent prompt in
        the window that is not excluded and holds no standing instruction."""
        for obs in reversed(window[-q.WINDOW_PROMPTS :]):
            if not find_standing_instructions(obs.text):
                proof = verbatim_evidence(
                    obs.text.strip()[:80], obs.text, obs.session_id, obs.message_id, obs.at
                )
                return [proof] if proof else []
        return []

    def judge_closed_slots(self, turn: Turn, window: list[Observation], now: datetime) -> list[str]:
        questions = {**q.choice_questions(self.registry), **q.category_questions(self.registry)}
        state = {"learner_prompts": self.learner_prompts(window)}
        answers = self.ask_jev(turn.user_id, state, questions).answers
        evidence = self.evidence_for_jev(window, now)
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
        if not chosen and any(UNCERTAIN_LOW <= score <= UNCERTAIN_HIGH for score, _ in scored):
            self.diagnostics["jev_uncertain"] += 1
        if chosen and self.apply_update(turn.user_id, "text_categories", chosen[:3], evidence, now):
            updated.append("text_categories")
        return updated

    def decide_choice(self, key: str, answer, current: SlotValue | None) -> str | None:
        """Set an empty slot at SET_MIN_CONFIDENCE; replace a different value only at CHANGE_MIN."""
        if (
            answer is None
            or answer.choice in (None, q.UNCLEAR)
            or answer.confidence is None
            or answer.confidence < q.SET_MIN_CONFIDENCE
        ):
            self.diagnostics["jev_uncertain"] += 1
            return None
        value = self.registry.validate(key, answer.choice)
        if value is None:
            self.diagnostics["value_invalid"] += 1
            return None
        if current and current.value == value:
            return None  # apply_update would count it as unchanged; nothing to decide
        if current and answer.confidence < q.CHANGE_MIN_CONFIDENCE:
            self.diagnostics["jev_below_change_threshold"] += 1
            return None
        return value

    # -- standing instructions ----------------------------------------------------------

    def judge_instructions(
        self, turn: Turn, instructions: list[str], styled: set[str], result: TurnResult
    ) -> list[InstructionVerdict]:
        """One Jev request per instruction. Both answers must be present with a score; a missing
        answer, an uncertain exclusion score or a Jev error is `UNKNOWN`, never a zero.
        Instructions that are a lasting style preference go into `styled`."""
        verdicts = []
        for instruction in instructions:
            try:
                answers = self.ask_jev(
                    turn.user_id, {"message": instruction}, q.instruction_questions()
                ).answers
            except JevError as exc:
                logger.warning("Jev instruction request failed: %s", exc)
                self.diagnostics["jev_error"] += 1
                result.errors += 1
                verdicts.append(InstructionVerdict.UNKNOWN)
                continue
            scores = [answers.get(q.EXCLUDE_KEY), answers.get(q.STYLE_KEY)]
            if any(a is None or a.noul is None for a in scores):
                self.diagnostics["jev_missing_answer"] += 1
                verdicts.append(InstructionVerdict.UNKNOWN)
                continue
            exclude, style = scores[0].noul, scores[1].noul
            if (
                UNCERTAIN_LOW <= exclude < q.INSTRUCTION_MIN_NOUL
                or UNCERTAIN_LOW <= style < q.INSTRUCTION_MIN_NOUL
            ):
                self.diagnostics["jev_uncertain"] += 1
            if exclude >= q.INSTRUCTION_MIN_NOUL:
                verdicts.append(InstructionVerdict.EXCLUSION)
            elif exclude >= UNCERTAIN_LOW:
                verdicts.append(InstructionVerdict.UNKNOWN)  # exclusion not ruled out
            else:
                verdicts.append(InstructionVerdict.SAFE_STYLE)
                if style >= q.INSTRUCTION_MIN_NOUL:
                    styled.add(instruction)
        return verdicts

    def blocked_slots(self, user_id: str) -> set[str]:
        return {p.slot_key for p in self.store.proposals(user_id, "pending")}
