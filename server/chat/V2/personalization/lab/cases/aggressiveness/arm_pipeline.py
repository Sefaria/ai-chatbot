"""The replay pipeline of an `aggressiveness` arm, shared by the `light` and `aggressive` cases.

`ArmPipeline` runs `P/arms.py`'s `ArmUpdater` on an in-memory store: on every turn one call on
`config["model"]` (`HaikuTypedClient`) answers the closed-set questions about the learner's latest
prompt, and the arm's rule (`min_support`, `ratio`) decides whether the profile changes. There is no
nightly job (`run_job` does nothing), so the arms differ only in their update rule. Every model call
is labelled `typed_questions`; a sensitive turn is dropped before any call (`screen_sensitive`).

A case's `pipeline.py` is two lines:

    from chat.V2.personalization.lab.cases.aggressiveness.arm_pipeline import build, select_items

with this in its `case.json`:

    "config": {"model": "claude-haiku-4-5-20251001", "min_support": 3, "ratio": 2.0,
               "sample_dataset": "anon-personas", "sample_size": 20, "sample_seed": 20261110}

(`aggressive` is `"min_support": 1, "ratio": 0.0`.) `select_items` takes the A2 seeded sample of the
sampled dataset (sorted user ids, `random.Random(seed).sample`) and every item of any other dataset.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime

from chat.V2.personalization.arms import ArmUpdater
from chat.V2.personalization.lab.replay import RecordingLLMClient
from chat.V2.personalization.llm import LLMResult, UsageLedger, estimate_cost
from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.store import InMemoryProfileStore
from chat.V2.personalization.typed import HaikuTypedClient
from chat.V2.personalization.types import Turn

STEPS = ["screen_sensitive", "typed_questions", "write_profile"]
STRATEGY = "arm"  # the ledger's strategy column


class _LedgerTyped:
    """Records each typed call (tokens and cost, from the client's `last_*` fields) in the ledger."""

    def __init__(self, inner: HaikuTypedClient, ledger: UsageLedger, model: str):
        self.inner, self.ledger, self.model = inner, ledger, model
        self.user_id = ""

    def answer(self, state, questions):
        answers = self.inner.answer(state, questions)
        tokens_in = self.inner.last_input_tokens or 0
        tokens_out = self.inner.last_output_tokens or 0
        cost = estimate_cost(self.model, tokens_in, tokens_out)
        self.ledger.record(
            STRATEGY,
            "turn",
            self.user_id,
            self.model,
            LLMResult("", tokens_in, tokens_out, cost, 0),
        )
        return answers


class ArmPipeline:
    def __init__(self, llm, model: str, *, min_support: int, ratio: float):
        self.registry = SlotRegistry.load()
        self.store = InMemoryProfileStore()
        self.ledger = UsageLedger()
        self.recorder = RecordingLLMClient(llm)
        self.typed = _LedgerTyped(HaikuTypedClient(self.recorder, model), self.ledger, model)
        self.updater = ArmUpdater(
            self.store, self.typed, min_support=min_support, ratio=ratio, registry=self.registry
        )

    def on_turn(self, user_id: str, turn: Turn) -> None:
        self.typed.user_id = user_id
        self.updater.on_turn(user_id, turn)

    def run_job(self, user_id: str, now: datetime) -> None:
        """The arms have no nightly job."""

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

    def sent_texts(self) -> list[str]:
        return self.recorder.sent_texts

    def calls(self) -> list[dict]:
        return [
            {"step": "typed_questions", "model": e.model, "in_tokens": e.input_tokens,
             "out_tokens": e.output_tokens}
            for e in self.ledger.entries
        ]  # fmt: skip

    def stats(self) -> dict:
        s = self.updater.stats
        diagnostics = {k: v for k, v in s.items() if k not in ("turns", "skipped_sensitive")}
        return {
            "skips": {"sensitive": s["skipped_sensitive"]} if s["skipped_sensitive"] else {},
            "diagnostics": dict(Counter(diagnostics)),
            "auto_accepted": 0,
        }

    def steps(self) -> list[str]:
        return list(STEPS)

    def step_counts(self) -> dict[str, tuple[int, int]]:
        s = self.updater.stats
        return {
            "screen_sensitive": (s["turns"], s["turns"] - s["skipped_sensitive"]),
            "typed_questions": (s["typed_calls"], s["typed_calls"]),
            "write_profile": (s["typed_calls"], s["writes"]),
        }


def build(config, llm=None, typed=None) -> ArmPipeline:
    for key in ("model", "min_support", "ratio"):
        if key not in config:
            raise KeyError(f"an aggressiveness arm needs config[{key!r}]")
    return ArmPipeline(
        llm, config["model"], min_support=int(config["min_support"]), ratio=float(config["ratio"])
    )


def select_items(items, config, dataset):
    """All items, except for the sampled dataset: `sample_size` personas chosen by seed, in user id order."""
    if dataset.name != config.get("sample_dataset"):
        return list(items)
    by_id = {item["user_id"]: item for item in items}
    ids = sorted(by_id)
    size = min(int(config["sample_size"]), len(ids))
    chosen = sorted(random.Random(config["sample_seed"]).sample(ids, size))
    return [by_id[user_id] for user_id in chosen]
