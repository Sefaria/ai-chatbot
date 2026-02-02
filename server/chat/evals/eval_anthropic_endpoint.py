"""
Braintrust evaluation script for the Anthropic-compatible endpoint.

This script evaluates the /api/v2/chat/anthropic endpoint using Braintrust's
Eval framework. It can run against a local server or a deployed endpoint.

Usage:
    # Run against local server (start server first with ./start.sh)
    BRAINTRUST_API_KEY=<key> python -m braintrust eval chat/evals/eval_anthropic_endpoint.py

    # Run against a specific endpoint
    BRAINTRUST_API_KEY=<key> EVAL_ENDPOINT=https://your-domain.com/api/v2/chat/anthropic \
        python -m braintrust eval chat/evals/eval_anthropic_endpoint.py

    # Run with a specific dataset from Braintrust
    BRAINTRUST_API_KEY=<key> EVAL_DATASET=my-dataset-name \
        python -m braintrust eval chat/evals/eval_anthropic_endpoint.py
"""

import os

import httpx
from braintrust import Eval, init_dataset

# Try to import autoevals scorers (optional but recommended)
try:
    from autoevals import AnswerRelevance, Factuality

    HAS_AUTOEVALS = True
except ImportError:
    HAS_AUTOEVALS = False


# Configuration
ENDPOINT_URL = os.environ.get("EVAL_ENDPOINT", "http://localhost:8001/api/v2/chat/anthropic")
DATASET_NAME = os.environ.get("EVAL_DATASET", None)
TIMEOUT_SECONDS = 120  # Agent can take a while with tool calls


# Sample evaluation dataset (used if no Braintrust dataset specified)
SAMPLE_DATA = [
    {
        "input": {"question": "What is Shabbat?"},
        "expected": "Shabbat is the Jewish day of rest",
        "metadata": {"category": "basics", "difficulty": "easy"},
    },
    {
        "input": {"question": "What does Genesis 1:1 say?"},
        "expected": "In the beginning God created the heaven and the earth",
        "metadata": {"category": "text_retrieval", "difficulty": "easy"},
    },
    {
        "input": {"question": "What is the relationship between Rashi and Genesis?"},
        "expected": "Rashi wrote commentary on Genesis",
        "metadata": {"category": "commentary", "difficulty": "medium"},
    },
    {
        "input": {"question": "What are the laws of keeping kosher?"},
        "expected": "kosher laws include separation of meat and dairy",
        "metadata": {"category": "halacha", "difficulty": "medium"},
    },
    {
        "input": {"question": "Explain the concept of tikkun olam"},
        "expected": "repairing the world",
        "metadata": {"category": "concepts", "difficulty": "medium"},
    },
]


def call_anthropic_endpoint(question: str) -> str:
    """
    Call the Anthropic-compatible endpoint and return the response text.

    Args:
        question: The user's question

    Returns:
        The assistant's response text
    """
    request_body = {
        "model": "sefaria-agent",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": question}],
    }

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        response = client.post(ENDPOINT_URL, json=request_body)
        response.raise_for_status()

    data = response.json()

    # Extract text from Anthropic format response
    content_blocks = data.get("content", [])
    text_parts = [block.get("text", "") for block in content_blocks if block.get("type") == "text"]
    return "\n".join(text_parts)


async def task(input_data: dict) -> str:
    """
    The evaluation task function.

    Takes input from the dataset and returns the model's output.
    """
    question = input_data.get("question", "")
    return call_anthropic_endpoint(question)


def contains_expected(output: str, expected: str) -> float:
    """
    Simple scorer: checks if expected keywords appear in output.

    Returns 1.0 if all expected words appear, partial score otherwise.
    """
    if not expected or not output:
        return 0.0

    expected_words = expected.lower().split()
    output_lower = output.lower()

    matches = sum(1 for word in expected_words if word in output_lower)
    return matches / len(expected_words)


def has_content(output: str, expected: str = None) -> float:
    """
    Scorer: checks if output has meaningful content (not empty/error).
    """
    if not output:
        return 0.0
    if "error" in output.lower() and len(output) < 100:
        return 0.0
    if len(output) < 20:
        return 0.5
    return 1.0


def get_data():
    """
    Get evaluation data - either from Braintrust dataset or sample data.
    """
    if DATASET_NAME:
        # Use Braintrust dataset
        return init_dataset("On Site Agent", dataset=DATASET_NAME)

    # Use sample data
    return SAMPLE_DATA


def get_scorers():
    """
    Get list of scorers to use for evaluation.
    """
    scorers = [
        contains_expected,
        has_content,
    ]

    # Add autoevals scorers if available
    if HAS_AUTOEVALS:
        scorers.extend(
            [
                Factuality(),
                AnswerRelevance(),
            ]
        )

    return scorers


# Run the evaluation when executed via `braintrust eval`
# This is at module level because braintrust CLI expects it
if os.environ.get("BRAINTRUST_API_KEY"):
    Eval(
        "Sefaria Agent - Anthropic Endpoint",
        data=get_data,
        task=task,
        scores=get_scorers(),
        experiment_name="anthropic-endpoint-eval",
    )
