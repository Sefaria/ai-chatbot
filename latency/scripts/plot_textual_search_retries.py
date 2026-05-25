#!/usr/bin/env python3
"""Visual analysis of repeated same-tool textual search retries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "latency" / "analysis"
PARTITION_FILENAME = "trace_latency_partition_rows.csv"
SPAN_FILENAME = "span_rows.csv"
TEXTUAL_SEARCH_TOOLS = [
    "text_search",
    "search_in_book",
    "english_semantic_search",
    "search_in_dictionaries",
    "catalog_search",
]
DISPLAY_TOOL_NAMES = {
    "text_search": "Text search",
    "search_in_book": "Search in book",
    "english_semantic_search": "English semantic search",
    "search_in_dictionaries": "Search in dictionaries",
    "catalog_search": "Catalog search",
}
SCRIPT_CONFIG = {
    "input_csv": None,
    "output_dir": None,
    "min_llm_gap_ms": 0,
    "figure_size": (13, 11),
}


def require_plotting_deps():
    try:
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "This script requires pandas, numpy, and matplotlib. "
            "Install them with: pip install pandas numpy matplotlib"
        ) from exc
    return pd, np, plt


def find_latest_file(data_dir: Path, filename: str) -> Path:
    candidates = sorted(
        data_dir.glob(f"*/{filename}"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError(f"Could not find {filename} under {data_dir}")
    return candidates[-1]


def get_input_csv() -> Path:
    configured = SCRIPT_CONFIG["input_csv"]
    return (
        Path(configured)
        if configured
        else find_latest_file(DATA_DIR, PARTITION_FILENAME)
    )


def get_span_csv(input_csv: Path) -> Path:
    sibling = input_csv.parent / SPAN_FILENAME
    if sibling.exists():
        return sibling
    return find_latest_file(DATA_DIR, SPAN_FILENAME)


def get_output_dir(input_csv: Path) -> Path:
    configured = SCRIPT_CONFIG["output_dir"]
    return Path(configured) if configured else input_csv.parent


def safe_json_loads(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def normalize_json_string(value: Any) -> str:
    loaded = safe_json_loads(value)
    if loaded is None:
        return str(value or "")
    return json.dumps(loaded, ensure_ascii=False, sort_keys=True)


def get_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def format_seconds_from_ms(value_ms: float) -> str:
    seconds = value_ms / 1000.0
    if abs(seconds) >= 100:
        return f"{seconds:.0f}s"
    if abs(seconds) >= 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.2f}s"


def format_share(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def unwrap_tool_output_payload(output_json: Any) -> Any:
    payload = safe_json_loads(output_json)
    current = payload
    for _ in range(4):
        if isinstance(current, dict) and "content" in current and len(current) == 1:
            next_payload = safe_json_loads(current.get("content"))
            if next_payload is None:
                return current.get("content")
            current = next_payload
            continue
        break
    return current


def looks_empty_or_insufficient(output_json: Any) -> bool:
    payload = unwrap_tool_output_payload(output_json)
    if payload in (None, "", [], {}, [None]):
        return True
    if isinstance(payload, dict):
        if payload.get("no_results") is True:
            return True
        for key in ("results", "items", "matches"):
            if key in payload and payload.get(key) in ([], {}, "", None):
                return True
    if isinstance(payload, list):
        return len(payload) == 0
    text = str(payload if payload is not None else output_json or "").lower()
    empty_markers = [
        '"results": []',
        '"matches": []',
        '"items": []',
        '"no_results": true',
        "no results",
        "0 results",
        "not found",
        "no matching",
    ]
    return any(marker in text for marker in empty_markers)


def extract_llm_between_ms(
    llm_rows: list[dict[str, Any]],
    current_end_s: float,
    next_start_s: float,
) -> int:
    total_ms = 0
    for row in llm_rows:
        start_s = get_float(row.get("span_start_s"))
        end_s = get_float(row.get("span_end_s"))
        if start_s is None or end_s is None or end_s <= start_s:
            continue
        clipped_start = max(start_s, current_end_s)
        clipped_end = min(end_s, next_start_s)
        if clipped_end <= clipped_start:
            continue
        total_ms += int(round((clipped_end - clipped_start) * 1000))
    return total_ms


def build_retry_dataframe(partition_df: Any, span_df: Any, pd: Any):
    valid_partition = partition_df[
        partition_df["total_turn_ms"].notna()
        & (~partition_df["has_partition_error"])
        & (~partition_df["has_llm_context_partition_error"])
    ].copy()
    trace_ids = set(valid_partition["root_span_id"].astype(str))

    span_df = span_df[
        span_df["root_span_id"].astype(str).isin(trace_ids)
        & (span_df["within_root_window"])
        & span_df["span_category"].isin(["tool", "llm"])
    ].copy()
    span_df["span_start_s_num"] = pd.to_numeric(
        span_df["span_start_s"], errors="coerce"
    )
    span_df["span_end_s_num"] = pd.to_numeric(span_df["span_end_s"], errors="coerce")
    span_df = span_df.sort_values(
        ["root_span_id", "span_start_s_num", "span_end_s_num", "span_category"]
    )

    retry_rows: list[dict[str, Any]] = []
    trace_flags: dict[str, dict[str, Any]] = {
        root_span_id: {
            "any_retry": False,
            "any_empty_first": False,
            "tools": set(),
            "retry_llm_between_ms": 0,
        }
        for root_span_id in trace_ids
    }

    grouped = span_df.groupby("root_span_id", sort=False)
    for root_span_id, group in grouped:
        rows = group.to_dict("records")
        for index, row in enumerate(rows):
            if row.get("span_category") != "tool":
                continue
            tool_name = row.get("span_name")
            if tool_name not in TEXTUAL_SEARCH_TOOLS:
                continue

            llm_rows: list[dict[str, Any]] = []
            next_tool: dict[str, Any] | None = None
            for later_row in rows[index + 1 :]:
                category = later_row.get("span_category")
                if category == "llm":
                    llm_rows.append(later_row)
                    continue
                if category == "tool":
                    next_tool = later_row
                    break

            if (
                next_tool is None
                or next_tool.get("span_name") != tool_name
                or not llm_rows
            ):
                continue

            current_end_s = get_float(row.get("span_end_s_num"))
            next_start_s = get_float(next_tool.get("span_start_s_num"))
            if (
                current_end_s is None
                or next_start_s is None
                or next_start_s <= current_end_s
            ):
                continue

            llm_between_ms = extract_llm_between_ms(
                llm_rows, current_end_s, next_start_s
            )
            if llm_between_ms < SCRIPT_CONFIG["min_llm_gap_ms"]:
                continue

            first_input = normalize_json_string(row.get("input_json"))
            second_input = normalize_json_string(next_tool.get("input_json"))
            changed_input = first_input != second_input
            first_empty = looks_empty_or_insufficient(row.get("output_json"))

            retry_rows.append(
                {
                    "root_span_id": root_span_id,
                    "tool_name": tool_name,
                    "changed_input": changed_input,
                    "first_output_empty": first_empty,
                    "llm_between_ms": llm_between_ms,
                    "llm_span_count_between": len(llm_rows),
                    "first_tool_duration_ms": int(float(row.get("duration_ms") or 0)),
                    "second_tool_duration_ms": int(
                        float(next_tool.get("duration_ms") or 0)
                    ),
                    "first_input_json": first_input,
                    "second_input_json": second_input,
                }
            )

            trace_flags[root_span_id]["any_retry"] = True
            trace_flags[root_span_id]["tools"].add(tool_name)
            trace_flags[root_span_id]["retry_llm_between_ms"] += llm_between_ms
            if first_empty:
                trace_flags[root_span_id]["any_empty_first"] = True

    retry_df = pd.DataFrame(retry_rows)
    return valid_partition, retry_df, trace_flags


def build_validation_lines(
    retry_df: Any, valid_partition: Any, trace_flags: dict[str, dict[str, Any]]
) -> list[str]:
    if retry_df.empty:
        return ["No retry sequences detected, so validation diagnostics are empty."]

    retry_trace_ids = set(retry_df["root_span_id"].astype(str))
    recomputed_trace_count = len(retry_trace_ids)
    flag_trace_count = sum(1 for flags in trace_flags.values() if flags["any_retry"])

    changed_input_rate = float(retry_df["changed_input"].mean())
    unchanged_input_rate = 1.0 - changed_input_rate
    llm_span_count_positive_rate = float(
        (retry_df["llm_span_count_between"] > 0).mean()
    )
    non_empty_first_rate = float((~retry_df["first_output_empty"]).mean())

    partition_lookup = valid_partition.set_index("root_span_id")
    comparable_retry_traces = 0
    retry_llm_within_trace_partition_count = 0
    for root_span_id, group in retry_df.groupby("root_span_id"):
        if root_span_id not in partition_lookup.index:
            continue
        comparable_retry_traces += 1
        retry_llm_between_ms = float(group["llm_between_ms"].sum())
        trace_tool_analysis_ms = float(
            partition_lookup.loc[root_span_id, "llm_tool_analysis_wall_ms"]
        )
        if retry_llm_between_ms <= trace_tool_analysis_ms + 5:
            retry_llm_within_trace_partition_count += 1

    lines = [
        "Validation diagnostics:",
        f"- Retry traces recomputed from sequences: {recomputed_trace_count}",
        f"- Retry traces from trace flags: {flag_trace_count}",
        f"- Retry sequences with changed input: {changed_input_rate * 100:.1f}%",
        f"- Retry sequences with unchanged input: {unchanged_input_rate * 100:.1f}%",
        f"- Retry sequences with at least one intervening LLM span: {llm_span_count_positive_rate * 100:.1f}%",
        f"- Retry sequences where first output was non-empty: {non_empty_first_rate * 100:.1f}%",
    ]
    if comparable_retry_traces > 0:
        lines.append(
            "- Retry traces where summed retry-LLM time stays within that trace's "
            f"llm_tool_analysis partition: {retry_llm_within_trace_partition_count}/{comparable_retry_traces} "
            f"({retry_llm_within_trace_partition_count / comparable_retry_traces * 100:.1f}%)"
        )

    by_tool = (
        retry_df.groupby("tool_name")
        .agg(
            retry_count=("tool_name", "size"),
            changed_input_rate=("changed_input", "mean"),
            first_output_empty_rate=("first_output_empty", "mean"),
            avg_llm_between_ms=("llm_between_ms", "mean"),
        )
        .sort_values("retry_count", ascending=False)
    )
    lines.extend(
        [
            "",
            "Validation by tool:",
            by_tool.to_string(),
        ]
    )
    return lines


def plot_retry_figure(
    *,
    retry_df: Any,
    valid_partition: Any,
    trace_flags: dict[str, dict[str, Any]],
    np: Any,
    plt: Any,
    output_dir: Path,
) -> list[str]:
    total_trace_count = len(valid_partition)
    total_llm_tool_analysis_ms = float(
        valid_partition["llm_tool_analysis_wall_ms"].sum()
    )
    traces_with_retry = sum(1 for flags in trace_flags.values() if flags["any_retry"])
    retry_trace_rate = (
        traces_with_retry / total_trace_count if total_trace_count else 0.0
    )
    retry_llm_between_ms_total = sum(
        float(flags["retry_llm_between_ms"]) for flags in trace_flags.values()
    )
    retry_vs_total_share = (
        retry_llm_between_ms_total / total_llm_tool_analysis_ms
        if total_llm_tool_analysis_ms > 0
        else 0.0
    )

    fig, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(SCRIPT_CONFIG["figure_size"][0], 8.8),
        gridspec_kw={"height_ratios": [1.0, 1.2]},
    )
    fig.suptitle("Repeated Same-Tool Textual Search Retries", fontsize=17, y=0.985)
    fig.text(
        0.5,
        0.958,
        "Pattern: same textual search tool called again after intervening LLM reasoning and before any different tool call.",
        ha="center",
        va="top",
        fontsize=10,
        color="#444444",
    )
    fig.text(
        0.5,
        0.938,
        (
            f"Trace retry rate: {retry_trace_rate * 100:.1f}%   "
            f"Estimated total retry-related LLM time: {format_seconds_from_ms(retry_llm_between_ms_total)}   "
            f"Share of total LLM tool-analysis time: {retry_vs_total_share * 100:.1f}%"
        ),
        ha="center",
        va="top",
        fontsize=10,
        color="#222222",
        fontweight="bold",
    )
    tool_trace_rates = []
    llm_between_totals = []
    for tool_name in TEXTUAL_SEARCH_TOOLS:
        display_name = DISPLAY_TOOL_NAMES.get(tool_name, tool_name)
        trace_count = sum(
            1 for flags in trace_flags.values() if tool_name in flags["tools"]
        )
        tool_trace_rates.append(
            (
                display_name,
                trace_count / total_trace_count if total_trace_count else 0.0,
            )
        )

        tool_retry_df = (
            retry_df[retry_df["tool_name"] == tool_name]
            if not retry_df.empty
            else retry_df
        )
        llm_total = (
            float(tool_retry_df["llm_between_ms"].sum())
            if not tool_retry_df.empty
            else 0.0
        )
        llm_between_totals.append((display_name, llm_total))

    # Subplot 1: trace rate by tool.
    ax = axes[0]
    names = [name for name, _ in tool_trace_rates]
    values = [value for _, value in tool_trace_rates]
    bars = ax.barh(names, values, color="#4C78A8", alpha=0.9)
    ax.set_title("Share of traces containing a same-tool retry", fontsize=13, pad=10)
    ax.set_xlabel("Fraction of valid traces")
    ax.set_xlim(0, max(values + [0.01]) * 1.18)
    for bar, value in zip(bars, values, strict=False):
        ax.text(
            bar.get_width() + 0.003,
            bar.get_y() + bar.get_height() / 2,
            f"{value * 100:.1f}%",
            va="center",
            ha="left",
            fontsize=9,
        )

    # Subplot 2: estimated retry-related LLM time by tool.
    ax = axes[1]
    llm_values_ms = [value for _, value in llm_between_totals]
    llm_values_seconds = [value / 1000.0 for value in llm_values_ms]
    bars = ax.barh(names, llm_values_seconds, color="#59A14F", alpha=0.9)
    ax.set_title(
        "Estimated total LLM reasoning time between repeated same-tool searches",
        fontsize=13,
        pad=10,
    )
    ax.set_xlabel("Estimated total LLM time between repeated searches (s)")
    ax.set_xlim(0, max(llm_values_seconds + [0.01]) * 1.18)
    for bar, value_ms, value_s in zip(
        bars, llm_values_ms, llm_values_seconds, strict=False
    ):
        share = (
            value_ms / total_llm_tool_analysis_ms
            if total_llm_tool_analysis_ms > 0
            else 0.0
        )
        ax.text(
            bar.get_width() + max(ax.get_xlim()[1] * 0.005, 0.05),
            bar.get_y() + bar.get_height() / 2,
            f"{value_s:.1f}s  ({share * 100:.1f}% of total)",
            va="center",
            ha="left",
            fontsize=9,
        )

    overall_changed_input_rate = (
        float(retry_df["changed_input"].mean()) if not retry_df.empty else 0.0
    )

    fig.text(
        0.01,
        0.065,
        "Case-retrieval rules: detect a textual-search tool call, require at least one intervening LLM span, then require the next tool call before any different tool to be the same search tool again.",
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="#555555",
    )
    fig.text(
        0.01,
        0.041,
        f"Validation note: {overall_changed_input_rate * 100:.1f}% of detected retry sequences changed the tool input. Estimated retry-related LLM time is descriptive, not causal.",
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="#555555",
    )
    fig.text(
        0.01,
        0.017,
        "Top panel is a trace-level rate. Bottom panel is a total-time decomposition by tool, not a mean.",
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.10, 1, 0.88))
    png_path = output_dir / "textual_search_retry_analysis.png"
    pdf_path = output_dir / "textual_search_retry_analysis.pdf"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)

    overall_non_empty_first_rate = (
        float((~retry_df["first_output_empty"]).mean()) if not retry_df.empty else 0.0
    )
    summary_lines = [
        f"Trace count: {total_trace_count}",
        f"Traces with same-tool textual search retry: {traces_with_retry} ({retry_trace_rate * 100:.1f}%)",
        f"Estimated retry-related LLM time: {retry_llm_between_ms_total / 1000.0:.1f} s",
        f"Share of total llm_tool_analysis_wall_ms: {retry_vs_total_share * 100:.1f}%",
        f"Retry sequences with non-empty first result: {overall_non_empty_first_rate * 100:.1f}%",
    ]
    if not retry_df.empty:
        summary_lines.extend(
            [
                "",
                "Retry sequences by tool:",
                retry_df.groupby("tool_name")
                .agg(
                    retry_count=("tool_name", "size"),
                    trace_count=("root_span_id", "nunique"),
                    first_output_empty_rate=("first_output_empty", "mean"),
                    avg_llm_between_ms=("llm_between_ms", "mean"),
                    total_llm_between_ms=("llm_between_ms", "sum"),
                )
                .to_string(),
            ]
        )
    summary_lines.extend(
        ["", *build_validation_lines(retry_df, valid_partition, trace_flags)]
    )
    return summary_lines


def main() -> int:
    pd, np, plt = require_plotting_deps()
    input_csv = get_input_csv()
    span_csv = get_span_csv(input_csv)
    output_dir = get_output_dir(input_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    partition_df = pd.read_csv(input_csv)
    span_df = pd.read_csv(span_csv)
    valid_partition, retry_df, trace_flags = build_retry_dataframe(
        partition_df, span_df, pd
    )
    summary_lines = plot_retry_figure(
        retry_df=retry_df,
        valid_partition=valid_partition,
        trace_flags=trace_flags,
        np=np,
        plt=plt,
        output_dir=output_dir,
    )
    summary_path = output_dir / "textual_search_retry_analysis.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))
    print(f"\nWrote retry analysis plot and summary to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
