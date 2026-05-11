# Plan: Move Braintrust Prompts into the Repo

Mirror what we did for scorers: prompts live in this repo as the source of truth, and a CI hook pushes them to Braintrust on merge to `main`.

This is significantly simpler than the scorer pipeline because prompts have no shared logic — no template, no `build.py`, no `built/` directory. Each prompt is a self-contained Python file that pushes itself to Braintrust when run (there's no need for build files because Braintrust isn't executing anything, we're pushing the prompts to Braintrust since it is the source of truth for all stakeholders, not because anything here runs there for prompts - unlike scorers which execute).

## Scope

Four prompts in this iteration:

| Setting | Slug | Caller |
|---|---|---|
| `CORE_PROMPT_SLUG` | `core-8fbc` | `server/chat/V2/views.py`, `anthropic_views.py` |
| `GUARDRAIL_PROMPT_SLUG` | `guardrail-checker` | `server/chat/V2/guardrail/guardrail_service.py` |
| `ROUTER_PROMPT_SLUG` | `router-classifier` | `server/chat/V2/router/router_service.py` |
| `RESPONSE_FORMAT_PROMPT_SLUG` | `response-format` | `server/chat/V2/agent/turn_orchestrator.py` |

**Deferred to a later iteration:** `REWRITER_PROMPT_SLUG` (`question-rewriter`) and `TRANSLATION_PROMPT_SLUG` (`Translation`). Both remain UI-managed for now — their settings entries in `server/chatbot_server/settings.py` and their callsites in `server/chat/V2/router/router_service.py` stay as-is.

The runtime path (`PromptService.load_prompt(slug)`) does not change. We are only changing **authorship** — moving prompt text out of the UI and into the repo. Existing slugs are preserved so nothing in the runtime needs to be updated.

## Repo layout

```
prompts/
├── plan.md                # this file — engineer-facing instructions live here too;
│                          #   renamed to README.md once bootstrap is complete
├── utilities.py           # shared helpers (currently: read_prompt_text)
├── core.py
├── guardrail.py
├── router.py
├── response_format.py
└── prompt_text/           # the prompt text itself, one file per prompt.
    ├── core.md            #   Kept separate from the .py push scripts because
    ├── guardrail.md       #   prompts are long and editing them as Python
    ├── router.md          #   string literals is awkward (escaping, syntax
    └── response_format.md #   highlighting, diff noise, no markdown rendering).

server/chatbot_server/
└── model_defaults.py      # NEW — single source of truth for default model
                           #   identifiers. Imported by settings.py AND by every
                           #   prompts/*.py, so the default model recorded on a
                           #   Braintrust push can never drift from the default
                           #   the runtime falls back to.
```

Each prompt file is self-contained and directly executable. Running `python prompts/core.py` pushes that one prompt to Braintrust. There are two small cross-file dependencies — the `model_defaults` import (see "Model and params" below) and `utilities.read_prompt_text(...)` (which reads the corresponding file in `prompt_text/`). Both are intentional design choices, not coincidences.

### prompts/utilities.py

A tiny module for helpers shared across prompt push scripts. Currently just one function:

```python
# prompts/utilities.py
"""Shared helpers for prompt push scripts."""

import pathlib

_PROMPT_TEXT_DIR = pathlib.Path(__file__).parent / "prompt_text"


def read_prompt_text(name: str) -> str:
    """Return the prompt text for `name` from prompts/prompt_text/<name>.md.

    Resolves relative to this file, not the current working directory,
    so the call works regardless of where the push script is invoked from.
    """
    return (_PROMPT_TEXT_DIR / f"{name}.md").read_text()
```

The function lives here, not inline in each prompt file, so the path math has exactly one home. If we add more shared helpers later (e.g. a `push_to_braintrust(...)` convenience wrapper), they go in this module too.

Each prompt file imports it with a plain `from utilities import read_prompt_text`. This works because Python automatically prepends the running script's directory to `sys.path`, so `python prompts/core.py` puts `prompts/` on the path and `utilities` resolves cleanly. No extra `sys.path` plumbing needed for this import — the `sys.path` hop in each prompt file is only for reaching `server/chatbot_server/model_defaults.py` (a sibling-of-prompts location, not in the path by default).

## Prompt file shape

```python
# prompts/core.py
"""Core system prompt for the Sefaria assistant.

Run directly to push this prompt to Braintrust. With no env vars set the push
targets the prod slug; set PROMPT_SLUG_OVERRIDE to push to a sandbox slug
instead. See prompts/plan.md for the full local-testing workflow.
"""

import os
import pathlib
import sys

import braintrust

from utilities import read_prompt_text

# Import shared model defaults from server/chatbot_server/model_defaults.py
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
from chatbot_server.model_defaults import AGENT_MAX_TOKENS, AGENT_MODEL as AGENT_MODEL_DEFAULT, AGENT_TEMPERATURE

# SLUG is the contract with the runtime; PROMPT_SLUG_OVERRIDE redirects to a sandbox
NAME = "Core"
SLUG = os.environ.get("PROMPT_SLUG_OVERRIDE", "core-8fbc")
DESCRIPTION = "Top-level system prompt for the Sefaria assistant"

# NOTE: The source of truth for the model is in the env, this field is for
# associating the right metadata in Braintrust.
MODEL = os.environ.get("AGENT_MODEL", AGENT_MODEL_DEFAULT)

# PARAMS are imported from model_defaults so they stay in sync with the runtime callsite.
PARAMS = {"temperature": AGENT_TEMPERATURE, "max_tokens": AGENT_MAX_TOKENS}

PROMPT = read_prompt_text("core")  # reads prompts/prompt_text/core.md


if __name__ == "__main__":
    # Safety gate: refuses to push unless PROMPT_SLUG_OVERRIDE (sandbox) or
    # BRAINTRUST_PUSH_TARGET=prod (CI / emergency only) is set
    slug_override = os.environ.get("PROMPT_SLUG_OVERRIDE")
    push_target = os.environ.get("BRAINTRUST_PUSH_TARGET")
    if not slug_override and push_target != "prod":
        print(
            "ERROR: local pushes must target a sandbox slug.\n"
            "\n"
            "  Set PROMPT_SLUG_OVERRIDE=<prod-slug>-<yourname>-<purpose> to test:\n"
            f"    PROMPT_SLUG_OVERRIDE={SLUG}-yourname-test python prompts/core.py\n"
            "\n"
            "  Prod pushes happen via the push-prompts CI workflow on merge to main.\n"
            "  If you genuinely need to push to prod from your laptop (e.g. CI is\n"
            "  down), set BRAINTRUST_PUSH_TARGET=prod — see prompts/plan.md.\n",
            file=sys.stderr,
        )
        sys.exit(2)

    project = braintrust.projects.create(name="On Site Agent")
    project.prompts.create(
        name=NAME,
        slug=SLUG,
        description=DESCRIPTION,
        model=MODEL,
        prompt=PROMPT,
        params=PARAMS,
        if_exists="replace",
    )
    print(f"Pushed: {SLUG}")
```

Notes:
- `SLUG` honors a `PROMPT_SLUG_OVERRIDE` env var. With it set, the push goes to a sandbox slug; without it, the script refuses to run unless `BRAINTRUST_PUSH_TARGET=prod` is also set (only CI does that). See "Local testing workflow" below.
- `MODEL` and `PARAMS` are **metadata for Braintrust**, not what the runtime calls. See "Model and params: where the values come from" below for the full story.
- `if_exists="replace"` means the same slug is updated in place rather than duplicated.
- **CI-only prod pushes.** The safety gate at the top of the `__main__` block refuses to run unless `PROMPT_SLUG_OVERRIDE` (sandbox) or `BRAINTRUST_PUSH_TARGET=prod` (CI / emergency) is set. This is what makes routine prod pushes physically restricted to the CI workflow. The boilerplate is inlined in each file to keep each file standalone — write a descriptive comment in each one explaining *why* the gate exists, not just what it does, so future readers understand the safety it's enforcing.

### Model and params: where the values come from

There are two distinct things the word "model" could mean here, and they're separate:

1. **What the runtime actually calls.** When the server handles a request, it calls `Anthropic.messages.create(model=settings.<NAME>_MODEL, ...)`. The `settings.*_MODEL` values in `server/chatbot_server/settings.py` (env-driven — e.g. `AGENT_MODEL`, `GUARDRAIL_MODEL`, `ROUTER_MODEL`) are the source of truth for what gets called. Changing them changes production behavior. **Prompt files don't influence this.**

2. **What Braintrust stores alongside the prompt.** When we push a prompt, Braintrust records a `model` field on it. This is metadata — it shows up in the Braintrust UI next to the prompt and is what Braintrust uses if anyone runs the prompt from inside Braintrust. The runtime ignores it.

We want these two values to always agree. The mechanism: a shared Python module that holds the default model identifiers, imported by both sides.

```python
# server/chatbot_server/model_defaults.py
AGENT_MODEL = "claude-sonnet-4-5-20250929"
AGENT_MAX_TOKENS = 8000
AGENT_TEMPERATURE = 0.7

GUARDRAIL_MODEL = "claude-haiku-4-5-20251001"
GUARDRAIL_MAX_TOKENS = 256
GUARDRAIL_TEMPERATURE = 0.0

ROUTER_MODEL = "claude-haiku-4-5-20251001"
ROUTER_MAX_TOKENS = 256
ROUTER_TEMPERATURE = 0.0
```

```python
# server/chatbot_server/settings.py
from .model_defaults import AGENT_MODEL as _AGENT_MODEL_DEFAULT
AGENT_MODEL = os.environ.get("AGENT_MODEL", _AGENT_MODEL_DEFAULT)
# ... same pattern for GUARDRAIL_MODEL and ROUTER_MODEL
```

```python
# prompts/core.py
from chatbot_server.model_defaults import AGENT_MAX_TOKENS, AGENT_MODEL as AGENT_MODEL_DEFAULT, AGENT_TEMPERATURE
MODEL = os.environ.get("AGENT_MODEL", AGENT_MODEL_DEFAULT)
PARAMS = {"temperature": AGENT_TEMPERATURE, "max_tokens": AGENT_MAX_TOKENS}
```

Both sides — the runtime via `settings.py` and the push script in `prompts/*.py` — read the **same env var** at runtime, falling back to the **same default constant** from `model_defaults.py`. There is exactly one literal in the codebase for each default model identifier. Drift between "what the runtime falls back to" and "what the push records in Braintrust" is impossible by construction.

The `model_defaults.py` module is deliberately pure Python (no Django, no other imports) so that scripts in `prompts/` can import it without booting Django. The prompt files use a small `sys.path` hop to reach it — see the file shape above. A future repo cleanup could turn the repo into a proper Python package and remove the hop, but that's out of scope here.

Mapping of prompt file → env var → settings line:

| Prompt file | Env var | Settings line |
|---|---|---|
| `core.py` | `AGENT_MODEL` | `settings.py:233` |
| `guardrail.py` | `GUARDRAIL_MODEL` | `settings.py:234` |
| `router.py` | `ROUTER_MODEL` | `settings.py:255` |
| `response_format.py` | `AGENT_MODEL` | `settings.py:233` (response_format is fed into the core agent's system prompt, so it runs on the same model as `core.py`) |

`PARAMS` (temperature, max_tokens, etc.) use the same drift-proofing approach as `MODEL`: the constants live in `model_defaults.py`, and both the runtime callsites (`guardrail_service.py`, `router_service.py`, `claude_service.py`) and the prompt push scripts import them from there. There is exactly one literal for each param value. They're not env-driven and not in `settings.py` — if you need to change a param, edit `model_defaults.py` and the runtime callsite and the Braintrust metadata all update together.

## CI workflow

`.github/workflows/push-prompts.yml`:

```yaml
name: Push Prompts to Braintrust

on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - "prompts/*.py"

jobs:
  push-prompts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install 'braintrust==0.5.3'
      - env:
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          # This is what makes CI the only routine path to prod. Each prompt
          # file refuses to push without either PROMPT_SLUG_OVERRIDE or this
          # var being set. CI sets it; developers don't (unless they're
          # emergency-pushing, which the plan documents as an escape hatch).
          BRAINTRUST_PUSH_TARGET: prod
        run: |
          set -e
          for file in prompts/*.py; do
            echo "Pushing: $file"
            python "$file"
          done
```

That's the entire pipeline. No build step, no `braintrust push` CLI, no built artifacts.

CI doesn't need to pass through model env vars: each prompt file resolves its `MODEL` field by reading the env var with a fallback to `server/chatbot_server/model_defaults.py`. The runtime resolves the same way. With no env vars set in CI, the push records the same default constant the runtime would fall back to — they cannot disagree. If devops decides to wire `AGENT_MODEL` / `GUARDRAIL_MODEL` / `ROUTER_MODEL` through to deploy environments later (overriding the default), the same env vars can be added to this workflow's `env:` block to keep CI in lockstep.

A staging/prod toggle is **deferred** pending a conversation with devops. When we do add it, the implementation will likely be either a separate Braintrust project or a slug suffix, gated on `workflow_dispatch` input or branch. The current design doesn't preclude either.

## Bootstrap (one-time, per prompt)

For each of the four prompts:

1. Open the prompt in the Braintrust UI.
2. Copy the prompt text into the matching `prompts/prompt_text/<name>.md`.
3. Verify `MODEL` and `PARAMS` in the `.py` file match what's in `model_defaults.py` for that prompt (see the mapping table in "Model and params: where the values come from").
4. Locally: `python prompts/<name>.py`. Confirm in the Braintrust UI that the slug was updated (not duplicated) and the version bumped.
5. Hit the running app on a path that uses that prompt. Confirm `PromptService.load_prompt(<slug>)` still resolves cleanly.

Bundle into one PR but commit per prompt so the bootstrap diff is reviewable.

## Developer workflow: editing a prompt end-to-end

**Read this before editing any prompt.** A prompt change has three phases: make the edit, try it out locally without affecting prod, then ship it through the normal PR process. Skipping the middle phase — i.e. running `python prompts/core.py` directly to "see what happens" — pushes straight to prod and overwrites what every user sees.

The walkthrough below uses `core.py` as the example, but the same shape applies to any prompt. There's also a section at the end for the case where a single change touches multiple prompts.

### Phase 1 — make the change

1. The prompt **text** lives in `prompts/prompt_text/<name>.md` (e.g. `prompts/prompt_text/core.md`). Open that file and edit the text in place. For most prompt changes, this is the only file you touch.
2. If your change involves the **model** (`MODEL = ...`) or **sampling params** (`PARAMS = {...}`), edit those in the matching `prompts/<name>.py` file. Remember that the values in these files are the source of truth — whatever you commit will overwrite the Braintrust UI on the next push.
3. **Do not touch the `SLUG` line in the `.py` file.** The `os.environ.get("PROMPT_SLUG_OVERRIDE", "<prod-slug>")` form is what makes the sandbox workflow work, and the committed default is the prod slug that CI will push to on merge. Replacing the default with a sandbox slug would commit code that — when CI runs — would push a sandbox-named entry to Braintrust and leave the real prod slug stale.

Save the file. Don't push anything yet.

### Phase 2 — try it out locally

The key idea: **push your edit to a throwaway sandbox slug in Braintrust, then start your local server pointed at that sandbox slug.** Prod stays on its own slug, untouched. You iterate as much as you want locally before anything reaches prod.

Pick a sandbox slug. Convention: `<prod-slug>-<yourname>-<purpose>`. For example, if you're tweaking the core prompt to be more concise: `core-8fbc-sarah-concise`. The name doesn't matter to the runtime — it just has to be different from the prod slug.

**Reuse this slug across every iteration of this one change** — `if_exists="replace"` updates it in place, and your local server keeps pointing at the same name. Pick a *new* slug only when you start a *different* change (e.g. next month's prompt tweak gets its own slug). Don't append `-v2`, `-v3` etc. as you iterate — Braintrust already tracks every push as a numbered version of the slug, so re-pushing to the same slug gives you full version history for free.

**Step 2a. Push to the sandbox slug.**

```bash
PROMPT_SLUG_OVERRIDE=core-8fbc-sarah-concise python prompts/core.py
# → "Pushed: core-8fbc-sarah-concise"
```

The `PROMPT_SLUG_OVERRIDE` env var redirects the push for this one invocation. Prod's `core-8fbc` is untouched. If you forget the override, the script refuses to run with an error message reminding you to set it — local pushes physically cannot target the prod slug (see Phase 3 for why).

**Step 2b. Start your local server pointed at the sandbox slug.**

Each prompt has a matching server-side env var that tells `PromptService.load_prompt(...)` which slug to fetch:

| Prompt file | Server-side env var |
|---|---|
| `core.py` | `CORE_PROMPT_SLUG` |
| `guardrail.py` | `GUARDRAIL_PROMPT_SLUG` |
| `router.py` | `ROUTER_PROMPT_SLUG` |
| `response_format.py` | `RESPONSE_FORMAT_PROMPT_SLUG` |

For our example:

```bash
CORE_PROMPT_SLUG=core-8fbc-sarah-concise ./start.sh
```

Your local server now loads your sandbox version of the core prompt. Every other prompt still loads from its prod slug.

**Step 2c. Run the eval harness against your sandbox slug. (Required.)**

Before you start clicking around in the browser, run the eval harness. Eyeballing a few queries isn't enough to catch regressions — the eval harness is what tells you whether your change is actually better than what's in prod today.

```bash
CORE_PROMPT_SLUG=core-8fbc-sarah-concise python evals/run_eval.py
```

Same env-var pattern as the server — the harness reads the slug from the environment, so it scores your sandbox prompt instead of prod. Compare results against a prod-slug baseline run. If scores regress, fix the prompt before moving on; do not skip ahead to phase 3 with a regression in hand and a plan to "address it in the PR."

**Step 2d. Use the app and iterate.**

Once evals are clean, open the app in the browser, send some queries, and sanity-check the qualitative feel. If something needs more tweaking:

1. Edit `prompts/prompt_text/core.md` (or `prompts/core.py` if changing model/params).
2. Re-run the push from Step 2a (same sandbox slug — `if_exists="replace"` updates it in place).
3. Re-run evals from Step 2c.
4. Restart the server (or wait out the prompt service's TTL cache — see `PromptService.cache_ttl`, default 300s) so it picks up the new version.

Repeat as many times as you need. Prod is never affected.

### Phase 3 — commit and ship

Once you're happy with the local behavior, ship it through the normal PR process. **You cannot push to prod from your laptop** — running `python prompts/<name>.py` without `PROMPT_SLUG_OVERRIDE` will refuse with an error message pointing you back to PR + CI. That's by design.

1. `git status` should show edits to `prompts/prompt_text/<name>.md` (the prompt text) and optionally `server/chatbot_server/model_defaults.py` (only if you changed model or params). If you accidentally edited the `SLUG` line in the `.py` file, revert that.
2. Commit: `git commit -m "feat(prompts): tighten core prompt for conciseness"` (use the conventional-commits format the rest of the repo uses; see top-level `CLAUDE.md`).
3. Push the branch and open a PR. Reviewers see the prompt diff like any other code change.
4. **On merge to `main`**, the `push-prompts` CI workflow runs `python prompts/<name>.py` for each prompt file with `BRAINTRUST_PUSH_TARGET=prod` set in the workflow's `env:` block. That env var is what unlocks the prod push — neither your laptop nor anyone else's can do this routinely.
5. **Clean up your sandbox slug** in the Braintrust UI once the PR is merged. The state you tested is now captured as a version on the prod slug, so the sandbox is redundant — Braintrust's version history on the prod slug is the right place to look up "what did this prompt look like a month ago," not your old sandbox slugs. Leaving sandbox slugs around clutters dashboards and confuses future readers.

That's the full loop.

#### Emergency push to prod from your laptop

The CI-only design has one escape hatch: if CI is down and you genuinely need to push to prod from your machine (e.g. reverting a bad prompt during an incident), set `BRAINTRUST_PUSH_TARGET=prod` and run the file directly:

```bash
BRAINTRUST_PUSH_TARGET=prod python prompts/core.py
```

This bypasses the safety gate intentionally. Use it sparingly — it skips PR review entirely and the prompt that ships is whatever's in your working tree. The friction (knowing this exists, knowing the var name) is calibrated to make accidents nearly impossible while still leaving a glass-break path available.

### Editing multiple prompts in one change

If your change touches more than one prompt, the only thing that changes is that you repeat phase 2a once per prompt (each with its own sandbox slug), and combine the env vars in phase 2b. Two things to keep separate in your head:

1. **`PROMPT_SLUG_OVERRIDE`** is for the **push step**. Each prompt file is pushed individually (`python prompts/<name>.py`), so you re-export the override before each push, picking a unique sandbox slug per prompt.
2. **`<NAME>_PROMPT_SLUG`** vars are for the **server start**. The server reads one slug per prompt from the environment, so you export *all* of them in front of `./start.sh` — one line, all in one command.

Concrete example, editing `router.py` and `core.py` together:

```bash
# Push each prompt to its own sandbox slug, one at a time.
PROMPT_SLUG_OVERRIDE=router-classifier-sarah-test python prompts/router.py
PROMPT_SLUG_OVERRIDE=core-8fbc-sarah-test python prompts/core.py

# Start the server with BOTH slug env vars set so it loads your sandbox
# versions of both prompts (and prod versions of everything else).
ROUTER_PROMPT_SLUG=router-classifier-sarah-test \
CORE_PROMPT_SLUG=core-8fbc-sarah-test \
./start.sh
```

Any prompt you don't override stays on prod for that local server — you're only redirecting the slugs you've actually edited.

### What NOT to do

- **Don't** commit a `PROMPT_SLUG_OVERRIDE` value into the file itself, or replace the prod slug default with a sandbox slug. The override is intentionally an env var so the committed code always reflects the prod slug.
- **Don't** leave sandbox slugs lying around in Braintrust. Clean them up when your PR merges — they show up in dashboards and confuse future readers.
- **Don't** reach for `BRAINTRUST_PUSH_TARGET=prod` for routine work. It's the emergency escape hatch, not a shortcut around PR review. If you find yourself using it more than once or twice a year, something is wrong with the CI workflow and we should fix that instead.

## Open / deferred

- **Staging vs prod toggle in CI.** Deferred pending devops conversation. Will be added as a follow-up — does not affect the initial rollout.
- **Rewriter and Translation prompts.** Not part of this iteration — they remain UI-managed. When we're ready, the migration is the same shape: add a `prompts/rewriter.py` / `prompts/translation.py` file matching the file shape above, run the bootstrap steps, and they get picked up by the CI workflow automatically (the glob already covers them).
- **Test for hardcoded slug overrides.** Considered writing a pytest that statically scans `prompts/*.py` to ensure every `SLUG` assignment uses the `os.environ.get("PROMPT_SLUG_OVERRIDE", "<prod-slug>")` form with the default matching `server/chatbot_server/settings.py`. Skipped for this iteration — revisit if a sandbox slug ever slips into a commit.
- **Repo-level Python packaging.** Each prompt file does a small `sys.path` hop to import from `server/chatbot_server/model_defaults.py`. Setting up the repo as a proper Python package (`pyproject.toml` at the root, editable install) would let the hop go away in favor of clean imports. Out of scope here — touches dev onboarding, `setup.sh`, and the existing `server/venv` model. Worth doing eventually.

## Commit sequence

1. `feat(prompts): scaffold prompts/ directory with plan, utilities, and prompt_text dir`
2. `refactor(server): lift model and param defaults into chatbot_server/model_defaults.py` — pure-Python module, no behavior change. `settings.py`, `claude_service.py`, `guardrail_service.py`, and `router_service.py` all import from it. Prerequisite for the prompt files.
3. `feat(prompts): add core prompt source` — adds both `prompts/core.py` and `prompts/prompt_text/core.md`. Verify in Braintrust UI.
4. `feat(prompts): add guardrail prompt source`
5. `feat(prompts): add router prompt source`
6. `feat(prompts): add response_format prompt source`
7. `ci: add push-prompts workflow`
8. `docs(prompts): rename plan.md to README.md` — bootstrap is complete, doc's role shifts from forward-looking plan to ongoing reference

After (7) merges to main, CI runs the full push and the loop is closed. (8) is a one-line cleanup commit.

(2) ships separately from any prompt file because the prompt files import from it. Land (2) first, confirm it doesn't change any runtime behavior, then start adding prompt files.
