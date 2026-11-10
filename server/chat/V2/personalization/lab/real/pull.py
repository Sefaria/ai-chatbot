"""Anonymized real-data pull from a local Braintrust export JSON file into $LA_LAB_DATA_DIR."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ...llm import ClaudeCodeLLMClient, LLMClient
from ...signals import KeywordSensitiveScreen, SensitiveScreen
from ..datasets import checksum
from . import scrub

DEFAULT_FIELDS_PATH = Path(__file__).with_name("fields.json")
DATASET_NAMES = ("anon-personas", "anon-vocab", "anon-visit", "anon-impact")
VISIT_CONTEXT_FIELDS = ("site", "browser_lang", "country")
REQUIRED_FIELD_KEYS = ("user_id", "conversation_id", "timestamp", "user_text", "assistant_text")
SHORT_REPLY_MAX_WORDS = 4
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ISO_TIME_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")


class PullError(RuntimeError):
    """Invalid pull configuration, missing staff marker, or path inside a git repository."""


class Source(Protocol):
    def rows(self) -> list[dict[str, Any]]: ...


class FakeSource:
    """In-memory row source for unit tests."""

    def __init__(self, rows: Iterable[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._rows)


class ExportFileSource:
    """Reads Braintrust root-span rows exported to a local JSON file outside any git repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        _assert_outside_git_repo(self.path, "export file")

    def rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        _assert_outside_git_repo(self.path, "export file")
        if not self.path.is_file():
            raise PullError(f"export file does not exist: {self.path}")
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if all(isinstance(item, dict) for item in lines):
                return lines
            raise PullError(f"export file is not valid JSON: {self.path}") from None
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            return [r for r in parsed["data"] if isinstance(r, dict)]
        raise PullError(
            f"export file must contain a list of rows or {{'data': [...]}}: {self.path}"
        )


def _is_inside_git_repo(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            return True
    return False


def _assert_outside_git_repo(path: Path, label: str) -> None:
    if _is_inside_git_repo(path):
        raise PullError(f"refusing {label} inside a git repository: {path}")


def _get_dotted(row: dict[str, Any], dotted_path: str | None) -> Any:
    if not dotted_path or not isinstance(dotted_path, str):
        return None
    if dotted_path in row:
        return row[dotted_path]
    cur: Any = row
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _extract_text(raw: Any, role: str) -> str | None:
    if isinstance(raw, str):
        return raw if raw.strip() else None
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str) and content.strip():
            return content
        messages = raw.get("messages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == role:
                    msg_content = msg.get("content")
                    if isinstance(msg_content, str) and msg_content.strip():
                        return msg_content
                    return None
    return None


def _normalize_timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if _ISO_TIME_RE.match(text):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_fields(fields: dict[str, Any]) -> tuple[str, list[Any], str]:
    if not isinstance(fields, dict):
        raise PullError("fields configuration must be a JSON object")
    for key in REQUIRED_FIELD_KEYS:
        val = fields.get(key)
        if not isinstance(val, str) or not val.strip():
            raise PullError(f"missing required field mapping: {key}")
    marker = fields.get("staff_marker")
    if not isinstance(marker, dict):
        raise PullError("missing staff_marker in fields configuration")
    path = marker.get("path") or marker.get("column")
    staff_values = marker.get("staff_values")
    rule = marker.get("rule", "drop_user_if_any")
    if not isinstance(path, str) or not path.strip():
        raise PullError("staff_marker must specify a non-empty path")
    if not isinstance(staff_values, list) or not staff_values:
        raise PullError("staff_marker must specify a non-empty staff_values list")
    if rule not in ("drop_user_if_any", "drop_row"):
        raise PullError(f"unsupported staff_marker rule: {rule!r}")
    return path, staff_values, rule


def _matches_staff_value(actual: Any, staff_values: list[Any]) -> bool:
    for expected in staff_values:
        if isinstance(expected, bool):
            if isinstance(actual, bool) and actual is expected:
                return True
        elif not isinstance(actual, bool) and actual == expected:
            return True
    return False


def _write_dataset_files(
    directory: Path,
    data_filename: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data_path = directory / data_filename
    data_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    names = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name != "sha256")
    sha = checksum(directory, names)
    (directory / "sha256").write_text(f"{sha}\n", encoding="utf-8")


def run(
    *,
    fields: dict[str, Any],
    out: str | Path,
    source: Source | None = None,
    export_path: str | Path | None = None,
    min_turns: int = 4,
    max_turns: int = 8,
    target_users: int = 200,
    llm: LLMClient | None = None,
    screen: SensitiveScreen | None = None,
    model: str = scrub.DEFAULT_SCRUB_MODEL,
    workers: int = 4,
    **_unused: Any,
) -> dict[str, dict[str, Any]]:
    """Run the anonymized real-data pull and write the four datasets under `out`."""
    staff_path, staff_values, staff_rule = _validate_fields(fields)
    out_dir = Path(out)
    _assert_outside_git_repo(out_dir, "output directory")

    if source is None:
        if export_path is None:
            raise PullError("either source or export_path is required")
        source = ExportFileSource(export_path)

    raw_rows = source.rows()
    sensitive_screen = screen or KeywordSensitiveScreen()
    llm_client = llm or ClaudeCodeLLMClient()

    # Pass 1: identify staff rows and (when rule == drop_user_if_any) staff users.
    staff_users: set[str] = set()
    non_staff_rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        raw_uid = _get_dotted(raw_row, fields["user_id"])
        uid = str(raw_uid).strip() if raw_uid is not None else ""
        is_staff = _matches_staff_value(_get_dotted(raw_row, staff_path), staff_values)
        if is_staff:
            if uid and staff_rule == "drop_user_if_any":
                staff_users.add(uid)
            continue
        non_staff_rows.append(raw_row)

    # Pass 2: extract fields, drop rows from staff users or unreadable rows, and screen sensitive turns.
    unreadable_dropped = 0
    sensitive_dropped = 0
    readable_non_staff_turns = 0
    by_user: dict[str, list[dict[str, Any]]] = {}

    for raw_row in non_staff_rows:
        raw_uid = _get_dotted(raw_row, fields["user_id"])
        uid = str(raw_uid).strip() if raw_uid is not None else ""
        if not uid or uid in staff_users:
            continue
        raw_cid = _get_dotted(raw_row, fields["conversation_id"])
        cid = str(raw_cid).strip() if raw_cid is not None else ""
        ts = _normalize_timestamp(_get_dotted(raw_row, fields["timestamp"]))
        user_text = _extract_text(_get_dotted(raw_row, fields["user_text"]), "user")
        assistant_text = _extract_text(_get_dotted(raw_row, fields["assistant_text"]), "assistant")
        if not cid or not ts or user_text is None or assistant_text is None:
            unreadable_dropped += 1
            continue

        readable_non_staff_turns += 1
        if sensitive_screen.is_sensitive(user_text):
            sensitive_dropped += 1
            continue

        context: dict[str, Any] = {}
        for ctx_key in VISIT_CONTEXT_FIELDS:
            mapped_path = fields.get(ctx_key)
            if isinstance(mapped_path, str) and mapped_path.strip():
                context[ctx_key] = _get_dotted(raw_row, mapped_path)

        by_user.setdefault(uid, []).append(
            {
                "uid": uid,
                "cid": cid,
                "at": ts,
                "user": user_text,
                "assistant": assistant_text,
                "context": context,
            }
        )

    # Pass 3: filter to learners with min_turns..max_turns user turns, capped at target_users.
    kept_users: list[tuple[str, list[dict[str, Any]]]] = []
    for uid, turns in by_user.items():
        if min_turns <= len(turns) <= max_turns:
            turns_sorted = sorted(turns, key=lambda t: t["at"])
            kept_users.append((uid, turns_sorted))
            if len(kept_users) >= target_users:
                break

    # Pass 4: pseudonymize and scrub every kept turn once.
    pseudonyms = scrub.Pseudonymizer()
    scrub_hits: Counter[str] = Counter({"email": 0, "phone": 0, "url": 0, "number": 0, "name": 0})
    name_pass_failures = 0

    personas_rows: list[dict[str, Any]] = []
    vocab_rows: list[dict[str, Any]] = []
    visit_rows: list[dict[str, Any]] = []
    impact_rows: list[dict[str, Any]] = []

    prompt_counter = 0
    hebrew_prompts = 0
    short_replies = 0
    total_kept_turns = 0

    # Deterministic scrub first, then the name pass over every text (parallel; order preserved).
    clean_texts: list[str] = []
    for _, turns in kept_users:
        for turn in turns:
            user_clean, u_hits = scrub.scrub_text(turn["user"])
            assistant_clean, a_hits = scrub.scrub_text(turn["assistant"])
            for k in ("email", "phone", "url", "number"):
                scrub_hits[k] += u_hits.get(k, 0) + a_hits.get(k, 0)
            clean_texts.append(user_clean)
            clean_texts.append(assistant_clean)

    def name_pass(text: str) -> tuple[str, int, bool]:
        return scrub.scrub_names(text, llm_client, model=model)

    if workers > 1 and len(clean_texts) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            named_results = list(pool.map(name_pass, clean_texts))
    else:
        named_results = [name_pass(t) for t in clean_texts]
    name_pass_failures = sum(1 for _, _, ok in named_results if not ok)
    text_idx = 0

    for uid, turns in kept_users:
        pseudo_uid = pseudonyms.user_id(uid)
        sessions_by_id: dict[str, list[dict[str, Any]]] = {}
        session_order: list[str] = []

        for turn_idx, turn in enumerate(turns, start=1):
            total_kept_turns += 1
            prompt_counter += 1

            user_named, u_names, _ = named_results[text_idx]
            assistant_named, a_names, _ = named_results[text_idx + 1]
            text_idx += 2
            scrub_hits["name"] += u_names + a_names

            if _HEBREW_RE.search(user_named):
                hebrew_prompts += 1
            word_count = len(user_named.split())
            if turn_idx > 1 and 0 < word_count <= SHORT_REPLY_MAX_WORDS:
                short_replies += 1

            pseudo_sid = pseudonyms.session_id(uid, turn["cid"])
            if pseudo_sid not in sessions_by_id:
                sessions_by_id[pseudo_sid] = []
                session_order.append(pseudo_sid)
            sessions_by_id[pseudo_sid].append(
                {
                    "id": f"{pseudo_uid}-t{turn_idx}",
                    "at": turn["at"],
                    "user": user_named,
                    "assistant": assistant_named,
                }
            )

            item_id = f"item-{prompt_counter}"
            vocab_rows.append({"id": item_id, "text": user_named})
            impact_rows.append({"id": item_id, "text": user_named})

            visit_item: dict[str, Any] = {"id": item_id, "text": user_named}
            for ctx_key in VISIT_CONTEXT_FIELDS:
                if ctx_key in turn["context"]:
                    visit_item[ctx_key] = turn["context"][ctx_key]
            visit_rows.append(visit_item)

        personas_rows.append(
            {
                "user_id": pseudo_uid,
                "sessions": [
                    {"session_id": sid, "turns": sessions_by_id[sid]} for sid in session_order
                ],
            }
        )

    # Residual scan over the output text of each dataset.
    persona_residual = sum(
        scrub.residual_hits(t["user"]) + scrub.residual_hits(t["assistant"])
        for p in personas_rows
        for s in p["sessions"]
        for t in s["turns"]
    )
    prompt_residual = sum(scrub.residual_hits(item["text"]) for item in vocab_rows)

    turns_hist: dict[str, int] = dict(
        sorted(
            Counter(str(len(turns)) for _, turns in kept_users).items(),
            key=lambda kv: int(kv[0]),
        )
    )
    hebrew_share = hebrew_prompts / total_kept_turns if total_kept_turns else 0.0
    short_share = short_replies / total_kept_turns if total_kept_turns else 0.0
    sensitive_share = (
        sensitive_dropped / readable_non_staff_turns if readable_non_staff_turns else 0.0
    )

    def make_summary(res_hits: int) -> dict[str, Any]:
        return {
            "users": len(kept_users),
            "turns": total_kept_turns,
            "turns_per_user": turns_hist,
            "hebrew_prompt_share": round(hebrew_share, 6),
            "short_reply_share": round(short_share, 6),
            "sensitive_dropped_share": round(sensitive_share, 6),
            "unreadable_dropped": unreadable_dropped,
            "scrub_hits": dict(scrub_hits),
            "residual_hits": res_hits,
            "name_pass_failures": name_pass_failures,
        }

    unmapped_visit = [
        key
        for key in VISIT_CONTEXT_FIELDS
        if not isinstance(fields.get(key), str) or not str(fields.get(key)).strip()
    ]

    datasets_spec: list[tuple[str, str, list[dict[str, Any]], int, dict[str, Any]]] = [
        (
            "anon-personas",
            "users.jsonl",
            personas_rows,
            persona_residual,
            {
                "anonymized": persona_residual == 0
                and name_pass_failures == 0
                and bool(personas_rows)
            },
        ),
        (
            "anon-vocab",
            "items.jsonl",
            vocab_rows,
            prompt_residual,
            {"anonymized": prompt_residual == 0 and name_pass_failures == 0 and bool(vocab_rows)},
        ),
        (
            "anon-visit",
            "items.jsonl",
            visit_rows,
            prompt_residual,
            {
                "anonymized": prompt_residual == 0 and name_pass_failures == 0 and bool(visit_rows),
                **(
                    {"blocked": f"unmapped fields: {', '.join(unmapped_visit)}"}
                    if unmapped_visit
                    else {}
                ),
            },
        ),
        (
            "anon-impact",
            "items.jsonl",
            impact_rows,
            prompt_residual,
            {"anonymized": prompt_residual == 0 and name_pass_failures == 0 and bool(impact_rows)},
        ),
    ]

    summaries: dict[str, dict[str, Any]] = {}
    for ds_name, filename, rows_data, res_hits, meta in datasets_spec:
        summary = make_summary(res_hits)
        _write_dataset_files(out_dir / ds_name, filename, rows_data, summary, meta)
        summaries[ds_name] = summary

    return summaries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m chat.V2.personalization.lab.real.pull",
        description=__doc__,
    )
    parser.add_argument("--fields", default=str(DEFAULT_FIELDS_PATH))
    parser.add_argument("--export", required=True, dest="export_path")
    parser.add_argument("--min-turns", type=int, default=4)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--target-users", type=int, default=200)
    parser.add_argument("--out", default=os.environ.get("LA_LAB_DATA_DIR", ""))
    parser.add_argument("--model", default=scrub.DEFAULT_SCRUB_MODEL)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.out:
        print("PullError: --out or LA_LAB_DATA_DIR is required", file=sys.stderr)
        return 2
    try:
        fields_data = json.loads(Path(args.fields).read_text(encoding="utf-8"))
        summaries = run(
            fields=fields_data,
            export_path=args.export_path,
            min_turns=args.min_turns,
            max_turns=args.max_turns,
            target_users=args.target_users,
            out=args.out,
            model=args.model,
            workers=args.workers,
        )
    except (PullError, OSError, json.JSONDecodeError) as error:
        print(f"PullError: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summaries["anon-personas"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
