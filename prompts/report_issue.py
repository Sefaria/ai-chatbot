"""Report-an-issue prompt for the Sefaria assistant's translation QA flow.

Composes prompts/prompt_text/report_issue.md with the translation methodology
document inlined, so the assistant judges reports against the same document the
reader sees from the "unverified machine translation" label.

Run directly to push this prompt to Braintrust. With no env vars set the push
is blocked by a safety gate — set PROMPT_SLUG_OVERRIDE to push to a sandbox slug
instead. See prompts/plan.md for the full local-testing workflow.
"""

import os
import pathlib
import sys

import braintrust

from utilities import read_prompt_text

# Reach server/chatbot_server/model_defaults.py without booting Django.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
from chatbot_server.model_defaults import (
    AGENT_MAX_TOKENS,
    AGENT_MODEL as AGENT_MODEL_DEFAULT,
    AGENT_TEMPERATURE,
)

SLUG = os.environ.get("PROMPT_SLUG_OVERRIDE", "report-issue")
# Prefix name so sandbox pushes are visually distinct in the Braintrust UI.
NAME = "[test] Report Issue" if SLUG != "report-issue" else "Report Issue"
DESCRIPTION = "Handles reader reports about unverified machine translations"

# Metadata for Braintrust — the runtime reads AGENT_MODEL from env/settings independently.
MODEL = os.environ.get("AGENT_MODEL", AGENT_MODEL_DEFAULT)

# Imported from model_defaults so this stays in sync with the runtime callsite automatically.
PARAMS = {"temperature": AGENT_TEMPERATURE, "max_tokens": AGENT_MAX_TOKENS}

# Marker in report_issue.md where the methodology document is spliced in. Deliberately
# not mustache syntax: {{...}} is reserved for runtime build_vars (e.g. response_format),
# and the methodology is resolved once, here, at push time.
METHODOLOGY_MARKER = "<!-- TRANSLATION_METHODOLOGY -->"


def build_prompt() -> str:
    """Return report_issue.md with the methodology document inlined."""
    template = read_prompt_text("report_issue")
    if METHODOLOGY_MARKER not in template:
        raise ValueError(
            f"report_issue.md is missing the {METHODOLOGY_MARKER} marker — the "
            "translation methodology would not reach the assistant."
        )
    return template.replace(METHODOLOGY_MARKER, read_prompt_text("translation_methodology"))


PROMPT = build_prompt()


if __name__ == "__main__":
    # Safety gate: refuses to push unless PROMPT_SLUG_OVERRIDE (sandbox) or
    # BRAINTRUST_PUSH_TARGET=prod (CI / emergency only) is set.
    slug_override = os.environ.get("PROMPT_SLUG_OVERRIDE")
    push_target = os.environ.get("BRAINTRUST_PUSH_TARGET")
    if not slug_override and push_target != "prod":
        print(
            "ERROR: local pushes must target a sandbox slug.\n"
            "\n"
            "  Set PROMPT_SLUG_OVERRIDE=<prod-slug>-<yourname>-<purpose> to test:\n"
            f"    PROMPT_SLUG_OVERRIDE={SLUG}-yourname-test python prompts/report_issue.py\n"
            "\n"
            "  Prod pushes happen via the push-prompts CI workflow on merge to main.\n",
            file=sys.stderr,
        )
        sys.exit(2)

    project = braintrust.projects.create(name="On Site Agent")
    prompt = project.prompts.create(
        name=NAME,
        slug=SLUG,
        description=DESCRIPTION,
        model=MODEL,
        prompt=PROMPT,
        params=PARAMS,
        if_exists="replace",
    )
    project.add_prompt(prompt)
    project.publish()
    print(f"Pushed: {SLUG}")
