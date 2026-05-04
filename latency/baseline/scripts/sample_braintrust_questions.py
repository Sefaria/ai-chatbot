#!/usr/bin/env python3
"""Sample chatbot questions from remote Braintrust project logs.

This script pulls traced chatbot questions from Braintrust, deduplicates them,
then writes deterministic per-seed samples into `latency/baseline/data/`.

Run:
    python latency/baseline/scripts/sample_braintrust_questions.py
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import braintrust
from dotenv import dotenv_values
from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "latency" / "baseline" / "data"
ENV_FILE = REPO_ROOT / "server" / ".env"
DEFAULT_PROJECT = "On Site Agent"
DEFAULT_BATCH_SIZE = 500
DEFAULT_EXCLUDED_ORIGINS = ("eval",)
DEFAULT_EXCLUDED_TAGS = ("dev",)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    # `server/.env` contains at least one semicolon-prefixed comment line, which
    # python-dotenv warns about. Strip those lines before parsing so this script
    # can load the Braintrust settings quietly.
    cleaned_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(";")
    ]
    for key, value in dotenv_values(stream=StringIO("\n".join(cleaned_lines))).items():
        if value is not None:
            os.environ.setdefault(key, value)


load_env_file(ENV_FILE)

# Edit these defaults directly when you want to change the sampled dataset.
SCRIPT_CONFIG = {
    "count": 10,
    "seeds": [1],
    "project": os.environ.get("BRAINTRUST_PROJECT", DEFAULT_PROJECT),
    "output_dir": DATA_DIR,
    "batch_size": DEFAULT_BATCH_SIZE,
    "max_records": None,
    "lookback_days": 30,
    "tags": [],
    "excluded_tags": list(DEFAULT_EXCLUDED_TAGS),
    "origins": [],
    "excluded_origins": list(DEFAULT_EXCLUDED_ORIGINS),
}


def get_config() -> SimpleNamespace:
    args = SimpleNamespace(
        count=SCRIPT_CONFIG["count"],
        seeds=list(SCRIPT_CONFIG["seeds"]),
        project=SCRIPT_CONFIG["project"],
        output_dir=Path(SCRIPT_CONFIG["output_dir"]),
        batch_size=SCRIPT_CONFIG["batch_size"],
        max_records=SCRIPT_CONFIG["max_records"],
        lookback_days=SCRIPT_CONFIG["lookback_days"],
        tags=list(SCRIPT_CONFIG["tags"]),
        excluded_tags=list(SCRIPT_CONFIG["excluded_tags"]),
        origins=list(SCRIPT_CONFIG["origins"]),
        excluded_origins=list(SCRIPT_CONFIG["excluded_origins"]),
    )
    args.cutoff_datetime = get_cutoff_datetime(args)
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


def build_btql_filter() -> dict[str, Any]:
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
                "right": {"op": "literal", "value": "chat-agent"},
            },
        ],
    }


def fetch_project_log_rows(
    project_id: str, args: SimpleNamespace
) -> list[dict[str, Any]]:
    cursor: str | None = None
    page_bar = tqdm(desc="Braintrust pages", unit="page")

    try:
        while True:
            query: dict[str, Any] = {
                "select": [{"op": "star"}],
                "from": {
                    "op": "function",
                    "name": {"op": "ident", "name": ["project_logs"]},
                    "args": [{"op": "literal", "value": project_id}],
                },
                "filter": build_btql_filter(),
                "limit": args.batch_size,
            }
            if cursor:
                query["cursor"] = cursor

            response = braintrust.api_conn().post(
                "btql",
                json={
                    "query": query,
                    "use_columnstore": False,
                    "brainstore_realtime": True,
                    "query_source": "latency_baseline_sampler",
                },
                headers={"Accept-Encoding": "gzip"},
            )
            response.raise_for_status()
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


def normalize_question(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    return normalized


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


def get_cutoff_datetime(args: SimpleNamespace) -> datetime | None:
    if args.lookback_days is None:
        return None
    return datetime.now(UTC) - timedelta(days=args.lookback_days)


def build_virtual_bundle(row: dict[str, Any], question: str) -> dict[str, Any] | None:
    row_input = row.get("input") or {}
    if not isinstance(row_input, dict):
        row_input = {}

    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    root_span_id = row.get("root_span_id")
    if not isinstance(root_span_id, str) or not root_span_id:
        return None

    return {
        "question": question,
        "root_span_id": root_span_id,
        "created": row.get("created"),
        "summary_text": row_input.get("summary"),
        "page_url": row_input.get("page_url"),
        "session_id": metadata.get("session_id"),
        "origin": metadata.get("origin"),
        "model": metadata.get("model"),
        "route": metadata.get("route"),
        "effective_core_prompt_id": metadata.get("core_prompt_id"),
        "effective_core_prompt_version": metadata.get("core_prompt_version"),
        "summary_included": metadata.get("summary_included"),
    }


def row_matches_filters(row: dict[str, Any], args: SimpleNamespace) -> bool:
    if not isinstance(row, dict):
        return False
    created = parse_iso_datetime(row.get("created"))
    if args.cutoff_datetime is not None and (
        created is None or created < args.cutoff_datetime
    ):
        return False
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    origin = metadata.get("origin")
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    if args.origins and origin not in args.origins:
        return False
    if origin in args.excluded_origins:
        return False
    if args.tags and not all(tag in tags for tag in args.tags):
        return False
    if any(tag in tags for tag in args.excluded_tags):
        return False
    return True


def iter_qualifying_questions(project_id: str, args: SimpleNamespace):
    row_bar = tqdm(desc="Qualifying rows", unit="row")
    yielded = 0

    try:
        for row in fetch_project_log_rows(project_id, args):
            if args.max_records is not None and yielded >= args.max_records:
                return
            if not row_matches_filters(row, args):
                continue
            row_input = row.get("input")
            if not isinstance(row_input, dict):
                continue
            question = normalize_question(row_input.get("message"))
            if not question:
                continue
            bundle = build_virtual_bundle(row, question)
            if bundle is None:
                continue
            yielded += 1
            row_bar.update(1)
            yield yielded - 1, bundle
    finally:
        row_bar.close()


def count_qualifying_questions(project_id: str, args: SimpleNamespace) -> int:
    total = 0
    for _, _item in iter_qualifying_questions(project_id, args):
        total += 1
    return total


def sample_positions(total_count: int, count: int, seed: int) -> list[int]:
    if count > total_count:
        raise RuntimeError(
            f"Requested {count} questions, but only {total_count} qualifying questions were found"
        )
    sampler = random.Random(seed)
    return sorted(sampler.sample(range(total_count), count))


def collect_sampled_questions(
    project_id: str,
    args: SimpleNamespace,
    positions_by_seed: dict[int, list[int]],
) -> dict[int, list[dict[str, str]]]:
    targets_to_seed_positions: dict[int, list[tuple[int, int]]] = {}
    for seed, positions in positions_by_seed.items():
        for order, position in enumerate(positions):
            targets_to_seed_positions.setdefault(position, []).append((seed, order))

    sampled_questions = {
        seed: [{} for _ in positions] for seed, positions in positions_by_seed.items()
    }
    remaining_targets = set(targets_to_seed_positions)
    progress = tqdm(total=len(remaining_targets), desc="Selected docs", unit="doc")

    try:
        for position, item in iter_qualifying_questions(project_id, args):
            if position not in remaining_targets:
                continue
            for seed, order in targets_to_seed_positions[position]:
                sampled_questions[seed][order] = item
            remaining_targets.remove(position)
            progress.update(1)
            if not remaining_targets:
                break
    finally:
        progress.close()

    if remaining_targets:
        raise RuntimeError(
            f"Did not collect all sampled questions; {len(remaining_targets)} targets were missing"
        )

    return sampled_questions


def output_path(output_dir: Path, project: str, count: int, seed: int) -> Path:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in project).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return output_dir / f"{slug}-n{count}-seed{seed}.json"


def write_sample(
    *,
    output_dir: Path,
    project: str,
    seed: int,
    count: int,
    sampled_questions: list[dict[str, Any]],
    total_questions: int,
    args: SimpleNamespace,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_path(output_dir, project, count, seed)
    payload = {
        "project": project,
        "sample_size": count,
        "seed": seed,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_questions": total_questions,
        "filters": {
            "origins": args.origins,
            "excluded_origins": args.excluded_origins,
            "tags": args.tags,
            "excluded_tags": args.excluded_tags,
            "max_records": args.max_records,
            "lookback_days": args.lookback_days,
            "cutoff_datetime_utc": (
                args.cutoff_datetime.isoformat()
                if args.cutoff_datetime is not None
                else None
            ),
        },
        "questions": sampled_questions,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def main() -> int:
    args = get_config()
    if args.count <= 0:
        raise RuntimeError("--count must be positive")
    if args.batch_size <= 0:
        raise RuntimeError("--batch-size must be positive")
    if args.max_records is not None and args.max_records <= 0:
        raise RuntimeError("--max-records must be positive when provided")
    if args.lookback_days is not None and args.lookback_days <= 0:
        raise RuntimeError("--lookback_days must be positive when provided")

    api_key = require_api_key()
    braintrust.login(api_key=api_key)

    project_id = get_project_id(args.project)
    total_questions = count_qualifying_questions(project_id, args)
    if total_questions == 0:
        raise RuntimeError(
            "No questions found in Braintrust project logs for the requested filters"
        )

    positions_by_seed = {
        seed: sample_positions(total_questions, args.count, seed) for seed in args.seeds
    }
    sampled_questions_by_seed = collect_sampled_questions(
        project_id, args, positions_by_seed
    )

    print(f"Found {total_questions} qualifying questions in project {args.project!r}.")

    for seed in tqdm(args.seeds, desc="Writing samples", unit="seed"):
        path = write_sample(
            output_dir=args.output_dir,
            project=args.project,
            seed=seed,
            count=args.count,
            sampled_questions=sampled_questions_by_seed[seed],
            total_questions=total_questions,
            args=args,
        )
        print(f"Wrote {len(sampled_questions_by_seed[seed])} questions to {path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
