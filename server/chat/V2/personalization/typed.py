"""Typed clients: answer closed-set questions (one-of-N, yes/no) about a piece of state.

Two implementations behind one protocol: `JevTypedClient` wraps the Jev judge (synthetic text
only; the server builds one only when PERSONALIZATION_ALLOW_JEV is "true") and `HaikuTypedClient`
asks the small model for one structured reply. Neither can generate free text for the profile; a
malformed or missing answer is a `TypedAnswer` with every field None ("no answer"), never a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .jev import JevClient, JevQuestion
from .llm import LLMClient
from .parsing import parse_json_object


@dataclass
class TypedQuestion:
    key: str
    kind: str  # "choice" (one of the criteria keys) or "noul" (a score from 0 to 1)
    instructions: str
    criteria: dict[str, str | None] | None = None


@dataclass
class TypedAnswer:
    key: str
    choice: str | None = None
    confidence: float | None = None
    noul: float | None = None


class TypedClient(Protocol):
    def answer(self, state: str, questions: list[TypedQuestion]) -> list[TypedAnswer]: ...


class JevTypedClient:
    """Adapts a Jev client. `state` is the JSON text of the state Jev should read."""

    def __init__(self, jev: JevClient):
        self.jev = jev
        self.last_input_tokens: int | None = None

    def answer(self, state, questions):
        response = self.jev.ask(
            json.loads(state),
            {q.key: JevQuestion(q.kind, q.instructions, q.criteria) for q in questions},
        )
        self.last_input_tokens = response.input_tokens
        out = []
        for q in questions:
            raw = response.answers.get(q.key)
            out.append(
                TypedAnswer(q.key)
                if raw is None
                else TypedAnswer(q.key, raw.choice, raw.confidence, raw.noul)
            )
        return out


SYSTEM = """You answer closed-set questions about a learner, using ONLY the state you are given.
Never answer from outside knowledge or guess: when the state gives no evidence, pick the option
named "unclear" for a choice, and a low score for a yes/no.

Reply with one JSON object and nothing else:
{"answers": {"<question key>": {"choice": "<one option key>", "confidence": <0 to 1>}
                              or {"noul": <0 to 1>}}}
Use "choice" with "confidence" for a one-of-N question and "noul" (how likely the answer is yes)
for a yes/no question. Answer every question."""

MAX_TOKENS = 600


def _unit(value: object) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    return float(value) if ok else None


class HaikuTypedClient:
    """One structured-output call on the small model for all questions."""

    def __init__(self, llm: LLMClient, model: str = ""):
        self.llm = llm
        self.model = model
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int = 0
        self.last_cost_usd: float = 0.0

    def _render(self, questions: list[TypedQuestion]) -> str:
        lines = []
        for q in questions:
            lines.append(f'- key "{q.key}" ({q.kind}): {q.instructions}')
            for option, description in (q.criteria or {}).items():
                lines.append(f'    option "{option}": {description or option}')
        return "\n".join(lines)

    def answer(self, state, questions):
        none = [TypedAnswer(q.key) for q in questions]
        user = f"<state>\n{state}\n</state>\n\nQuestions:\n{self._render(questions)}"
        result = self.llm.complete(
            system=SYSTEM, user=user, model=self.model, max_tokens=MAX_TOKENS
        )
        self.last_input_tokens = result.input_tokens
        self.last_output_tokens = result.output_tokens
        self.last_cost_usd = result.cost_usd
        reply = parse_json_object(result.text)
        answers = reply.get("answers") if isinstance(reply, dict) else None
        if not isinstance(answers, dict):
            return none
        out = []
        for q in questions:
            raw = answers.get(q.key)
            if not isinstance(raw, dict):
                out.append(TypedAnswer(q.key))
            elif q.kind == "noul":
                out.append(TypedAnswer(q.key, noul=_unit(raw.get("noul"))))
            else:
                choice, confidence = raw.get("choice"), _unit(raw.get("confidence"))
                if (
                    not isinstance(choice, str)
                    or choice not in (q.criteria or {})
                    or confidence is None
                ):
                    out.append(TypedAnswer(q.key))
                else:
                    out.append(TypedAnswer(q.key, choice=choice, confidence=confidence))
        return out
