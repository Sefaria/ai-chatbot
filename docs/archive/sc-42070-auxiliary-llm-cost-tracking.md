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

## Implementation

### Pricing data ✅

`server/chat/V2/model_pricing.json` — auto-generated from LiteLLM's open-source pricing data (Anthropic + OpenAI models, ~150 entries). Updated weekly via `.github/workflows/update-pricing.yaml` which auto-PRs on changes.

`server/scripts/update_pricing.py` — standalone script to fetch and filter the LiteLLM JSON. Used by the GitHub Action and can be run manually.

### Cost accumulation ✅

`CostAccumulator` in `server/chat/V2/pricing.py` — collects guardrail + router costs during a turn via a `contextvars.ContextVar`. Each service calls `get_cost_accumulator().add_from_response(model, response)` after its API call. The orchestrator initializes the accumulator at the top of `generate_sse()` and resets it in the same function's `finally:` block to prevent cross-request state leakage on reused WSGI threads.

- **Guardrail + Router** (run inside the agent thread): Add to the shared `CostAccumulator` via the ContextVar. The accumulator reference is visible across `asyncio.to_thread` boundaries because the orchestrator copies the current context before dispatching the agent thread.
- **Summary** (runs on the main thread after the agent completes): Does **not** use the shared accumulator. `SummaryService.update_summary` returns a `SummaryResult(summary, cost_usd)` named tuple, and `views.py` adds the explicit cost to the turn total. This keeps summary cost attribution unambiguous — it doesn't rely on reading a delta from a shared bucket.

No DB schema changes — `total_cost_usd` just becomes more accurate.

### Deferred: Braintrust observability

Wrap the shared Anthropic client with `braintrust.wrap_anthropic()` for auxiliary call visibility in Braintrust traces. Independent of cost tracking.
