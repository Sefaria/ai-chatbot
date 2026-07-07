#!/usr/bin/env python3
"""Run the Braintrust latency dataset locally with verbose agent-loop JSONL tracing.

Edit PROBE_CONFIG below, then run:

    python3 latency/scripts/run_agent_loop_probe.py

The script starts a local Django backend with AGENT_FILE_TRACE_ENABLED=1,
replays the configured Braintrust dataset against it, and writes:

    latency/runs/<run_id>/
      agent_debug_events.jsonl
      agent_debug_turn_summary.csv
      results.jsonl
      summary.json
      manifest.json
      backend.log
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import run_baseline_beta_from_braintrust as baseline
from analyze_agent_file_trace import load_events, summarize_turn, write_csv
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"
RUNS_DIR = REPO_ROOT / "latency" / "runs"
RECOMMENDED_WORKER_COUNT = 3


# =============================================================================
# Probe Configuration
# =============================================================================
#
# This is the main entry point for the agent-loop probe. Change values here,
# then run this file directly.
#
PROBE_CONFIG: dict[str, Any] = {
    # Braintrust dataset source.
    "project": os.environ.get("BRAINTRUST_PROJECT", baseline.DEFAULT_PROJECT),
    "dataset_name": baseline.DEFAULT_DATASET_NAME,
    "dataset_fetch_batch_size": 200,
    # Set to a small number such as 3 for smoke tests, or None for the full dataset.
    "limit": None,
    # Parallel request count.
    # Recommendation:
    #   1 = easiest trace reading
    #   3 = best default for the full dataset with heavy JSONL logging
    #   5+ = stress/concurrency testing after the probe is stable
    "max_concurrency": RECOMMENDED_WORKER_COUNT,
    "stop_on_error": False,
    # Local Django server.
    "start_server": True,
    "run_migrations": False,
    "host": "127.0.0.1",
    "port": 8001,
    "server_start_timeout_seconds": 60,
    # Output.
    "output_dir": RUNS_DIR,
    "run_id": None,
    "trace_filename": "agent_debug_events.jsonl",
    "turn_summary_filename": "agent_debug_turn_summary.csv",
    "backend_log_filename": "backend.log",
    # Auth for the local chatbot server.
    "user_id": "agent-loop-probe-user",
    "token_expires_in_days": 365,
    # Braintrust experiment logging for replay results. Keep this false when
    # you want the probe to be independent of Braintrust tracing/experiments.
    "write_braintrust_experiment": False,
}


def build_run_id(dataset_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = dataset_name.lower().replace(" ", "-").replace("/", "-").replace("_", "-")
    return f"{timestamp}-agent-loop-probe-{slug}"


def get_config() -> SimpleNamespace:
    config = dict(PROBE_CONFIG)
    run_id = config["run_id"] or build_run_id(str(config["dataset_name"]))
    output_dir = Path(config["output_dir"])
    run_dir = output_dir / run_id
    config["run_id"] = run_id
    config["output_dir"] = output_dir
    return SimpleNamespace(
        **config,
        run_dir=run_dir,
        base_url=f"http://{config['host']}:{config['port']}",
        trace_path=run_dir / str(config["trace_filename"]),
        turn_summary_path=run_dir / str(config["turn_summary_filename"]),
        backend_log_path=run_dir / str(config["backend_log_filename"]),
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def require_local_token(config: SimpleNamespace) -> str:
    token = os.environ.get("CHATBOT_USER_TOKEN_PROBE", "").strip()
    if token:
        return token

    secret = os.environ.get("CHATBOT_USER_TOKEN_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "CHATBOT_USER_TOKEN_SECRET is required in server/.env or the shell, "
            "or set CHATBOT_USER_TOKEN_PROBE to a prebuilt token."
        )
    token = baseline.generate_user_token(
        user_id=config.user_id,
        secret=secret,
        expires_in_days=int(config.token_expires_in_days),
    )
    os.environ["CHATBOT_USER_TOKEN_PROBE"] = token
    return token


async def wait_for_server(base_url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{base_url}/api/v2/prompts/defaults")
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"Server did not become ready at {base_url}: {last_error}")


def run_migrations(env: dict[str, str]) -> None:
    subprocess.run(
        [str(SERVER_DIR / "venv" / "bin" / "python"), "manage.py", "migrate"],
        cwd=SERVER_DIR,
        env=env,
        check=True,
    )


def start_server(config: SimpleNamespace, env: dict[str, str]) -> subprocess.Popen:
    config.backend_log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = config.backend_log_path.open("w", encoding="utf-8")
    cmd = [
        str(SERVER_DIR / "venv" / "bin" / "python"),
        "manage.py",
        "runserver",
        f"{config.host}:{config.port}",
        "--noreload",
    ]
    process = subprocess.Popen(
        cmd,
        cwd=SERVER_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


def build_server_env(config: SimpleNamespace) -> dict[str, str]:
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "chatbot_server.settings"
    env["AGENT_FILE_TRACE_ENABLED"] = "1"
    env["AGENT_FILE_TRACE_RUN_ID"] = config.run_id
    env["AGENT_FILE_TRACE_PATH"] = str(config.trace_path)
    return env


def configure_baseline_runner(config: SimpleNamespace) -> SimpleNamespace:
    baseline.SCRIPT_CONFIG.update(
        {
            "project": config.project,
            "dataset_name": config.dataset_name,
            "output_dir": config.output_dir,
            "beta_base_url": config.base_url,
            "dataset_fetch_batch_size": config.dataset_fetch_batch_size,
            "limit": config.limit,
            "stop_on_error": config.stop_on_error,
            "max_concurrency": config.max_concurrency,
            "user_id": config.user_id,
            "token_expires_in_days": config.token_expires_in_days,
            "braintrust_experiment_name": None,
        }
    )
    return baseline.get_config()


def write_probe_manifest(
    *,
    config: SimpleNamespace,
    fetched_rows: list[dict[str, Any]],
    results: list[dict[str, Any]] | None = None,
    experiment_info: dict[str, Any] | None = None,
) -> None:
    manifest = {
        "run_id": config.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "target": "local_agent_loop_probe",
        "dataset_name": config.dataset_name,
        "project": config.project,
        "fetched_row_count": len(fetched_rows),
        "result_count": len(results or []),
        "base_url": config.base_url,
        "host": socket.gethostname(),
        "python": sys.version,
        "cwd": str(REPO_ROOT),
        "trace_path": str(config.trace_path),
        "turn_summary_path": str(config.turn_summary_path),
        "backend_log_path": str(config.backend_log_path),
        "config": json_safe(PROBE_CONFIG),
    }
    if experiment_info:
        manifest["braintrust_experiment"] = experiment_info
    (config.run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_trace_summary(config: SimpleNamespace) -> None:
    events = load_events(config.trace_path)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        key = (event.get("run_id") or "", event.get("trace_id") or "")
        grouped.setdefault(key, []).append(event)
    rows = [summarize_turn(turn_events) for turn_events in grouped.values()]
    rows.sort(key=lambda row: (row["run_id"], row["trace_id"]))
    write_csv(rows, config.turn_summary_path)


async def run_probe() -> None:
    server_process: subprocess.Popen | None = None
    with tqdm(total=8, desc="Agent loop probe", unit="phase") as progress:
        config = get_config()
        if config.max_concurrency <= 0:
            raise RuntimeError("max_concurrency must be positive")
        progress.set_postfix_str("config")
        progress.update(1)

        config.run_dir.mkdir(parents=True, exist_ok=False)
        baseline_config = configure_baseline_runner(config)
        user_token = require_local_token(config)
        server_env = build_server_env(config)
        print(
            f"Using max_concurrency={config.max_concurrency}. "
            f"Recommended full-dataset default is {RECOMMENDED_WORKER_COUNT}; "
            "use 1 for the clearest single-threaded traces."
        )
        progress.set_postfix_str("auth/env")
        progress.update(1)

        fetched_rows = baseline.fetch_dataset_rows(baseline_config)
        items = [baseline.dataset_row_to_item(row) for row in fetched_rows]
        if config.limit is not None:
            items = items[: int(config.limit)]
        if not items:
            raise RuntimeError("No usable dataset items fetched from Braintrust")
        progress.set_postfix_str(f"dataset {len(items)}")
        progress.update(1)

        write_probe_manifest(config=config, fetched_rows=fetched_rows)
        progress.set_postfix_str("manifest")
        progress.update(1)

        try:
            if config.run_migrations:
                run_migrations(server_env)
            if config.start_server:
                server_process = start_server(config, server_env)
            progress.set_postfix_str("server start")
            progress.update(1)

            await wait_for_server(
                config.base_url, int(config.server_start_timeout_seconds)
            )
            progress.set_postfix_str("server ready")
            progress.update(1)

            results = await baseline.run_all_examples(
                items,
                bool(config.stop_on_error),
                int(config.max_concurrency),
                config.base_url,
                user_token,
            )
            baseline.write_results(config.run_dir, results)

            experiment_info = None
            if config.write_braintrust_experiment:
                experiment_info = await baseline.log_results_as_experiment(
                    config=baseline_config,
                    results=results,
                    run_id=config.run_id,
                )
            progress.set_postfix_str("replay done")
            progress.update(1)

            write_trace_summary(config)
            write_probe_manifest(
                config=config,
                fetched_rows=fetched_rows,
                results=results,
                experiment_info=experiment_info,
            )
            progress.set_postfix_str("outputs")
            progress.update(1)

            print(f"Wrote probe run to {config.run_dir}")
            print(f"Trace JSONL: {config.trace_path}")
            print(f"Turn summary CSV: {config.turn_summary_path}")
            print(
                json.dumps(
                    baseline.build_summary(results), indent=2, ensure_ascii=False
                )
            )
        finally:
            if server_process and server_process.poll() is None:
                server_process.terminate()
                try:
                    server_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server_process.kill()
                    server_process.wait(timeout=10)


def main() -> int:
    asyncio.run(run_probe())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
