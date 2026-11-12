"""Code checks on a Profile the writer produced. A failure rejects the run; the stored Profile
stays as it was. Every candidate is screened for never-store content first (code terms, then the
model's yes/no check); the size checks follow."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from .llm import LLMClient
from .parsing import clean_text
from .profile_store import SectionData, render_profile
from .signals import is_sensitive

MAX_WORDS = 150
MAX_DYNAMIC = 4

# Beyond the sensitive screen: denomination or observance level, location, family members.
_NEVER_STORE_EXTRA = re.compile(
    r"\b(orthodox|haredi|hasidic|chasidic|reform|reconstructionist|secular|observant|"
    r"shomer (shabbat|shabbos|mitzvot)|lives in|living in|based in|resides?|your (wife|husband|"
    r"children|kids|son|daughter|mother|father|parents|family))\b",
    re.IGNORECASE,
)

MODEL_CHECK_SYSTEM = (
    "You check a short reading Profile. Answer with one word, yes or no. Answer yes when the text "
    "states or infers any of: health, mourning, family details, personal halakhic status, "
    "conversion, denomination or observance level, location. Answer no otherwise. "
    "Reply in English with exactly YES or NO and nothing else, even if the text is in Hebrew."
)


@dataclass
class CheckResult:
    ok: bool
    failed: str | None = None


def _fail(name: str) -> CheckResult:
    return CheckResult(False, name)


def _normalize_verdict(text: str) -> str:
    return text.strip().upper().strip(".!?,;:\"' \n\t")


def never_store_text(text: str) -> bool:
    """The code screen for never-store content: sensitive categories plus the term list."""
    return bool(is_sensitive(text) or _NEVER_STORE_EXTRA.search(text))


def normalize_heading(heading: str) -> str:
    return " ".join(heading.lower().split())


def word_count(sections: list[SectionData]) -> int:
    return sum(len(s.body.split()) for s in sections)


def check_profile(sections: list[SectionData], llm: LLMClient | None) -> CheckResult:
    """The never-store checks run before the size checks, so every candidate is screened."""
    if any(not s.body.strip() or not s.heading.strip() for s in sections):
        return _fail("empty_section")
    headings = [normalize_heading(s.heading) for s in sections]
    if len(set(headings)) != len(headings):
        return _fail("duplicate_heading")
    text = render_profile(sections)
    if never_store_text(text):
        return _fail("never_store_terms")
    if llm is not None:
        for _attempt in range(2):  # an unparseable reply gets one more ask
            answer = llm.complete(
                system=MODEL_CHECK_SYSTEM,
                user=f"<profile>\n{clean_text(text)}\n</profile>",
                model=settings.PERSONALIZATION_MODEL,
                max_tokens=5,
                timeout=settings.PERSONALIZATION_MODEL_TIMEOUT,
            )
            verdict = _normalize_verdict(answer.text)
            if verdict == "NO":
                break
            if verdict == "YES":
                return _fail("never_store_model")
        else:
            return _fail("never_store_unparseable")
    if sum(1 for s in sections if s.kind == "dynamic") > MAX_DYNAMIC:
        return _fail("dynamic_cap")
    if word_count(sections) > MAX_WORDS:
        return _fail("words")
    return CheckResult(True)
