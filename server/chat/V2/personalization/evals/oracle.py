"""Stand-in models for the harness: an empty one ("fake") and a golden-profile one ("oracle").

The oracle answers every extraction prompt with the persona's golden values and quotes copied
verbatim from the prompt it was shown. It exists to prove that the pipeline and the scorers reach
a high score when the model is right; whatever it still gets wrong is the strategy's doing.
"""

from __future__ import annotations

import json
import re

from ..strategies.prompts import CONSOLIDATION_SYSTEM, INSTRUCTION_SYSTEM, REALTIME_SYSTEM

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
        message = user.split("Learner: ", 1)[1].rsplit("\nAssistant:", 1)[0]
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
        if "must_not_store" in stored or "answer_style" not in golden:
            return json.dumps({"slot": None})
        return json.dumps({"slot": "answer_style", "value": golden_value(golden["answer_style"])})

    def respond(system: str, user: str) -> str:
        if system.startswith(REALTIME_SYSTEM[:60]):
            return realtime(user)
        if system.startswith(CONSOLIDATION_SYSTEM[:60]):
            return consolidation(user)
        if system.startswith(INSTRUCTION_SYSTEM[:60]):
            return instruction(user)
        return EMPTY_REPLY

    return respond
