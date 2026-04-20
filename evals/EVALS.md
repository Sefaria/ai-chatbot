# Eval Guide

Evals measure the chatbot's response quality against a fixed dataset of questions scored by automated judges. They are the main signal for whether a code change improved, regressed, or didn't affect the chatbot's behavior.

## When to run evals

Run evals before merging any change that could affect what the chatbot says or how it says it. This includes:

- **Prompt changes** — system prompt edits, tone/style instructions, guardrail wording
- **Tool changes** — adding, removing, or modifying agent tools or their descriptions
- **Model changes** — switching the underlying Claude model or version
- **Scorer changes** — updating eval scoring logic (run to confirm the scorer behaves as intended)

You do **not** need to run evals for:
- Frontend-only changes (UI, styling)
- Infrastructure changes (deployment config, env vars) that don't touch the agent
- Dataset-only additions (adding new questions to Braintrust without changing the chatbot)

## How to run evals

Make sure you have the required env vars set (see `server/.env`):
- `BRAINTRUST_API_KEY`
- `CHATBOT_USER_TOKEN`

```bash
# Run all scorers against dev (default — master branch deploy)
python evals/run_eval.py --all-scorers

# Run against production
python evals/run_eval.py --all-scorers --prod

# Run against your local server
python evals/run_eval.py --all-scorers --local

# Run a specific subset of scorers
python evals/run_eval.py --scorers antisemitism-ab61,html-format-dc7d

# Use a different dataset
python evals/run_eval.py --all-scorers --dataset "My Test Dataset"

# Give the experiment a custom name
python evals/run_eval.py --all-scorers --experiment "My experiment name"
```

**Which server to run against:**

| Environment | Branch | URL | Flag |
|-------------|--------|-----|------|
| Dev | `master` | `https://chat-dev.sefaria.org` | _(default)_ |
| Production | `production` | `https://chat.sefaria.org` | `--prod` |
| Local | any | `http://localhost:8001` | `--local` |

Evals run against dev by default, since `master` is where changes land first. Run against `--prod` only when investigating a production-specific issue.

## Reading the output

After the run completes, a threshold analysis is printed comparing this run to the pinned baseline:

```
============================================================
THRESHOLD ANALYSIS
Baseline: Automated Eval - 2026-03-15 11:00 | Tolerance: 10%
============================================================
Scorer                              Baseline    Current    Delta  Status
---------------------------------------------------------------------------
antisemitism_ab61                      92.0%      90.0%   -2.0%    PASS
brand_adherence                        85.0%      68.0%  -17.0%    FAIL
html_format_dc7d                      100.0%      98.0%   -2.0%    PASS
============================================================
NOT READY TO MERGE: (1 scorer(s) exceeded 10% threshold for regression).
NOTE: The code changes must be reviewed by a member of the eval team
before merging due to this regression.
```

- **PASS** — scorer did not regress more than 10% vs the baseline
- **FAIL** — scorer dropped more than 10 percentage points vs the baseline
- **NEW** — scorer has no baseline data yet (first time it's been run)
- **READY TO MERGE** — all scorers within tolerance
- **NOT READY TO MERGE** — one or more scorers exceeded the regression threshold

## What to do when you see NOT READY TO MERGE

1. Look at which scorer(s) failed and by how much
2. Open the experiment in Braintrust and inspect the failing rows to understand why
3. If the regression is caused by your change, revise it and re-run
4. If the regression is expected (e.g. you intentionally changed behavior that scorer measures), bring it to the eval team for review before merging

The eval team can approve a merge despite a failing threshold — the decision is theirs, not the script's. The message is informational; there is no hard CI gate at this time.

## The baseline system

The **pinned baseline** is the experiment that all future runs are compared against. It represents the last known-good state of the chatbot.

**How it gets updated:** When a PR is merged to `master`, a GitHub Actions workflow automatically finds the most recent eval experiment that was run on that branch and pins it as the new baseline. You don't need to do anything manually.

**If you didn't run evals on your branch:** The baseline is left unchanged on merge. This is fine for changes that don't affect chatbot behavior. For changes that do, run evals before merging.

**If no baseline exists yet:** The threshold analysis will print the current scores without comparison. The first merge to `master` after an eval run will establish the baseline.
