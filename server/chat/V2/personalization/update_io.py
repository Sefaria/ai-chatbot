"""Input builder and output validator for the nightly `la-p13n-profile-update` call.

Pure of orchestration: `build_update_input` reads the store and returns the text for the model plus
the prompt list (so a support number maps back to a date); `validate_update_output` checks the
model's JSON against the code rules. The job wires them together.
"""

from __future__ import annotations

from datetime import datetime

from . import jev_questions as q
from .checks import MAX_DYNAMIC, MAX_WORDS, normalize_heading
from .core_slots import CORE_HEADINGS, CORE_KEYS
from .parsing import clean_text, parse_json_object
from .profile_store import PromptRow, get_sections, get_settings, unprocessed_user_prompts
from .signals import is_sensitive, session_language

MAX_PROMPTS = 150
ORIGINS = ("stated", "inferred", "user")
NEEDS_SUPPORT = ("stated", "inferred")


class UpdateOutputError(Exception):
    """The model's output broke a rule; `reason` is a fixed code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def core_heading(key: str) -> str:
    return CORE_HEADINGS[key]


def support_dates(section: dict, prompts: list[PromptRow]) -> datetime:
    """The newest date among the section's supporting prompts (numbers are 1-based)."""
    return max(prompts[n - 1].server_timestamp for n in section["support"])


def _update_prompts(user_id: str) -> list[PromptRow]:
    """The last MAX_PROMPTS screened prompts of any age (after the last Clear), oldest first, rule
    screened and cleaned. An exact duplicate text is kept once, at its latest occurrence, so its
    date shows when the learner last asked it."""
    rows = unprocessed_user_prompts(user_id, None, newest=MAX_PROMPTS)
    out: list[PromptRow] = []
    for p in rows:
        if is_sensitive(p.text):
            continue
        text = clean_text(p.text)[: q.MAX_PROMPT_CHARS]
        if text:
            out.append(PromptRow(p.message_id, p.session_id, text, p.server_timestamp))
    last_at = {p.text: i for i, p in enumerate(out)}
    return [p for i, p in enumerate(out) if last_at[p.text] == i]


def build_update_input(user_id: str, now: datetime) -> tuple[str, list[PromptRow]]:
    settings = get_settings(user_id)
    sections = get_sections(user_id)
    prompts = _update_prompts(user_id)
    current = (
        "\n\n".join(
            f"[{s.key or 'dynamic'}] {s.heading} ({s.origin}, last supported "
            f"{s.last_supported_at.date().isoformat()})\n{s.body}"
            for s in sections
        )
        or "none"
    )
    blocks = [
        f"<today>{now.date().isoformat()}</today>",
        f"<current_profile>\n{current}\n</current_profile>",
    ]
    instructions = settings.instructions_text.strip()
    if settings.instructions_enabled and instructions:
        blocks.append(f"<instructions>\n{clean_text(instructions)}\n</instructions>")
    language = session_language([p.text for p in prompts]) or "unknown"
    lines = [
        f"[P{i}] {p.server_timestamp.date().isoformat()} {p.text}"
        for i, p in enumerate(prompts, start=1)
    ]
    blocks += [
        f"<allowed_core_slots>{', '.join(CORE_KEYS)}</allowed_core_slots>",
        f"<language_signal>{language}</language_signal>",
        "<prompts>\n" + "\n".join(lines) + "\n</prompts>",
    ]
    return "\n".join(blocks), prompts


def _is_count(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def validate_update_output(raw: str, n_prompts: int) -> list[dict]:
    """The model's sections, each `{key, heading, body, origin, support}`, or UpdateOutputError.
    Core headings are set from code; dynamic headings and bodies are stripped."""
    parsed = parse_json_object(raw) if isinstance(raw, str) else None
    if not parsed or not isinstance(parsed.get("sections"), list):
        raise UpdateOutputError("not_json_sections")
    sections: list[dict] = []
    seen_keys: set[str] = set()
    seen_headings: set[str] = set()
    words = dynamic = 0
    for entry in parsed["sections"]:
        if not isinstance(entry, dict):
            raise UpdateOutputError("bad_section")
        key = entry.get("key")
        if key is not None and (not isinstance(key, str) or key not in CORE_KEYS):
            raise UpdateOutputError("bad_key")
        body = entry.get("body")
        if not isinstance(body, str) or not body.strip():
            raise UpdateOutputError("bad_body")
        body = body.strip()
        if key is None:
            heading = entry.get("heading")
            if not isinstance(heading, str) or not heading.strip():
                raise UpdateOutputError("bad_heading")
            heading = heading.strip()
            dynamic += 1
        else:
            heading = core_heading(key)
            if key in seen_keys:
                raise UpdateOutputError("duplicate_key")
            seen_keys.add(key)
        norm = normalize_heading(heading)
        if norm in seen_headings:
            raise UpdateOutputError("duplicate_heading")
        seen_headings.add(norm)
        origin = entry.get("origin")
        if origin not in ORIGINS:
            raise UpdateOutputError("bad_origin")
        support = entry.get("support", [])
        if not isinstance(support, list) or not all(
            _is_count(n) and 1 <= n <= n_prompts for n in support
        ):
            raise UpdateOutputError("bad_support")
        if origin in NEEDS_SUPPORT and not support:
            raise UpdateOutputError("missing_support")
        words += len(body.split())
        sections.append(
            {
                "key": key,
                "heading": heading,
                "body": body,
                "origin": origin,
                "support": sorted(set(support)),
            }
        )
    if dynamic > MAX_DYNAMIC:
        raise UpdateOutputError("too_many_dynamic")
    if words >= MAX_WORDS:
        raise UpdateOutputError("too_long")
    return sections
