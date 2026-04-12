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

### Step 1: Add a pricing utility ✅

Created `server/chat/V2/pricing.py` with `MODEL_PRICING` dict (Haiku + Sonnet) and `compute_cost(model, input_tokens, output_tokens)` helper that returns USD or None for unknown models.

### Step 2: Return token usage from each auxiliary service ✅

- `GuardrailResult` — added `input_tokens`, `output_tokens`, `model` fields. Set from `response.usage` in `check_message()`.
- `RouterResult` — added the same fields. `_classify_message()` now returns a tuple with usage. Deterministic classification returns zero usage.
- `ConversationSummary` — added non-persisted `llm_input_tokens`, `llm_output_tokens`, `llm_model` attributes set after LLM call. Fallback paths set zeros.

### Step 3: Compute auxiliary costs in the orchestrator ✅

- `GuardrailGateResult` dataclass wraps `blocked_response` + usage fields, replacing the old `AgentResponse | None` return.
- `Router.run_router()` now returns a 4-tuple including a usage dict.
- `turn_orchestrator.py` accumulates `auxiliary_cost` from guardrail + router, combines with `sdk_result.total_cost_usd`.
- Summary cost computed in `views.py` and added to `agent_response.total_cost_usd` before persistence.

### Step 4: Update logging and persistence ✅

No schema changes needed — `total_cost_usd` just becomes a more accurate number. The existing path (`AgentResponse.total_cost_usd` → `TurnLoggingService` → DB) handles it transparently.

### Step 5: Optional observability improvement

Wrap the shared Anthropic client in `get_anthropic_client()` with `braintrust.wrap_anthropic()` so auxiliary LLM calls automatically get Braintrust spans with token metrics. This is independent of cost tracking but improves visibility. **Deferred — not required for cost accuracy.**
