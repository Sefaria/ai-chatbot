# Braintrust Scorers

Custom LLM-based scorers for evaluating AI assistant responses.

## Quick Start

```bash
# Add a new scorer
cp prompts/non_psak.py prompts/my_scorer.py
# Edit NAME, SLUG, DESCRIPTION, PROMPT

# Build and push
python build.py
braintrust push built/*.py
```

## Directory Structure

```
evals/scorers/
├── README.md          # This file
├── _base.py           # Shared template (extraction, handler, registration)
├── build.py           # Combines _base.py + prompts/* → built/*
├── prompts/           # Scorer definitions (one file per scorer)
│   └── non_psak.py    # Example: NAME, SLUG, DESCRIPTION, PROMPT
└── built/             # Generated files (git-ignored)
    └── *.py           # Self-contained scorers ready to push
```

## Adding a New Scorer

1. Create a new file in `prompts/`:

```python
# prompts/my_scorer.py
"""Brief description of what this scorer evaluates."""

NAME = "My Scorer"
SLUG = "my-scorer"  # URL-safe, unique identifier
DESCRIPTION = "One-line description shown in Braintrust UI"

PROMPT = """Your evaluation prompt here.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Define PASS/FAIL/NOT_RELEVANT criteria...

Return ONLY the letter of your choice, followed by your reasoning.
(a) PASS - appropriately handled
(b) FAIL - inappropriately handled
(c) NOT_RELEVANT - this evaluation doesn't apply"""
```

2. Build and push:

```bash
python build.py
braintrust push built/my_scorer.py
```

## Editing an Existing Scorer

1. Edit the file in `prompts/`
2. Rebuild: `python build.py`
3. Push: `braintrust push built/<scorer>.py`

Changes to `_base.py` affect all scorers—rebuild all with `python build.py`.

## Prompt Guidelines

All prompts must:
- Include `{query}`, `{response}`, `{page_url}`, `{summary}` placeholders
- Define clear PASS/FAIL/NOT_RELEVANT criteria
- End with the (a)/(b)/(c) choice format
- Return the letter first, then reasoning

The scorer returns:
- `score: 1.0` for PASS (a)
- `score: 0.0` for FAIL (b)
- `score: None` for NOT_RELEVANT (c)

## Why This Architecture?

**Braintrust limitation:** Pushed scorers run remotely and cannot import local files. Each scorer must be completely self-contained in a single file.

**The problem:** We have 20+ scorers that share ~150 lines of extraction logic and handler code. Copy-pasting this into each file would be unmaintainable.

**Our solution:** A simple build system:
- `_base.py` contains all shared code with `$NAME`, `$SLUG`, `$DESCRIPTION`, `$PROMPT` placeholders
- `prompts/*.py` contains just the unique parts of each scorer
- `build.py` combines them into self-contained files in `built/`
- Only `built/*.py` files are pushed to Braintrust

This gives us:
- **DRY code:** Shared logic lives in one place
- **Simple scorer files:** Each prompt file is ~50-100 lines
- **No runtime dependencies:** Generated files are fully self-contained

### Why This Isn't Overengineering

We considered simpler alternatives:

1. **One giant file with all scorers:** Would be 1000+ lines. Hard to navigate, review, or find specific scorers. Git diffs become noisy.

2. **Copy-paste shared code into each file:** 20 files × 150 lines of duplicated code. One bug fix requires 20 edits. Drift is inevitable.

3. **No build step, just accept duplication:** Maintenance nightmare within a month.

The build system adds exactly two things:
- One 40-line script (`build.py`)
- One command to run before pushing (`python build.py`)

That's it. No config files, no dependencies, no complex abstractions. The script does string replacement—the simplest possible transformation. Anyone can read it and understand what it does in 30 seconds.

The tradeoff is worth it: we get single-source-of-truth for shared logic, while each scorer file contains only what makes it unique.

## CI/CD

The GitHub Action (`.github/workflows/push-scorers.yml`) automatically builds and pushes scorers when changes to `_base.py`, `prompts/*.py`, or `build.py` are merged to main.

Required secrets:
- `BRAINTRUST_API_KEY` - for pushing scorers
- `OPENAI_API_KEY` - used by scorers at runtime (set in Braintrust)

## Engineer Workflow

### Adding a scorer

1. Create `prompts/my_scorer.py` with NAME, SLUG, DESCRIPTION, PROMPT
2. Build locally: `python build.py my_scorer`
3. Test locally: `braintrust push built/my_scorer.py` (uses your local env vars)
4. Verify in Braintrust UI
5. Commit and push—CI will rebuild and push on merge

### Editing shared logic

1. Edit `_base.py`
2. Rebuild all: `python build.py`
3. Test one scorer: `braintrust push built/non_psak.py`
4. Commit and push—CI will rebuild and push all scorers

### Debugging

If a scorer isn't extracting data correctly, check `_base.py`'s `extract_data()` function. It handles two scenarios:
- **Direct args:** `input`/`output` passed directly (local testing)
- **Trace spans:** `input`/`output` are None, data comes via `kwargs["trace"]` (pushed scorers)

## Environment Variables

Scorers need these at runtime:
- `OPENAI_API_KEY` - for the evaluation LLM call
- `BRAINTRUST_API_KEY` - set automatically by Braintrust

When testing locally, export these before pushing:
```bash
export BRAINTRUST_API_KEY=<your-key>
export OPENAI_API_KEY=<your-key>
braintrust push built/my_scorer.py
```
