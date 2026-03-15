#!/usr/bin/env python3
"""
Build script for Braintrust scorers.

Combines templates with scorer definitions to generate self-contained files in built/.

Scorer Types:
  - LLM scorers (llm_scorers/): Define NAME, SLUG, DESCRIPTION, PROMPT
    Uses templates/llm_scorers.py which includes OpenAI evaluation logic

  - Code scorers (code_scorers/): Define NAME, SLUG, DESCRIPTION, and a handler() function
    Uses templates/code_scorers.py which is minimal (just registration)

Usage:
    python build.py              # Build all scorers
    python build.py non_psak     # Build specific LLM scorer
    python build.py html_format  # Build specific code scorer

Then push the built files (one at a time):
    braintrust push built/html_format.py
"""

import sys
import re
from pathlib import Path

SCORERS_DIR = Path(__file__).parent
LLM_DIR = SCORERS_DIR / "llm_scorers"
CODE_DIR = SCORERS_DIR / "code_scorers"
TEMPLATES_DIR = SCORERS_DIR / "templates"
BUILT_DIR = SCORERS_DIR / "built"


def build_llm_scorer(scorer_file: Path) -> None:
    """Build an LLM-based scorer that uses a prompt for evaluation."""
    namespace = {}
    exec(scorer_file.read_text(), namespace)

    name = namespace.get("NAME")
    slug = namespace.get("SLUG")
    description = namespace.get("DESCRIPTION")
    prompt = namespace.get("PROMPT")

    if not all([name, slug, description, prompt]):
        print(
            f"  Skipping {scorer_file.name}: missing NAME, SLUG, DESCRIPTION, or PROMPT"
        )
        return

    template = (TEMPLATES_DIR / "llm_scorers.py").read_text()

    output = template.replace("$NAME", name)
    output = output.replace("$SLUG", slug)
    output = output.replace("$DESCRIPTION", description)
    output = output.replace("$PROMPT", prompt)

    output_file = BUILT_DIR / scorer_file.name
    output_file.write_text(output)
    print(f"  Built (LLM): {output_file.name}")


def build_code_scorer(scorer_file: Path) -> None:
    """Build a code-based scorer that uses custom logic for evaluation."""
    content = scorer_file.read_text()
    namespace = {}
    exec(content, namespace)

    name = namespace.get("NAME")
    slug = namespace.get("SLUG")
    description = namespace.get("DESCRIPTION")

    if not all([name, slug, description]):
        print(f"  Skipping {scorer_file.name}: missing NAME, SLUG, or DESCRIPTION")
        return

    if "handler" not in namespace:
        print(f"  Skipping {scorer_file.name}: missing handler() function")
        return

    # Extract the handler function source code
    # Match from "def handler" until the next top-level definition or end of file
    # Uses negative lookahead to not stop at indented lines or blank lines
    handler_match = re.search(
        r"^(def handler\(.*\n(?:(?:[ \t]+.*|)\n)*)",
        content,
        re.MULTILINE,
    )
    if not handler_match:
        print(f"  Skipping {scorer_file.name}: could not extract handler() function")
        return

    handler_code = handler_match.group(1).rstrip()

    # Extract any imports that the handler needs
    # Filter out imports already in the template (typing.Any, braintrust, pydantic)
    imports_match = re.search(r"^((?:from .+|import .+)\n)+", content, re.MULTILINE)
    extra_imports = ""
    if imports_match:
        imports = imports_match.group(0)
        for line in imports.strip().split("\n"):
            # Skip imports already in template
            if any(
                skip in line
                for skip in ["braintrust", "pydantic", "from typing import Any"]
            ):
                continue
            extra_imports += line + "\n"

    template = (TEMPLATES_DIR / "code_scorers.py").read_text()

    output = template.replace("$NAME", name)
    output = output.replace("$SLUG", slug)
    output = output.replace("$DESCRIPTION", description)
    output = output.replace("$HANDLER", handler_code)

    # Add extra imports after the template imports if needed
    if extra_imports:
        output = output.replace(
            "from pydantic import BaseModel",
            f"from pydantic import BaseModel\n\n{extra_imports.rstrip()}",
        )

    output_file = BUILT_DIR / scorer_file.name
    output_file.write_text(output)
    print(f"  Built (code): {output_file.name}")


def main():
    BUILT_DIR.mkdir(exist_ok=True)

    # Collect scorer files to build
    llm_files = []
    code_files = []

    if len(sys.argv) > 1:
        # Build specific scorers - check both directories
        for name in sys.argv[1:]:
            llm_path = LLM_DIR / f"{name}.py"
            code_path = CODE_DIR / f"{name}.py"
            if llm_path.exists():
                llm_files.append(llm_path)
            elif code_path.exists():
                code_files.append(code_path)
            else:
                print(
                    f"  Warning: {name}.py not found in llm_scorers/ or code_scorers/"
                )
    else:
        # Build all
        llm_files = list(LLM_DIR.glob("*.py"))
        code_files = list(CODE_DIR.glob("*.py"))

    total = len(llm_files) + len(code_files)
    if total == 0:
        print("No scorer files found to build.")
        return

    print(f"Building {total} scorer(s)...")

    for scorer_file in llm_files:
        build_llm_scorer(scorer_file)

    for scorer_file in code_files:
        build_code_scorer(scorer_file)

    print("\nDone. Push each file individually:")
    print("  braintrust push built/<scorer_name>.py")


if __name__ == "__main__":
    main()
