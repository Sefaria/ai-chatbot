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
