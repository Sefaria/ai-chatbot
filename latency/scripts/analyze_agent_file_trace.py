#!/usr/bin/env python3
"""Summarize local agent-loop JSONL traces produced by AGENT_FILE_TRACE_ENABLED."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Trace JSONL file not found: {path}")
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def event_duration(
    events: list[dict[str, Any]], start_type: str, end_type: str
) -> int | None:
    start = next((event for event in events if event["event_type"] == start_type), None)
    end = next((event for event in events if event["event_type"] == end_type), None)
    if not start or not end:
        return None
    return int(end["elapsed_ms"]) - int(start["elapsed_ms"])


def summarize_turn(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: event["elapsed_ms"])
    first = ordered[0]
    last = ordered[-1]
    counts = Counter(event["event_type"] for event in ordered)
    sdk_messages = [
        event for event in ordered if event["event_type"] == "sdk_message_received"
    ]
    sdk_message_types = Counter(
        ((event.get("payload") or {}).get("message_type") or "unknown")
        for event in sdk_messages
    )
    tool_names = Counter(
        ((event.get("payload") or {}).get("tool_name") or "unknown")
        for event in ordered
        if event["event_type"] == "tool_call_start"
    )
    first_final_token = next(
        (
            event
            for event in ordered
            if event["event_type"] == "progress_update"
            and ((event.get("payload") or {}).get("update") or {}).get("type")
            == "message_delta"
        ),
        None,
    )

    return {
        "run_id": first.get("run_id"),
        "trace_id": first.get("trace_id"),
        "session_id": first.get("session_id"),
        "turn_id": first.get("turn_id"),
        "event_count": len(ordered),
        "total_elapsed_ms": int(last["elapsed_ms"]),
        "guardrail_ms": event_duration(ordered, "guardrail_start", "guardrail_end"),
        "router_ms": event_duration(ordered, "router_start", "router_end"),
        "sdk_run_ms": event_duration(ordered, "sdk_run_start", "sdk_run_end"),
        "time_to_first_final_response_token_ms": (
            int(first_final_token["elapsed_ms"]) if first_final_token else None
        ),
        "sdk_message_count": len(sdk_messages),
        "sdk_message_types_json": json.dumps(dict(sdk_message_types), sort_keys=True),
        "tool_call_count": counts["tool_call_start"],
        "tool_names_json": json.dumps(dict(tool_names), sort_keys=True),
        "event_type_counts_json": json.dumps(dict(counts), sort_keys=True),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "trace_jsonl",
        nargs="?",
        default="latency/runs/agent-loop-debug/events.jsonl",
        help="Path to AGENT_FILE_TRACE_PATH JSONL output.",
    )
    parser.add_argument(
        "--out",
        default="latency/runs/agent-loop-debug/turn_summary.csv",
        help="CSV summary path.",
    )
    args = parser.parse_args()

    events = load_events(Path(args.trace_jsonl))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(event.get("run_id") or "", event.get("trace_id") or "")].append(event)

    rows = [summarize_turn(turn_events) for turn_events in grouped.values()]
    rows.sort(key=lambda row: (row["run_id"], row["trace_id"]))
    write_csv(rows, Path(args.out))
    print(f"Wrote {len(rows)} turn summaries to {args.out}")


if __name__ == "__main__":
    main()
