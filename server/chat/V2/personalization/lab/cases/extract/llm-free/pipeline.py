"""extract/llm-free: one free-form model call per turn, the model rewrites the profile.

`build` is the replay pipeline of strategy `realtime` (iteration 1's design A, `P/strategies/realtime.py`)
and nothing else: on every turn the code sets `preferred_language`, then one call on `config.model`
sees the current profile and the turn and returns `{"updates": [{slot, value, quote}]}`, applied at
once with no thresholds. A reply that does not parse, an unknown slot, a value outside its options or a
quote that is not verbatim changes nothing. A turn with a standing instruction is quarantined and never
sent; a sensitive turn is dropped before the model.

It adds no scoring and no strategy code. `select_items` takes the A2 seeded sample of `anon-personas`
(sorted user ids, `random.Random(seed).sample`); `syn1` is run whole.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from chat.V2.personalization.lab.replay import EMPTY_REPLY, strategy_pipeline
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.strategies.prompts import REALTIME_SYSTEM


def build(config, llm=None, typed=None):
    return strategy_pipeline(config, llm)


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


def _turn_of(message: str, personas) -> str | None:
    return next(
        (
            t["id"]
            for p in personas
            for s in p["sessions"]
            for t in s["turns"]
            if t["user"] == message
        ),
        None,
    )


def mock_llm():
    """Answers the per-turn call from the fixture's `replies`, by the turn whose text follows
    "Learner: "; every other call gets an empty reply."""
    fixture = _fixture()

    def respond(system: str, user: str) -> str:
        if not system.startswith(REALTIME_SYSTEM[:60]):
            return EMPTY_REPLY
        message = user.split("Learner: ", 1)[1]
        spec = fixture["replies"].get(_turn_of(message, fixture["personas"]))
        if isinstance(spec, list):
            quote = message.strip()[:30]
            return json.dumps(
                {"updates": [{"slot": s, "value": v, "quote": quote} for s, v in spec]}
            )
        return spec if isinstance(spec, str) else json.dumps(spec or {"updates": []})

    return FakeLLMClient(respond)
