"""extract/haiku-typed: the closed-set questions answered by the small model in one structured call.

`build` is the replay pipeline of strategy `hybrid_haiku` (`HaikuTypedClient`, `P/typed.py`): on
every turn the code sets `preferred_language`, then one call on `config.model` over the last 12
prompts answers `hebrew_fluency`, `terminology_comfort`, `register` and one yes/no per text
category; thresholds and evidence are the strategy's own (`typed_questions.py`). A reply that
does not parse, or an answer outside its options, is "no answer" and changes nothing.

`HaikuTypedPipeline` only completes what the replay pipeline does for `hybrid_jev`: it simulates
the learner's tap on each proposal and counts the small model's calls as `typed_questions`. It
changes no scoring and no strategy code. `select_items` takes the A2 seeded sample of
`anon-personas` (sorted user ids, `random.Random(seed).sample`); `syn1` is run whole.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from chat.V2.personalization.lab.replay import EMPTY_REPLY, StrategyPipeline
from chat.V2.personalization.llm import FakeLLMClient

STEPS = [
    "screen_sensitive",
    "find_instructions",
    "typed_questions",
    "extract_batch",
    "verify_evidence",
    "validate_values",
    "write_profile",
]


class HaikuTypedPipeline(StrategyPipeline):
    def on_turn(self, user_id, turn) -> None:
        result = self.strategy.on_turn(turn, turn.at)
        self._diagnostics.update(result.diagnostics)
        self._turns += 1
        if result.skipped_reason:
            self._skips[result.skipped_reason] += 1
        for proposal in result.proposals:  # the learner taps "yes", as for hybrid and hybrid_jev
            self.store.resolve_proposal(user_id, proposal.id, True, turn.at)
            self._auto_accepted += 1

    def calls(self) -> list[dict]:
        rows = super().calls()
        for row, entry in zip(rows, self.ledger.entries, strict=True):
            row["step"] = "typed_questions" if entry.phase == "turn" else "extract_batch"
        return rows

    def steps(self) -> list[str]:
        return list(STEPS)


def build(config, llm=None, typed=None) -> HaikuTypedPipeline:
    return HaikuTypedPipeline(config["strategy"], llm, config["model"])


def select_items(items, config, dataset):
    """All items, except for the sampled dataset: `sample_size` personas chosen by seed, in user id order."""
    if dataset.name != config.get("sample_dataset"):
        return list(items)
    by_id = {item["user_id"]: item for item in items}
    ids = sorted(by_id)
    size = min(int(config["sample_size"]), len(ids))
    chosen = sorted(random.Random(config["sample_seed"]).sample(ids, size))
    return [by_id[user_id] for user_id in chosen]


# -- the mock: five invented personas and a scripted stand-in model -------------------------------

_FIXTURE = Path(__file__).parent / "mock_fixture.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def mock_items():
    return _fixture()["personas"]


def _turn_of(last_prompt: str, personas) -> str | None:
    return next(
        (
            t["id"]
            for p in personas
            for s in p["sessions"]
            for t in s["turns"]
            if t["user"][:500] == last_prompt
        ),
        None,
    )


def _typed_reply(spec, questions: list[tuple[str, str]]) -> str:
    if isinstance(spec, str):
        return spec  # sent as is: malformed on purpose
    spec = spec or {}
    answers = {}
    for key, kind in questions:
        given = spec.get(key)
        if kind == "noul":
            answers[key] = {"noul": 0.02 if given is None else given}
        else:
            choice, confidence = given or ["unclear", 0.9]
            answers[key] = {"choice": choice, "confidence": confidence}
    return json.dumps({"answers": answers})


def mock_llm():
    """Answers the small model's typed questions from the fixture's `replies`, by the turn whose
    prompt is the last one in the state; every other prompt (the nightly batch) gets an empty reply."""
    fixture = _fixture()

    def respond(system: str, user: str) -> str:
        if not system.startswith("You answer closed-set questions"):
            return EMPTY_REPLY
        state = json.loads(user.split("<state>\n", 1)[1].split("\n</state>", 1)[0])
        turn_id = _turn_of(state["learner_prompts"][-1]["text"], fixture["personas"])
        questions = re.findall(r'- key "([^"]+)" \((\w+)\)', user)
        return _typed_reply(fixture["replies"].get(turn_id), questions)

    return FakeLLMClient(respond)
