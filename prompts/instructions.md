# Editing a Prompt

**Read this before touching any prompt file.** The push scripts block local runs that would hit prod — but `BRAINTRUST_PUSH_TARGET=prod` bypasses that gate. Follow the three phases below.

## Prompt → env var reference

| Prompt | Text file | Push script | Server slug env var |
|---|---|---|---|
| Core | `prompt_text/core.md` | `core.py` | `CORE_PROMPT_SLUG` |
| Guardrail | `prompt_text/guardrail.md` | `guardrail.py` | `GUARDRAIL_PROMPT_SLUG` |
| Router | `prompt_text/router.md` | `router.py` | `ROUTER_PROMPT_SLUG` |
| Response format | `prompt_text/response_format.md` | `response_format.py` | `RESPONSE_FORMAT_PROMPT_SLUG` |

Model and sampling params are defined in `server/chatbot_server/model_defaults.py` and imported by both the runtime callsites and the push scripts — edit there if you need to change them.

## Phase 1 — make the change

Edit `prompts/prompt_text/<name>.md`. That's it for most changes.

If you need to change model or params, edit `server/chatbot_server/model_defaults.py`. Do not touch the `SLUG` line in the `.py` file.

## Phase 2 — test locally against a sandbox slug

Pick a sandbox slug: `<prod-slug>-<yourname>-<purpose>` (e.g. `core-8fbc-sarah-concise`). Reuse it across all iterations of this change — don't append `-v2`, `-v3`, etc.

**2a. Push to the sandbox:**

```bash
PROMPT_SLUG_OVERRIDE=core-8fbc-sarah-concise python prompts/core.py
```

**2b. Start your local server pointed at the sandbox:**

```bash
CORE_PROMPT_SLUG=core-8fbc-sarah-concise ./start.sh
```

**2c. Run evals (required):**

```bash
CORE_PROMPT_SLUG=core-8fbc-sarah-concise python evals/run_eval.py
```

Compare against a prod-slug baseline. Fix regressions before moving on.

**2d. Iterate** — edit the `.md`, re-push to the same sandbox slug, re-run evals. Prod is never touched.

**Multiple prompts:** repeat 2a per prompt with its own sandbox slug, then combine all slug env vars in front of `./start.sh`:

```bash
PROMPT_SLUG_OVERRIDE=router-classifier-sarah-test python prompts/router.py
PROMPT_SLUG_OVERRIDE=core-8fbc-sarah-test python prompts/core.py

ROUTER_PROMPT_SLUG=router-classifier-sarah-test \
CORE_PROMPT_SLUG=core-8fbc-sarah-test \
./start.sh
```

## Phase 3 — commit and ship

`git status` should show edits to `prompts/prompt_text/<name>.md` and optionally `server/chatbot_server/model_defaults.py`. Nothing else.

Commit, open a PR, merge. On merge to `main`, CI runs each push script with `BRAINTRUST_PUSH_TARGET=prod` and updates Braintrust.

After the PR merges, **delete your sandbox slug** in the Braintrust UI.

### Emergency prod push

If CI is down and you need to push immediately:

```bash
BRAINTRUST_PUSH_TARGET=prod python prompts/core.py
```

Use sparingly — skips PR review entirely.

## What NOT to do

- Don't commit a `PROMPT_SLUG_OVERRIDE` value or replace the prod slug default in any `.py` file
- Don't leave sandbox slugs in Braintrust after your PR merges
- Don't use `BRAINTRUST_PUSH_TARGET=prod` for routine work
