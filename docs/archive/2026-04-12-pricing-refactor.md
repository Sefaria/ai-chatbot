# Pricing Refactor: Auto-Updated Pricing + Cost Accumulator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static pricing dict with an auto-updated JSON from LiteLLM, and replace per-service usage threading with a `CostAccumulator` context so any future LLM call is automatically tracked.

**Architecture:** A checked-in `model_pricing.json` (filtered from LiteLLM to Anthropic + OpenAI entries) is loaded by `pricing.py` at import time. A `CostAccumulator` stored in a `contextvars.ContextVar` collects costs from auxiliary LLM calls during a turn. The orchestrator initializes it, each service adds to it after API calls, and the orchestrator reads the total at the end. A GitHub Action auto-updates the pricing JSON weekly.

**Tech Stack:** Python, contextvars, GitHub Actions, LiteLLM pricing JSON

**Key context:**
- The agent runs inside a background thread via `ctx.run(run_agent)` which copies contextvars. Inside that, `asyncio.to_thread()` calls to services also copy context. So a mutable `CostAccumulator` stored in a ContextVar is visible to guardrail, router, and SDK phases.
- The summary service runs in the **main thread** (in `generate_sse()` after the agent thread), outside the ContextVar scope. Summary cost stays as an explicit `compute_cost()` call in `views.py` — this is already implemented and is only one call site.
- `SummaryService` creates its own `anthropic.Anthropic()` client instead of using `get_anthropic_client()`. We fix that as a cleanup.

---

### Task 1: Generate and check in `model_pricing.json`

**Files:**
- Create: `server/scripts/update_pricing.py`
- Create: `server/chat/V2/model_pricing.json`

- [ ] **Step 1: Write the update script**

Create `server/scripts/update_pricing.py` — a standalone script that fetches the LiteLLM pricing JSON, filters to `litellm_provider` in `("anthropic", "openai")`, extracts only `input_cost_per_token` and `output_cost_per_token` (skipping entries where either is None), and writes the result to `server/chat/V2/model_pricing.json`.

```python
"""Fetch LLM pricing from LiteLLM and write filtered JSON for Anthropic + OpenAI models."""

import json
import urllib.request
from pathlib import Path

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
PROVIDERS = {"anthropic", "openai"}
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "chat" / "V2" / "model_pricing.json"


def fetch_and_filter() -> dict:
    with urllib.request.urlopen(LITELLM_URL) as resp:
        data = json.loads(resp.read())

    filtered = {}
    for model_name, info in sorted(data.items()):
        if info.get("litellm_provider") not in PROVIDERS:
            continue
        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue
        filtered[model_name] = {
            "input_cost_per_token": input_cost,
            "output_cost_per_token": output_cost,
        }
    return filtered


def main():
    pricing = fetch_and_filter()
    OUTPUT_PATH.write_text(json.dumps(pricing, indent=2) + "\n")
    print(f"Wrote {len(pricing)} models to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script to generate the JSON file**

Run: `cd /Users/daniel/code/ai-chatbot/server && python scripts/update_pricing.py`
Expected: prints "Wrote N models to .../model_pricing.json" and the file exists.

- [ ] **Step 3: Verify the JSON contains our models**

Run: `python3 -c "import json; d=json.load(open('chat/V2/model_pricing.json')); print('haiku' in str(d.keys()), 'gpt-4o' in str(d.keys()))"`
Expected: `True True`

- [ ] **Step 4: Commit**

```bash
git add server/scripts/update_pricing.py server/chat/V2/model_pricing.json
git commit -m "chore: add LiteLLM pricing data and update script"
```

---

### Task 2: Update `pricing.py` to load from JSON + add CostAccumulator

**Files:**
- Modify: `server/chat/V2/pricing.py`
- Test: `server/chat/tests/test_pricing.py`

- [ ] **Step 1: Write failing tests for JSON-backed pricing and CostAccumulator**

Update `server/chat/tests/test_pricing.py`:

```python
"""Tests for the pricing utility."""

from chat.V2.pricing import CostAccumulator, compute_cost, get_cost_accumulator, init_cost_accumulator


class TestComputeCost:
    def test_haiku_cost(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert cost is not None
        # Haiku: $1.00/M input, $5.00/M output (from LiteLLM JSON)
        expected = (1000 * 1e-06) + (100 * 5e-06)
        assert abs(cost - expected) < 1e-12

    def test_openai_model(self):
        cost = compute_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost is not None
        assert cost > 0

    def test_unknown_model_returns_none(self):
        cost = compute_cost("unknown-model", input_tokens=100, output_tokens=50)
        assert cost is None

    def test_zero_tokens(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0)
        assert cost == 0.0


class TestCostAccumulator:
    def test_starts_at_zero(self):
        acc = CostAccumulator()
        assert acc.total == 0.0

    def test_add_known_model(self):
        acc = CostAccumulator()
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert acc.total > 0

    def test_add_unknown_model_ignored(self):
        acc = CostAccumulator()
        acc.add("unknown-model", input_tokens=1000, output_tokens=100)
        assert acc.total == 0.0

    def test_accumulates_multiple_calls(self):
        acc = CostAccumulator()
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        first = acc.total
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert acc.total == first * 2

    def test_context_var_lifecycle(self):
        assert get_cost_accumulator() is None
        acc = init_cost_accumulator()
        assert get_cost_accumulator() is acc
        acc.add("claude-haiku-4-5-20251001", input_tokens=500, output_tokens=50)
        assert get_cost_accumulator().total == acc.total
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_pricing.py -v`
Expected: FAIL — `CostAccumulator`, `get_cost_accumulator`, `init_cost_accumulator` don't exist yet.

- [ ] **Step 3: Rewrite pricing.py**

Replace `server/chat/V2/pricing.py` with:

```python
"""
Pricing utility — token usage to USD conversion + per-turn cost accumulation.

Pricing data is loaded from model_pricing.json (auto-updated from LiteLLM via
GitHub Action). CostAccumulator collects auxiliary LLM costs during a turn via
a contextvars.ContextVar so services don't need to thread usage through returns.
"""

import contextvars
import json
import logging
from pathlib import Path

logger = logging.getLogger("chat.pricing")

_PRICING_PATH = Path(__file__).parent / "model_pricing.json"
_MODEL_PRICING: dict[str, dict[str, float]] = json.loads(_PRICING_PATH.read_text())


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Compute USD cost from token counts and model name.

    Returns None if the model isn't in the pricing table.
    """
    pricing = _MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (input_tokens * pricing["input_cost_per_token"]) + (
        output_tokens * pricing["output_cost_per_token"]
    )


# --- Cost accumulator (per-turn context) ---

_cost_accumulator_var: contextvars.ContextVar[
    "CostAccumulator | None"
] = contextvars.ContextVar("cost_accumulator", default=None)


class CostAccumulator:
    """Collects LLM costs during a single turn. Mutable so changes are visible
    across asyncio.to_thread boundaries (ContextVar copies the reference)."""

    def __init__(self) -> None:
        self._total: float = 0.0

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        cost = compute_cost(model, input_tokens, output_tokens)
        if cost is not None:
            self._total += cost
        elif input_tokens > 0:
            logger.warning(f"No pricing for model: {model}")

    @property
    def total(self) -> float:
        return self._total


def init_cost_accumulator() -> CostAccumulator:
    """Create a fresh accumulator and store it in the current context."""
    acc = CostAccumulator()
    _cost_accumulator_var.set(acc)
    return acc


def get_cost_accumulator() -> CostAccumulator | None:
    """Retrieve the current turn's accumulator, or None if not initialized."""
    return _cost_accumulator_var.get()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_pricing.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/pricing.py server/chat/tests/test_pricing.py
git commit -m "feat: load pricing from JSON, add CostAccumulator context"
```

---

### Task 3: Wire accumulator into services (guardrail + router)

**Files:**
- Modify: `server/chat/V2/guardrail/guardrail_service.py`
- Modify: `server/chat/V2/router/router_service.py`
- Modify: `server/chat/tests/test_guardrail_service.py`
- Modify: `server/chat/tests/test_router_service.py`

The goal is to have each service call `get_cost_accumulator().add(...)` after its API call, then **revert** the usage fields we added to `GuardrailResult` and `RouterResult` (they're no longer needed).

- [ ] **Step 1: Update guardrail_service.py**

Revert `GuardrailResult` to its original fields (remove `input_tokens`, `output_tokens`, `model`). After the `self.client.messages.create()` call, add accumulator tracking:

```python
response = self.client.messages.create(...)
result = self._parse_response(response)

accumulator = get_cost_accumulator()
if accumulator:
    accumulator.add(
        settings.GUARDRAIL_MODEL,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

return result
```

Add the import: `from ..pricing import get_cost_accumulator`

- [ ] **Step 2: Update router_service.py**

Revert `RouterResult` to its original fields. Revert `_classify_message` to return just `RouteType`. After `self.client.messages.create()` in `_classify_message`, add accumulator tracking:

```python
response = self.client.messages.create(...)
route = self._parse_classification(response)

accumulator = get_cost_accumulator()
if accumulator:
    accumulator.add(
        settings.ROUTER_MODEL,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

return route
```

Revert `classify` to its original signature (no usage unpacking).

- [ ] **Step 3: Update guardrail tests**

Revert `_make_anthropic_response` to not need `input_tokens`/`output_tokens` params (use MagicMock defaults). Remove the `test_usage_tracking` test from guardrail service tests (usage is now tracked via the accumulator, tested in test_pricing.py). The mock response still needs `.usage.input_tokens` and `.usage.output_tokens` to be integers (not MagicMocks) since the service reads them:

```python
def _make_anthropic_response(text: str):
    """Build a mock Anthropic Messages response."""
    block = MagicMock()
    block.text = text
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    response = MagicMock()
    response.content = [block]
    response.usage = usage
    return response
```

- [ ] **Step 4: Update router tests**

Same pattern as guardrail: keep usage mock ints on the response, remove `test_usage_tracking`, `test_deterministic_classify_zero_usage`, `test_error_returns_zero_usage` tests. Revert any assertion changes about `result.input_tokens` etc.

- [ ] **Step 5: Run tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_guardrail_service.py chat/tests/test_router_service.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/chat/V2/guardrail/guardrail_service.py server/chat/V2/router/router_service.py server/chat/tests/test_guardrail_service.py server/chat/tests/test_router_service.py
git commit -m "refactor: use CostAccumulator in guardrail and router services"
```

---

### Task 4: Wire accumulator into summary service + fix client

**Files:**
- Modify: `server/chat/V2/summarization/summary_service.py`

The summary service runs in the main thread (outside the ContextVar scope), so it uses `compute_cost()` directly — no accumulator. But we still clean up:

1. Switch from `anthropic.Anthropic(api_key=...)` to `get_anthropic_client(api_key)` for consistency.
2. Keep the `llm_input_tokens` / `llm_output_tokens` / `llm_model` non-persisted attributes — `views.py` uses them to compute summary cost explicitly.

- [ ] **Step 1: Update summary_service.py**

In `__init__`, replace `self.client = anthropic.Anthropic(api_key=self.api_key)` with:
```python
from ..utils import get_anthropic_client
self.client = get_anthropic_client(self.api_key)
```

Remove the `import anthropic` at the top of the file (no longer needed directly).

- [ ] **Step 2: Run tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/ -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add server/chat/V2/summarization/summary_service.py
git commit -m "refactor: use get_anthropic_client in summary service"
```

---

### Task 5: Simplify orchestrator and guardrail gate

**Files:**
- Modify: `server/chat/V2/agent/turn_orchestrator.py`
- Modify: `server/chat/V2/agent/guardrail_gate.py`
- Modify: `server/chat/V2/agent/router.py`
- Modify: `server/chat/tests/test_guardrail_gate.py`

The orchestrator no longer needs to manually compute costs from guardrail/router usage. It just initializes the accumulator, runs the turn, and reads the total at the end.

- [ ] **Step 1: Simplify guardrail_gate.py**

Revert `GuardrailGateResult` → return `AgentResponse | None` again. Remove the `GuardrailGateResult` dataclass and the usage fields. The gate doesn't need to expose usage — the service accumulates it via the ContextVar.

```python
async def run_guardrail(self, ...) -> AgentResponse | None:
    ...
    # guardrail_result no longer has usage fields
    if guardrail_result.allowed:
        return None
    ...
    return AgentResponse(...)
```

- [ ] **Step 2: Simplify router.py**

Revert `run_router` return to `tuple[str | None, str, list[ConversationMessage]]` (3-tuple, no usage dict). Remove all usage dict construction.

- [ ] **Step 3: Simplify turn_orchestrator.py**

Remove all manual cost computation (the `auxiliary_cost` variable, `compute_cost()` calls, warning logs). Replace with:

At the top of `run_turn`, initialize the accumulator:
```python
from ..pricing import init_cost_accumulator
cost_accumulator = init_cost_accumulator()
```

Revert to the original guardrail gate call pattern:
```python
guardrail_response = await self.guardrail_gate.run_guardrail(...)
if guardrail_response:
    guardrail_response.total_cost_usd = cost_accumulator.total or None
    return guardrail_response
```

Revert to the original router call (3-tuple):
```python
router_prompt_id, route, messages = await self.router.run_router(...)
```

At the end, combine SDK cost with accumulator total:
```python
total_cost_usd = sdk_result.total_cost_usd
if total_cost_usd is not None:
    total_cost_usd += cost_accumulator.total
elif cost_accumulator.total > 0:
    total_cost_usd = cost_accumulator.total
```

Remove the `logging` import and `logger` if no longer used. Remove the `from ..pricing import compute_cost` import; add `from ..pricing import init_cost_accumulator`.

- [ ] **Step 4: Update guardrail gate tests**

Revert `test_guardrail_gate.py` to test `AgentResponse | None` return (not `GuardrailGateResult`). Remove `test_usage_passed_through`. Revert test method names back to originals (`test_allowed_returns_none`).

- [ ] **Step 5: Run tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_guardrail_gate.py -v`
Expected: all pass.

- [ ] **Step 6: Run full test suite**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest -v`
Expected: all pass with no regressions.

- [ ] **Step 7: Commit**

```bash
git add server/chat/V2/agent/turn_orchestrator.py server/chat/V2/agent/guardrail_gate.py server/chat/V2/agent/router.py server/chat/tests/test_guardrail_gate.py
git commit -m "refactor: simplify orchestrator to use CostAccumulator"
```

---

### Task 6: Add GitHub Action for auto-updating pricing

**Files:**
- Create: `.github/workflows/update-pricing.yaml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Update model pricing

on:
  schedule:
    - cron: "0 9 * * 1"  # Weekly on Monday 9am UTC
  workflow_dispatch: {}    # Manual trigger

permissions:
  contents: write
  pull-requests: write

jobs:
  update-pricing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Fetch latest pricing
        run: python server/scripts/update_pricing.py

      - name: Check for changes
        id: diff
        run: |
          git diff --quiet server/chat/V2/model_pricing.json && echo "changed=false" >> "$GITHUB_OUTPUT" || echo "changed=true" >> "$GITHUB_OUTPUT"

      - name: Create PR
        if: steps.diff.outputs.changed == 'true'
        uses: peter-evans/create-pull-request@v7
        with:
          commit-message: "chore: update model pricing from LiteLLM"
          title: "chore: update model pricing from LiteLLM"
          body: "Auto-generated by the weekly pricing update workflow."
          branch: chore/update-model-pricing
          labels: dependencies
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/update-pricing.yaml
git commit -m "ci: add weekly GitHub Action to update model pricing"
```

---

### Task 7: Update plan doc + final verification

**Files:**
- Modify: `docs/plans/sc-42070-auxiliary-llm-cost-tracking.md`

- [ ] **Step 1: Update the plan to reflect the refactor**

Mark the pricing auto-update and CostAccumulator approach in the plan doc. Note that Step 5 (Braintrust observability) is deferred.

- [ ] **Step 2: Run full test suite one final time**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs/plans/sc-42070-auxiliary-llm-cost-tracking.md
git commit -m "docs: update sc-42070 plan with refactor decisions"
```
