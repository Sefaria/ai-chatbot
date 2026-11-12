"""Code checks on a Profile the writer produced. A failure rejects the run; the stored Profile
stays as it was. The cheap checks run first; the model's yes/no check runs last, and only when
everything else passed."""

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
    "conversion, denomination or observance level, location. Answer no otherwise."
)


@dataclass
class CheckResult:
    ok: bool
    failed: str | None = None


def _fail(name: str) -> CheckResult:
    return CheckResult(False, name)


def normalize_heading(heading: str) -> str:
    return " ".join(heading.lower().split())


def word_count(sections: list[SectionData]) -> int:
    return sum(len(s.body.split()) for s in sections)


def check_profile(sections: list[SectionData], llm: LLMClient | None) -> CheckResult:
    if any(not s.body.strip() or not s.heading.strip() for s in sections):
        return _fail("empty_section")
    headings = [normalize_heading(s.heading) for s in sections]
    if len(set(headings)) != len(headings):
        return _fail("duplicate_heading")
    if sum(1 for s in sections if s.kind == "dynamic") > MAX_DYNAMIC:
        return _fail("dynamic_cap")
    if word_count(sections) > MAX_WORDS:
        return _fail("words")
    text = render_profile(sections)
    if is_sensitive(text) or _NEVER_STORE_EXTRA.search(text):
        return _fail("never_store_terms")
    if llm is not None:
        answer = llm.complete(
            system=MODEL_CHECK_SYSTEM,
            user=f"<profile>\n{clean_text(text)}\n</profile>",
            model=settings.PERSONALIZATION_MODEL,
            max_tokens=5,
            timeout=settings.PERSONALIZATION_MODEL_TIMEOUT,
        )
        if not answer.text.strip().lower().startswith("no"):
            return _fail("never_store_model")
    return CheckResult(True)
