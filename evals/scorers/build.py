#!/usr/bin/env python3
"""
Build script for Braintrust scorers.

Combines _base.py template with each prompt file in prompts/ to generate
self-contained scorer files in built/.

Usage:
    python build.py           # Build all scorers
    python build.py non_psak  # Build specific scorer

Then push:
    braintrust push built/*.py
"""

import sys
from pathlib import Path

SCORERS_DIR = Path(__file__).parent
PROMPTS_DIR = SCORERS_DIR / "prompts"
BUILT_DIR = SCORERS_DIR / "built"
BASE_FILE = SCORERS_DIR / "_base.py"


def build_scorer(prompt_file: Path) -> None:
    """Build a single scorer from its prompt file."""
    # Load prompt file to get NAME, SLUG, DESCRIPTION, PROMPT
    namespace = {}
    exec(prompt_file.read_text(), namespace)

    name = namespace.get("NAME")
    slug = namespace.get("SLUG")
    description = namespace.get("DESCRIPTION")
    prompt = namespace.get("PROMPT")

    if not all([name, slug, description, prompt]):
        print(f"  Skipping {prompt_file.name}: missing required fields")
        return

    # Load base template and substitute
    base = BASE_FILE.read_text()

    # Use simple string replacement (not Template) to avoid issues with braces in prompt
    output = base.replace("$NAME", name)
    output = output.replace("$SLUG", slug)
    output = output.replace("$DESCRIPTION", description)
    output = output.replace("$PROMPT", prompt)

    # Write to built/
    output_file = BUILT_DIR / prompt_file.name
    output_file.write_text(output)
    print(f"  Built: {output_file.name}")


def main():
    BUILT_DIR.mkdir(exist_ok=True)

    # Get list of prompt files to build
    if len(sys.argv) > 1:
        # Build specific scorers
        prompt_files = [PROMPTS_DIR / f"{name}.py" for name in sys.argv[1:]]
        prompt_files = [f for f in prompt_files if f.exists()]
    else:
        # Build all
        prompt_files = list(PROMPTS_DIR.glob("*.py"))

    if not prompt_files:
        print("No prompt files found to build.")
        return

    print(f"Building {len(prompt_files)} scorer(s)...")
    for prompt_file in prompt_files:
        build_scorer(prompt_file)

    print("\nDone. Push with: braintrust push evals/scorers/built/*.py")


if __name__ == "__main__":
    main()
