"""
Braintrust Evaluation Script for LC Chatbot
============================================

Runs evaluations against the chatbot API using Braintrust datasets and scorers.

Prerequisites:
- Set BRAINTRUST_API_KEY environment variable
- Set CHATBOT_USER_TOKEN environment variable (encrypted auth token)
- Backend server running (for local evals): python manage.py runserver 0.0.0.0:8001
- Create custom scorers in Braintrust UI
- Create a dataset in Braintrust UI

Authentication:
- Token must be provided via CHATBOT_USER_TOKEN env var

Usage:
    python evals/run_eval.py --all-scorers
    python evals/run_eval.py --dataset "My Dataset" --experiment "Test Run"
"""

import os
import sys
import asyncio
import argparse
import httpx
from datetime import datetime
from pathlib import Path

from braintrust import EvalAsync, init_dataset, invoke
from dotenv import load_dotenv

# Load environment variables from server/.env
env_path = Path(__file__).parent.parent / "server" / ".env"
load_dotenv(env_path)

BRAINTRUST_PROJECT = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
DEFAULT_DATASET = "Benchmark"

# API Configuration
API_BASE_URL = os.environ.get("CHATBOT_API_URL", "http://localhost:8001")
PROD_API_URL = "https://chat-dev.sefaria.org"
# Auth token must be provided externally (encrypted token, not plain user ID)
USER_TOKEN = os.environ.get("CHATBOT_USER_TOKEN")


# =============================================================================
# Chatbot Client
# =============================================================================


class ChatbotClient:
    """Client for the LC Chatbot Anthropic-compatible API."""

    def __init__(self, base_url: str = API_BASE_URL, api_key: str = None):
        self.base_url = base_url.rstrip("/")
        # Auth token must be provided via api_key param or CHATBOT_USER_TOKEN env var
        self.api_key = api_key or USER_TOKEN
        if not self.api_key:
            raise ValueError(
                "CHATBOT_USER_TOKEN env var must be set. "
                "Contact the engineering team to obtain a valid token."
            )
        self.client = httpx.AsyncClient(timeout=120.0)

    async def chat(self, message: str, session_id: str = None) -> dict:
        """
        Send a message to the chatbot and get a response.

        Uses the Anthropic-compatible endpoint for Braintrust integration.
        """
        url = f"{self.base_url}/api/v2/chat/anthropic"

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
        }
        if session_id:
            headers["X-Session-ID"] = session_id

        payload = {
            "model": "sefaria-agent",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": message}],
        }

        response = await self.client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()

    def extract_text(self, response: dict) -> str:
        """Extract text content from Anthropic-format response."""
        content = response.get("content", [])
        text_parts = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts)

    def extract_tool_calls(self, response: dict) -> list:
        """Extract tool calls from response."""
        content = response.get("content", [])
        return [block for block in content if block.get("type") == "tool_use"]


# =============================================================================
# Custom Scorer Wrapper
# =============================================================================


def run_custom_scorer(
    scorer_slug: str,
    output: str,
    expected: str = None,
    input_text: str = None,
    metadata: dict = None,
):
    """
    Invoke a custom scorer defined in Braintrust UI.

    Args:
        scorer_slug: The slug of the custom scorer (e.g., "accuracy-scorer")
        output: The model's output to score
        expected: The expected/reference output (if applicable)
        input_text: The original input prompt
        metadata: Additional metadata for the scorer

    Returns:
        The scorer result
    """
    try:
        scorer_input = {"output": output}

        if expected is not None:
            scorer_input["expected"] = expected
        if input_text is not None:
            scorer_input["input"] = input_text
        if metadata is not None:
            scorer_input["metadata"] = metadata

        # invoke() is synchronous
        result = invoke(
            project_name=BRAINTRUST_PROJECT, slug=scorer_slug, input=scorer_input
        )

        return result

    except Exception as e:
        print(f"Error running scorer {scorer_slug}: {e}")
        return {"score": 0.0, "error": str(e)}


def create_scorer(slug: str, description: str = ""):
    """Factory function to create scorer functions for Braintrust UI scorers."""

    def scorer(output, expected=None, input=None, metadata=None):
        result = run_custom_scorer(slug, output, expected, input, metadata)
        return result.get("score", 0.0) if isinstance(result, dict) else result

    scorer.__name__ = slug.replace("-", "_")
    scorer.__doc__ = description
    return scorer


def get_all_project_scorers() -> list:
    """
    Fetch all scorers defined in Braintrust UI for this project.

    Returns:
        List of scorer functions for all project scorers
    """
    import braintrust

    api_key = os.environ.get("BRAINTRUST_API_KEY")
    if not api_key:
        print("ERROR: BRAINTRUST_API_KEY not set")
        return []

    try:
        # Initialize braintrust and get API connection
        braintrust.login()
        conn = braintrust.api_conn()

        # List all functions (scorers)
        response = conn.get("/v1/function")
        if not response.ok:
            print(f"Error fetching functions: {response.status_code} {response.text}")
            return []

        data = response.json()
        functions = data.get("objects", data) if isinstance(data, dict) else data

        # Filter to only scorer-type functions (exclude prompts, tools, etc.)
        scorer_functions = [f for f in functions if f.get("function_type") == "scorer"]

        # Filter out test scorers (those starting with TEST_)
        scorer_functions = [
            f for f in scorer_functions if not f.get("slug", "").startswith("TEST_")
        ]

        scorers = []
        print(
            f"Found {len(scorer_functions)} scorers in Braintrust (out of {len(functions)} total functions):"
        )
        for func in scorer_functions:
            slug = func.get("slug")
            name = func.get("name", "")
            if slug:
                print(f"  - {slug}: {name}")
                scorers.append(create_scorer(slug, name))

        if not scorers:
            print(f"No scorers found in project '{BRAINTRUST_PROJECT}'")

        return scorers

    except Exception as e:
        print(f"Error fetching scorers from Braintrust: {e}")
        return []


# =============================================================================
# Main Evaluation
# =============================================================================


async def run_evaluation(
    client: ChatbotClient,
    dataset_name: str = DEFAULT_DATASET,
    experiment_name: str = None,
    scorers: list = None,
    max_concurrency: int = 3,
):
    """
    Run evaluation using Braintrust.

    Args:
        client: ChatbotClient instance to use for API calls
        dataset_name: Name of the dataset in Braintrust. Dataset rows should have
            one of these fields: 'prompt', 'input', or 'query'
        experiment_name: Name for this experiment run
        scorers: List of scorer functions (uses defaults if None)
        max_concurrency: Max concurrent evaluations
    """
    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("ERROR: BRAINTRUST_API_KEY not set")
        print("Set it with: export BRAINTRUST_API_KEY='your-api-key'")
        sys.exit(1)

    # Default experiment name
    if experiment_name is None:
        experiment_name = (
            f"Automated Benchmark Eval - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    async def task(input_data):
        """Task function that runs the chatbot."""
        # Handle different input formats
        if isinstance(input_data, dict):
            prompt = (
                input_data.get("prompt")
                or input_data.get("input")
                or input_data.get("query")
                or str(input_data)
            )
        else:
            prompt = str(input_data)

        try:
            response = await client.chat(prompt)
            output = client.extract_text(response)
            return output
        except Exception as e:
            print(f"Error calling chatbot: {e}")
            return f"ERROR: {e}"

    print("\n" + "=" * 70)
    print(f"Starting Evaluation: {experiment_name}")
    print(f"Project: {BRAINTRUST_PROJECT}")
    print(f"Dataset: {dataset_name}")
    print(f"API: {client.base_url}")
    print("=" * 70 + "\n")

    try:
        result = await EvalAsync(
            BRAINTRUST_PROJECT,
            data=init_dataset(BRAINTRUST_PROJECT, name=dataset_name),
            task=task,
            scores=scorers,
            experiment_name=experiment_name,
            metadata={
                "api_url": client.base_url,
                "timestamp": datetime.now().isoformat(),
            },
            max_concurrency=max_concurrency,
        )

        print("\n" + "=" * 70)
        print("Evaluation Complete!")
        print("=" * 70)
        print("\nView results in Braintrust UI")
        print(f"Project: {BRAINTRUST_PROJECT}")
        print(f"Experiment: {experiment_name}")

        return result

    finally:
        await client.close()


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Run Braintrust evaluations for LC Chatbot"
    )
    parser.add_argument(
        "--dataset",
        "-d",
        default=DEFAULT_DATASET,
        help=f"Braintrust dataset name (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--experiment",
        "-e",
        default=None,
        help="Experiment name (default: auto-generated with timestamp)",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=3,
        help="Max concurrent evaluations (default: 3)",
    )
    parser.add_argument(
        "--api-url", default=None, help=f"Chatbot API URL (default: {API_BASE_URL})"
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help=f"Use production API ({PROD_API_URL})",
    )
    parser.add_argument(
        "--scorers",
        "-s",
        default=None,
        help="Comma-separated list of Braintrust UI scorer slugs (e.g., 'accuracy,relevance,citation')",
    )
    parser.add_argument(
        "--all-scorers",
        action="store_true",
        help="Use all scorers defined in Braintrust UI for this project",
    )

    args = parser.parse_args()

    # Create client with appropriate URL
    if args.prod:
        client = ChatbotClient(base_url=PROD_API_URL)
    elif args.api_url:
        client = ChatbotClient(base_url=args.api_url)
    else:
        client = ChatbotClient()

    # Build scorers list from CLI argument
    scorers = None
    if args.all_scorers:
        scorers = get_all_project_scorers()
    elif args.scorers:
        scorer_slugs = [s.strip() for s in args.scorers.split(",")]
        scorers = [create_scorer(slug) for slug in scorer_slugs]

    asyncio.run(
        run_evaluation(
            client=client,
            dataset_name=args.dataset,
            experiment_name=args.experiment,
            scorers=scorers,
            max_concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
