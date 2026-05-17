#!/usr/bin/env python3
"""Build latency dataframes for a Braintrust experiment by joining trace spans.

This exporter starts from one Braintrust experiment, extracts the per-row
`trace_id` values, fetches the corresponding trace rows and child spans from
Braintrust project logs, and writes:

1. `trace_rows.csv/jsonl` — one raw root row per trace
2. `span_rows.csv/jsonl` — one raw span row per attached span
3. `trace_latency_partition_rows.csv/jsonl` — one row per trace with a
   mutually exclusive wall-clock latency partition

The partition table keeps the raw wall-clock buckets and also adds a heuristic
LLM-context split:

- planning
- tool_analysis
- final_generation
- other
- unknown_context
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import braintrust
import requests
from dotenv import dotenv_values
from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPO_ROOT / "latency" / "analysis"
DATA_DIR = ANALYSIS_ROOT
ENV_FILE = REPO_ROOT / "server" / ".env"
DEFAULT_PROJECT = "On Site Agent"
DEFAULT_PAGE_SIZE = 500
DEFAULT_ROOT_ID_BATCH_SIZE = 25
ROOT_SPAN_NAME = "chat-agent"
TOOL_ANALYSIS_WINDOW_MS = 2000
PARTITION_COMPONENT_COLUMNS = [
    "guardrail_wall_ms",
    "router_wall_ms",
    "llm_ttft_wall_ms",
    "llm_post_first_token_wall_ms",
    "llm_unknown_wall_ms",
    "tool_wall_ms",
    "score_wall_ms",
    "facet_wall_ms",
    "classifier_wall_ms",
    "function_wall_ms",
    "automation_wall_ms",
    "agent_overhead_wall_ms",
    "other_instrumented_wall_ms",
    "uninstrumented_gap_ms",
]
LLM_CONTEXT_COLUMNS = [
    "llm_planning_wall_ms",
    "llm_tool_analysis_wall_ms",
    "llm_final_generation_wall_ms",
    "llm_other_wall_ms",
    "llm_unknown_context_wall_ms",
]
PARTITION_SHARE_COMPONENTS = [
    "guardrail",
    "router",
    "llm_ttft",
    "llm_post_first_token",
    "llm_unknown",
    "llm_total",
    "tool",
    "score",
    "facet",
    "classifier",
    "function",
    "automation",
    "agent_overhead",
    "other_instrumented",
    "uninstrumented_gap",
    "llm_planning",
    "llm_tool_analysis",
    "llm_final_generation",
    "llm_other",
    "llm_unknown_context",
]
CATEGORY_PRIORITY = {
    "llm": 0,
    "tool": 1,
    "guardrail": 2,
    "router": 3,
    "score": 4,
    "facet": 5,
    "classifier": 6,
    "function": 7,
    "automation": 8,
    "other": 9,
    "claude_agent": 10,
}
LLM_CONTEXT_PRIORITY = {
    "tool_analysis": 0,
    "final_generation": 1,
    "planning": 2,
    "other": 3,
    "unknown_context": 4,
}


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

SCRIPT_CONFIG = {
    "project": os.environ.get("BRAINTRUST_PROJECT", DEFAULT_PROJECT),
    "experiment_name": None,
    "experiment_id": None,
    "output_dir": DATA_DIR,
    "page_size": DEFAULT_PAGE_SIZE,
    "root_id_batch_size": DEFAULT_ROOT_ID_BATCH_SIZE,
    "max_btql_retries": 4,
    "retry_backoff_seconds": 2.0,
    "tool_analysis_window_ms": TOOL_ANALYSIS_WINDOW_MS,
}


@dataclass(frozen=True)
class IntervalSpan:
    category: str
    start_ms: int
    end_ms: int
    span_id: str
    time_to_first_token_ms: int | None
    llm_context: str | None = None
    llm_context_method: str | None = None
    llm_context_low_confidence: bool = False


@dataclass(frozen=True)
class LlmSpanClassification:
    span_id: str
    label: str
    method: str
    low_confidence: bool


def get_config() -> SimpleNamespace:
    args = SimpleNamespace(
        project=SCRIPT_CONFIG["project"],
        experiment_name=SCRIPT_CONFIG["experiment_name"],
        experiment_id=SCRIPT_CONFIG["experiment_id"],
        output_dir=Path(SCRIPT_CONFIG["output_dir"]),
        page_size=SCRIPT_CONFIG["page_size"],
        root_id_batch_size=SCRIPT_CONFIG["root_id_batch_size"],
        max_btql_retries=SCRIPT_CONFIG["max_btql_retries"],
        retry_backoff_seconds=SCRIPT_CONFIG["retry_backoff_seconds"],
        tool_analysis_window_ms=SCRIPT_CONFIG["tool_analysis_window_ms"],
    )
    return args


def require_api_key() -> str:
    api_key = os.environ.get("BRAINTRUST_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BRAINTRUST_API_KEY is required")
    return api_key


def extract_braintrust_items(response_data: Any) -> list[dict[str, Any]]:
    if isinstance(response_data, dict):
        items = response_data.get("objects", response_data.get("data", []))
    else:
        items = response_data
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def get_project_id(project_name: str) -> str:
    response = braintrust.api_conn().get(
        "/v1/project", params={"project_name": project_name}
    )
    response.raise_for_status()
    projects = extract_braintrust_items(response.json())
    project = next(
        (item for item in projects if item.get("name") == project_name), None
    )
    if not project or not project.get("id"):
        raise RuntimeError(f"Braintrust project not found: {project_name}")
    return str(project["id"])


def build_root_filter() -> dict[str, Any]:
    return {
        "op": "and",
        "children": [
            {
                "op": "eq",
                "left": {"op": "ident", "name": ["is_root"]},
                "right": {"op": "literal", "value": True},
            },
            {
                "op": "eq",
                "left": {"op": "ident", "name": ["span_attributes", "name"]},
                "right": {"op": "literal", "value": ROOT_SPAN_NAME},
            },
        ],
    }


def build_root_span_id_filter(root_span_ids: list[str]) -> dict[str, Any]:
    return build_string_field_filter(["root_span_id"], root_span_ids)


def build_string_field_filter(field_path: list[str], values: list[str]) -> dict[str, Any]:
    if len(values) == 1:
        return {
            "op": "eq",
            "left": {"op": "ident", "name": field_path},
            "right": {"op": "literal", "value": values[0]},
        }
    return {
        "op": "or",
        "children": [
            {
                "op": "eq",
                "left": {"op": "ident", "name": field_path},
                "right": {"op": "literal", "value": value},
            }
            for value in values
        ],
    }


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    return normalized


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def should_retry_btql_error(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    return False


def post_btql_with_retries(
    *,
    query: dict[str, Any],
    query_source: str,
    max_retries: int,
    retry_backoff_seconds: float,
    page_desc: str,
):
    attempt = 0
    while True:
        attempt += 1
        try:
            response = braintrust.api_conn().post(
                "btql",
                json={
                    "query": query,
                    "use_columnstore": False,
                    "brainstore_realtime": True,
                    "query_source": query_source,
                },
                headers={"Accept-Encoding": "gzip"},
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            if attempt > max_retries or not should_retry_btql_error(exc):
                raise
            sleep_seconds = retry_backoff_seconds * (2 ** (attempt - 1))
            tqdm.write(
                f"{page_desc}: retry {attempt}/{max_retries} after {exc} "
                f"(sleeping {sleep_seconds:.1f}s)"
            )
            time.sleep(sleep_seconds)


def post_btql_sql_with_retries(
    *,
    query_text: str,
    max_retries: int,
    retry_backoff_seconds: float,
    page_desc: str,
):
    attempt = 0
    while True:
        attempt += 1
        try:
            response = braintrust.api_conn().post(
                "btql",
                json={"query": query_text, "fmt": "json"},
                headers={"Accept-Encoding": "gzip"},
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            if attempt > max_retries or not should_retry_btql_error(exc):
                raise
            sleep_seconds = retry_backoff_seconds * (2 ** (attempt - 1))
            tqdm.write(
                f"{page_desc}: retry {attempt}/{max_retries} after {exc} "
                f"(sleeping {sleep_seconds:.1f}s)"
            )
            time.sleep(sleep_seconds)


def iter_btql_rows(
    *,
    project_id: str,
    query_filter: dict[str, Any],
    page_size: int,
    query_source: str,
    page_desc: str,
    max_retries: int,
    retry_backoff_seconds: float,
):
    cursor: str | None = None
    page_bar = tqdm(desc=page_desc, unit="page")

    try:
        while True:
            query: dict[str, Any] = {
                "select": [{"op": "star"}],
                "from": {
                    "op": "function",
                    "name": {"op": "ident", "name": ["project_logs"]},
                    "args": [{"op": "literal", "value": project_id}],
                },
                "filter": query_filter,
                "limit": page_size,
            }
            if cursor:
                query["cursor"] = cursor

            response = post_btql_with_retries(
                query=query,
                query_source=query_source,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
                page_desc=page_desc,
            )
            payload = response.json()
            batch = extract_braintrust_items(payload)
            page_bar.update(1)
            if batch:
                yield from batch

            cursor = payload.get("cursor")
            if not cursor:
                return
    finally:
        page_bar.close()


def fetch_experiment_object(args: SimpleNamespace) -> dict[str, Any]:
    if args.experiment_id:
        response = braintrust.api_conn().get(f"/v1/experiment/{args.experiment_id}")
        response.raise_for_status()
        return response.json()

    response = braintrust.api_conn().get(
        "/v1/experiment", params={"project_name": args.project}
    )
    response.raise_for_status()
    experiments = extract_braintrust_items(response.json())
    if args.experiment_name:
        experiment = next(
            (item for item in experiments if item.get("name") == args.experiment_name), None
        )
        if not experiment:
            raise RuntimeError(
                f"Experiment {args.experiment_name!r} not found in project {args.project!r}"
            )
        return experiment

    beta_experiments = [
        item
        for item in experiments
        if (item.get("metadata") or {}).get("run_type") == "beta_baseline"
    ]
    if not beta_experiments:
        raise RuntimeError(
            "No beta_baseline experiments found. Set SCRIPT_CONFIG['experiment_name'] or "
            "SCRIPT_CONFIG['experiment_id']."
        )
    beta_experiments.sort(key=lambda item: item.get("created", ""), reverse=True)
    return beta_experiments[0]


def fetch_experiment_rows(experiment_id: str, args: SimpleNamespace) -> list[dict[str, Any]]:
    query = f"""
    SELECT id, input, output, expected, metadata, tags, scores
    FROM experiment('{experiment_id}')
    LIMIT 1000
    """
    response = post_btql_sql_with_retries(
        query_text=query,
        max_retries=args.max_btql_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        page_desc="Experiment rows",
    )
    payload = response.json()
    return extract_braintrust_items(payload)


def batched_strings(values: list[str], batch_size: int):
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def fetch_rows_for_root_ids(
    project_id: str,
    root_span_ids: list[str],
    args: SimpleNamespace,
) -> dict[str, list[dict[str, Any]]]:
    rows_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    batch_bar = tqdm(
        total=(len(root_span_ids) + args.root_id_batch_size - 1)
        // args.root_id_batch_size,
        desc="Root-id batches",
        unit="batch",
    )

    try:
        for batch_index, root_id_batch in enumerate(
            batched_strings(root_span_ids, args.root_id_batch_size), start=1
        ):
            for row in iter_btql_rows(
                project_id=project_id,
                query_filter=build_root_span_id_filter(root_id_batch),
                page_size=args.page_size,
                query_source="latency_current_span_scan",
                page_desc=f"Span pages batch {batch_index}",
                max_retries=args.max_btql_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
            ):
                root_span_id = row.get("root_span_id")
                if isinstance(root_span_id, str) and root_span_id:
                    rows_by_root[root_span_id].append(row)
            batch_bar.update(1)
    finally:
        batch_bar.close()

    return rows_by_root


def extract_trace_id_from_experiment_row(row: dict[str, Any]) -> str | None:
    output = row.get("output") or {}
    if isinstance(output, dict):
        trace_id = output.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id
    return None


def build_experiment_row_map(
    experiment_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    row_map: dict[str, dict[str, Any]] = {}
    for row in experiment_rows:
        trace_id = extract_trace_id_from_experiment_row(row)
        if trace_id:
            row_map[trace_id] = row
    return row_map


def get_metrics(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    return metrics if isinstance(metrics, dict) else {}


def get_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def get_span_attributes(row: dict[str, Any]) -> dict[str, Any]:
    attrs = row.get("span_attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def get_input_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("input") or {}
    return payload if isinstance(payload, dict) else {}


def get_output_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("output") or {}
    return payload if isinstance(payload, dict) else {}


def get_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def get_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    return None


def get_duration_ms(row: dict[str, Any]) -> int | None:
    metrics = get_metrics(row)
    start = get_float(metrics.get("start"))
    end = get_float(metrics.get("end"))
    if start is None or end is None or end < start:
        return None
    return int(round((end - start) * 1000))


def get_time_to_first_token_ms(row: dict[str, Any]) -> int | None:
    metrics = get_metrics(row)
    ttft = get_float(metrics.get("time_to_first_token"))
    if ttft is None:
        return None
    return int(round(ttft * 1000))


def get_time_range_seconds(row: dict[str, Any]) -> tuple[float | None, float | None]:
    metrics = get_metrics(row)
    return get_float(metrics.get("start")), get_float(metrics.get("end"))


def classify_span(row: dict[str, Any]) -> str:
    attrs = get_span_attributes(row)
    name = attrs.get("name")
    span_type = attrs.get("type")

    if row.get("is_root") is True and name == ROOT_SPAN_NAME:
        return "root"
    if name == "guardrail":
        return "guardrail"
    if name == "router":
        return "router"
    if name == "Claude Agent":
        return "claude_agent"
    if span_type == "tool":
        return "tool"
    if span_type == "llm":
        return "llm"
    if span_type == "score":
        return "score"
    if span_type == "facet":
        return "facet"
    if span_type == "classifier":
        return "classifier"
    if span_type == "automation":
        return "automation"
    if span_type == "function":
        return "function"
    return "other"


def is_within_root_window(
    *,
    span_start_s: float | None,
    span_end_s: float | None,
    root_start_s: float | None,
    root_end_s: float | None,
) -> bool:
    if (
        span_start_s is None
        or span_end_s is None
        or root_start_s is None
        or root_end_s is None
    ):
        return False
    epsilon = 0.001
    return span_start_s >= root_start_s - epsilon and span_end_s <= root_end_s + epsilon


def to_json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def safe_share(numerator: int | float, denominator: int | None) -> float | None:
    if denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def percentile(sorted_values: list[int], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = index - lower
    return (
        sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction
    )


def build_span_row(
    *,
    row: dict[str, Any],
    root_row: dict[str, Any],
    root_start_s: float | None,
    root_end_s: float | None,
) -> dict[str, Any]:
    attrs = get_span_attributes(row)
    metadata = get_metadata(row)
    metrics = get_metrics(row)
    input_payload = get_input_payload(row)
    output_payload = get_output_payload(row)

    span_start_s, span_end_s = get_time_range_seconds(row)
    duration_ms = get_duration_ms(row)
    ttft_ms = get_time_to_first_token_ms(row)
    within_root_window = is_within_root_window(
        span_start_s=span_start_s,
        span_end_s=span_end_s,
        root_start_s=root_start_s,
        root_end_s=root_end_s,
    )

    return {
        "root_span_id": root_row.get("root_span_id"),
        "trace_created": root_row.get("created"),
        "trace_origin": get_metadata(root_row).get("origin"),
        "trace_model": get_metadata(root_row).get("model"),
        "trace_route": get_metadata(root_row).get("route"),
        "span_id": row.get("span_id"),
        "row_id": row.get("id"),
        "is_root": row.get("is_root"),
        "span_name": attrs.get("name"),
        "span_type": attrs.get("type"),
        "span_category": classify_span(row),
        "created": row.get("created"),
        "span_start_s": span_start_s,
        "span_end_s": span_end_s,
        "duration_ms": duration_ms,
        "time_to_first_token_ms": ttft_ms,
        "post_first_token_ms": (
            None
            if duration_ms is None or ttft_ms is None
            else max(duration_ms - ttft_ms, 0)
        ),
        "within_root_window": within_root_window,
        "metrics_prompt_tokens": get_int(metrics.get("prompt_tokens")),
        "metrics_completion_tokens": get_int(metrics.get("completion_tokens")),
        "metrics_tokens": get_int(metrics.get("tokens")),
        "metrics_prompt_cached_tokens": get_int(metrics.get("prompt_cached_tokens")),
        "metrics_prompt_cache_creation_tokens": get_int(
            metrics.get("prompt_cache_creation_tokens")
        ),
        "metrics_total_cost_usd": get_float(metrics.get("total_cost_usd")),
        "metadata_keys": to_json_string(sorted(metadata.keys())),
        "input_keys": to_json_string(sorted(input_payload.keys())),
        "output_keys": to_json_string(sorted(output_payload.keys())),
        "metadata_json": to_json_string(metadata),
        "input_json": to_json_string(input_payload),
        "output_json": to_json_string(output_payload),
    }


def build_trace_row(
    root_row: dict[str, Any],
    rows: list[dict[str, Any]],
    experiment_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root_metadata = get_metadata(root_row)
    root_metrics = get_metrics(root_row)
    root_input = get_input_payload(root_row)
    root_output = get_output_payload(root_row)
    experiment_input = (
        experiment_row.get("input")
        if isinstance(experiment_row, dict) and isinstance(experiment_row.get("input"), dict)
        else {}
    )
    experiment_output = (
        experiment_row.get("output")
        if isinstance(experiment_row, dict) and isinstance(experiment_row.get("output"), dict)
        else {}
    )
    experiment_metadata = (
        experiment_row.get("metadata")
        if isinstance(experiment_row, dict) and isinstance(experiment_row.get("metadata"), dict)
        else {}
    )
    root_start_s, root_end_s = get_time_range_seconds(root_row)
    total_turn_ms = get_int(root_metrics.get("latency_ms")) or get_duration_ms(root_row)

    category_duration_ms: Counter[str] = Counter()
    category_count: Counter[str] = Counter()
    tool_name_counts: Counter[str] = Counter()
    tool_name_duration_ms: Counter[str] = Counter()

    llm_ttft_ms_total = 0
    llm_post_first_token_ms_total = 0
    llm_ttft_count = 0
    attached_outside_root_window_count = 0
    attached_outside_root_window_duration_ms = 0

    for row in rows:
        if row is root_row or row.get("is_root") is True:
            continue

        category = classify_span(row)
        duration_ms = get_duration_ms(row)
        ttft_ms = get_time_to_first_token_ms(row)
        span_start_s, span_end_s = get_time_range_seconds(row)
        within_root_window = is_within_root_window(
            span_start_s=span_start_s,
            span_end_s=span_end_s,
            root_start_s=root_start_s,
            root_end_s=root_end_s,
        )

        if within_root_window:
            category_count[category] += 1
            if duration_ms is not None:
                category_duration_ms[category] += duration_ms
            if category == "tool":
                tool_name = get_span_attributes(row).get("name")
                if isinstance(tool_name, str) and tool_name:
                    tool_name_counts[tool_name] += 1
                    if duration_ms is not None:
                        tool_name_duration_ms[tool_name] += duration_ms
            if category == "llm" and ttft_ms is not None:
                llm_ttft_ms_total += ttft_ms
                llm_ttft_count += 1
                if duration_ms is not None:
                    llm_post_first_token_ms_total += max(duration_ms - ttft_ms, 0)
        else:
            attached_outside_root_window_count += 1
            if duration_ms is not None:
                attached_outside_root_window_duration_ms += duration_ms

    claude_agent_ms = category_duration_ms.get("claude_agent", 0)
    llm_ms = category_duration_ms.get("llm", 0)
    tool_ms = category_duration_ms.get("tool", 0)
    guardrail_ms = category_duration_ms.get("guardrail", 0)
    router_ms = category_duration_ms.get("router", 0)
    score_ms = category_duration_ms.get("score", 0)
    facet_ms = category_duration_ms.get("facet", 0)
    classifier_ms = category_duration_ms.get("classifier", 0)
    function_ms = category_duration_ms.get("function", 0)
    automation_ms = category_duration_ms.get("automation", 0)
    other_ms = category_duration_ms.get("other", 0)

    agent_overhead_ms = max(claude_agent_ms - llm_ms - tool_ms, 0)
    prepost_agent_overhead_ms = (
        max(total_turn_ms - guardrail_ms - router_ms - claude_agent_ms, 0)
        if total_turn_ms is not None
        else None
    )

    summary_text = experiment_input.get("summary_text")
    if not isinstance(summary_text, str):
        summary_text = root_input.get("summary")
    question = normalize_text(experiment_input.get("question"))
    if question is None:
        question = normalize_text(root_input.get("message"))
    output_content = root_output.get("content")
    request = experiment_output.get("request") or {}
    response_payload = experiment_output.get("response") or {}
    error_payload = experiment_output.get("error") or {}

    return {
        "root_span_id": root_row.get("root_span_id"),
        "created": root_row.get("created"),
        "question": question,
        "question_char_count": len(question) if question else 0,
        "summary_text": summary_text,
        "summary_char_count": len(summary_text) if isinstance(summary_text, str) else 0,
        "page_url": experiment_input.get("page_url") or root_input.get("page_url"),
        "origin": root_metadata.get("origin"),
        "model": root_metadata.get("model"),
        "route": root_metadata.get("route"),
        "session_id": experiment_metadata.get("session_id") or root_metadata.get("session_id"),
        "user_id": root_metadata.get("user_id"),
        "summary_included": experiment_metadata.get("summary_included")
        if "summary_included" in experiment_metadata
        else root_metadata.get("summary_included"),
        "core_prompt_id": experiment_metadata.get("effective_core_prompt_id")
        or root_metadata.get("core_prompt_id"),
        "core_prompt_version": experiment_metadata.get("effective_core_prompt_version")
        or root_metadata.get("core_prompt_version"),
        "root_start_s": root_start_s,
        "root_end_s": root_end_s,
        "total_turn_ms": total_turn_ms,
        "metrics_llm_calls": get_int(root_metrics.get("llm_calls")),
        "metrics_tool_count": get_int(root_metrics.get("tool_count")),
        "metrics_prompt_tokens": get_int(root_metrics.get("prompt_tokens")),
        "metrics_completion_tokens": get_int(root_metrics.get("completion_tokens")),
        "metrics_tokens": get_int(root_metrics.get("tokens")),
        "metrics_prompt_cached_tokens": get_int(
            root_metrics.get("prompt_cached_tokens")
        ),
        "metrics_prompt_cache_creation_tokens": get_int(
            root_metrics.get("prompt_cache_creation_tokens")
        ),
        "metrics_total_cost_usd": get_float(root_metrics.get("total_cost_usd")),
        "root_output_content_char_count": (
            len(output_content) if isinstance(output_content, str) else 0
        ),
        "trace_tool_calls_json": to_json_string(root_metadata.get("tool_calls") or []),
        "trace_refs_json": to_json_string(root_metadata.get("refs") or []),
        "guardrail_ms": guardrail_ms,
        "router_ms": router_ms,
        "claude_agent_ms": claude_agent_ms,
        "llm_ms": llm_ms,
        "tool_ms": tool_ms,
        "score_ms": score_ms,
        "facet_ms": facet_ms,
        "classifier_ms": classifier_ms,
        "function_ms": function_ms,
        "automation_ms": automation_ms,
        "other_ms": other_ms,
        "llm_ttft_ms_total": llm_ttft_ms_total,
        "llm_post_first_token_ms_total": llm_post_first_token_ms_total,
        "llm_ttft_span_count": llm_ttft_count,
        "agent_overhead_ms": agent_overhead_ms,
        "prepost_agent_overhead_ms": prepost_agent_overhead_ms,
        "guardrail_span_count": category_count.get("guardrail", 0),
        "router_span_count": category_count.get("router", 0),
        "claude_agent_span_count": category_count.get("claude_agent", 0),
        "llm_span_count": category_count.get("llm", 0),
        "tool_span_count": category_count.get("tool", 0),
        "score_span_count": category_count.get("score", 0),
        "facet_span_count": category_count.get("facet", 0),
        "classifier_span_count": category_count.get("classifier", 0),
        "function_span_count": category_count.get("function", 0),
        "automation_span_count": category_count.get("automation", 0),
        "other_span_count": category_count.get("other", 0),
        "attached_outside_root_window_count": attached_outside_root_window_count,
        "attached_outside_root_window_duration_ms": attached_outside_root_window_duration_ms,
        "tool_name_counts_json": to_json_string(dict(tool_name_counts)),
        "tool_name_duration_ms_json": to_json_string(dict(tool_name_duration_ms)),
        "root_tags_json": to_json_string(normalize_string_list(root_row.get("tags"))),
        "experiment_row_id": experiment_row.get("id") if experiment_row else None,
        "experiment_status": experiment_output.get("status"),
        "experiment_trace_id": experiment_output.get("trace_id"),
        "request_user_message_char_count": get_int(
            request.get("user_message_char_count")
        ),
        "request_question_char_count": get_int(request.get("question_char_count")),
        "experiment_first_event_latency_ms": get_int(
            (experiment_output.get("metrics") or {}).get("first_event_latency_ms")
        ),
        "experiment_harness_latency_ms": get_int(
            (experiment_output.get("metrics") or {}).get("harness_latency_ms")
        ),
        "experiment_error_type": error_payload.get("type"),
        "experiment_error_message": error_payload.get("message"),
        "experiment_response_char_count": (
            len(response_payload.get("content"))
            if isinstance(response_payload.get("content"), str)
            else 0
        ),
    }


def parse_jsonish_keys(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, dict):
        return [str(key) for key in parsed.keys()]
    return [str(parsed)]


def build_llm_metadata_text(span_row: dict[str, Any]) -> str:
    parts = [
        str(span_row.get("span_name") or ""),
        str(span_row.get("span_type") or ""),
        str(span_row.get("metadata_keys") or ""),
        str(span_row.get("input_keys") or ""),
        str(span_row.get("output_keys") or ""),
        str(span_row.get("metadata_json") or ""),
        str(span_row.get("input_json") or ""),
        str(span_row.get("output_json") or ""),
    ]
    return " ".join(parts).lower()


def maybe_classify_llm_from_metadata(
    span_row: dict[str, Any],
) -> tuple[str | None, bool]:
    text = build_llm_metadata_text(span_row)

    final_keywords = [
        "final",
        "answer",
        "response",
        "synthesis",
        "synthesizing",
        "user-facing",
    ]
    analysis_keywords = [
        "tool result",
        "tool output",
        "observation",
        "analy",
        "inspect",
        "retrieved",
        "search result",
        "function result",
    ]
    planning_keywords = [
        "planner",
        "planning",
        "tool_selection",
        "tool choice",
        "decide",
        "search query",
        "query rewrite",
    ]

    if any(keyword in text for keyword in final_keywords):
        return "final_generation", False
    if any(keyword in text for keyword in analysis_keywords):
        return "tool_analysis", False
    if any(keyword in text for keyword in planning_keywords):
        return "planning", False
    return None, False


def classify_llm_spans(
    span_rows: list[dict[str, Any]],
    *,
    tool_analysis_window_ms: int,
) -> dict[str, LlmSpanClassification]:
    valid_rows = [
        row
        for row in span_rows
        if row.get("is_root") is not True
        and row.get("within_root_window") is True
        and get_float(row.get("span_start_s")) is not None
        and get_float(row.get("span_end_s")) is not None
        and get_float(row.get("span_end_s")) > get_float(row.get("span_start_s"))
    ]
    ordered_rows = sorted(
        valid_rows,
        key=lambda row: (
            get_float(row.get("span_start_s")) or 0.0,
            get_float(row.get("span_end_s")) or 0.0,
            str(row.get("span_category") or ""),
        ),
    )

    llm_rows = [row for row in ordered_rows if row.get("span_category") == "llm"]
    tool_rows = [row for row in ordered_rows if row.get("span_category") == "tool"]
    classifications: dict[str, LlmSpanClassification] = {}

    for llm_index, llm_row in enumerate(llm_rows):
        span_id = str(
            llm_row.get("span_id") or llm_row.get("row_id") or f"llm-{llm_index}"
        )
        metadata_label, metadata_low_confidence = maybe_classify_llm_from_metadata(
            llm_row
        )
        if metadata_label is not None:
            classifications[span_id] = LlmSpanClassification(
                span_id=span_id,
                label=metadata_label,
                method="metadata",
                low_confidence=metadata_low_confidence,
            )
            continue

        llm_start_s = get_float(llm_row.get("span_start_s"))
        llm_end_s = get_float(llm_row.get("span_end_s"))
        if llm_start_s is None or llm_end_s is None:
            classifications[span_id] = LlmSpanClassification(
                span_id=span_id,
                label="unknown_context",
                method="unknown",
                low_confidence=True,
            )
            continue

        previous_non_llm = None
        next_non_llm = None
        for row in reversed(ordered_rows[: ordered_rows.index(llm_row)]):
            if row.get("span_category") != "llm":
                previous_non_llm = row
                break
        for row in ordered_rows[ordered_rows.index(llm_row) + 1 :]:
            if row.get("span_category") != "llm":
                next_non_llm = row
                break

        previous_tool = None
        next_tool = None
        any_tool_completed_before = False
        any_tool_after = False
        for tool_row in tool_rows:
            tool_start = get_float(tool_row.get("span_start_s"))
            tool_end = get_float(tool_row.get("span_end_s"))
            if tool_start is None or tool_end is None:
                continue
            if tool_end <= llm_start_s:
                any_tool_completed_before = True
                previous_tool = tool_row
            if tool_start >= llm_end_s and next_tool is None:
                any_tool_after = True
                next_tool = tool_row
            elif tool_start >= llm_end_s:
                any_tool_after = True

        is_last_llm = llm_index == len(llm_rows) - 1
        previous_non_llm_is_tool = (
            previous_non_llm is not None
            and previous_non_llm.get("span_category") == "tool"
        )
        starts_shortly_after_tool = False
        if previous_tool is not None:
            previous_tool_end = get_float(previous_tool.get("span_end_s"))
            if previous_tool_end is not None:
                starts_shortly_after_tool = (
                    llm_start_s - previous_tool_end
                ) * 1000 <= tool_analysis_window_ms

        no_tools_exist = len(tool_rows) == 0
        low_confidence = False
        label = "unknown_context"
        method = "temporal_heuristic"

        any_tool_exists = len(tool_rows) > 0

        if no_tools_exist and is_last_llm:
            label = "final_generation"
        elif is_last_llm and any_tool_exists and not any_tool_after:
            label = "final_generation"
        elif any_tool_completed_before and (
            previous_non_llm_is_tool or starts_shortly_after_tool
        ):
            label = "tool_analysis"
        elif any_tool_after and not any_tool_completed_before:
            label = "planning"
        elif previous_non_llm is not None or next_non_llm is not None:
            label = "other"
            low_confidence = True
        else:
            label = "unknown_context"
            method = "unknown"
            low_confidence = True

        classifications[span_id] = LlmSpanClassification(
            span_id=span_id,
            label=label,
            method=method,
            low_confidence=low_confidence,
        )

    return classifications


def choose_llm_span(
    active_spans: list[IntervalSpan],
) -> tuple[IntervalSpan | None, bool]:
    llm_spans = [span for span in active_spans if span.category == "llm"]
    if not llm_spans:
        return None, False
    if len(llm_spans) == 1:
        return llm_spans[0], False
    chosen = sorted(
        llm_spans, key=lambda span: (span.start_ms, span.end_ms, span.span_id)
    )[-1]
    return chosen, True


def assign_llm_interval(
    *,
    llm_span: IntervalSpan | None,
    interval_start_ms: int,
    interval_end_ms: int,
    counters: dict[str, int],
) -> str:
    if llm_span is None:
        counters["llm_unknown_wall_ms"] += interval_end_ms - interval_start_ms
        counters["llm_unknown_context_wall_ms"] += interval_end_ms - interval_start_ms
        return "unknown_context"

    if llm_span.time_to_first_token_ms is None:
        counters["llm_unknown_wall_ms"] += interval_end_ms - interval_start_ms
    else:
        ttft_end_ms = min(
            llm_span.end_ms, llm_span.start_ms + llm_span.time_to_first_token_ms
        )
        if interval_end_ms <= ttft_end_ms:
            counters["llm_ttft_wall_ms"] += interval_end_ms - interval_start_ms
        elif interval_start_ms >= ttft_end_ms:
            counters["llm_post_first_token_wall_ms"] += (
                interval_end_ms - interval_start_ms
            )
        else:
            counters["llm_ttft_wall_ms"] += ttft_end_ms - interval_start_ms
            counters["llm_post_first_token_wall_ms"] += interval_end_ms - ttft_end_ms

    label = llm_span.llm_context or "unknown_context"
    if label == "planning":
        counters["llm_planning_wall_ms"] += interval_end_ms - interval_start_ms
    elif label == "tool_analysis":
        counters["llm_tool_analysis_wall_ms"] += interval_end_ms - interval_start_ms
    elif label == "final_generation":
        counters["llm_final_generation_wall_ms"] += interval_end_ms - interval_start_ms
    elif label == "other":
        counters["llm_other_wall_ms"] += interval_end_ms - interval_start_ms
    else:
        counters["llm_unknown_context_wall_ms"] += interval_end_ms - interval_start_ms
        label = "unknown_context"
    return label


def empty_partition_counters() -> dict[str, int]:
    counters = {
        column: 0 for column in PARTITION_COMPONENT_COLUMNS + LLM_CONTEXT_COLUMNS
    }
    counters["overlapping_interval_count"] = 0
    counters["overlapping_wall_ms"] = 0
    return counters


def build_interval_spans_for_trace(
    trace_row: dict[str, Any],
    span_rows: list[dict[str, Any]],
    llm_classifications: dict[str, LlmSpanClassification],
) -> list[IntervalSpan]:
    root_start_s = get_float(trace_row.get("root_start_s"))
    root_end_s = get_float(trace_row.get("root_end_s"))
    total_turn_ms = get_int(trace_row.get("total_turn_ms"))
    if root_start_s is None or root_end_s is None or root_end_s <= root_start_s:
        return []
    root_duration_ms = total_turn_ms or int(round((root_end_s - root_start_s) * 1000))

    interval_spans: list[IntervalSpan] = []
    for row in span_rows:
        if row.get("is_root") is True:
            continue
        if row.get("within_root_window") is not True:
            continue
        span_start_s = get_float(row.get("span_start_s"))
        span_end_s = get_float(row.get("span_end_s"))
        duration_ms = get_int(row.get("duration_ms"))
        if (
            span_start_s is None
            or span_end_s is None
            or duration_ms is None
            or span_end_s <= span_start_s
        ):
            continue

        clipped_start = max(span_start_s, root_start_s)
        clipped_end = min(span_end_s, root_end_s)
        if clipped_end <= clipped_start:
            continue

        start_ms = int(round((clipped_start - root_start_s) * 1000))
        end_ms = int(round((clipped_end - root_start_s) * 1000))
        start_ms = max(start_ms, 0)
        end_ms = min(end_ms, root_duration_ms)
        if end_ms <= start_ms:
            continue

        span_id = str(row.get("span_id") or row.get("row_id") or "")
        llm_classification = llm_classifications.get(span_id)
        interval_spans.append(
            IntervalSpan(
                category=str(row.get("span_category") or "other"),
                start_ms=start_ms,
                end_ms=end_ms,
                span_id=span_id,
                time_to_first_token_ms=get_int(row.get("time_to_first_token_ms")),
                llm_context=llm_classification.label if llm_classification else None,
                llm_context_method=llm_classification.method
                if llm_classification
                else None,
                llm_context_low_confidence=(
                    llm_classification.low_confidence if llm_classification else False
                ),
            )
        )

    return interval_spans


def build_partition_row(
    trace_row: dict[str, Any],
    span_rows: list[dict[str, Any]],
    *,
    tool_analysis_window_ms: int = TOOL_ANALYSIS_WINDOW_MS,
) -> dict[str, Any]:
    total_turn_ms = get_int(trace_row.get("total_turn_ms"))
    root_start_s = get_float(trace_row.get("root_start_s"))
    root_end_s = get_float(trace_row.get("root_end_s"))

    if total_turn_ms is None and root_start_s is not None and root_end_s is not None:
        total_turn_ms = int(round((root_end_s - root_start_s) * 1000))

    llm_classifications = classify_llm_spans(
        span_rows, tool_analysis_window_ms=tool_analysis_window_ms
    )
    method_counts: Counter[str] = Counter(
        classification.method for classification in llm_classifications.values()
    )
    low_confidence_span_ids = {
        classification.span_id
        for classification in llm_classifications.values()
        if classification.low_confidence
    }

    counters = empty_partition_counters()
    interval_spans = build_interval_spans_for_trace(
        trace_row, span_rows, llm_classifications
    )
    boundaries = {0, total_turn_ms or 0}
    for span in interval_spans:
        boundaries.add(span.start_ms)
        boundaries.add(span.end_ms)
    sorted_boundaries = sorted(
        boundary for boundary in boundaries if boundary is not None
    )

    llm_conflict_span_ids: set[str] = set()
    llm_unclassified_span_ids: set[str] = set()

    for index in range(len(sorted_boundaries) - 1):
        interval_start_ms = sorted_boundaries[index]
        interval_end_ms = sorted_boundaries[index + 1]
        if interval_end_ms <= interval_start_ms:
            continue

        active_spans = [
            span
            for span in interval_spans
            if span.start_ms < interval_end_ms and span.end_ms > interval_start_ms
        ]
        interval_length_ms = interval_end_ms - interval_start_ms

        if len(active_spans) > 1:
            counters["overlapping_interval_count"] += 1
            counters["overlapping_wall_ms"] += interval_length_ms

        if not active_spans:
            counters["uninstrumented_gap_ms"] += interval_length_ms
            continue

        active_spans.sort(
            key=lambda span: (
                CATEGORY_PRIORITY.get(span.category, CATEGORY_PRIORITY["other"]),
                span.start_ms,
                span.end_ms,
                span.span_id,
            )
        )
        chosen = active_spans[0]

        if chosen.category == "llm":
            chosen_llm, llm_conflict = choose_llm_span(active_spans)
            if llm_conflict:
                llm_conflict_span_ids.update(
                    span.span_id for span in active_spans if span.category == "llm"
                )
            assigned_label = assign_llm_interval(
                llm_span=chosen_llm,
                interval_start_ms=interval_start_ms,
                interval_end_ms=interval_end_ms,
                counters=counters,
            )
            if chosen_llm is None or assigned_label == "unknown_context":
                if chosen_llm is not None:
                    llm_unclassified_span_ids.add(chosen_llm.span_id)
        elif chosen.category == "tool":
            counters["tool_wall_ms"] += interval_length_ms
        elif chosen.category == "guardrail":
            counters["guardrail_wall_ms"] += interval_length_ms
        elif chosen.category == "router":
            counters["router_wall_ms"] += interval_length_ms
        elif chosen.category == "score":
            counters["score_wall_ms"] += interval_length_ms
        elif chosen.category == "facet":
            counters["facet_wall_ms"] += interval_length_ms
        elif chosen.category == "classifier":
            counters["classifier_wall_ms"] += interval_length_ms
        elif chosen.category == "function":
            counters["function_wall_ms"] += interval_length_ms
        elif chosen.category == "automation":
            counters["automation_wall_ms"] += interval_length_ms
        elif chosen.category == "claude_agent":
            counters["agent_overhead_wall_ms"] += interval_length_ms
        else:
            counters["other_instrumented_wall_ms"] += interval_length_ms

    partition_sum_ms = sum(counters[column] for column in PARTITION_COMPONENT_COLUMNS)
    partition_residual_ms = (total_turn_ms or 0) - partition_sum_ms
    partition_abs_residual_ms = abs(partition_residual_ms)

    llm_total_wall_ms = (
        counters["llm_ttft_wall_ms"]
        + counters["llm_post_first_token_wall_ms"]
        + counters["llm_unknown_wall_ms"]
    )
    llm_context_total_wall_ms = sum(counters[column] for column in LLM_CONTEXT_COLUMNS)
    llm_context_residual_ms = llm_total_wall_ms - llm_context_total_wall_ms

    instrumented_wall_ms = partition_sum_ms - counters["uninstrumented_gap_ms"]
    non_llm_tool_wall_ms = (
        (total_turn_ms or 0) - llm_total_wall_ms - counters["tool_wall_ms"]
    )
    agent_known_work_wall_ms = llm_total_wall_ms + counters["tool_wall_ms"]
    overhead_total_wall_ms = (
        counters["agent_overhead_wall_ms"]
        + counters["other_instrumented_wall_ms"]
        + counters["uninstrumented_gap_ms"]
    )

    row = {
        "root_span_id": trace_row.get("root_span_id"),
        "created": trace_row.get("created"),
        "question": trace_row.get("question"),
        "question_char_count": get_int(trace_row.get("question_char_count")) or 0,
        "summary_char_count": get_int(trace_row.get("summary_char_count")) or 0,
        "page_url": trace_row.get("page_url"),
        "origin": trace_row.get("origin"),
        "model": trace_row.get("model"),
        "route": trace_row.get("route"),
        "session_id": trace_row.get("session_id"),
        "user_id": trace_row.get("user_id"),
        "summary_included": trace_row.get("summary_included"),
        "core_prompt_id": trace_row.get("core_prompt_id"),
        "core_prompt_version": trace_row.get("core_prompt_version"),
        "root_start_s": root_start_s,
        "root_end_s": root_end_s,
        "total_turn_ms": total_turn_ms,
        "guardrail_wall_ms": counters["guardrail_wall_ms"],
        "router_wall_ms": counters["router_wall_ms"],
        "llm_ttft_wall_ms": counters["llm_ttft_wall_ms"],
        "llm_post_first_token_wall_ms": counters["llm_post_first_token_wall_ms"],
        "llm_unknown_wall_ms": counters["llm_unknown_wall_ms"],
        "tool_wall_ms": counters["tool_wall_ms"],
        "score_wall_ms": counters["score_wall_ms"],
        "facet_wall_ms": counters["facet_wall_ms"],
        "classifier_wall_ms": counters["classifier_wall_ms"],
        "function_wall_ms": counters["function_wall_ms"],
        "automation_wall_ms": counters["automation_wall_ms"],
        "agent_overhead_wall_ms": counters["agent_overhead_wall_ms"],
        "other_instrumented_wall_ms": counters["other_instrumented_wall_ms"],
        "uninstrumented_gap_ms": counters["uninstrumented_gap_ms"],
        "llm_planning_wall_ms": counters["llm_planning_wall_ms"],
        "llm_tool_analysis_wall_ms": counters["llm_tool_analysis_wall_ms"],
        "llm_final_generation_wall_ms": counters["llm_final_generation_wall_ms"],
        "llm_other_wall_ms": counters["llm_other_wall_ms"],
        "llm_unknown_context_wall_ms": counters["llm_unknown_context_wall_ms"],
        "partition_sum_ms": partition_sum_ms,
        "partition_residual_ms": partition_residual_ms,
        "partition_abs_residual_ms": partition_abs_residual_ms,
        "has_partition_error": partition_abs_residual_ms > 5,
        "overlapping_interval_count": counters["overlapping_interval_count"],
        "overlapping_wall_ms": counters["overlapping_wall_ms"],
        "attached_outside_root_window_count": get_int(
            trace_row.get("attached_outside_root_window_count")
        )
        or 0,
        "attached_outside_root_window_duration_ms": get_int(
            trace_row.get("attached_outside_root_window_duration_ms")
        )
        or 0,
        "metrics_llm_calls": trace_row.get("metrics_llm_calls"),
        "metrics_tool_count": trace_row.get("metrics_tool_count"),
        "metrics_prompt_tokens": trace_row.get("metrics_prompt_tokens"),
        "metrics_completion_tokens": trace_row.get("metrics_completion_tokens"),
        "metrics_tokens": trace_row.get("metrics_tokens"),
        "metrics_prompt_cached_tokens": trace_row.get("metrics_prompt_cached_tokens"),
        "metrics_prompt_cache_creation_tokens": trace_row.get(
            "metrics_prompt_cache_creation_tokens"
        ),
        "metrics_total_cost_usd": trace_row.get("metrics_total_cost_usd"),
        "root_output_content_char_count": trace_row.get(
            "root_output_content_char_count"
        ),
        "tool_name_counts_json": trace_row.get("tool_name_counts_json"),
        "tool_name_duration_ms_json": trace_row.get("tool_name_duration_ms_json"),
        "root_tags_json": trace_row.get("root_tags_json"),
        "llm_context_classification_method_json": to_json_string(dict(method_counts)),
        "llm_context_unclassified_span_count": len(llm_unclassified_span_ids),
        "llm_context_conflict_span_count": len(llm_conflict_span_ids),
        "llm_context_low_confidence_span_count": len(low_confidence_span_ids),
        "llm_total_wall_ms": llm_total_wall_ms,
        "instrumented_wall_ms": instrumented_wall_ms,
        "non_llm_tool_wall_ms": non_llm_tool_wall_ms,
        "agent_known_work_wall_ms": agent_known_work_wall_ms,
        "overhead_total_wall_ms": overhead_total_wall_ms,
        "llm_context_total_wall_ms": llm_context_total_wall_ms,
        "llm_context_residual_ms": llm_context_residual_ms,
        "has_llm_context_partition_error": abs(llm_context_residual_ms) > 5,
        "llm_planning_or_routing_wall_ms": counters["llm_planning_wall_ms"],
        "llm_tool_related_wall_ms": counters["llm_tool_analysis_wall_ms"],
        "llm_generation_wall_ms": counters["llm_final_generation_wall_ms"],
        "non_llm_overhead_wall_ms": overhead_total_wall_ms,
    }

    share_values = {
        "guardrail": row["guardrail_wall_ms"],
        "router": row["router_wall_ms"],
        "llm_ttft": row["llm_ttft_wall_ms"],
        "llm_post_first_token": row["llm_post_first_token_wall_ms"],
        "llm_unknown": row["llm_unknown_wall_ms"],
        "llm_total": llm_total_wall_ms,
        "tool": row["tool_wall_ms"],
        "score": row["score_wall_ms"],
        "facet": row["facet_wall_ms"],
        "classifier": row["classifier_wall_ms"],
        "function": row["function_wall_ms"],
        "automation": row["automation_wall_ms"],
        "agent_overhead": row["agent_overhead_wall_ms"],
        "other_instrumented": row["other_instrumented_wall_ms"],
        "uninstrumented_gap": row["uninstrumented_gap_ms"],
        "llm_planning": row["llm_planning_wall_ms"],
        "llm_tool_analysis": row["llm_tool_analysis_wall_ms"],
        "llm_final_generation": row["llm_final_generation_wall_ms"],
        "llm_other": row["llm_other_wall_ms"],
        "llm_unknown_context": row["llm_unknown_context_wall_ms"],
    }
    for component in PARTITION_SHARE_COMPONENTS:
        row[f"{component}_share"] = safe_share(share_values[component], total_turn_ms)

    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_output_dir(base_output_dir: Path, args: SimpleNamespace) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_slug_source = args.experiment_name or args.experiment_id or "latest-beta-baseline"
    slug = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in f"{args.project}-{experiment_slug_source}"
    ).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return base_output_dir / f"{timestamp}-{slug}"


def summarize_partition_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [
        row["total_turn_ms"]
        for row in rows
        if isinstance(row.get("total_turn_ms"), int)
    ]
    residuals = [
        row["partition_residual_ms"]
        for row in rows
        if isinstance(row.get("partition_residual_ms"), int)
    ]
    llm_context_residuals = [
        row["llm_context_residual_ms"]
        for row in rows
        if isinstance(row.get("llm_context_residual_ms"), int)
    ]
    share_means: dict[str, float | None] = {}
    for component in PARTITION_SHARE_COMPONENTS:
        key = f"{component}_share"
        values = [row[key] for row in rows if isinstance(row.get(key), float)]
        share_means[key] = (sum(values) / len(values)) if values else None

    sorted_totals = sorted(totals)
    return {
        "trace_count": len(rows),
        "mean_total_turn_ms": (sum(totals) / len(totals)) if totals else None,
        "mean_partition_residual_ms": (sum(residuals) / len(residuals))
        if residuals
        else None,
        "max_abs_partition_residual_ms": (
            max(abs(value) for value in residuals) if residuals else None
        ),
        "partition_error_count": sum(
            1 for row in rows if row.get("has_partition_error") is True
        ),
        "mean_llm_context_residual_ms": (
            sum(llm_context_residuals) / len(llm_context_residuals)
            if llm_context_residuals
            else None
        ),
        "max_abs_llm_context_residual_ms": (
            max(abs(value) for value in llm_context_residuals)
            if llm_context_residuals
            else None
        ),
        "llm_context_error_count": sum(
            1 for row in rows if row.get("has_llm_context_partition_error") is True
        ),
        "p50_total_turn_ms": percentile(sorted_totals, 0.50),
        "p90_total_turn_ms": percentile(sorted_totals, 0.90),
        "p95_total_turn_ms": percentile(sorted_totals, 0.95),
        "mean_component_shares": share_means,
    }


def write_run_outputs(
    *,
    output_dir: Path,
    trace_rows: list[dict[str, Any]],
    span_rows: list[dict[str, Any]],
    partition_rows: list[dict[str, Any]],
    args: SimpleNamespace,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_csv_path = output_dir / "trace_rows.csv"
    trace_jsonl_path = output_dir / "trace_rows.jsonl"
    span_csv_path = output_dir / "span_rows.csv"
    span_jsonl_path = output_dir / "span_rows.jsonl"
    partition_csv_path = output_dir / "trace_latency_partition_rows.csv"
    partition_jsonl_path = output_dir / "trace_latency_partition_rows.jsonl"
    metadata_path = output_dir / "run_metadata.json"

    write_csv(trace_csv_path, trace_rows)
    write_jsonl(trace_jsonl_path, trace_rows)
    write_csv(span_csv_path, span_rows)
    write_jsonl(span_jsonl_path, span_rows)
    write_csv(partition_csv_path, partition_rows)
    write_jsonl(partition_jsonl_path, partition_rows)

    metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": args.project,
        "experiment_id": args.experiment_id,
        "experiment_name": args.experiment_name,
        "trace_row_count": len(trace_rows),
        "span_row_count": len(span_rows),
        "partition_row_count": len(partition_rows),
        "partition_summary": summarize_partition_rows(partition_rows),
        "files": {
            "trace_csv": str(trace_csv_path),
            "trace_jsonl": str(trace_jsonl_path),
            "span_csv": str(span_csv_path),
            "span_jsonl": str(span_jsonl_path),
            "partition_csv": str(partition_csv_path),
            "partition_jsonl": str(partition_jsonl_path),
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def print_partition_summary(rows: list[dict[str, Any]]) -> None:
    summary = summarize_partition_rows(rows)
    print(f"Partition traces: {summary['trace_count']}")
    print(f"Mean total_turn_ms: {summary['mean_total_turn_ms']}")
    print(f"Mean partition_residual_ms: {summary['mean_partition_residual_ms']}")
    print(f"Max abs partition residual ms: {summary['max_abs_partition_residual_ms']}")
    print(f"Partition error count: {summary['partition_error_count']}")
    print(f"Mean llm_context_residual_ms: {summary['mean_llm_context_residual_ms']}")
    print(
        f"Max abs llm context residual ms: {summary['max_abs_llm_context_residual_ms']}"
    )
    print(f"LLM context error count: {summary['llm_context_error_count']}")
    print(
        "Latency percentiles ms: "
        f"p50={summary['p50_total_turn_ms']}, "
        f"p90={summary['p90_total_turn_ms']}, "
        f"p95={summary['p95_total_turn_ms']}"
    )


def run_export() -> Path:
    args = get_config()
    if args.page_size <= 0:
        raise RuntimeError("page_size must be positive")
    if args.root_id_batch_size <= 0:
        raise RuntimeError("root_id_batch_size must be positive")
    if args.max_btql_retries < 0:
        raise RuntimeError("max_btql_retries must be non-negative")
    if args.retry_backoff_seconds <= 0:
        raise RuntimeError("retry_backoff_seconds must be positive")
    if args.tool_analysis_window_ms <= 0:
        raise RuntimeError("tool_analysis_window_ms must be positive")

    api_key = require_api_key()
    braintrust.login(api_key=api_key)

    experiment = fetch_experiment_object(args)
    args.experiment_id = experiment.get("id")
    args.experiment_name = experiment.get("name")

    project_id = get_project_id(args.project)
    experiment_rows = fetch_experiment_rows(args.experiment_id, args)
    if not experiment_rows:
        raise RuntimeError("No experiment rows found")
    experiment_rows_by_trace = build_experiment_row_map(experiment_rows)
    trace_ids = list(experiment_rows_by_trace)
    if not trace_ids:
        raise RuntimeError("No trace_id values found in experiment row outputs")

    root_rows = list(
        iter_btql_rows(
            project_id=project_id,
            query_filter={
                "op": "and",
                "children": [
                    build_root_filter(),
                    build_string_field_filter(["id"], trace_ids),
                ],
            },
            page_size=args.page_size,
            query_source="latency_experiment_root_scan",
            page_desc="Experiment root pages",
            max_retries=args.max_btql_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
        )
    )
    if not root_rows:
        raise RuntimeError("No root trace rows matched the experiment trace ids")

    root_rows_by_trace_id = {
        str(row["id"]): row
        for row in root_rows
        if isinstance(row.get("id"), str) and row.get("id")
    }
    matched_trace_ids = [
        trace_id for trace_id in trace_ids if trace_id in root_rows_by_trace_id
    ]
    root_span_ids = [
        root_rows_by_trace_id[trace_id]["root_span_id"]
        for trace_id in matched_trace_ids
        if isinstance(root_rows_by_trace_id[trace_id].get("root_span_id"), str)
        and root_rows_by_trace_id[trace_id].get("root_span_id")
    ]
    if not root_span_ids:
        raise RuntimeError("Matched experiment trace ids had no root_span_id values")
    rows_by_root = fetch_rows_for_root_ids(project_id, root_span_ids, args)

    trace_rows: list[dict[str, Any]] = []
    span_rows: list[dict[str, Any]] = []
    partition_rows: list[dict[str, Any]] = []
    trace_bar = tqdm(matched_trace_ids, desc="Building dataframes", unit="trace")

    for trace_id in trace_bar:
        root_row = root_rows_by_trace_id[trace_id]
        root_span_id = root_row["root_span_id"]
        experiment_row = experiment_rows_by_trace.get(trace_id)
        rows = rows_by_root.get(root_span_id, [root_row])
        rows = sorted(
            rows,
            key=lambda row: (
                parse_iso_datetime(row.get("created"))
                or datetime.min.replace(tzinfo=UTC)
            ),
        )
        root_start_s, root_end_s = get_time_range_seconds(root_row)
        trace_row = build_trace_row(root_row, rows, experiment_row=experiment_row)
        trace_rows.append(trace_row)

        per_trace_span_rows: list[dict[str, Any]] = []
        for row in rows:
            span_row = build_span_row(
                row=row,
                root_row=root_row,
                root_start_s=root_start_s,
                root_end_s=root_end_s,
            )
            span_rows.append(span_row)
            per_trace_span_rows.append(span_row)

        partition_rows.append(
            build_partition_row(
                trace_row,
                per_trace_span_rows,
                tool_analysis_window_ms=args.tool_analysis_window_ms,
            )
        )

    output_dir = build_output_dir(args.output_dir, args)
    write_run_outputs(
        output_dir=output_dir,
        trace_rows=trace_rows,
        span_rows=span_rows,
        partition_rows=partition_rows,
        args=args,
    )

    print(f"Wrote {len(trace_rows)} trace rows to {output_dir / 'trace_rows.csv'}")
    print(f"Wrote {len(span_rows)} span rows to {output_dir / 'span_rows.csv'}")
    print(
        "Wrote "
        f"{len(partition_rows)} partition rows to "
        f"{output_dir / 'trace_latency_partition_rows.csv'}"
    )
    print(f"Experiment: {args.experiment_name} ({args.experiment_id})")
    print_partition_summary(partition_rows)
    return output_dir


def main() -> int:
    run_export()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
