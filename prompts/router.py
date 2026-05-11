"""Router prompt — message classifier for the Sefaria assistant.

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
    ROUTER_MAX_TOKENS,
    ROUTER_MODEL as ROUTER_MODEL_DEFAULT,
    ROUTER_TEMPERATURE,
)

NAME = "Router"
SLUG = os.environ.get("PROMPT_SLUG_OVERRIDE", "router-classifier")
DESCRIPTION = "Classifies messages as Translation, Discovery, or Other"

# Metadata for Braintrust — the runtime reads ROUTER_MODEL from env/settings independently.
MODEL = os.environ.get("ROUTER_MODEL", ROUTER_MODEL_DEFAULT)

# Imported from model_defaults so this stays in sync with the runtime callsite automatically.
PARAMS = {"temperature": ROUTER_TEMPERATURE, "max_tokens": ROUTER_MAX_TOKENS}

PROMPT = read_prompt_text("router")


if __name__ == "__main__":
    # Safety gate: refuses to push unless PROMPT_SLUG_OVERRIDE (sandbox) or
    # BRAINTRUST_PUSH_TARGET=prod (CI / emergency only) is set. This is what
    # keeps routine prod pushes physically restricted to the CI workflow —
    # a local run without these vars cannot reach the prod slug at all.
    slug_override = os.environ.get("PROMPT_SLUG_OVERRIDE")
    push_target = os.environ.get("BRAINTRUST_PUSH_TARGET")
    if not slug_override and push_target != "prod":
        print(
            "ERROR: local pushes must target a sandbox slug.\n"
            "\n"
            "  Set PROMPT_SLUG_OVERRIDE=<prod-slug>-<yourname>-<purpose> to test:\n"
            f"    PROMPT_SLUG_OVERRIDE={SLUG}-yourname-test python prompts/router.py\n"
            "\n"
            "  Prod pushes happen via the push-prompts CI workflow on merge to main.\n"
            "  If you genuinely need to push to prod from your laptop (e.g. CI is\n"
            "  down), set BRAINTRUST_PUSH_TARGET=prod — see prompts/plan.md.\n",
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
