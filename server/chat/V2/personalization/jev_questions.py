"""Every Jev question and threshold the Profile job uses, in one place.

Jev answers closed questions only: a score for "yes" on a yes/no question. It gates (is anything
here worth a model call, which slots, which prompts are sensitive); it never picks a value and
never writes text. The model id is pinned.
"""

from __future__ import annotations

from .core_slots import CORE_SLOTS
from .jev import JevAnswer, JevQuestion

MODEL = "jev-1.13.0"

# A "yes" score below LOW is a clear no; above HIGH a clear yes; in between is uncertain, and an
# uncertain answer counts as yes ("ask the model") for every gating question.
LOW = 0.35
HIGH = 0.65

MAX_PROMPT_CHARS = 1000  # per prompt, in the Jev state
MAX_PROMPTS_PER_RUN = 30  # the next run takes the rest; the watermark only passes what was read

NEVER_STORE = (
    "health or illness",
    "mourning or bereavement",
    "family details",
    "personal halakhic status",
    "conversion",
    "denomination or observance level",
    "where the person lives",
)

RELEVANT_KEY = "relevant"
RELEVANT = JevQuestion(
    "noul",
    "Does any of these prompts say something about how this person learns or what they want from "
    "answers (language, reading level, interests, preferred kind of answer, learning habits)?",
)

SLOT_QUESTIONS = {
    key: JevQuestion(
        "noul", f"Do these prompts give evidence about the person's {heading.lower()}?"
    )
    for key, heading in CORE_SLOTS
}

HEADING_QUESTION = (
    "Do the two section headings name the same thing? Answer yes when they would hold the same "
    "content."
)


def slot_key(slot: str) -> str:
    return f"slot_{slot}"


def sensitive_key(index: int) -> str:
    return f"sensitive_{index}"


def sensitive_question(index: int) -> JevQuestion:
    return JevQuestion(
        "noul",
        f"Does prompt number {index} touch any of: {'; '.join(NEVER_STORE)}?",
    )


def heading_key() -> str:
    return "same_heading"


def heading_question(a: str, b: str) -> JevQuestion:
    return JevQuestion("noul", f'{HEADING_QUESTION} First: "{a}". Second: "{b}".')


def build_questions(prompt_count: int) -> dict[str, JevQuestion]:
    questions = {RELEVANT_KEY: RELEVANT}
    questions.update({slot_key(k): q for k, q in SLOT_QUESTIONS.items()})
    questions.update({sensitive_key(i): sensitive_question(i) for i in range(prompt_count)})
    return questions


def score(answer: JevAnswer | None) -> float | None:
    return None if answer is None else answer.noul


def counts_as_yes(answer: JevAnswer | None) -> bool:
    """Yes or uncertain is a yes; a missing answer is uncertain."""
    value = score(answer)
    return value is None or value >= LOW


def heading_verdict(answer: JevAnswer | None) -> bool | None:
    """True when clearly the same, False when clearly different, None when uncertain."""
    value = score(answer)
    if value is None or LOW <= value <= HIGH:
        return None
    return value > HIGH
