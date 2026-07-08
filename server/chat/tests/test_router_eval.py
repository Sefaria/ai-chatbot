"""
Evaluation tests for the router-classifier prompt against the Braintrust dataset.

Requires live Braintrust + Anthropic API access

"""

import json

import pytest
from django.conf import settings


def _load_dataset():
    """Fetch the Router classification dataset from Braintrust."""
    import braintrust

    ds = braintrust.init_dataset(project="On Site Agent", name="Router classification")
    rows = []
    for row in ds:
        user_input = row.get("input")
        expected = row.get("expected")
        if user_input and expected:
            rows.append((user_input, expected))
    return rows


def _load_router_prompt():
    """Fetch the router-classifier prompt text from Braintrust."""
    import braintrust

    prompt = braintrust.load_prompt(project="On Site Agent", slug="router-classifier")
    built = prompt.build()
    messages = built.get("messages", [])
    for msg in messages:
        if msg.get("role") == "system":
            return msg["content"]
    # Fallback: use first message content
    if messages:
        return messages[0].get("content", "")
    raise RuntimeError("Could not extract system prompt from router-classifier")


def _classify(client, system_prompt: str, user_message: str) -> str:
    """Run the router classifier and return the route string."""
    from chat.V2.utils import strip_markdown_fences

    response = client.messages.create(
        model=settings.ROUTER_MODEL,
        max_tokens=256,
        temperature=0.0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    text = strip_markdown_fences(response.content[0].text)
    data = json.loads(text)
    return data.get("route", "").lower()


# ---------------------------------------------------------------------------
# Collect dataset rows as pytest parameters (at import time if eval enabled)
# ---------------------------------------------------------------------------

try:
    _dataset_rows = _load_dataset()
    _system_prompt = _load_router_prompt()
except Exception as e:
    pytest.skip(f"Could not load Braintrust dataset/prompt: {e}", allow_module_level=True)


@pytest.mark.parametrize("user_input,expected_route", _dataset_rows)
def test_router_classification(user_input, expected_route):
    """Verify the router-classifier prompt returns the correct route for each dataset row."""
    import anthropic

    client = anthropic.Anthropic()
    actual_route = _classify(client, _system_prompt, user_input)
    assert actual_route == expected_route, (
        f"Input: {user_input!r}\nExpected: {expected_route}, Got: {actual_route}"
    )
