#!/usr/bin/env python3
"""Run a saved latency baseline locally and store metrics on disk.

This script replays baseline items directly through the agent runtime so the
saved `summary_text` and `page_url` can be injected faithfully. Results are
stored under `latency/baseline/runs/<run-id>/`.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import statistics
import sys
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import django
from dotenv import dotenv_values
from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "server"
RUNS_DIR = REPO_ROOT / "latency" / "baseline" / "runs"
ENV_FILE = SERVER_DIR / ".env"

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


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
# Force this repo's Django settings even if the shell is currently pointed at
# another project.
os.environ["DJANGO_SETTINGS_MODULE"] = "chatbot_server.settings"
django.setup()

from chat.V2.agent import ClaudeAgentService, ConversationMessage, MessageContext  # noqa: E402


SCRIPT_CONFIG = {
    "baseline_file": REPO_ROOT
    / "latency"
    / "baseline"
    / "data"
    / "on-site-agent-n10-seed1.json",
    "output_dir": RUNS_DIR,
    "limit": None,
    "stop_on_error": False,
    "max_concurrency": 3,
}


def get_config() -> SimpleNamespace:
    return SimpleNamespace(
        baseline_file=Path(SCRIPT_CONFIG["baseline_file"]),
        output_dir=Path(SCRIPT_CONFIG["output_dir"]),
        limit=SCRIPT_CONFIG["limit"],
        stop_on_error=SCRIPT_CONFIG["stop_on_error"],
        max_concurrency=SCRIPT_CONFIG["max_concurrency"],
    )


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Baseline file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_run_id(baseline_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{baseline_path.stem}"


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
    agent_ms = sorted(r["metrics"]["agent_latency_ms"] for r in successes)

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
        "agent_latency_ms": {
            "mean": statistics.fmean(agent_ms) if agent_ms else None,
            "p50": percentile(agent_ms, 0.50),
            "p95": percentile(agent_ms, 0.95),
            "max": max(agent_ms) if agent_ms else None,
        },
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def item_to_context(item: dict[str, Any]) -> MessageContext:
    return MessageContext(
        summary_text=item.get("summary_text"),
        page_url=item.get("page_url"),
        session_id=item.get("session_id"),
        origin=item.get("origin"),
    )


async def run_example(
    item: dict[str, Any],
    index: int,
    agent_services: dict[str, ClaudeAgentService],
) -> dict[str, Any]:
    model = item.get("model") or os.environ.get("AGENT_MODEL")
    if model not in agent_services:
        agent_services[model] = ClaudeAgentService(model=model)
    agent = agent_services[model]

    core_prompt_id = item.get("effective_core_prompt_id")
    start = time.perf_counter()
    response = await agent.send_message(
        messages=[ConversationMessage(role="user", content=item["question"])],
        core_prompt_id=core_prompt_id,
        context=item_to_context(item),
    )
    harness_latency_ms = int((time.perf_counter() - start) * 1000)

    return {
        "index": index,
        "status": "success",
        "input": item,
        "trace_id": response.trace_id,
        "output": {
            "content": response.content,
            "tool_calls": response.tool_calls,
        },
        "metrics": {
            "harness_latency_ms": harness_latency_ms,
            "agent_latency_ms": response.latency_ms,
            "llm_calls": response.llm_calls,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cache_creation_tokens": response.cache_creation_tokens,
            "cache_read_tokens": response.cache_read_tokens,
            "total_cost_usd": response.total_cost_usd,
            "tool_calls_count": len(response.tool_calls),
        },
    }


async def run_all_examples(
    items: list[dict[str, Any]], stop_on_error: bool, max_concurrency: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    agent_services: dict[str, ClaudeAgentService] = {}
    semaphore = asyncio.Semaphore(max_concurrency)
    progress = tqdm(total=len(items), desc="Running baseline", unit="example")

    async def run_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await run_example(item, index, agent_services)
            except Exception as exc:
                result = {
                    "index": index,
                    "status": "error",
                    "input": item,
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
        await asyncio.gather(
            *(service.close() for service in agent_services.values()),
            return_exceptions=True,
        )

    results.sort(key=lambda result: result["index"])
    return results


def write_manifest(
    run_dir: Path, baseline_path: Path, baseline_data: dict[str, Any]
) -> None:
    manifest = {
        "run_id": run_dir.name,
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_file": str(baseline_path),
        "baseline_name": baseline_path.name,
        "sample_size": len(baseline_data.get("questions", [])),
        "host": socket.gethostname(),
        "python": sys.version,
        "cwd": str(REPO_ROOT),
        "config": json_safe(SCRIPT_CONFIG),
    }
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
    args = get_config()
    if args.max_concurrency <= 0:
        raise RuntimeError("max_concurrency must be positive")
    baseline_data = load_baseline(args.baseline_file)
    items = baseline_data.get("questions") or []
    if not isinstance(items, list) or not items:
        raise RuntimeError("Baseline file has no questions")
    if args.limit is not None:
        items = items[: args.limit]

    run_id = build_run_id(args.baseline_file)
    run_dir = ensure_run_dir(args.output_dir, run_id)
    write_manifest(run_dir, args.baseline_file, baseline_data)

    results = asyncio.run(
        run_all_examples(items, args.stop_on_error, args.max_concurrency)
    )
    write_results(run_dir, results)

    print(f"Wrote run results to {run_dir}")
    print(json.dumps(build_summary(results), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
