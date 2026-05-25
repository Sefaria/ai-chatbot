"""Shared helpers for prompt push scripts."""

import pathlib

_PROMPT_TEXT_DIR = pathlib.Path(__file__).parent / "prompt_text"


def read_prompt_text(name: str) -> str:
    """Return the prompt text for `name` from prompts/prompt_text/<name>.md.

    Resolves relative to this file, not the current working directory,
    so the call works regardless of where the push script is invoked from.
    """
    return (_PROMPT_TEXT_DIR / f"{name}.md").read_text()
