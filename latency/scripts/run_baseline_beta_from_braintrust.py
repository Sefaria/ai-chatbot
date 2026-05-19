#!/usr/bin/env python3
"""Run a Braintrust-hosted replay dataset against chat-beta and store metrics.

This script:
1. loads a replay-bundle dataset from Braintrust
2. generates or reads a beta user token from `server/.env.beta.local`
3. sends each question to `https://chat-beta.sefaria.org/api/chat/stream`
4. stores per-example results under `latency/runs/<run-id>/`

Note:
The beta endpoint does not accept direct `summary_text` / `page_url` injection.
Those fields are preserved in the local result payload for analysis, but the
remote beta run is only faithful to the dataset's user message.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import braintrust
import httpx
from braintrust import EvalAsync
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
RUNS_DIR = REPO_ROOT / "latency" / "runs"
ENV_FILE = SERVER_DIR / ".env"
BETA_ENV_FILE = SERVER_DIR / ".env.beta.local"
DEFAULT_PROJECT = "On Site Agent"
DEFAULT_DATASET_NAME = "Latency Replay Dataset - Prod Sample - 2026-05"
SECTION_SEPARATOR = "\n\n"
CONVERSATION_SUMMARY_SECTION = "Conversation summary:\n{summary_text}"
PAGE_CONTEXT_SECTION = (
    "Page context:\n"
    "The user is currently on the Sefaria page: {page_url}. "
    "If the context is relevant, use that information in your response."
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    cleaned_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(";")
    ]
    for key, value in dotenv_values(stream=StringIO("\n".join(cleaned_lines))).items():
        if value is not None:
            os.environ.setdefault(key, value)


load_env_file(ENV_FILE)
load_env_file(BETA_ENV_FILE)

SCRIPT_CONFIG = {
    "project": os.environ.get("BRAINTRUST_PROJECT", DEFAULT_PROJECT),
    "dataset_name": DEFAULT_DATASET_NAME,
    "output_dir": RUNS_DIR,
    "beta_base_url": os.environ.get(
        "CHATBOT_BETA_BASE_URL", "https://chat-beta.sefaria.org"
    ),
    "dataset_fetch_batch_size": 200,
    "limit": None,
    "stop_on_error": False,
    "max_concurrency": 8,
    "user_id": "eval-user",
    "token_expires_in_days": 365,
    "braintrust_experiment_name": None,
}


def get_config() -> SimpleNamespace:
    return SimpleNamespace(
        project=SCRIPT_CONFIG["project"],
        dataset_name=SCRIPT_CONFIG["dataset_name"],
        output_dir=Path(SCRIPT_CONFIG["output_dir"]),
        beta_base_url=SCRIPT_CONFIG["beta_base_url"],
        dataset_fetch_batch_size=SCRIPT_CONFIG["dataset_fetch_batch_size"],
        limit=SCRIPT_CONFIG["limit"],
        stop_on_error=SCRIPT_CONFIG["stop_on_error"],
        max_concurrency=SCRIPT_CONFIG["max_concurrency"],
        user_id=SCRIPT_CONFIG["user_id"],
        token_expires_in_days=SCRIPT_CONFIG["token_expires_in_days"],
        braintrust_experiment_name=SCRIPT_CONFIG["braintrust_experiment_name"],
    )


def require_api_key() -> str:
    api_key = os.environ.get("BRAINTRUST_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BRAINTRUST_API_KEY is required")
    return api_key


def extract_braintrust_items(response_data: Any) -> list[dict[str, Any]]:
    if isinstance(response_data, dict):
        items = response_data.get("objects", response_data)
    else:
        items = response_data
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def build_run_id(dataset_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = dataset_name.lower().replace(" ", "-").replace("/", "-").replace("_", "-")
    return f"{timestamp}-{slug}"


def ensure_run_dir(output_dir: Path, run_id: str) -> Path:
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] == "error"]

    harness_ms = sorted(r["metrics"]["harness_latency_ms"] for r in successes)
    first_event_ms = sorted(
        r["metrics"]["first_event_latency_ms"]
        for r in successes
        if r["metrics"]["first_event_latency_ms"] is not None
    )
    agent_ms = sorted(
        r["metrics"]["agent_latency_ms"]
        for r in successes
        if r["metrics"]["agent_latency_ms"] is not None
    )

    return {
        "total_examples": len(results),
        "successes": len(successes),
        "failures": len(failures),
        "harness_latency_ms": {
            "mean": statistics.fmean(harness_ms) if harness_ms else None,
            "p50": percentile(harness_ms, 0.50),
            "p95": percentile(harness_ms, 0.95),
            "max": max(harness_ms) if harness_ms else None,
        },
        "first_event_latency_ms": {
            "mean": statistics.fmean(first_event_ms) if first_event_ms else None,
            "p50": percentile(first_event_ms, 0.50),
            "p95": percentile(first_event_ms, 0.95),
            "max": max(first_event_ms) if first_event_ms else None,
        },
        "agent_latency_ms": {
            "mean": statistics.fmean(agent_ms) if agent_ms else None,
            "p50": percentile(agent_ms, 0.50),
            "p95": percentile(agent_ms, 0.95),
            "max": max(agent_ms) if agent_ms else None,
        },
    }


def default_experiment_name(config: SimpleNamespace, run_id: str) -> str:
    if config.braintrust_experiment_name:
        return str(config.braintrust_experiment_name)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"Beta Baseline - {config.dataset_name} - {timestamp} - {run_id.split('-', 1)[0]}"


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def generate_user_token(user_id: str, secret: str, expires_in_days: int = 365) -> str:
    expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
    payload = {"id": user_id, "expiration": expires_at.isoformat()}
    payload_bytes = json.dumps(payload).encode("utf-8")

    key = hashlib.sha256(secret.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    encrypted = aesgcm.encrypt(nonce, payload_bytes, None)
    token_bytes = nonce + encrypted
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


def get_beta_user_token(config: SimpleNamespace) -> str:
    token = os.environ.get("CHATBOT_USER_TOKEN_BETA", "").strip()
    if token:
        return token

    secret = os.environ.get("CHATBOT_USER_TOKEN_SECRET_BETA", "").strip()
    if not secret:
        raise RuntimeError(
            "Set CHATBOT_USER_TOKEN_SECRET_BETA or CHATBOT_USER_TOKEN_BETA in "
            "server/.env.beta.local"
        )

    token = generate_user_token(
        user_id=config.user_id,
        secret=secret,
        expires_in_days=config.token_expires_in_days,
    )
    os.environ["CHATBOT_USER_TOKEN_BETA"] = token
    return token


def fetch_dataset_rows(config: SimpleNamespace) -> list[dict[str, Any]]:
    require_api_key()
    braintrust.login()
    dataset = braintrust.init_dataset(project=config.project, name=config.dataset_name)
    rows = list(dataset.fetch(batch_size=config.dataset_fetch_batch_size))
    if not rows:
        raise RuntimeError(
            f"Braintrust dataset {config.dataset_name!r} in project {config.project!r} is empty"
        )
    return rows


def dataset_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    input_payload = row.get("input") or {}
    metadata = row.get("metadata") or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    if not isinstance(metadata, dict):
        metadata = {}

    question = input_payload.get("message")
    if not isinstance(question, str) or not question.strip():
        raise RuntimeError(f"Dataset row {row.get('id')} is missing input.message")

    return {
        "question": question,
        "summary_text": input_payload.get("summary_text"),
        "page_url": input_payload.get("page_url"),
        "root_span_id": metadata.get("root_span_id") or row.get("id"),
        "created": metadata.get("created"),
        "session_id": metadata.get("session_id"),
        "origin": metadata.get("origin"),
        "model": metadata.get("model"),
        "route": metadata.get("route"),
        "effective_core_prompt_id": metadata.get("effective_core_prompt_id"),
        "effective_core_prompt_version": metadata.get("effective_core_prompt_version"),
        "summary_included": metadata.get("summary_included"),
        "dataset_row_id": row.get("id"),
        "dataset_tags": row.get("tags"),
    }


def build_experiment_cases(
    results: list[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for result in results:
        item = result["input"]
        request = result.get("request") or {}
        cases.append(
            {
                "input": {
                    "dataset_row_id": item.get("dataset_row_id"),
                    "root_span_id": item.get("root_span_id"),
                    "question": item.get("question"),
                    "summary_text": item.get("summary_text"),
                    "page_url": item.get("page_url"),
                    "user_message": request.get("user_message"),
                },
                "metadata": {
                    "run_id": run_id,
                    "index": result.get("index"),
                    "session_id": item.get("session_id"),
                    "origin": item.get("origin"),
                    "model": item.get("model"),
                    "route": item.get("route"),
                    "effective_core_prompt_id": item.get("effective_core_prompt_id"),
                    "effective_core_prompt_version": item.get(
                        "effective_core_prompt_version"
                    ),
                    "summary_included": item.get("summary_included"),
                    "request_user_message_char_count": request.get(
                        "user_message_char_count"
                    ),
                    "request_question_char_count": request.get("question_char_count"),
                },
                "tags": item.get("dataset_tags") or [],
            }
        )
    return cases


async def log_results_as_experiment(
    *,
    config: SimpleNamespace,
    results: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any] | None:
    experiment_name = default_experiment_name(config, run_id)
    result_map = {
        result["input"].get("dataset_row_id")
        or result["input"].get("root_span_id"): result
        for result in results
    }
    cases = build_experiment_cases(results, run_id)

    async def task(input_data: dict[str, Any]) -> dict[str, Any]:
        row_id = input_data.get("dataset_row_id") or input_data.get("root_span_id")
        result = result_map[row_id]
        output: dict[str, Any] = {
            "status": result["status"],
            "request": result.get("request"),
            "trace_id": result.get("trace_id"),
            "metrics": result.get("metrics"),
        }
        if result["status"] == "success":
            output["response"] = result.get("output")
        else:
            output["error"] = result.get("error")
        return output

    eval_result = await EvalAsync(
        config.project,
        data=cases,
        task=task,
        scores=[],
        experiment_name=experiment_name,
        metadata={
            "run_type": "beta_baseline",
            "dataset_name": config.dataset_name,
            "api_url": config.beta_base_url,
            "run_id": run_id,
            "target": "beta",
            "prompt_format": "summary_prefixed_user_message",
            "source": "run_baseline_beta_from_braintrust.py",
        },
        max_concurrency=config.max_concurrency,
        summarize_scores=False,
    )

    summary = getattr(eval_result, "summary", None)
    if summary is None:
        return {"experiment_name": experiment_name}
    return {
        "experiment_name": getattr(summary, "experiment_name", experiment_name),
        "experiment_id": getattr(summary, "experiment_id", None),
        "experiment_url": getattr(summary, "experiment_url", None),
        "project_name": getattr(summary, "project_name", config.project),
        "project_id": getattr(summary, "project_id", None),
        "project_url": getattr(summary, "project_url", None),
    }


def build_beta_user_message(item: dict[str, Any]) -> str:
    parts: list[str] = []
    summary_text = item.get("summary_text")
    page_url = item.get("page_url")
    question = item["question"]

    if isinstance(summary_text, str) and summary_text.strip():
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
    if isinstance(page_url, str) and page_url.strip():
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))
    parts.append(question)
    return SECTION_SEPARATOR.join(parts)


async def run_example_remote_beta(
    item: dict[str, Any],
    index: int,
    client: httpx.AsyncClient,
    base_url: str,
    user_token: str,
) -> dict[str, Any]:
    core_prompt_id = item.get("effective_core_prompt_id")
    user_message = build_beta_user_message(item)
    request_record = {
        "user_message": user_message,
        "user_message_char_count": len(user_message),
        "question_char_count": len(item.get("question") or ""),
        "summary_included": bool(item.get("summary_text")),
        "page_url_included": bool(item.get("page_url")),
        "core_prompt_id": core_prompt_id,
    }
    session_id = f"eval_{uuid.uuid4().hex[:16]}"
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "messageId": message_id,
        "text": user_message,
        "userId": user_token,
        "timestamp": datetime.now(UTC).isoformat(),
        "context": {"origin": "eval"},
    }
    if core_prompt_id:
        payload["context"]["corePromptSlug"] = core_prompt_id

    start = time.perf_counter()
    first_event_latency_ms: int | None = None
    current_event: str | None = None
    final_response: dict[str, Any] | None = None
    async with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/api/chat/stream",
        json=payload,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if first_event_latency_ms is None and line.strip():
                first_event_latency_ms = int((time.perf_counter() - start) * 1000)
            if line.startswith("event: "):
                current_event = line[7:]
                continue
            if not line.startswith("data: "):
                continue
            try:
                parsed = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if current_event == "error":
                raise RuntimeError(parsed.get("error", "Unknown beta stream error"))
            if "markdown" in parsed:
                final_response = parsed

    harness_latency_ms = int((time.perf_counter() - start) * 1000)
    if final_response is None:
        raise RuntimeError("No final response received from beta stream")

    stats = final_response.get("stats") or {}
    content_text = final_response.get("markdown", "")

    return {
        "index": index,
        "status": "success",
        "input": item,
        "request": request_record,
        "trace_id": final_response.get("traceId"),
        "output": {
            "content": content_text,
            "tool_calls": final_response.get("toolCalls"),
        },
        "metrics": {
            "harness_latency_ms": harness_latency_ms,
            "first_event_latency_ms": first_event_latency_ms,
            "agent_latency_ms": stats.get("latencyMs"),
            "llm_calls": stats.get("llmCalls"),
            "input_tokens": stats.get("inputTokens"),
            "output_tokens": stats.get("outputTokens"),
            "cache_creation_tokens": stats.get("cacheCreationTokens"),
            "cache_read_tokens": stats.get("cacheReadTokens"),
            "total_cost_usd": stats.get("totalCostUsd"),
            "tool_calls_count": stats.get("toolCalls"),
        },
    }


async def run_all_examples(
    items: list[dict[str, Any]],
    stop_on_error: bool,
    max_concurrency: int,
    beta_base_url: str,
    beta_user_token: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(max_concurrency)
    progress = tqdm(total=len(items), desc="Running beta baseline", unit="example")
    client = httpx.AsyncClient(timeout=300.0)

    async def run_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            core_prompt_id = item.get("effective_core_prompt_id")
            user_message = build_beta_user_message(item)
            request_record = {
                "user_message": user_message,
                "user_message_char_count": len(user_message),
                "question_char_count": len(item.get("question") or ""),
                "summary_included": bool(item.get("summary_text")),
                "page_url_included": bool(item.get("page_url")),
                "core_prompt_id": core_prompt_id,
            }
            try:
                return await run_example_remote_beta(
                    item, index, client, beta_base_url, beta_user_token
                )
            except Exception as exc:
                result = {
                    "index": index,
                    "status": "error",
                    "input": item,
                    "request": request_record,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
                if stop_on_error:
                    raise RuntimeError(json.dumps(result, ensure_ascii=False)) from exc
                return result

    tasks = [
        asyncio.create_task(run_one(index, item)) for index, item in enumerate(items)
    ]
    try:
        for task in asyncio.as_completed(tasks):
            try:
                result = await task
            except RuntimeError as exc:
                result = json.loads(str(exc))
                results.append(result)
                progress.update(1)
                raise
            results.append(result)
            progress.update(1)
    finally:
        progress.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()

    results.sort(key=lambda result: result["index"])
    return results


def write_manifest(
    run_dir: Path,
    config: SimpleNamespace,
    fetched_rows: list[dict[str, Any]],
    experiment_info: dict[str, Any] | None = None,
) -> None:
    manifest = {
        "run_id": run_dir.name,
        "created_at": datetime.now(UTC).isoformat(),
        "target": "beta",
        "dataset_name": config.dataset_name,
        "project": config.project,
        "fetched_row_count": len(fetched_rows),
        "beta_base_url": config.beta_base_url,
        "host": socket.gethostname(),
        "python": sys.version,
        "cwd": str(REPO_ROOT),
        "config": json_safe(SCRIPT_CONFIG),
    }
    if experiment_info:
        manifest["braintrust_experiment"] = experiment_info
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_results(run_dir: Path, results: list[dict[str, Any]]) -> None:
    results_path = run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    summary = build_summary(results)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    config = get_config()
    if config.max_concurrency <= 0:
        raise RuntimeError("max_concurrency must be positive")

    fetched_rows = fetch_dataset_rows(config)
    items = [dataset_row_to_item(row) for row in fetched_rows]
    if config.limit is not None:
        items = items[: config.limit]
    if not items:
        raise RuntimeError("No usable dataset items fetched from Braintrust")

    beta_user_token = get_beta_user_token(config)

    run_id = build_run_id(config.dataset_name)
    run_dir = ensure_run_dir(config.output_dir, run_id)
    write_manifest(run_dir, config, fetched_rows)

    results = asyncio.run(
        run_all_examples(
            items,
            config.stop_on_error,
            config.max_concurrency,
            config.beta_base_url,
            beta_user_token,
        )
    )
    write_results(run_dir, results)
    experiment_info = asyncio.run(
        log_results_as_experiment(config=config, results=results, run_id=run_id)
    )
    write_manifest(run_dir, config, fetched_rows, experiment_info=experiment_info)

    print(f"Wrote run results to {run_dir}")
    if experiment_info:
        print(
            json.dumps(
                {"braintrust_experiment": experiment_info},
                indent=2,
                ensure_ascii=False,
            )
        )
    print(json.dumps(build_summary(results), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
