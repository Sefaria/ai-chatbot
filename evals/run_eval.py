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
import subprocess
import sys
import time
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
        items = response_data.get("objects", response_data)
    else:
        items = response_data
    if isinstance(items, list):
        return [i for i in items if i is not None]
    return items


# Configuration
PROJECT = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
DEFAULT_DATASET = "Benchmark"
DEV_API_URL = "https://chat-dev.sefaria.org"  # main branch → dev deploy
PROD_API_URL = "https://chat.sefaria.org"  # production branch → prod deploy
LOCAL_API_URL = "http://localhost:8001"
USER_TOKEN = os.environ.get("CHATBOT_USER_TOKEN")
DEFAULT_EXPERIMENT_NAME = "Automated Eval"

# Braintrust's SDK caches a short-lived JWT after the first login() call and
# never refreshes it unless force_login=True. Long eval runs outlive the JWT,
# so we retry auth failures with a forced re-login and back off on other
# transient errors. One initial attempt + three retries (with 2s/5s/10s
# backoff between them) = SCORER_MAX_ATTEMPTS total.
SCORER_RETRY_DELAYS = (2, 5, 10)
SCORER_MAX_ATTEMPTS = 1 + len(SCORER_RETRY_DELAYS)

# Regression threshold: how much a scorer's pass rate can drop vs the pinned
# baseline before the run is considered out of tolerance. Override per scorer
# as needed once we have more data on expected variance.
REGRESSION_TOLERANCE = 0.10
SCORER_TOLERANCES: dict[str, float] = {}


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
            "context": {
                "origin": "eval",
            },
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


_AUTH_ERROR_NEEDLES = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "jwt",
    "access token",
    "token expired",
    "token has expired",
    "invalid token",
    "expired token",
)


def _is_braintrust_auth_error(exc: Exception) -> bool:
    """Return True if the exception looks like a 401/403 from Braintrust.

    Prefers a structured HTTP status (the SDK wraps `requests`, so HTTPError
    carries a `response` with a status code). Falls back to narrow message
    patterns — we avoid bare "token" because it matches unrelated errors like
    "token limit" or "tokenizer".
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    message = str(exc).lower()
    return any(needle in message for needle in _AUTH_ERROR_NEEDLES)


def create_scorer(slug: str):
    """
    Create a scorer function that invokes a Braintrust UI-defined scorer.

    Retries are required because Braintrust's SDK caches a JWT after the first
    login() and will not refresh it on its own. We force a fresh login after an
    auth-looking failure, and back off on other transient errors. Final failure
    re-raises so the Eval framework records an error row instead of a fake 0.0.
    """

    def scorer(output, expected=None, input=None, metadata=None):
        scorer_input = {"output": output}
        if expected is not None:
            scorer_input["expected"] = expected
        if input is not None:
            scorer_input["input"] = input
        if metadata is not None:
            scorer_input["metadata"] = metadata

        last_exc: Exception | None = None
        for attempt in range(SCORER_MAX_ATTEMPTS):
            force_login = last_exc is not None and _is_braintrust_auth_error(last_exc)
            try:
                result = invoke(
                    project_name=PROJECT,
                    slug=slug,
                    input=scorer_input,
                    force_login=force_login,
                )
                if isinstance(result, dict):
                    if "score" not in result:
                        raise ValueError(
                            f"Scorer {slug} returned dict without 'score': {result}"
                        )
                    return result["score"]
                return result
            except ValueError:
                # Malformed scorer response is deterministic — retrying won't help.
                raise
            except Exception as e:
                last_exc = e
                print(
                    f"Scorer {slug} attempt {attempt + 1}/{SCORER_MAX_ATTEMPTS} "
                    f"failed ({type(e).__name__}): {e}"
                )
                if attempt < SCORER_MAX_ATTEMPTS - 1:
                    time.sleep(SCORER_RETRY_DELAYS[attempt])

        assert last_exc is not None
        raise last_exc

    scorer.__name__ = slug.replace("-", "_")
    return scorer


def get_current_branch() -> str:
    """Return the current git branch name, embedded in experiment metadata so
    CI can find this run again on merge and pin it as the new baseline."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def fetch_pinned_experiment() -> dict | None:
    """Fetch the currently pinned baseline experiment for this project from
    Braintrust. The baseline is stored in project settings as baseline_experiment_id."""
    try:
        conn = braintrust.api_conn()
        r = conn.get("/v1/project", params={"project_name": PROJECT})
        if not r.ok:
            return None
        projects = extract_braintrust_items(r.json())
        project = next((p for p in projects if p.get("name") == PROJECT), None)
        if not project:
            return None
        baseline_id = (project.get("settings") or {}).get("baseline_experiment_id")
        if not baseline_id:
            return None
        r2 = conn.get(f"/v1/experiment/{baseline_id}")
        return r2.json() if r2.ok else None
    except Exception:
        return None


def fetch_experiment_scores(experiment_id: str) -> dict:
    """Fetch per-scorer pass rates for a given experiment from the Braintrust
    summary endpoint. Returns a dict of scorer name → score stats."""
    try:
        response = braintrust.api_conn().get(f"/v1/experiment/{experiment_id}/summary")
        if not response.ok:
            return {}
        return response.json().get("scores", {})
    except Exception:
        return {}


def _get_mean(score_val) -> float | None:
    """Extract a scalar mean from a score value regardless of its shape.
    The SDK returns objects with a .mean attribute; the REST API returns dicts
    with a 'mean' key. Both are handled here so callers don't need to care."""
    if score_val is None:
        return None
    if isinstance(score_val, (int, float)):
        return float(score_val)
    if isinstance(score_val, dict):
        return score_val["mean"] if "mean" in score_val else score_val.get("score")
    if hasattr(score_val, "mean"):
        return _get_mean(score_val.mean)
    try:
        return float(score_val)
    except (TypeError, ValueError):
        return None


def _normalize(name: str) -> str:
    """Normalize a scorer name to a consistent key for comparison.
    Braintrust uses hyphens in scorer slugs but Python function names use
    underscores, so both forms must map to the same key."""
    return name.replace("-", "_").lower()


def analyze_threshold(current_scores: dict) -> None:
    """Compare the current eval run's scores against the pinned baseline and
    print a pass/fail table. Each scorer is checked against REGRESSION_TOLERANCE
    (default 10%); per-scorer overrides can be set in SCORER_TOLERANCES.

    Prints READY TO MERGE if all scorers are within tolerance, or NOT READY TO
    MERGE with a reviewer note if any scorer regressed beyond the threshold."""
    baseline = fetch_pinned_experiment()
    baseline_scores: dict = {}
    if baseline:
        baseline_scores = fetch_experiment_scores(baseline["id"])

    normalized_baseline = {
        _normalize(k): _get_mean(v) for k, v in baseline_scores.items()
    }
    if baseline and not normalized_baseline:
        print("WARNING: Baseline experiment found but scores could not be fetched — comparison skipped.")

    print(f"\n{'=' * 60}")
    print("THRESHOLD ANALYSIS")
    if baseline:
        print(
            f"Baseline: {baseline.get('name', baseline['id'])} | Tolerance: {REGRESSION_TOLERANCE:.0%}"
        )
        total_baseline = len(normalized_baseline)
        total_current = len(current_scores)
        if total_current < total_baseline:
            print(
                f"Comparing {total_current} of {total_baseline} scorers (partial run)"
            )
    else:
        print("No baseline set — merge this branch to establish the first baseline.")
    print(f"{'=' * 60}")

    if not current_scores:
        print("No scores available for analysis.")
        return

    failures: list[str] = []
    col = 35

    if baseline and normalized_baseline:
        print(
            f"{'Scorer':<{col}} {'Baseline':>10} {'Current':>10} {'Delta':>9}  Status"
        )
        print("-" * (col + 44))
        for scorer_name in sorted(current_scores):
            current_mean = _get_mean(current_scores[scorer_name])
            baseline_mean = normalized_baseline.get(_normalize(scorer_name))
            tolerance = SCORER_TOLERANCES.get(
                _normalize(scorer_name), REGRESSION_TOLERANCE
            )

            if current_mean is None:
                continue
            if baseline_mean is None:
                print(
                    f"{scorer_name:<{col}} {'N/A':>10} {current_mean:>10.1%} {'N/A':>9}  NEW"
                )
                continue

            delta = current_mean - baseline_mean
            status = "FAIL" if delta < -tolerance else "PASS"
            if status == "FAIL":
                failures.append(scorer_name)
            print(
                f"{scorer_name:<{col}} {baseline_mean:>10.1%} {current_mean:>10.1%} {delta:>+9.1%}  {status}"
            )
    else:
        print(f"{'Scorer':<{col}} {'Score':>10}")
        print("-" * (col + 12))
        for scorer_name in sorted(current_scores):
            current_mean = _get_mean(current_scores[scorer_name])
            if current_mean is not None:
                print(f"{scorer_name:<{col}} {current_mean:>10.1%}")

    print(f"{'=' * 60}")

    if failures:
        count = len(failures)
        print(
            f"NOT READY TO MERGE: ({count} scorer(s) exceeded {REGRESSION_TOLERANCE:.0%} threshold for regression). "
            f"NOTE: The code changes must be reviewed by a member of the eval team "
            f"before merging due to this regression."
        )
    elif baseline:
        print("READY TO MERGE: All scorers within tolerance.")
    else:
        print(
            "No baseline to compare against — this run will become the baseline on merge."
        )


def pin_baseline_for_branch(branch: str) -> None:
    """Pin the most recent experiment for a given branch as the project baseline.

    Called by CI on merge to main. Looks up all experiments whose metadata
    includes the source branch name, picks the latest by creation date, unpins
    the previous baseline, and pins the new one. If no experiment exists for the
    branch (i.e. the developer never ran evals), the baseline is left unchanged."""
    braintrust.login()

    response = braintrust.api_conn().get(
        "/v1/experiment", params={"project_name": PROJECT}
    )
    if not response.ok:
        print(f"ERROR: Could not fetch experiments: {response.status_code}")
        sys.exit(1)

    experiments = extract_braintrust_items(response.json())
    branch_experiments = [
        e for e in experiments if (e.get("metadata") or {}).get("branch") == branch
    ]

    if not branch_experiments:
        print(f"No experiments found for branch '{branch}'. Baseline unchanged.")
        return

    branch_experiments.sort(key=lambda e: e.get("created", ""), reverse=True)
    latest = branch_experiments[0]

    r_project = braintrust.api_conn().get(
        "/v1/project", params={"project_name": PROJECT}
    )
    if not r_project.ok:
        print(f"ERROR: Could not fetch project: {r_project.status_code}")
        sys.exit(1)
    projects = extract_braintrust_items(r_project.json())
    project = next((p for p in projects if p.get("name") == PROJECT), None)
    if not project:
        print(f"ERROR: Project '{PROJECT}' not found")
        sys.exit(1)

    conn = braintrust.api_conn()
    resp = conn.session.patch(
        conn.base_url.rstrip("/") + f"/v1/project/{project['id']}",
        json={"settings": {"baseline_experiment_id": latest["id"]}},
        headers={"Authorization": f"Bearer {conn.token}"},
    )
    if resp.ok:
        print(f"Pinned '{latest.get('name', latest['id'])}' as the new baseline.")
    else:
        print(f"ERROR: Could not pin experiment: {resp.status_code} {resp.text[:200]}")
        sys.exit(1)


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
            f"I can't find that scorer, please double check and try again: {', '.join(invalid_scorer)}"
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
        result = await EvalAsync(
            PROJECT,
            data=init_dataset(PROJECT, name=dataset_name),
            task=task,
            scores=scorers,
            experiment_name=experiment_name,
            metadata={
                "api_url": client.base_url,
                "timestamp": datetime.now().isoformat(),
                "branch": get_current_branch(),
            },
            max_concurrency=max_concurrency,
        )
        print(f"\nComplete! View results in Braintrust: {PROJECT} / {experiment_name}")
        if result and hasattr(result, "summary") and result.summary:
            analyze_threshold(result.summary.scores)
        else:
            print("WARNING: Could not retrieve scores for threshold analysis.")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Run Braintrust evaluations for LC Chatbot",
        epilog="By default, runs against dev (main branch). Use --prod for production or --local for localhost.",
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
        "--prod",
        action="store_true",
        help=f"Run against production ({PROD_API_URL}) instead of dev",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=f"Run against local dev server ({LOCAL_API_URL})",
    )
    parser.add_argument(
        "--scorers", "-s", help="Comma-separated list of Braintrust scorer slugs"
    )
    parser.add_argument(
        "--all-scorers",
        action="store_true",
        help="Use all scorers defined in Braintrust for this project",
    )
    parser.add_argument(
        "--pin-baseline",
        metavar="BRANCH",
        help="Pin the latest experiment for BRANCH as the baseline (used by CI on merge)",
    )
    args = parser.parse_args()

    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("ERROR: BRAINTRUST_API_KEY not set")
        sys.exit(1)

    if args.pin_baseline:
        pin_baseline_for_branch(args.pin_baseline)
        return

    braintrust.login()

    # Validate dataset exists
    if not validate_dataset(args.dataset):
        sys.exit(1)

    # Validate and create scorers
    if args.scorers:
        scorer_slugs = [s.strip() for s in args.scorers.split(",")]
        if not validate_scorers(scorer_slugs):
            sys.exit(1)
        scorers = [create_scorer(s) for s in scorer_slugs]
    else:
        scorers = get_all_scorers()

    if args.local:
        base_url = LOCAL_API_URL
    elif args.prod:
        base_url = PROD_API_URL
    else:
        base_url = DEV_API_URL
    client = ChatbotClient(base_url=base_url)

    asyncio.run(
        run_evaluation(client, args.dataset, args.experiment, scorers, args.concurrency)
    )


if __name__ == "__main__":
    main()
