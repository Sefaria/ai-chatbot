"""
Braintrust Evaluation Script for LC Chatbot

Runs the chatbot against a Braintrust dataset and scores responses using
custom scorers defined in the Braintrust UI. Results are logged to Braintrust
for analysis and comparison across experiments.

The script validates that the specified dataset and scorers exist in Braintrust
before running the evaluation, providing clear error messages if not found.

Usage:
    python evals/run_eval.py --all-scorers                    # Run with all project scorers
    python evals/run_eval.py --scorers accuracy-374376eb2     # Run with specific scorer slugs
    python evals/run_eval.py --local                          # Test against local server
    python evals/run_eval.py -d "My Dataset" -e "Test Run"    # Custom dataset/experiment

Environment Variables:
    BRAINTRUST_API_KEY   - Required. API key for Braintrust.
    CHATBOT_USER_TOKEN   - Required. Encrypted auth token for chatbot API.
    BRAINTRUST_PROJECT   - Optional. Project name (default: "On Site Agent").
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import braintrust
import httpx
from braintrust import EvalAsync, init_dataset, invoke
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "server" / ".env")


def extract_braintrust_items(response_data):
    """
    Extract items from Braintrust API response.

    Braintrust returns paginated results with items under "objects" key.
    Falls back to treating response as direct list for compatibility.
    """
    if isinstance(response_data, dict):
        return response_data.get("objects", response_data)
    return response_data


# Configuration
PROJECT = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
DEFAULT_DATASET = "Benchmark"
PROD_API_URL = "https://chat-dev.sefaria.org"
LOCAL_API_URL = "http://localhost:8001"
USER_TOKEN = os.environ.get("CHATBOT_USER_TOKEN")
DEFAULT_EXPERIMENT_NAME = "Automated Eval"


class ChatbotClient:
    """
    HTTP client for the LC Chatbot streaming API.

    Sends messages to the chatbot and collects the streamed response.
    Uses SSE (Server-Sent Events) to handle long-running requests without timeouts.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        if not USER_TOKEN:
            raise ValueError("CHATBOT_USER_TOKEN env var must be set")
        self.client = httpx.AsyncClient(timeout=300.0)

    async def chat(self, message: str) -> str:
        """
        Send a message to the chatbot and return the response text.

        Streams the response via SSE, collecting chunks until the final
        message event containing the full markdown response.
        """
        session_id = f"eval_{uuid.uuid4().hex[:16]}"
        payload = {
            "sessionId": session_id,
            "messageId": f"msg_{uuid.uuid4().hex[:16]}",
            "text": message,
            "userId": USER_TOKEN,
            "timestamp": datetime.now().isoformat(),
        }

        final_response = None
        async with self.client.stream(
            "POST", f"{self.base_url}/api/chat/stream", json=payload
        ) as response:
            response.raise_for_status()
            current_event = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    try:
                        parsed = json.loads(line[6:])
                        if current_event == "error":
                            raise Exception(parsed.get("error", "Unknown error"))
                        # Final message contains the complete markdown response
                        if "markdown" in parsed:
                            final_response = parsed
                    except json.JSONDecodeError:
                        pass

        if not final_response:
            raise Exception("No response received from chatbot")
        return final_response.get("markdown", "")

    async def close(self):
        await self.client.aclose()


def create_scorer(slug: str):
    """
    Create a scorer function that invokes a Braintrust UI-defined scorer.

    Braintrust scorers are LLM-based evaluators defined in the Braintrust UI.
    This wrapper calls them via the Braintrust API and returns the numeric score.
    """

    def scorer(output, expected=None, input=None, metadata=None):
        scorer_input = {"output": output}
        if expected is not None:
            scorer_input["expected"] = expected
        if input is not None:
            scorer_input["input"] = input
        if metadata is not None:
            scorer_input["metadata"] = metadata
        try:
            result = invoke(project_name=PROJECT, slug=slug, input=scorer_input)
            if isinstance(result, dict):
                if "score" not in result:
                    raise ValueError(
                        f"Scorer {slug} returned dict without 'score': {result}"
                    )
                return result["score"]
            return result
        except Exception as e:
            print(f"Error running scorer {slug}: {e}")
            return 0.0

    scorer.__name__ = slug.replace("-", "_")
    return scorer


def get_available_scorer_slugs() -> set:
    """
    Fetch all available scorer slugs from Braintrust.

    Returns a set of slugs for scorers defined in Braintrust UI.
    """
    try:
        braintrust.login()
        response = braintrust.api_conn().get("/v1/function")
        if not response.ok:
            return set()

        data = response.json()
        functions = extract_braintrust_items(data)
        return {
            f.get("slug")
            for f in functions
            if f.get("function_type") == "scorer" and f.get("slug")
        }
    except Exception:
        return set()


def get_all_scorers() -> list:
    """
    Fetch all scorer functions defined in Braintrust UI for this project.

    Queries the Braintrust API for all functions of type "scorer",
    filtering out test scorers (those prefixed with TEST_).
    """
    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("ERROR: BRAINTRUST_API_KEY not set")
        return []

    try:
        braintrust.login()
        response = braintrust.api_conn().get("/v1/function")
        if not response.ok:
            print(f"Error fetching functions: {response.status_code}")
            return []

        data = response.json()
        functions = extract_braintrust_items(data)
        scorer_funcs = [
            f
            for f in functions
            if f.get("function_type") == "scorer"
            and not f.get("slug", "").startswith("TEST_")
        ]

        print(f"Found {len(scorer_funcs)} scorers:")
        scorers = []
        for f in scorer_funcs:
            if slug := f.get("slug"):
                print(f"  - {slug}: {f.get('name', '')}")
                scorers.append(create_scorer(slug))
        return scorers

    except Exception as e:
        print(f"Error fetching scorers: {e}")
        return []


def validate_scorers(scorer_slugs: list[str]) -> bool:
    """
    Validate that all provided scorer slugs exist in Braintrust.

    Returns True if all scorers are valid, False otherwise.
    """
    valid_scorers = get_available_scorer_slugs()
    if not valid_scorers:
        print("ERROR: Could not fetch available scorers from Braintrust")
        return False

    invalid_scorer = [s for s in scorer_slugs if s not in valid_scorers]
    if invalid_scorer:
        print(
            f"I can't find that scorer, please double check and try again: {', '.join(invalid)}"
        )
        return False
    return True


def validate_dataset(dataset_name: str) -> bool:
    """
    Validate that the dataset exists in Braintrust.

    Returns True if dataset exists, False otherwise.
    """
    try:
        braintrust.login()
        response = braintrust.api_conn().get("/v1/dataset")
        if not response.ok:
            print("ERROR: Could not fetch datasets from Braintrust")
            return False

        data = response.json()
        datasets = extract_braintrust_items(data)
        dataset_names = {d.get("name") for d in datasets if d.get("name")}

        if dataset_name not in dataset_names:
            print(
                f"I can't find that dataset, please double check and try again: {dataset_name}"
            )
            return False
        return True

    except Exception as e:
        print(f"ERROR: Could not validate dataset: {e}")
        return False


async def run_evaluation(
    client: ChatbotClient,
    dataset_name: str,
    experiment_name: str,
    scorers: list,
    max_concurrency: int,
):
    """
    Run the evaluation pipeline.

    Loads the dataset from Braintrust, runs each prompt through the chatbot,
    scores the responses, and logs results to Braintrust for analysis.
    """
    if not experiment_name:
        experiment_name = (
            f"{DEFAULT_EXPERIMENT_NAME} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

    async def task(input_data):
        # Dataset rows may use different field names for the prompt
        prompt = (
            input_data.get("prompt")
            or input_data.get("input")
            or input_data.get("query")
            or input_data.get("message")
            or str(input_data)
            if isinstance(input_data, dict)
            else str(input_data)
        )
        try:
            return await client.chat(prompt)
        except Exception as e:
            print(f"Error: {e}")
            return f"ERROR: {e}"

    print(f"\n{'=' * 60}")
    print(f"Evaluation: {experiment_name}")
    print(f"Project: {PROJECT} | Dataset: {dataset_name} | API: {client.base_url}")
    print(f"{'=' * 60}\n")

    try:
        await EvalAsync(
            PROJECT,
            data=init_dataset(PROJECT, name=dataset_name),
            task=task,
            scores=scorers,
            experiment_name=experiment_name,
            metadata={
                "api_url": client.base_url,
                "timestamp": datetime.now().isoformat(),
            },
            max_concurrency=max_concurrency,
        )
        print(f"\nComplete! View results in Braintrust: {PROJECT} / {experiment_name}")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Run Braintrust evaluations for LC Chatbot",
        epilog="By default, runs against production. Use --local for local development server.",
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
        help="Experiment name (default: auto-generated with timestamp)",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=3,
        help="Max concurrent API calls (default: 3)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=f"Use local dev server ({LOCAL_API_URL}) instead of production",
    )
    parser.add_argument(
        "--scorers", "-s", help="Comma-separated list of Braintrust scorer slugs"
    )
    parser.add_argument(
        "--all-scorers",
        action="store_true",
        help="Use all scorers defined in Braintrust for this project",
    )
    args = parser.parse_args()

    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("ERROR: BRAINTRUST_API_KEY not set")
        sys.exit(1)

    braintrust.login()

    # Validate dataset exists
    if not validate_dataset(args.dataset):
        sys.exit(1)

    # Validate and create scorers
    scorers = None
    if args.all_scorers:
        scorers = get_all_scorers()
    elif args.scorers:
        scorer_slugs = [s.strip() for s in args.scorers.split(",")]
        if not validate_scorers(scorer_slugs):
            sys.exit(1)
        scorers = [create_scorer(s) for s in scorer_slugs]

    base_url = LOCAL_API_URL if args.local else PROD_API_URL
    client = ChatbotClient(base_url=base_url)

    asyncio.run(
        run_evaluation(client, args.dataset, args.experiment, scorers, args.concurrency)
    )


if __name__ == "__main__":
    main()
