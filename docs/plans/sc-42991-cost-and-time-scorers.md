# sc-42991 — Cost and time scorers

## Goal

Add two Braintrust scorers that expose per-turn cost and latency during eval
runs, so that prompt iterations in the "Evaluation-Driven System Prompt
Refinement" epic can be compared not only on quality but also on cost and
speed. Values are emitted as raw aggregate numbers (USD and milliseconds)
rather than normalized 0–1 scores, so experiment comparisons in Braintrust are
human-readable.

## Dependencies

Downstream of:
- PR #112 (`chore/sc-42070/include-all-llm-calls-in-cost-tracking`) — supplies
  `stats.totalCostUsd` including guardrail, router, and summary costs.
- PR #115 (`fix/eval-braintrust-jwt-refresh`) — makes long eval runs survive
  the JWT expiry window, which is a precondition for any additional scorer to
  run reliably.

Branch: `chore/sc-42991/create-cost-and-time-scorers`, created off #112 and
then merged with #115 so it carries both upstreams.

## Current state

`evals/run_eval.py::ChatbotClient.chat()` consumes the SSE stream but only
keeps the final markdown. The SSE final payload already carries a `stats` dict
containing `totalCostUsd` and `latencyMs` (populated in
`server/chat/V2/views.py::_build_response_payload` via
`logging/turn_logging_service.py::build_stats`). The eval task therefore has
no access to cost or latency even though both are already computed server-side.

Existing scorers (`evals/scorers/code_scorers/` and `llm_scorers/`) use a
shared convention where the scorer's `extract_data` helper unwraps
`output.get("content")` to recover the response string. As long as any new
output shape keeps the markdown under `content`, existing scorers keep working
unchanged.

## Approach

Plumb the server-reported stats through the eval pipeline and surface them as
two simple code scorers that echo the raw number back to Braintrust.

### 1. Capture stats in `ChatbotClient.chat()`

Change the return type from a bare markdown string to a dict shaped as
`{"content": <markdown>, "totalCostUsd": <float>, "latencyMs": <int>}`. The
SSE stream already carries these values on the final event, so the change is
local to how we parse the stream — extract `stats.totalCostUsd` and
`stats.latencyMs` alongside `markdown` when we see the final message. Missing
or malformed stats degrade to `None` rather than failing the run, so scorers
can emit `{"score": None}` on bad rows without taking the whole experiment
down.

Server-reported `latencyMs` is used rather than wall-clock in the client, so
numbers are comparable across prompt experiments regardless of network jitter
between the eval runner and the server.

### 2. Pass through to task output

`run_evaluation.task` currently returns the markdown string; update it to
return the full dict. Because existing scorers already unwrap `content`, no
other scorer file needs to change.

### 3. Two new code scorers

Add `evals/scorers/code_scorers/cost_usd.py` and `.../latency_ms.py` using the
established code-scorer template. Each handler:

- Reads the numeric value from `output["totalCostUsd"]` /
  `output["latencyMs"]` (and falls back to span metadata via the existing
  `extract_data` trace-span path for the pushed-scorer scenario).
- Returns `{"score": <raw number>, "name": ..., "metadata": {"cost_usd": x}}`
  (or analogous for latency) so Braintrust aggregates the raw dollars and
  milliseconds as cumulative scores across a run, and the number also shows
  up in the per-row metadata panel.
- Returns `{"score": None}` if the value is missing, so rows that fail before
  the server emits stats don't skew the aggregate.

Slugs follow the existing convention (`cost-usd-<hash>`, `latency-ms-<hash>`).

### 4. Build and push

Run `python evals/scorers/build.py cost_usd latency_ms` to produce the
self-contained files under `built/`, then push each one:

```bash
source server/venv/bin/activate
python evals/scorers/build.py cost_usd latency_ms
braintrust push evals/scorers/built/cost_usd.py
braintrust push evals/scorers/built/latency_ms.py
```

Note: `build.py` only carries the top-level `handler` function into the built
artifact — any module-level helper functions in the source get dropped. The
handlers in `cost_usd.py` and `latency_ms.py` therefore inline their
value-extraction logic rather than calling a helper, so the pushed scorer is
self-contained. Pre-existing scorers like `html_format.py` quietly share this
limitation; out of scope here.

### 5. Tests

Extend `evals/test_run_eval.py` with a test that drives `ChatbotClient.chat()`
against a mocked SSE stream containing `stats` and asserts the returned dict
carries `content`, `totalCostUsd`, and `latencyMs`. Add a small unit test per
scorer covering the happy path (numeric value in output), the missing-value
path, and the trace-span fallback.

## Visibility in Braintrust

Because the scorers return the raw USD and millisecond values as their
`score`, Braintrust's experiment view will show mean / p50 / p95 cost and
latency side-by-side with the quality scorers, and the experiment diff view
will highlight regressions when a prompt tweak makes runs more expensive or
slower. No special dashboards required — this is the same surface area every
other scorer uses.

## Open considerations

- Thresholds / budgets: intentionally out of scope. Raw numbers first, alerts
  or thresholds later if the team wants them.
- Latency units: milliseconds to match what the server emits. We could switch
  to seconds if the Braintrust aggregate view reads poorly at ms resolution;
  decide after first real run.

## Findings from first full run (2026-04-15)

First end-to-end run against the local server: experiment
`Automated Eval - 2026-04-15 14:01`, Benchmark dataset, 88/88 rows in 28:57.
URL: https://www.braintrust.dev/app/Sefaria/p/On%20Site%20Agent/experiments/Automated%20Eval%20-%202026-04-15%2014%3A01

The latency scorer (`latency-ms-4202`) raised
`ValueError: score (X) must be between 0 and 1` on every row. The cost scorer
hit the same constraint whenever cost > $1 (it stayed silent on this run only
because per-turn cost happened to be sub-dollar, not because the design is
sound). Braintrust enforces `0 ≤ score ≤ 1` on the `score` field, so the
"raw numbers as scores" approach was incompatible.

## Revised approach (2026-04-15)

Cost and latency aren't scores at all — they're **metrics**. Braintrust
distinguishes the two: scores are normalized [0,1] judgments; metrics are
arbitrary numerics that aggregate (sum per row, average per experiment) and
show up in the experiment table next to scores. This is exactly the
"accumulative, human-readable" semantics we want.

Implementation:
- The eval task in `evals/run_eval.py` now calls
  `current_span().log(metrics={"cost_usd": ..., "latency_seconds": ...})`
  after each successful chat turn. Latency converts to seconds for
  readability; cost stays in USD.
- The pushed `cost-usd-4201` and `latency-ms-4202` scorers are deleted
  (both `code_scorers/` and `built/`). They should be removed from the
  Braintrust UI as well — they will continue to error on runs until then.
- `create_scorer` no longer folds cost/latency into scorer metadata — it
  only unwraps `output["content"]` for LLM scorers.

Tests in `evals/test_run_eval.py` cover both the happy path (metrics get
logged) and the missing-stats path (no log call when the server omits stats).

Local-vs-prod note: `--local` uses `http://localhost:8001`, which is bound by
local Anthropic API throughput rather than prod's. The 28:57 wall time is not
a meaningful baseline for prod latency.

## Baseline-comparison gotcha (2026-04-15)

`evals/run_eval.py` calls `EvalAsync` without a `base_experiment` argument, so
Braintrust auto-picks the comparison. The auto-pick is not "most recent prior
run" — it followed a chain that landed on `Automated Eval - 2026-02-16 19:38`
(2 months stale, run against `chat-dev.sefaria.org`). The eval's printed
SUMMARY and the experiment record's `base_exp_id` even disagree on which
ancestor was used; either way it's not a representative baseline.

This makes the SUMMARY's regression numbers misleading on three axes at once:

1. **Cross-environment.** Today's run hit `localhost:8001`; the auto-picked
   ancestors mostly hit `chat-dev.sefaria.org`. Different latency and routing
   characteristics.
2. **Cross-time on `main`.** Between the auto-picked ancestor and now, `main`
   has had a guardrail rewrite (structured outputs, slug changes, rejection
   message overhaul), router/logging changes, and scorer infrastructure moves.
   The agent has drifted significantly even though this branch only adds
   scorer plumbing.
3. **LLM-judge scorers can drift on the Braintrust side.** Scorers like
   `brand_adherence` and `theological_questions` are LLM-graded; matching
   slugs don't guarantee identical judge prompts over time.

Re-running the diff manually against the most recent prior run with non-empty
scores — `Automated Eval - 2026-03-22 14:39` (also `--local`, 24 days old) —
still shows real regressions but at smaller magnitude, mostly attributable to
guardrail changes on `main`:

| Scorer | Today | Mar 22 | Δ |
| --- | ---: | ---: | ---: |
| brand_adherence_98e5 | 0.477 | 0.966 | -48.86% |
| specific_reference_retrieval_e8c4 | 0.667 | 1.000 | -33.33% |
| theological_questions_ab7a | 0.432 | 0.628 | -19.55% |
| html_format_dc7d | 0.584 | 0.675 | -9.09% |
| link_are_valid_06b8 | 0.886 | 0.932 | -4.55% |
| sefaria_translation_scorer | 0.750 | 0.500 | +25.00% |
| (others within ±5%) | | | |

Side-observation while pulling the data: every recent automated run against
`https://chat-dev.sefaria.org` returns an empty `scores` block in the
summarize endpoint (Mar 16, Mar 24, Mar 25, and a 14:50 run today against
prod-dev all show `scores: {}`), while every `--local` run since Mar 19 has
~19 populated scorers. Worth investigating separately — it suggests scorers
aren't being attached on prod-dev runs.

Recommendation for future runs: pass an explicit `base_experiment` to
`EvalAsync` (a known-good prod-dev run) or pin a "blessed" baseline in the
Braintrust UI, so the SUMMARY isn't comparing across environments and across
months of agent drift.

## Second full run (2026-04-15 14:50) — metrics validated

Re-ran against `http://localhost:8001` with the same Benchmark dataset and
concurrency=3, now on commit `05b1f4a` (the scorer-to-metric refactor).
Experiment: `Automated Eval - 2026-04-15 14:50-6e1cf278` (id `fb030606`).
The 14:01 baseline was on the prior commit `a49bd79`; the only code delta
between the two runs is eval-pipeline plumbing (cost/latency emission),
which doesn't touch agent behavior, so run-to-run variance on quality
scorers is pure LLM nondeterminism.

**Health of the run.** 88/88 rows completed with zero task-level errors and
zero auth errors (the JWT refresh logic held across the 30-minute run). Seven
transient Braintrust scorer-infra timeouts hit (BrainstoreQuery / 240 s
function timeouts) and all were retried successfully. The experiment-level
`errors` metric dropped from 1.00 (14:01, where `latency_ms_4202` failed on
every row) to 0.08 (only the transient infra timeouts).

**Metrics coverage.** `cost_usd` and `latency_seconds` are populated on all
88 task rows. Values: cost min $0.0024, median $0.124, avg $0.109, max $0.274,
total $9.62; latency min 1.32 s, median 30.11 s, avg 28.99 s, max 69.29 s.
Both show up in the experiment table next to scorers as intended.

**Run-to-run noise floor.** Against the 14:01 baseline (same code, same
target), 14 of 16 scorers with N ≥ 40 moved within ±6 pp. Outliers on the
big-N side: `theological_questions` +12.9 pp and `sefaria_sources_check`
−8.5 pp, both LLM-judge scorers whose per-row verdicts are themselves
stochastic. Tiny-N scorers swung harder — `sefaria_translation_scorer` (N=4)
moved −50 pp and `suicide_and_self_harm` (N=5) moved +20 pp, both driven by a
single flipped verdict. Practical rule: on this benchmark, real regressions
need to clear roughly ±6 pp (N ≥ 40) or ±25 pp (N ≤ 5) before they're
distinguishable from noise.

Reports: `sc-42991-reports/eval-variance-2026-04-15.pdf` (variance table) and
`sc-42991-reports/cost-latency-metrics-2026-04-15.pdf` (metrics rollout).
