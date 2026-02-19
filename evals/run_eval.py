"""
Braintrust Evaluation Script for LC Chatbot

Runs the chatbot against a Braintrust dataset and scores responses using
custom scorers defined in the Braintrust UI. Results are logged to Braintrust
for analysis and comparison across experiments.

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
            return result.get("score", 0.0) if isinstance(result, dict) else result
        except Exception as e:
            print(f"Error running scorer {slug}: {e}")
            return 0.0

    scorer.__name__ = slug.replace("-", "_")
    return scorer


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
        functions = data.get("objects", data) if isinstance(data, dict) else data
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
    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("ERROR: BRAINTRUST_API_KEY not set")
        sys.exit(1)

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

    base_url = LOCAL_API_URL if args.local else PROD_API_URL
    client = ChatbotClient(base_url=base_url)

    scorers = None
    if args.all_scorers:
        scorers = get_all_scorers()
    elif args.scorers:
        braintrust.login()
        scorers = [create_scorer(s.strip()) for s in args.scorers.split(",")]

    asyncio.run(
        run_evaluation(client, args.dataset, args.experiment, scorers, args.concurrency)
    )


if __name__ == "__main__":
    main()
