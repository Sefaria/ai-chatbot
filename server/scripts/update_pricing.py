"""Fetch LLM pricing from LiteLLM and write filtered JSON for Anthropic + OpenAI models."""

import json
import urllib.request
from pathlib import Path

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
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
