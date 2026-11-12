"""The Jev gate: one request that decides whether a run is worth a model call, which core slots
the prompts touch and which prompts are sensitive. Jev never picks values and never writes.

Anything that goes wrong (no key, an error, a timeout, a 429, a malformed reply) leaves the model
path fully open: the result is the `off` result with source `fallback`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from django.conf import settings

from . import jev_questions as q
from .core_slots import CORE_KEYS
from .jev import KEY_ENV, HttpJevClient, JevAnswer, JevClient

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    relevant: bool
    slots: set[str]
    sensitive: set[int]  # indexes into the prompt list
    source: str  # "jev" | "fallback" | "off"
    # Jev's raw yes-scores by question key, kept for the run record (also set in shadow mode).
    scores: dict[str, float | None] = field(default_factory=dict)


def _open(source: str, scores: dict | None = None) -> GateResult:
    return GateResult(True, set(CORE_KEYS), set(), source, scores or {})


def _client(jev: JevClient | None) -> JevClient:
    return jev or HttpJevClient(model=q.MODEL, timeout=settings.PERSONALIZATION_JEV_TIMEOUT)


def _valid(answers, questions) -> bool:
    """Every requested question has an answer of its type with a numeric score in [0, 1]."""
    if not isinstance(answers, dict):
        return False
    for key, question in questions.items():
        answer = answers.get(key)
        if not isinstance(answer, JevAnswer) or answer.type != question.type:
            return False
        value = answer.noul
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not 0 <= value <= 1:  # also rejects NaN
            return False
    return True


def ask_gate(prompts: list[str], mode: str, jev: JevClient | None = None) -> GateResult:
    if mode not in ("shadow", "on"):
        return _open("off")
    if not os.environ.get(KEY_ENV):
        return _open("fallback")
    state = {"learner_prompts": [p[: q.MAX_PROMPT_CHARS] for p in prompts]}
    try:
        response = _client(jev).ask(state, q.build_questions(len(prompts)))
    except Exception as exc:  # JevError, timeouts, anything: the model path continues
        logger.warning("jev gate fell back: %s", type(exc).__name__)
        return _open("fallback")
    questions = q.build_questions(len(prompts))
    answers = response.answers
    if not _valid(answers, questions):
        logger.warning("jev gate fell back: incomplete or malformed answers")
        return _open("fallback")
    scores = {key: q.score(answers.get(key)) for key in questions}
    result = GateResult(
        relevant=q.counts_as_yes(answers.get(q.RELEVANT_KEY)),
        slots={k for k in CORE_KEYS if q.counts_as_yes(answers.get(q.slot_key(k)))},
        sensitive={
            i for i in range(len(prompts)) if q.counts_as_yes(answers.get(q.sensitive_key(i)))
        },
        source="jev",
        scores=scores,
    )
    logger.info("jev gate answers (%s): %s", mode, scores)
    if mode == "shadow":
        return _open("off", scores)
    return result


def same_heading(a: str, b: str, jev: JevClient | None = None) -> bool | None:
    """Do two headings name the same thing? None when uncertain, off, shadow or on any error:
    the model decides then."""
    if settings.PERSONALIZATION_JEV_GATE != "on" or not os.environ.get(KEY_ENV):
        return None
    try:
        response = _client(jev).ask(
            {"headings": [a, b]}, {q.heading_key(): q.heading_question(a, b)}
        )
    except Exception as exc:
        logger.warning("jev heading check fell back: %s", type(exc).__name__)
        return None
    return q.heading_verdict(response.answers.get(q.heading_key()))
