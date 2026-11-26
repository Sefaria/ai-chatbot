"""The fixed catalog of step names a result may report.

A result's `steps` entries are `{name, n_in, n_out}` with `name` taken from this catalog only, so
a result can never carry free text (which matters for real-data results). A case that needs a new
step adds it here, on `lab/core`.
"""

from __future__ import annotations

STEPS: dict[str, str] = {
    "load_turns": "Read the learner's turns in time order",
    "screen_sensitive": "Drop sensitive turns before any model or the store sees them",
    "detect_language": "Decide the learner's language from the script of their prompts",
    "find_instructions": "Find standing instructions inside a prompt",
    "classify_instruction": "Decide whether a standing instruction is a safe style or hides sources",
    "extract_turn": "Ask the model for slot values from the learner's latest prompt",
    "extract_batch": "Ask the model for slot values from a window of recent prompts",
    "typed_questions": "Ask a closed-set question (one of N, yes or no) of a typed client",
    "verify_evidence": "Keep a value only when its quote occurs verbatim in a prompt",
    "validate_values": "Keep a value only when it is legal for its slot",
    "write_profile": "Write the accepted values to the profile",
    "resolve_proposals": "Accept or reject a proposal the learner was asked to confirm",
    "expire_slots": "Remove values past their shelf life",
    "consolidate": "Merge the window's evidence into one profile",
    "assign_vocabulary": "Label a prompt's vocabulary (traditional, academic, transliterated, ...)",
    "read_site_signals": "Read the site language, browser language and country",
    "render_profile": "Turn the profile into the block that goes into the prompt",
    "answer_plain": "Answer a prompt with no profile",
    "answer_personalized": "Answer the same prompt with the profile in the prompt",
    "judge_pair": "Have a blind judge compare two answers",
    "judge_label": "Have a judge score one answer against a rule",
    "score": "Compute the case's metrics from the collected items",
}


def check_step_name(name: str) -> str:
    if name not in STEPS:
        raise ValueError(f"step name {name!r} is not in the catalog (L/steps.py)")
    return name
