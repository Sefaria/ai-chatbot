"""aggressiveness/aggressive: every confident prompt may rewrite the profile (`min_support` 1, `ratio` 0).

`build` and `select_items` are the shared arm pipeline's (`cases/aggressiveness/arm_pipeline.py`):
on every turn one call on `config.model` answers the closed-set questions about the latest prompt
and the arm's rule decides the profile; the latest signal wins. `select_items` takes the A2 seeded
sample of `anon-personas`; `syn1` is run whole.

The mock is three invented personas and a scripted stand-in model whose replies are keyed by the
turn's prompt (`mock_fixture.json`, no real text).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from chat.V2.personalization.lab.cases.aggressiveness.arm_pipeline import build, select_items
from chat.V2.personalization.lab.replay import EMPTY_REPLY
from chat.V2.personalization.llm import FakeLLMClient

__all__ = ["build", "select_items", "mock_items", "mock_llm"]

_FIXTURE = Path(__file__).parent / "mock_fixture.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def mock_items():
    return _fixture()["personas"]


def _turn_of(prompt: str, personas) -> str | None:
    return next(
        (
            t["id"]
            for p in personas
            for s in p["sessions"]
            for t in s["turns"]
            if t["user"][:500] == prompt
        ),
        None,
    )


def _typed_reply(spec, questions: list[tuple[str, str]]) -> str:
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
    """Answers the typed questions from the fixture's `replies`, by the turn whose prompt is shown."""
    fixture = _fixture()

    def respond(system: str, user: str) -> str:
        if not system.startswith("You answer closed-set questions"):
            return EMPTY_REPLY
        state = json.loads(user.split("<state>\n", 1)[1].split("\n</state>", 1)[0])
        turn_id = _turn_of(state["learner_prompts"][-1]["text"], fixture["personas"])
        questions = re.findall(r'- key "([^"]+)" \((\w+)\)', user)
        return _typed_reply(fixture["replies"].get(turn_id), questions)

    return FakeLLMClient(respond)
