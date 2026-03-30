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

## First step: Decide on approach

Before implementing, evaluate these options and pick one:

### Option A: Move auxiliary calls inside the Agent SDK

The ideal solution. If guardrail, router, and summary ran as part of the agent pipeline, the SDK's built-in `total_cost_usd` on `ResultMessage` would automatically include them — no manual cost calculation needed. This is the most accurate and maintainable approach since the SDK computes cost server-side using Anthropic's actual pricing.

### Option B: Use Braintrust's cost tracking

Braintrust already wraps our spans and provides `estimated_cost`. Investigate whether Braintrust's tracing can capture auxiliary LLM calls and aggregate cost automatically. The guardrail and router already create child spans — if Braintrust can compute cost from those, this may require minimal code changes.

### Option C: Manual cost aggregation

Extract token counts from each auxiliary service's Anthropic API response and compute cost ourselves using a pricing lookup. Aggregate into `total_cost_usd` in the orchestrator. This is the most straightforward but least maintainable — pricing tables need manual updates when Anthropic changes prices.

## WIP reference

There's a partial implementation on branch `fix/add-gaurdrail-llm-call-log` (PR #42) that covers guardrail only. Review it for context.
