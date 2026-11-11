"""Shared contract and rules for the four memory strategies.

The privacy and safety rules live here, in code, not in prompts: sensitive turns never reach a
model or the store, stated slots are never overwritten, model output is validated, evidence
quotes must be verbatim, and prompts are truncated before they are sent.
"""

from __future__ import annotations

import contextvars
import logging
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..jev import JevClient
from ..llm import LLMClient, LLMResult, UsageLedger
from ..parsing import locate_quote, parse_json_object, validate_envelope
from ..registry import SlotRegistry
from ..signals import (
    cited_categories,
    detect_language,
    find_standing_instructions,
    hebrew_ratio,
    is_sensitive,
)
from ..store import STALE_REVISION, ProfileStore
from ..types import Evidence, InstructionVerdict, Observation, Proposal, Turn, WriteResult

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 1000
MAX_CLAUSES = 5  # standing-instruction clauses classified per turn; the rest are UNKNOWN

# Writes that keep the turn ledger (observations); they are not counted in `writes_applied`.
LEDGER = "ledger"


@dataclass
class TurnResult:
    updated_slots: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    llm_calls: int = 0
    skipped_reason: str | None = None
    errors: int = 0  # memory-side failures swallowed so the chat turn never fails
    # Everything below is for this turn only.
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    writes_applied: int = 0  # profile writes (slots, proposals); the observation is not counted
    writes_refused: dict[str, int] = field(default_factory=dict)  # by reason
    diagnostics: dict[str, int] = field(default_factory=dict)


@dataclass
class BatchResult:
    users_processed: int = 0
    updated_slots: list[tuple[str, str]] = field(default_factory=list)  # (user_id, slot_key)
    llm_calls: int = 0
    errors: int = 0
    # Everything below is for this batch only.
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    writes_applied: int = 0
    writes_refused: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=dict)


@dataclass
class RunState:
    """Per-call state of one `on_turn` or `run_batch`. It lives in a context variable, never on
    the strategy instance, so one strategy can serve concurrent turns."""

    diagnostics: Counter = field(default_factory=Counter)
    writes_refused: Counter = field(default_factory=Counter)
    writes_applied: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    revisions: dict[str, int] = field(default_factory=dict)  # the expected revision, per user
    stale: set[str] = field(default_factory=set)  # users whose run ended on a stale revision

    def add_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost_usd

    def fill(self, result: TurnResult | BatchResult) -> None:
        result.input_tokens, result.output_tokens = self.input_tokens, self.output_tokens
        result.cost_usd = self.cost_usd
        result.writes_applied = self.writes_applied
        result.writes_refused = dict(self.writes_refused)
        result.diagnostics = dict(self.diagnostics)


_current_run: contextvars.ContextVar[RunState | None] = contextvars.ContextVar(
    "personalization_run", default=None
)


def truncate_prompt(text: str) -> str:
    return text[:MAX_PROMPT_CHARS]


def classify_turn(
    instructions: list[str],
    classifier: Callable[[list[str]], list[InstructionVerdict]],
) -> list[InstructionVerdict]:
    """One verdict per standing instruction, `UNKNOWN` for anything that cannot be trusted.

    `classifier` returns one verdict per instruction. A client error, a reply that is not a list
    of verdicts, or a count that differs from the number of instructions makes every instruction
    `UNKNOWN`; a single item that is not a verdict makes only that one `UNKNOWN`.
    """
    unknown = [InstructionVerdict.UNKNOWN] * len(instructions)
    over = unknown[MAX_CLAUSES:]  # clauses beyond the cap are never cleared
    instructions = instructions[:MAX_CLAUSES]
    unknown = unknown[:MAX_CLAUSES]
    try:
        verdicts = classifier(list(instructions))
    except Exception:
        logger.exception("instruction classification failed")
        return unknown + over
    if not isinstance(verdicts, list) or len(verdicts) != len(instructions):
        return unknown + over
    checked = [
        v if isinstance(v, InstructionVerdict) else InstructionVerdict.UNKNOWN for v in verdicts
    ]
    return checked + over


def is_clean(verdicts: list[InstructionVerdict]) -> bool:
    """A turn keeps its text only when it has no instruction or every one is `SAFE_STYLE`."""
    return all(v is InstructionVerdict.SAFE_STYLE for v in verdicts)


class MemoryStrategy:
    """Subclasses implement `_on_turn` and `_run_batch`; the public `on_turn` and `run_batch`
    wrap them in a fresh `RunState` and copy its counts into the result."""

    name = "base"

    def __init__(
        self,
        store: ProfileStore,
        registry: SlotRegistry,
        llm: LLMClient,
        ledger: UsageLedger,
        model: str,
    ):
        self.store = store
        self.registry = registry
        self.llm = llm
        self.ledger = ledger
        self.model = model

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        with self.collecting() as run:
            result = self._on_turn(turn, now)
        run.fill(result)
        return result

    def run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        with self.collecting() as run:
            result = self._run_batch(now, user_ids)
        run.fill(result)
        return result

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        raise NotImplementedError

    def _run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        raise NotImplementedError

    # -- per-call state -----------------------------------------------------------------

    @staticmethod
    @contextmanager
    def collecting() -> Iterator[RunState]:
        """A fresh RunState for the calls inside the block."""
        run = RunState()
        token = _current_run.set(run)
        try:
            yield run
        finally:
            _current_run.reset(token)

    @property
    def run(self) -> RunState:
        """The current call's state; a throwaway one when a helper is called on its own."""
        return _current_run.get() or RunState()

    @property
    def diagnostics(self) -> Counter:
        return self.run.diagnostics

    def begin(self, user_id: str) -> None:
        """Read the user's revision once, before the first model or Jev call for this user."""
        run = self.run
        run.revisions[user_id] = self.store.revision(user_id)
        run.stale.discard(user_id)

    def is_stale(self, user_id: str) -> bool:
        return user_id in self.run.stale

    def write(
        self, user_id: str, call: Callable[[int | None], WriteResult], kind: str = "profile"
    ) -> WriteResult:
        """Run one store write with the expected revision and carry the new revision forward.

        After a `stale_revision` refusal the run has ended for this user: later writes are
        skipped (and not counted again).
        """
        run = self.run
        if user_id in run.stale:
            return WriteResult(False, STALE_REVISION, run.revisions.get(user_id, 0))
        result = call(run.revisions.get(user_id))
        if result.applied:
            if kind != LEDGER:
                run.writes_applied += 1
            if user_id in run.revisions:
                run.revisions[user_id] = result.revision
        elif result.reason:
            run.writes_refused[result.reason] += 1
            run.diagnostics[result.reason] += 1
            if result.reason == STALE_REVISION:
                run.stale.add(user_id)
        return result

    # -- shared rules -------------------------------------------------------------------

    def turn_skip_reason(self, turn: Turn) -> str | None:
        """Why this turn must not be stored or sent to a model, or None."""
        if is_sensitive(turn.user_message):
            return "sensitive"
        if not self.store.get_settings(turn.user_id).store_enabled:
            return "store_disabled"
        return None

    def log_observation(
        self,
        turn: Turn,
        now: datetime,
        *,
        excluded: bool = False,
        consumed: bool = False,
        quarantined: bool = False,
    ) -> WriteResult:
        """Append the turn to the ledger. `excluded` stores only a content-free marker: no text,
        no signals about the content, and it stays out of every later window, input and evidence.
        `quarantined` stores an empty, flagged row for a turn holding a standing instruction (the
        store hides it from reads). `consumed` is for strategies that never consolidate the row."""
        if quarantined:
            text, signals, consumed = "", {}, True
        elif excluded:
            text, signals = "", {"excluded": True}
        else:
            text = turn.user_message
            signals = {
                "language": detect_language(text),
                "hebrew_ratio": round(hebrew_ratio(text), 3),
                "cited_categories": cited_categories(text),
            }
        observation = Observation(
            user_id=turn.user_id,
            session_id=turn.session_id,
            message_id=turn.message_id,
            text=text,
            at=turn.at,
            signals=signals,
            consumed_at=now if consumed else None,
            quarantined=quarantined,
        )
        return self.write(
            turn.user_id,
            lambda expected: self.store.add_observation(observation, expected),
            kind=LEDGER,
        )

    def start_turn(
        self,
        turn: Turn,
        now: datetime,
        *,
        excluded: bool = False,
        consumed: bool = False,
        quarantined: bool = False,
    ) -> str | None:
        """Read the revision, then log the turn. Returns why the run ends here (`duplicate` for a
        retried message, `stale_revision`, `store_disabled`), or None to go on."""
        self.begin(turn.user_id)
        logged = self.log_observation(
            turn, now, excluded=excluded, consumed=consumed, quarantined=quarantined
        )
        return None if logged.applied else (logged.reason or "observation_refused")

    def keep_turn(self, turn: Turn, cleared: list[str]) -> WriteResult:
        """A turn whose instructions were all cleared keeps only those clauses as its text: the
        rest of the message was never classified and stays out of inference. The language signal
        comes from the whole message (a number, not text)."""
        text = " ".join(cleared)
        full = turn.user_message
        signals = {
            "language": detect_language(full),
            "hebrew_ratio": round(hebrew_ratio(full), 3),
            "cited_categories": cited_categories(text),
        }
        return self.write(
            turn.user_id,
            lambda expected: self.store.release_observation(
                turn.user_id, turn.message_id, text, signals, expected
            ),
            kind=LEDGER,
        )

    def envelope(
        self,
        reply: dict | None,
        required: dict[str, type],
        optional: dict[str, type] | None = None,
        **kwargs,
    ) -> dict | None:
        """The validated reply, or None with `invalid_envelope` counted."""
        valid = validate_envelope(reply, required, optional, **kwargs)
        if valid is None:
            self.diagnostics["invalid_envelope"] += 1
        return valid

    def call_model(self, phase: str, user_id: str, system: str, user: str) -> LLMResult:
        result = self.llm.complete(system=system, user=user, model=self.model)
        self.ledger.record(self.name, phase, user_id, self.model, result)
        self.run.add_usage(result.input_tokens, result.output_tokens, result.cost_usd)
        return result

    def parse_reply(self, text: str) -> dict | None:
        """Parse a model reply to a JSON object, counting repairs and failures."""
        parsed = parse_json_object(text, self.diagnostics)
        if parsed is None:
            self.diagnostics["reply_unparsable"] += 1
        return parsed

    def model_evidence(
        self, quote: object, text: str, session_id: str, message_id: str, at: datetime
    ) -> Evidence | None:
        """Evidence for a model-supplied quote; a quote that is not in `text` is counted."""
        proof = verbatim_evidence(quote, text, session_id, message_id, at)
        if proof is None:
            self.diagnostics["quote_not_verbatim"] += 1
        return proof

    def apply_update(
        self,
        user_id: str,
        slot_key: str,
        value: object,
        evidence: list[Evidence],
        now: datetime,
        *,
        require_evidence: bool = True,
    ) -> bool:
        """Validate and write an inferred value. True only when the profile changed.

        Reconfirming the same inferred value with valid new evidence refreshes its evidence and
        shelf life but counts as `unchanged`. A refusal is counted by its reason (`write`).
        """
        slot = self.registry.get(slot_key)
        normalized = self.registry.validate(slot_key, value) if slot else None
        if normalized is None:
            self.diagnostics["value_invalid"] += 1
            return False
        if require_evidence and not evidence:
            self.diagnostics["no_valid_evidence"] += 1
            return False
        current = self.store.get_profile(user_id).get(slot_key)
        expires_at = now + timedelta(days=slot.shelf_life_days) if slot.shelf_life_days else None

        def set_inferred(expected: int | None) -> WriteResult:
            return self.store.set_inferred(
                user_id, slot_key, normalized, evidence, now, expires_at, expected
            )

        if current and current.value == normalized:
            self.diagnostics["unchanged"] += 1
            # A standing instruction may be one the learner asked us never to keep (for example
            # "never show me X"): its words are not refreshed into evidence.
            quotes = [e.quote for e in evidence]
            if (
                current.status == "inferred"
                and evidence
                and not any(find_standing_instructions(q) for q in quotes)
            ):
                self.write(user_id, set_inferred)
            return False
        if not self.write(user_id, set_inferred).applied:
            return False
        self.diagnostics["applied"] += 1
        return True


def verbatim_evidence(
    quote: object, text: str, session_id: str, message_id: str, at: datetime
) -> Evidence | None:
    """Evidence only when `quote` occurs in a message the strategy saw.

    The match ignores Unicode form, whitespace runs and Hebrew or curly quote marks; the stored
    quote is the learner's own substring.
    """
    if not isinstance(quote, str):
        return None
    found = locate_quote(quote, text)
    return Evidence(found, session_id, message_id, at) if found else None


def build_strategy(
    name: str,
    store: ProfileStore,
    registry: SlotRegistry,
    llm: LLMClient,
    ledger: UsageLedger,
    model: str,
    jev: JevClient | None = None,
) -> MemoryStrategy:
    from .explicit import ExplicitStrategy
    from .hybrid import HybridStrategy
    from .hybrid_jev import HybridHaikuStrategy, HybridJevStrategy
    from .nightly import NightlyStrategy
    from .realtime import RealtimeStrategy

    classes = {
        c.name: c
        for c in (
            RealtimeStrategy,
            NightlyStrategy,
            HybridStrategy,
            HybridJevStrategy,
            HybridHaikuStrategy,
            ExplicitStrategy,
        )
    }
    if name not in classes:
        raise ValueError(f"unknown strategy {name!r}; expected one of {sorted(classes)}")
    if name == "hybrid_jev":
        if jev is None:
            raise ValueError("the hybrid_jev strategy needs a Jev client (jev=...)")
        return HybridJevStrategy(store, registry, llm, ledger, model, jev)
    return classes[name](store, registry, llm, ledger, model)
