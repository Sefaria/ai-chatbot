"""Replay a persona's history through a pipeline on a simulated clock, and the stand-in models.

The clock is iteration 1's, with one fix: a turn between 00:00 and 03:00 UTC belongs to the
previous night, so the nightly job (03:00 UTC) runs after it, not before. Between two turns of
different nights a job runs at 03:00 on the day after the earlier turn's night; one more job runs
after the last turn. The profile is snapshotted after every turn and after every job.

A `Pipeline` (see `Pipeline` below) is what a replay case's `pipeline.py` builds. `StrategyPipeline`
adapts one of the five iteration-1 memory strategies to it; `strategy_pipeline` is the ready
`build(config, llm, typed)` for a case whose config is `{"strategy": "<name>"}`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol

from ..jev import JevAnswer
from ..llm import LLMClient, LLMResult, UsageLedger
from ..registry import SlotRegistry
from ..store import InMemoryProfileStore
from ..strategies.base import build_strategy
from ..strategies.prompts import CONSOLIDATION_SYSTEM, INSTRUCTION_SYSTEM, REALTIME_SYSTEM
from ..types import Turn

NIGHT_HOUR = 3  # the nightly job runs at 03:00 UTC


class Pipeline(Protocol):
    """What `pipeline.build(config, llm, typed)` returns for a `replay` case.

    Required: `on_turn`, `run_job`, `profile` (slot -> value, a str or, for a list slot, a list of
    str) and `steps`. Optional, used when present: `profile_detail(user_id)` (slot -> {value,
    status, evidence[], updated_at, expires_at}), `proposals(user_id)`, `observations(user_id)`,
    `sent_texts()`, `calls()` (rows {step, model, in_tokens, out_tokens}), `stats()` ({skips,
    diagnostics, auto_accepted, ...}), `step_counts()` ({step: (n_in, n_out)}), `set_stated(user_id,
    slot, value, at)`, `apply_interview(user_id, answers, at)`, `conflicts_marked(user_id)`.
    """

    def on_turn(self, user_id: str, turn: Turn) -> None: ...
    def run_job(self, user_id: str, now: datetime) -> None: ...
    def profile(self, user_id: str) -> dict[str, Any]: ...
    def steps(self) -> list[str]: ...


class RecordingLLMClient:
    """Wraps a client and keeps every text sent to the model (for the privacy checks)."""

    def __init__(self, inner: LLMClient):
        self.inner = inner
        self.sent: list[tuple[str, str]] = []
        self.results: list[LLMResult] = []

    def complete(self, *, system, user, model, max_tokens=600):
        self.sent.append((system, user))
        result = self.inner.complete(system=system, user=user, model=model, max_tokens=max_tokens)
        self.results.append(result)
        return result

    @property
    def sent_texts(self) -> list[str]:
        return [text for pair in self.sent for text in pair]


class RecordingJevClient:
    """Wraps a Jev client and keeps every state sent (for the privacy checks)."""

    def __init__(self, inner):
        self.inner = inner
        self.states: list[str] = []

    def ask(self, state, questions):
        self.states.append(json.dumps(state, ensure_ascii=False))
        return self.inner.ask(state, questions)


# -- the clock --------------------------------------------------------------------------------


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def night_key(at: datetime):
    """The calendar date of the night a turn belongs to: a turn before 03:00 UTC is the previous night's."""
    return (at - timedelta(hours=NIGHT_HOUR)).date()


def job_time(at: datetime) -> datetime:
    """When the nightly job that follows a turn runs: 03:00 UTC after the turn's night."""
    return datetime.combine(night_key(at) + timedelta(days=1), time(NIGHT_HOUR), tzinfo=UTC)


def persona_turns(persona: dict) -> list[Turn]:
    turns = [
        Turn(
            persona["user_id"],
            session["session_id"],
            t["id"],
            t["user"],
            t["assistant"],
            parse_time(t["at"]),
        )  # fmt: skip
        for session in persona["sessions"]
        for t in session["turns"]
    ]
    return sorted(turns, key=lambda t: t.at)


# -- the result of one replay -----------------------------------------------------------------


@dataclass
class PersonaReplay:
    persona: dict
    snapshots: list[dict] = field(default_factory=list)
    final_profile: dict = field(default_factory=dict)
    turns: int = 0
    skips: Counter = field(default_factory=Counter)
    observations: list[str] = field(default_factory=list)
    sent_texts: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    auto_accepted: int = 0
    jobs: list[datetime] = field(default_factory=list)
    idempotent: bool | None = None
    conflicts_marked: int = 0
    step_counts: dict = field(default_factory=dict)

    def turn_snapshots(self) -> list[dict]:
        return [s for s in self.snapshots if s["kind"] == "turn"]


def _detail(pipeline, user_id: str) -> dict:
    if hasattr(pipeline, "profile_detail"):
        return pipeline.profile_detail(user_id)
    return {
        slot: value
        if isinstance(value, dict)
        else {"value": value, "status": "inferred", "evidence": [], "updated_at": None, "expires_at": None}
        for slot, value in pipeline.profile(user_id).items()
    }  # fmt: skip


def _snapshot(pipeline, user_id: str, kind: str, turn_id: str | None, at: datetime, index: int):
    proposals = pipeline.proposals(user_id) if hasattr(pipeline, "proposals") else []
    return {
        "index": index, "kind": kind, "turn_id": turn_id, "at": at.isoformat(),
        "profile": _detail(pipeline, user_id), "proposals": proposals,
    }  # fmt: skip


def replay_persona(pipeline, persona: dict, *, check_idempotent: bool = False) -> PersonaReplay:
    """Feed the persona's turns to `pipeline` on the simulated clock; snapshot after every turn and job."""
    user_id = persona["user_id"]
    turns = persona_turns(persona)
    replay = PersonaReplay(persona)

    def snap(kind, turn_id, at):
        replay.snapshots.append(
            _snapshot(pipeline, user_id, kind, turn_id, at, len(replay.snapshots))
        )

    def job(at):
        pipeline.run_job(user_id, at)
        replay.jobs.append(at)
        snap("batch", None, at)

    if persona.get("interview") and turns and hasattr(pipeline, "apply_interview"):
        pipeline.apply_interview(user_id, persona["interview"], turns[0].at)
    for i, turn in enumerate(turns):
        if i and night_key(turn.at) != night_key(turns[i - 1].at):
            job(job_time(turns[i - 1].at))
        pipeline.on_turn(user_id, turn)
        replay.turns += 1
        for event in persona.get("stated_events", []):
            if event["after_turn"] == turn.message_id and hasattr(pipeline, "set_stated"):
                pipeline.set_stated(user_id, event["slot"], event["value"], turn.at)
        snap("turn", turn.message_id, turn.at)
    if turns:
        job(job_time(turns[-1].at))
        if check_idempotent:
            before = json.dumps(_detail(pipeline, user_id), sort_keys=True, default=str)
            pipeline.run_job(user_id, job_time(turns[-1].at) + timedelta(days=1))
            after = json.dumps(_detail(pipeline, user_id), sort_keys=True, default=str)
            replay.idempotent = before == after
    replay.final_profile = replay.snapshots[-1]["profile"] if replay.snapshots else {}
    if hasattr(pipeline, "observations"):
        replay.observations = list(pipeline.observations(user_id))
    if hasattr(pipeline, "sent_texts"):
        replay.sent_texts = list(pipeline.sent_texts())
    if hasattr(pipeline, "calls"):
        replay.calls = list(pipeline.calls())
    if hasattr(pipeline, "conflicts_marked"):
        replay.conflicts_marked = pipeline.conflicts_marked(user_id)
    if hasattr(pipeline, "step_counts"):
        replay.step_counts = dict(pipeline.step_counts())
    if hasattr(pipeline, "stats"):
        stats = pipeline.stats()
        replay.skips = Counter(stats.get("skips", {}))
        replay.diagnostics = dict(stats.get("diagnostics", {}))
        replay.auto_accepted = stats.get("auto_accepted", 0)
    return replay


# -- the iteration-1 strategies as a Pipeline -------------------------------------------------


class StrategyPipeline:
    """One of the five memory strategies, on an in-memory store, as a `Pipeline`.

    The hybrid designs simulate the learner tapping "yes" on every proposal right after the turn
    that created it (the count is `stats()["auto_accepted"]`). The explicit design takes the
    persona's interview answers as stated slots before the first turn.
    """

    def __init__(self, strategy: str, llm: LLMClient, model: str, jev=None):
        self.name = strategy
        self.registry = SlotRegistry.load()
        self.store = InMemoryProfileStore()
        self.ledger = UsageLedger()
        self.recorder = RecordingLLMClient(llm)
        self.jev_recorder = RecordingJevClient(jev) if jev is not None else None
        self.strategy = build_strategy(
            strategy, self.store, self.registry, self.recorder, self.ledger, model,
            jev=self.jev_recorder,
        )  # fmt: skip
        self._diagnostics: Counter = Counter()
        self._skips: Counter = Counter()
        self._auto_accepted = 0
        self._turns = 0

    def on_turn(self, user_id: str, turn: Turn) -> None:
        result = self.strategy.on_turn(turn, turn.at)
        self._diagnostics.update(result.diagnostics)
        self._turns += 1
        if result.skipped_reason:
            self._skips[result.skipped_reason] += 1
        if self.name in ("hybrid", "hybrid_jev"):
            for proposal in result.proposals:
                self.store.resolve_proposal(user_id, proposal.id, True, turn.at)
                self._auto_accepted += 1

    def run_job(self, user_id: str, now: datetime) -> None:
        self._diagnostics.update(self.strategy.run_batch(now).diagnostics)

    def apply_interview(self, user_id, answers, at) -> None:
        if self.name == "explicit":
            self.strategy.apply_interview(user_id, answers, at)

    def set_stated(self, user_id, slot, value, at) -> None:
        self.store.set_stated(user_id, slot, value, at)

    def profile(self, user_id: str) -> dict:
        return {k: s.value for k, s in self.store.get_profile(user_id).items()}

    def profile_detail(self, user_id: str) -> dict:
        return {
            key: {
                "value": slot.value,
                "status": slot.status,
                "evidence": [
                    {"quote": e.quote, "session_id": e.session_id, "message_id": e.message_id}
                    for e in slot.evidence
                ],
                "updated_at": slot.updated_at.isoformat(),
                "expires_at": slot.expires_at.isoformat() if slot.expires_at else None,
            }
            for key, slot in self.store.get_profile(user_id).items()
        }

    def proposals(self, user_id: str) -> list[dict]:
        return [
            {"slot": p.slot_key, "value": p.value, "quote": p.quote, "status": p.status}
            for p in self.store.proposals(user_id)
        ]

    def observations(self, user_id: str) -> list[str]:
        return [o.text for o in self.store.observations(user_id)]

    def sent_texts(self) -> list[str]:
        return self.recorder.sent_texts + (self.jev_recorder.states if self.jev_recorder else [])

    def calls(self) -> list[dict]:
        rows = []
        for e in self.ledger.entries:
            step = (
                "typed_questions" if e.model.startswith("jev")
                else "extract_turn" if e.phase == "turn" else "extract_batch"
            )  # fmt: skip
            rows.append(
                {
                    "step": step,
                    "model": e.model,
                    "in_tokens": e.input_tokens,
                    "out_tokens": e.output_tokens,
                }
            )
        return rows

    def stats(self) -> dict:
        return {
            "skips": dict(self._skips), "diagnostics": dict(self._diagnostics),
            "auto_accepted": self._auto_accepted,
        }  # fmt: skip

    def steps(self) -> list[str]:
        names = [
            "screen_sensitive",
            "find_instructions",
            "verify_evidence",
            "validate_values",
            "write_profile",
        ]
        if self.name == "explicit":
            return ["write_profile"]
        if self.name in ("realtime", "hybrid", "hybrid_jev"):
            names.insert(2, "extract_turn")
        if self.name in ("nightly", "hybrid", "hybrid_jev"):
            names.insert(2, "extract_batch")
        if self.name == "hybrid_jev":
            names.insert(2, "typed_questions")
        return names

    def step_counts(self) -> dict[str, tuple[int, int]]:
        extract = sum(1 for c in self.calls() if c["step"] in ("extract_turn", "extract_batch"))
        counts = {
            "screen_sensitive": (self._turns, self._turns - self._skips["sensitive"]),
            "write_profile": (extract, self._diagnostics.get("applied", 0)),
        }
        return {k: v for k, v in counts.items() if k in self.steps()}


def strategy_pipeline(config: dict, llm: LLMClient, typed=None) -> StrategyPipeline:
    """`build` for a case whose config names an iteration-1 design: `{"strategy": "hybrid", ...}`
    (the runner fills `strategy` from the variant when the config lists `variants`). The model is
    `config["model"]`; a Jev client is taken from `typed` when it has one."""
    return StrategyPipeline(
        config["strategy"], llm, config.get("model", "claude-haiku-4-5-20251001"),
        jev=getattr(typed, "jev", None),
    )  # fmt: skip


# -- stand-in models: an empty one ("fake") and a golden-profile one ("oracle") ---------------


# The oracle answers every extraction prompt with the persona's golden values and quotes copied
# verbatim from the prompt it was shown. It proves that the pipeline and the scorers reach a high
# score when the model is right; whatever it still gets wrong is the strategy's doing.

EMPTY_REPLY = json.dumps({"updates": [], "clear": []})
QUOTE_CHARS = 40


def fake_responder(system: str, user: str) -> str:
    return EMPTY_REPLY


def golden_value(golden: dict | None):
    """A value that satisfies a golden form, or None for an absent slot."""
    if not golden:
        return None
    if "value" in golden:
        return golden["value"]
    if "must_include" in golden:
        return golden["must_include"] + golden.get("any_include", [])[:1]
    return golden["any_of"][0]


def _quote(text: str) -> str:
    return text.strip().split("\n")[0][:QUOTE_CHARS].strip()


def oracle_responder(persona: dict):
    golden = {k: v for k, v in persona["golden"].items() if v}
    tags = {t["user"]: t.get("tags", []) for s in persona["sessions"] for t in s["turns"]}

    def realtime(user: str) -> str:
        message = user.split("Learner: ", 1)[1]
        quote = _quote(message)
        updates = [
            {"slot": key, "value": golden_value(g), "quote": quote} for key, g in golden.items()
        ]
        return json.dumps({"updates": updates})

    def consolidation(user: str) -> str:
        sessions: list[tuple[str, str]] = []  # first (message id, text) of each session
        expecting_first = False
        for line in user.split("Recent prompts by session:", 1)[1].splitlines():
            if line.startswith("Session "):
                expecting_first = True
            elif expecting_first and (m := re.match(r"\s+\[(.+?)\] (.*)$", line)):
                sessions.append((m.group(1), m.group(2)))
                expecting_first = False
        evidence = [{"message_id": mid, "quote": _quote(text)} for mid, text in sessions]
        updates = [
            {"slot": key, "value": golden_value(g), "evidence": evidence}
            for key, g in golden.items()
        ]
        return json.dumps({"updates": updates, "clear": []})

    def instruction(user: str) -> str:
        stored = next((t for text, t in tags.items() if user.strip() in text), [])
        if "must_not_store" in stored:
            return json.dumps({"slot": None, "excludes_sources": True})
        if "answer_style" not in golden:
            return json.dumps({"slot": None, "excludes_sources": False})
        value = golden_value(golden["answer_style"])
        return json.dumps({"slot": "answer_style", "value": value, "excludes_sources": False})

    def respond(system: str, user: str) -> str:
        if system.startswith(REALTIME_SYSTEM[:60]):
            return realtime(user)
        if system.startswith(CONSOLIDATION_SYSTEM[:60]):
            return consolidation(user)
        if system.startswith(INSTRUCTION_SYSTEM[:60]):
            return instruction(user)
        return EMPTY_REPLY

    return respond


def fake_jev_responder(state: dict, questions: dict) -> dict:
    """A Jev that is never sure: unclear choices and zero noul."""
    return {
        key: JevAnswer("choice", choice="unclear", confidence=1.0)
        if question.type == "choice"
        else JevAnswer("noul", noul=0.0)
        for key, question in questions.items()
    }


def oracle_jev_responder(persona: dict):
    """Answers from the golden profile (choices and categories) and from the turn tags (instructions)."""
    golden = persona["golden"]
    tagged = {t["user"]: t.get("tags", []) for s in persona["sessions"] for t in s["turns"]}
    categories = golden.get("text_categories") or {}
    wanted = set(categories.get("must_include", [])) | set(categories.get("any_include", []))

    def respond(state: dict, questions: dict) -> dict:
        tags: list[str] = []
        if "message" in state:
            tags = next((t for text, t in tagged.items() if state["message"] in text), [])
        answers = {}
        for key, question in questions.items():
            if question.type == "choice":
                want = (golden.get(key) or {}).get("value")
                answers[key] = JevAnswer("choice", choice=want or "unclear", confidence=1.0)
            elif key.startswith("category:"):
                score = 0.95 if key.split(":", 1)[1] in wanted else 0.02
                answers[key] = JevAnswer("noul", noul=score)
            elif key == "asks_to_exclude_sources":
                answers[key] = JevAnswer("noul", noul=0.97 if "must_not_store" in tags else 0.02)
            else:
                answers[key] = JevAnswer("noul", noul=0.95 if "instruction" in tags else 0.02)
        return answers

    return respond
