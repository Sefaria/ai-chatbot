"""extract/jev: the closed-set questions answered by Jev, one request per turn over the last 12 prompts.

`build` is the replay pipeline of strategy `hybrid_jev` (`P/strategies/hybrid_jev.py`): on every
turn the code sets `preferred_language`, then one Jev request over the last 12 prompts answers
`hebrew_fluency`, `terminology_comfort`, `register` and one yes/no per text category; the
thresholds and the evidence are the strategy's own (`typed_questions.py`). The replay pipeline
already simulates the learner's tap on each proposal for this strategy and counts Jev's requests as
`typed_questions`, so this case adds nothing to it: the same questions and the same sample as
`extract/haiku-typed`, only the answerer differs. The typed client is the runner's
`JevTypedClient`, built only when `PERSONALIZATION_ALLOW_JEV=true` and the key is in the
shell, and never for a real dataset that is not anonymized; without it the case is `invalid`.

`select_items` takes the A2 seeded sample of `anon-personas` (sorted user ids,
`random.Random(seed).sample`); `syn1` is run whole.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from chat.V2.personalization.jev import FakeJevClient, JevAnswer, JevError
from chat.V2.personalization.lab.replay import EMPTY_REPLY
from chat.V2.personalization.lab.replay import strategy_pipeline as build  # noqa: F401
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.typed import JevTypedClient


def select_items(items, config, dataset):
    """All items, except for the sampled dataset: `sample_size` personas chosen by seed, in user id order."""
    if dataset.name != config.get("sample_dataset"):
        return list(items)
    by_id = {item["user_id"]: item for item in items}
    ids = sorted(by_id)
    size = min(int(config["sample_size"]), len(ids))
    chosen = sorted(random.Random(config["sample_seed"]).sample(ids, size))
    return [by_id[user_id] for user_id in chosen]


# -- the mock: six invented personas and a scripted stand-in Jev ----------------------------------

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


def mock_llm():
    """The nightly batch's model: an empty reply to everything (the case asks Jev, not the model)."""
    return FakeLLMClient(lambda system, user: EMPTY_REPLY)


def mock_typed():
    """A Jev that answers from the fixture's `replies`, by the turn whose prompt is the last one in
    the state; a question with no scripted score gets `unclear` / 0.02."""
    fixture = _fixture()

    def respond(state: dict, questions: dict) -> dict:
        if "learner_prompts" not in state:  # an instruction request: nothing scripted
            return {k: JevAnswer("noul", noul=0.02) for k in questions}
        turn_id = _turn_of(state["learner_prompts"][-1]["text"], fixture["personas"])
        spec = fixture["replies"].get(turn_id) or {}
        if spec == "error":
            raise JevError("Jev HTTP 500")
        answers = {}
        for key, question in questions.items():
            given = spec.get(key)
            if question.type == "choice":
                choice, confidence = given or ["unclear", 0.9]
                answers[key] = JevAnswer("choice", choice=choice, confidence=confidence)
            else:
                answers[key] = JevAnswer("noul", noul=0.02 if given is None else given)
        return answers

    return JevTypedClient(FakeJevClient(respond))
