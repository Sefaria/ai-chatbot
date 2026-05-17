"""Fetch LLM pricing from LiteLLM and write filtered JSON for Anthropic + OpenAI models."""

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
PROVIDERS = {"anthropic", "openai"}
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "chat" / "V2" / "model_pricing.json"

FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 5

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("update_pricing")


def _fetch_pricing_json() -> dict:
    """Fetch the LiteLLM pricing JSON with a simple retry on transient failures.

    Re-raises on the final attempt — the GitHub Action surfaces the failure
    via a red workflow run, which is the signal to investigate.
    """
    last_exc: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(LITELLM_URL, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            logger.warning("LiteLLM fetch attempt %d/%d failed: %s", attempt, FETCH_ATTEMPTS, exc)
            if attempt < FETCH_ATTEMPTS:
                time.sleep(FETCH_BACKOFF_SECONDS * attempt)

    logger.error("Giving up after %d attempts", FETCH_ATTEMPTS)
    if last_exc is None:
        raise RuntimeError("Failed to fetch LiteLLM pricing, but no exception was captured")
    raise last_exc


def fetch_and_filter() -> dict:
    data = _fetch_pricing_json()

    filtered = {}
    for model_name, info in sorted(data.items()):
        if info.get("litellm_provider") not in PROVIDERS:
            continue
        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue
        entry = {
            "input_cost_per_token": input_cost,
            "output_cost_per_token": output_cost,
        }
        cache_creation = info.get("cache_creation_input_token_cost")
        if cache_creation is not None:
            entry["cache_creation_input_token_cost"] = cache_creation
        cache_read = info.get("cache_read_input_token_cost")
        if cache_read is not None:
            entry["cache_read_input_token_cost"] = cache_read
        filtered[model_name] = entry
    return filtered


def main():
    pricing = fetch_and_filter()
    OUTPUT_PATH.write_text(json.dumps(pricing, indent=2) + "\n")
    logger.info("Wrote %d models to %s", len(pricing), OUTPUT_PATH)


if __name__ == "__main__":
    main()
