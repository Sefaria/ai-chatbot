"""Replay a persona's history through one strategy on a simulated clock."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from ..llm import LedgerEntry, LLMClient, LLMResult, UsageLedger
from ..registry import SlotRegistry
from ..store import InMemoryProfileStore
from ..strategies.base import build_strategy
from ..types import Turn


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


@dataclass
class PersonaRun:
    strategy: str
    persona: dict
    snapshots: list[dict] = field(default_factory=list)
    final_profile: dict = field(default_factory=dict)
    ledger_entries: list[LedgerEntry] = field(default_factory=list)
    raw_input_tokens: int = 0
    skips: Counter = field(default_factory=Counter)
    observations: list[str] = field(default_factory=list)
    sent_texts: list[str] = field(default_factory=list)
    turns: int = 0
    auto_accepted: int = 0
    batches: list[datetime] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


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


def batch_time(after: datetime) -> datetime:
    """03:00 UTC on the day after `after`."""
    return datetime.combine(after.date() + timedelta(days=1), time(3), tzinfo=UTC)


def _snapshot(
    store, user_id: str, kind: str, turn_id: str | None, at: datetime, index: int
) -> dict:
    profile = {
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
        for key, slot in store.get_profile(user_id).items()
    }
    proposals = [
        {"slot": p.slot_key, "value": p.value, "quote": p.quote, "status": p.status}
        for p in store.proposals(user_id)
    ]
    return {
        "index": index, "kind": kind, "turn_id": turn_id, "at": at.isoformat(),
        "profile": profile, "proposals": proposals,
    }  # fmt: skip


def run_persona(
    strategy_name: str,
    persona: dict,
    llm: LLMClient,
    model: str,
    registry: SlotRegistry,
    jev=None,
) -> PersonaRun:
    store, ledger = InMemoryProfileStore(), UsageLedger()
    recorder = RecordingLLMClient(llm)
    jev_recorder = RecordingJevClient(jev) if jev is not None else None
    strategy = build_strategy(
        strategy_name, store, registry, recorder, ledger, model, jev=jev_recorder
    )
    run = PersonaRun(strategy_name, persona)
    user_id = persona["user_id"]
    turns = persona_turns(persona)
    events = persona.get("stated_events", [])

    def snap(kind, turn_id, at):
        run.snapshots.append(_snapshot(store, user_id, kind, turn_id, at, len(run.snapshots)))

    def batch(at):
        strategy.run_batch(at)
        run.batches.append(at)
        snap("batch", None, at)

    if strategy_name == "explicit" and persona.get("interview") and turns:
        strategy.apply_interview(user_id, persona["interview"], turns[0].at)

    for i, turn in enumerate(turns):
        if i and turn.at.date() > turns[i - 1].at.date():
            batch(batch_time(turns[i - 1].at))
        result = strategy.on_turn(turn, turn.at)
        run.turns += 1
        if result.skipped_reason:
            run.skips[result.skipped_reason] += 1
        if strategy_name in ("hybrid", "hybrid_jev"):
            for proposal in result.proposals:  # simulates the learner tapping "yes"
                store.resolve_proposal(user_id, proposal.id, True, turn.at)
                run.auto_accepted += 1
        for event in events:
            if event["after_turn"] == turn.message_id:
                store.set_stated(user_id, event["slot"], event["value"], turn.at)
        snap("turn", turn.message_id, turn.at)
    if turns:
        batch(batch_time(turns[-1].at))

    run.final_profile = run.snapshots[-1]["profile"] if run.snapshots else {}
    run.ledger_entries = ledger.entries
    run.raw_input_tokens = sum(
        r.raw_input_tokens if r.raw_input_tokens is not None else r.input_tokens
        for r in recorder.results
    )
    run.observations = [o.text for o in store.observations(user_id)]
    run.diagnostics = dict(strategy.diagnostics)
    run.sent_texts = recorder.sent_texts + (jev_recorder.states if jev_recorder else [])
    return run
