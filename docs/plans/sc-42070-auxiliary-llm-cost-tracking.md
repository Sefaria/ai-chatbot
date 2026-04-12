# sc-42070: Include All Auxiliary LLM Calls in Cost Tracking

**Branch:** `chore/sc-42070/include-all-llm-calls-in-cost-tracking`
**Blocks:** sc-42991 (Create cost and time scorers)
**Related:** sc-40204 (Ensure accurate cost metrics for responses)

## Problem

`total_cost_usd` currently only captures the main agent SDK call. Three auxiliary services make direct Anthropic API calls whose costs are not tracked:

| Service | When |
|---------|------|
| Guardrail | Pre-agent, every message |
| Router | Pre-agent, 1-2x per turn |
| Summary | Post-agent, ~1x per turn |

The reported cost per response is understated, which undermines cost comparisons between models (the whole point of sc-42991).

## Goal

`total_cost_usd` should reflect the true total cost of all LLM calls made during a turn.

## Decision: Option C — Manual cost aggregation

Evaluated three approaches. Option C is the best fit.

### Options considered

**Option A: Move auxiliary calls inside the Agent SDK — rejected.**
The SDK has no pre/post-execution hooks for arbitrary LLM calls. Its hook system (`PreToolUse`, `PostToolUse`, `Stop`, etc.) is tool-lifecycle only. Guardrail and router must run before the agent and influence prompt selection — there's no mechanism for this. PR #42 tried a creative workaround (`set_model()` to run guardrail as the first SDK query) but it only covered guardrail, added latency, and the codebase has been completely refactored since.

**Option B: Use Braintrust's cost tracking — rejected.**
Confirmed by reading the Braintrust SDK source (`wrappers/anthropic.py`, `_anthropic_utils.py`): `wrap_anthropic()` creates spans and logs token counts but does not compute cost. There is no `estimated_cost` field in the Braintrust span SDK. To get cost you'd still need a pricing lookup — which is just Option C. The only benefit would be auto-capturing token counts into Braintrust spans for observability (worth doing separately).

**Option C: Manual cost aggregation — chosen.**
Each auxiliary service already gets an Anthropic API response with `usage.input_tokens` and `usage.output_tokens`. Extract those, compute cost via a small pricing dict, and aggregate into `total_cost_usd`. The pricing table concern is minor in practice: only 2-3 cheap models are used for auxiliary calls, price changes are infrequent, and the SDK still handles the expensive main call accurately.

### WIP reference

Branch `fix/add-gaurdrail-llm-call-log` (PR #42) covers guardrail only. The codebase has been heavily refactored since — none of its changes apply cleanly. Reusable ideas: the `_sum_costs` helper pattern and `parse_guardrail_response` extraction.

## Implementation plan

### Step 1: Add a pricing utility

Create a small `MODEL_PRICING` dict mapping model names to per-token input/output prices, and a `compute_cost(model, usage)` helper that returns USD. Keep this in a central location (e.g. `server/chat/V2/pricing.py`). Only needs entries for the 2-3 models used by auxiliary services (Haiku for guardrail/router, summary model).

### Step 2: Return token usage from each auxiliary service

Each service already has the Anthropic `response.usage` object available. Thread it back through the return types:

- `GuardrailResult` — add `input_tokens`, `output_tokens`, `model` fields. Set them in `guardrail_service.py` from `response.usage`.
- `RouterResult` — add the same fields. The router can make 1-2 LLM calls (classify + optional rewrite), so sum both.
- `ConversationSummary` — add the same fields. Set them in `summary_service.py` from `response.usage`.

### Step 3: Compute auxiliary costs in the orchestrator

In `turn_orchestrator.py`, after each phase completes, use `compute_cost()` to convert token counts to USD. Accumulate a running `auxiliary_cost_usd` total across guardrail, router, and SDK phases. Combine with `sdk_result.total_cost_usd` before building the `AgentResponse`.

The summary service runs in `views.py` after the orchestrator returns. Either move summarization into the orchestrator, or compute summary cost in `views.py` and add it to the response cost before persisting.

### Step 4: Update logging and persistence

The existing path (`AgentResponse.total_cost_usd` → `TurnLoggingService` → DB) already handles a single cost value. No schema changes needed — `total_cost_usd` just becomes a more accurate number. Optionally log a cost breakdown in the Braintrust span metadata for debugging.

### Step 5: Optional observability improvement

Wrap the shared Anthropic client in `get_anthropic_client()` with `braintrust.wrap_anthropic()` so auxiliary LLM calls automatically get Braintrust spans with token metrics. This is independent of cost tracking but improves visibility.
