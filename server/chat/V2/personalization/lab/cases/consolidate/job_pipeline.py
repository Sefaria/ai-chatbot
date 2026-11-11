"""The replay pipeline of the "Run the job" button, shared by the `consolidate` cases.

`JobPipeline` logs every turn as an observation (no model call; a sensitive turn is dropped, a
standing instruction is quarantined, as in the nightly design) and runs `P/job.py` when the replay
presses the button: after the last turn of each night, which is the end of a session, and once
more after the last turn. The model is `config["variant"]` (the runner fills it from `variants`)
or `config["model"]`; one consolidation call per job run, none when nothing new was observed.

A case's `pipeline.py` is two lines:

    from chat.V2.personalization.lab.cases.consolidate.job_pipeline import build, select_items

with this in its `case.json`, `button-diff` first, `button-apply` second:

    "config": {"mode": "candidate", "variants": ["claude-haiku-4-5-20251001", "claude-haiku-5-5"],
               "sample_dataset": "anon-personas", "sample_size": 20, "sample_seed": 20261110}
    "config": {"mode": "apply", "variants": [...same...], "sample_dataset": "anon-personas", ...}

`"candidate"` is the review step: the job writes a candidate and the harness approves it at once
(`apply_candidate`); `"apply"` writes the profile directly. The metrics come from the core: the
replay presses the button a second time after the last session with nothing new to read and
compares the profile byte for byte (`idempotent`); `stated_overwritten` reads the persona's
`stated_events`; `injection_writes` the turns tagged `injected` (a profile value whose evidence
cites one); `conflict_marked` is `conflicts_marked(user_id)` over the turns tagged `conflict`.
`stats()["diagnostics"]` adds the job's own counts (`ignored` reasons, `stated_kept`,
`instruction_prompt`, `applied_candidates`, ...).
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import datetime

from chat.V2.personalization.job import apply_candidate, run_job
from chat.V2.personalization.lab.replay import RecordingLLMClient
from chat.V2.personalization.llm import UsageLedger
from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.store import InMemoryProfileStore
from chat.V2.personalization.strategies.nightly import NightlyStrategy
from chat.V2.personalization.types import Turn

STEPS = ["screen_sensitive", "consolidate", "verify_evidence", "validate_values", "write_profile"]
MODES = ("candidate", "apply")


class JobPipeline:
    def __init__(self, llm, model: str, mode: str):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.model, self.mode = model, mode
        self.registry = SlotRegistry.load()
        self.store = InMemoryProfileStore()
        self.ledger = UsageLedger()
        self.recorder = RecordingLLMClient(llm)
        # Only its `on_turn` is used: it logs the observation and makes no model call.
        self.logger = NightlyStrategy(self.store, self.registry, self.recorder, self.ledger, model)
        self._skips: Counter = Counter()
        self._diagnostics: Counter = Counter()
        self._conflicts: dict[str, list[dict]] = defaultdict(list)
        self._turns = 0
        self._jobs_with_call = 0
        self._changes = 0

    def on_turn(self, user_id: str, turn: Turn) -> None:
        result = self.logger.on_turn(turn, turn.at)
        self._turns += 1
        if result.skipped_reason:
            self._skips[result.skipped_reason] += 1

    def run_job(self, user_id: str, now: datetime) -> None:
        """Press the button. In `candidate` mode the harness plays the learner and applies."""
        result = run_job(
            self.store, user_id, now=now, llm=self.recorder, mode=self.mode,
            registry=self.registry, model=self.model, ledger=self.ledger,
        )  # fmt: skip
        self._diagnostics["jobs"] += 1
        self._diagnostics.update({f"job_{k}": v for k, v in result.ignored.items()})
        self._jobs_with_call += result.llm_calls
        applied = result.applied
        if self.mode == "candidate" and self.store.get_candidate(user_id) is not None:
            self._diagnostics["candidates"] += 1
            outcome = apply_candidate(self.store, user_id, now=now, registry=self.registry)
            if outcome.applied:
                self._diagnostics["applied_candidates"] += 1
                applied = True
            else:
                self._diagnostics[f"apply_refused_{outcome.reason}"] += 1
        if applied:
            self._changes += len(result.changes)
        known = {(c["slot"], str(c["a"]), str(c["b"])) for c in self._conflicts[user_id]}
        for c in result.conflicts:
            if (c["slot"], str(c["a"]), str(c["b"])) not in known:
                self._conflicts[user_id].append(c)

    def set_stated(self, user_id, slot, value, at) -> None:
        self.store.set_stated(user_id, slot, value, at)

    def conflicts_marked(self, user_id: str) -> int:
        return len(self._conflicts[user_id])

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

    def observations(self, user_id: str) -> list[str]:
        return [o.text for o in self.store.observations(user_id)]

    def sent_texts(self) -> list[str]:
        return self.recorder.sent_texts

    def calls(self) -> list[dict]:
        return [
            {"step": "consolidate", "model": e.model, "in_tokens": e.input_tokens,
             "out_tokens": e.output_tokens}
            for e in self.ledger.entries
        ]  # fmt: skip

    def stats(self) -> dict:
        return {
            "skips": dict(self._skips),
            "diagnostics": dict(self._diagnostics),
            "auto_accepted": self._diagnostics["applied_candidates"],
        }

    def steps(self) -> list[str]:
        return list(STEPS)

    def step_counts(self) -> dict[str, tuple[int, int]]:
        d = self._diagnostics
        return {
            "screen_sensitive": (self._turns, self._turns - self._skips["sensitive"]),
            "consolidate": (d["jobs"], self._jobs_with_call),
            "write_profile": (self._jobs_with_call, self._changes),
        }


def build(config, llm=None, typed=None) -> JobPipeline:
    for key in ("mode",):
        if key not in config:
            raise KeyError(f"a consolidate case needs config[{key!r}]")
    model = config.get("variant") or config.get("model")
    if not model:
        raise KeyError("a consolidate case needs config['model'] or config['variants']")
    return JobPipeline(llm, model, config["mode"])


def select_items(items, config, dataset):
    """All items, except for the sampled dataset: `sample_size` personas chosen by seed, in user id order."""
    if dataset.name != config.get("sample_dataset"):
        return list(items)
    by_id = {item["user_id"]: item for item in items}
    ids = sorted(by_id)
    size = min(int(config["sample_size"]), len(ids))
    chosen = sorted(random.Random(config["sample_seed"]).sample(ids, size))
    return [by_id[user_id] for user_id in chosen]
