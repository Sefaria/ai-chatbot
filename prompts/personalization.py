"""Personalization prompt chapters for the Sefaria assistant.

Run directly to push the personalization chapters to Braintrust, the same
way prompts/core.py pushes the main prompt. With no env vars set the push is
blocked by a safety gate. Set PROMPT_SLUG_OVERRIDE=<suffix> to push sandbox
copies instead: each live slug becomes `<live slug>-<suffix>` and the name gets
a "[test] " prefix. (This differs from core.py, where the override is the whole
slug, because this one script pushes several prompts.)

The chapter text is read straight from the bundled .md files the server ships
(server/chat/V2/prompts/chapters/), so this script needs only `braintrust`
installed and imports nothing from Django or the chat package.
"""

import argparse
import os
import pathlib
import sys

import braintrust

# Reach server/chatbot_server/model_defaults.py without booting Django.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
from chatbot_server.model_defaults import (
    AGENT_MAX_TOKENS,
    AGENT_MODEL as AGENT_MODEL_DEFAULT,
    AGENT_TEMPERATURE,
)

CHAPTERS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "server" / "chat" / "V2" / "prompts" / "chapters"
)

# (chapter file stem, live slug, display name, description)
PROMPTS = [
    (
        "personalization_answer",
        "la-p13n-personalization-answer",
        "LA Personalization Answer",
        "Answering-time personalization guidance",
    ),
    (
        "personalization_profile_used",
        "la-p13n-personalization-profile-used",
        "LA Personalization Profile Used",
        "Profile-used notice guidance for personalized answers",
    ),
    (
        "profile_update",
        "la-p13n-profile-update",
        "LA Profile Update",
        "Nightly build of the user profile from recent prompts",
    ),
    (
        "profile_alter",
        "la-p13n-profile-alter",
        "LA Profile Alter",
        "Applies a user's alter request to their profile",
    ),
]

# Metadata for Braintrust only: the runtime picks its own models and params per call.
MODEL = os.environ.get("AGENT_MODEL", AGENT_MODEL_DEFAULT)
PARAMS = {"temperature": AGENT_TEMPERATURE, "max_tokens": AGENT_MAX_TOKENS}


def build_prompts(env=None, only=None):
    """The list of (slug, name, description, text) to push for the given environment.

    `only` restricts the push to those live slugs (the unsuffixed ones in PROMPTS).
    """
    env = os.environ if env is None else env
    suffix = env.get("PROMPT_SLUG_OVERRIDE")
    out = []
    for stem, slug, name, description in PROMPTS:
        if only is not None and slug not in only:
            continue
        if suffix:
            slug = f"{slug}-{suffix}"
            name = f"[test] {name}"
        text = (CHAPTERS_DIR / f"{stem}.md").read_text(encoding="utf-8")
        out.append((slug, name, description, text))
    return out


def check_gate(env=None):
    """Exit 2 unless PROMPT_SLUG_OVERRIDE (sandbox) or BRAINTRUST_PUSH_TARGET=prod is set.

    Same gate as prompts/core.py: keeps routine prod pushes restricted to the CI workflow.
    """
    env = os.environ if env is None else env
    if not env.get("PROMPT_SLUG_OVERRIDE") and env.get("BRAINTRUST_PUSH_TARGET") != "prod":
        print(
            "ERROR: local pushes must target a sandbox slug.\n"
            "\n"
            "  Set PROMPT_SLUG_OVERRIDE=<yourname>-<purpose> to test; each live slug\n"
            "  gets that suffix appended:\n"
            "    PROMPT_SLUG_OVERRIDE=yourname-test python prompts/personalization.py\n"
            "\n"
            "  Prod pushes happen via the push-prompts CI workflow on merge to main.\n"
            "  If you genuinely need to push to prod from your laptop (e.g. CI is\n"
            "  down), set BRAINTRUST_PUSH_TARGET=prod — see prompts/plan.md.\n",
            file=sys.stderr,
        )
        sys.exit(2)


def parse_only(value):
    """The live slugs named by a comma-separated --only value; exit 2 on an unknown slug."""
    only = [s.strip() for s in value.split(",") if s.strip()]
    known = {slug for _, slug, _, _ in PROMPTS}
    unknown = [s for s in only if s not in known]
    if unknown:
        print(
            f"ERROR: unknown slug(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
        sys.exit(2)
    return only


def push(env=None, only=None):
    check_gate(env)
    project = braintrust.projects.create(name="On Site Agent")
    pushed = []
    for slug, name, description, text in build_prompts(env, only):
        prompt = project.prompts.create(
            name=name,
            slug=slug,
            description=description,
            model=MODEL,
            prompt=text,
            params=PARAMS,
            if_exists="replace",
        )
        project.add_prompt(prompt)
        pushed.append(slug)
    project.publish()
    for slug in pushed:
        print(f"Pushed: {slug}")
    return pushed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated live slugs to push; default is all")
    args = parser.parse_args()
    push(only=parse_only(args.only) if args.only else None)
