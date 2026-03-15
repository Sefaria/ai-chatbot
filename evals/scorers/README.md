# Braintrust Scorers

Custom scorers for evaluating AI assistant responses. Supports both LLM-based evaluation (using prompts) and code-based evaluation (using Python logic).

## Quick Start

```bash
# Add a new LLM scorer
cp llm_scorers/non_psak.py llm_scorers/my_scorer.py
# Edit NAME, SLUG, DESCRIPTION, PROMPT

# Add a new code scorer
cp code_scorers/html_format.py code_scorers/my_scorer.py
# Edit NAME, SLUG, DESCRIPTION, handler()

# Build and push
python build.py my_scorer           # Build specific scorer (just the name, no path/extension)
python build.py                     # Or build all scorers
braintrust push built/my_scorer.py  # Push built file (one at a time)
```

## Directory Structure

```
evals/scorers/
├── llm_scorers/        # LLM-based scorers (prompt evaluation)
├── code_scorers/       # Code-based scorers (Python logic)
├── templates/          # Shared templates
├── build.py            # Combines templates + scorers → built/
└── built/              # Generated files (git-ignored)
```

## Scorer Types

### LLM Scorers (`llm_scorers/`)

Use an LLM (GPT) to evaluate responses based on a prompt. Good for subjective criteria like "is this response helpful?" or "does this avoid issuing rulings?"

```python
# llm_scorers/my_scorer.py
"""Brief description."""

NAME = "My Scorer"
SLUG = "my-scorer-xxxx"  # URL-safe, unique
DESCRIPTION = "One-line description for Braintrust UI"

PROMPT = """Your evaluation prompt.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

PASS/FAIL criteria...

Return ONLY the letter of your choice, followed by reasoning.
(a) PASS - appropriately handled
(b) FAIL - inappropriately handled
(c) NOT_RELEVANT - this evaluation doesn't apply"""
```

### Code Scorers (`code_scorers/`)

Use Python logic to evaluate responses. Good for objective criteria like "is the HTML valid?" or "is the response under 400 words?"

```python
# code_scorers/my_scorer.py
"""Brief description."""

from typing import Any
import re

NAME = "My Scorer"
SLUG = "my-scorer-xxxx"
DESCRIPTION = "One-line description for Braintrust UI"


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    # Extract and evaluate the response
    # ...

    return {
        "score": 1.0,  # or 0.0, or None
        "name": NAME,
        "metadata": {"choice": "a", "details": "..."}
    }
```

## Score Values

Both scorer types return:
- `score: 1.0` for PASS
- `score: 0.0` for FAIL
- `score: None` for NOT_RELEVANT (skipped in aggregations)

## Why This Architecture?

**Braintrust limitation:** Pushed scorers run remotely and cannot import local files. Each scorer must be completely self-contained in a single file.

**The problem:** We have 20+ scorers that share extraction logic and registration code. Copy-pasting this into each file would be unmaintainable.

**Our solution:** A simple build system:
- `templates/` contains shared code with placeholders (`$NAME`, `$SLUG`, etc.)
- `llm_scorers/` and `code_scorers/` contain just the unique parts of each scorer
- `build.py` combines them into self-contained files in `built/`
- Only `built/*.py` files are pushed to Braintrust

This gives us:
- **DRY code:** Shared logic lives in one place
- **Simple scorer files:** Each file is 50-100 lines
- **No runtime dependencies:** Generated files are fully self-contained

### Why This Isn't Overengineering

We considered simpler alternatives:

1. **One giant file with all scorers:** Would be 1000+ lines. Hard to navigate, review, or find specific scorers. Git diffs become noisy.

2. **Copy-paste shared code into each file:** 20 files × 150 lines of duplicated code. One bug fix requires 20 edits. Drift is inevitable.

3. **No build step, just accept duplication:** Maintenance nightmare within a month.

The build system adds exactly two things:
- One script (`build.py`)
- One command to run before pushing (`python build.py`)

That's it. No config files, no dependencies, no complex abstractions. The script does string replacement—the simplest possible transformation.

## CI/CD

The GitHub Action (`.github/workflows/push-scorers.yml`) automatically builds and pushes scorers when changes to `templates/*.py`, `llm_scorers/*.py`, `code_scorers/*.py`, or `build.py` are merged to main.

Required secrets:
- `BRAINTRUST_API_KEY` - for pushing scorers
- `OPENAI_API_KEY` - used by LLM scorers at runtime

## Engineer Workflow

### Adding an LLM scorer

1. Create `llm_scorers/my_scorer.py` with NAME, SLUG, DESCRIPTION, PROMPT
2. Build: `python build.py my_scorer`
3. Test: `braintrust push built/my_scorer.py`
4. Verify in Braintrust UI
5. Commit and push—CI will rebuild and push on merge

### Adding a code scorer

1. Create `code_scorers/my_scorer.py` with NAME, SLUG, DESCRIPTION, handler()
2. Build: `python build.py my_scorer`
3. Test: `braintrust push built/my_scorer.py`
4. Verify in Braintrust UI
5. Commit and push—CI will rebuild and push on merge

### Editing shared logic

1. Edit `templates/llm_scorers.py` or `templates/code_scorers.py`
2. Rebuild all: `python build.py`
3. Test one scorer: `braintrust push built/non_psak.py`
4. Commit and push—CI will rebuild and push all scorers

### Debugging LLM scorers

If a scorer isn't extracting data correctly, check `templates/llm_scorers.py`'s `extract_data()` function. It handles two scenarios:
- **Direct args:** `input`/`output` passed directly (local testing)
- **Trace spans:** `input`/`output` are None, data comes via `kwargs["trace"]` (pushed scorers)

## Environment Variables

LLM scorers need these at runtime:
- `OPENAI_API_KEY` - for the evaluation LLM call
- `BRAINTRUST_API_KEY` - set automatically by Braintrust

When testing locally:
```bash
export BRAINTRUST_API_KEY=<your-key>
export OPENAI_API_KEY=<your-key>
braintrust push built/my_scorer.py
```
