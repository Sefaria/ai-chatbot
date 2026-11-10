"""Labeling runner for real anonymized datasets (anon-personas, anon-vocab, anon-visit, anon-impact).

Reads unlabeled items from `$LA_LAB_DATA_DIR/<name>/`, builds a labeling prompt from the matching
brief in `L/data/briefs/`, calls the headless Claude Code client (`ClaudeCodeLLMClient`) for
structured JSON output, writes valid labeled items to `labeled.jsonl` next to the data, and
recomputes `sha256`. Refuses to write anywhere inside a git repository.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

from .. import llm as llm_module
from ..parsing import parse_json_object
from . import datasets, validate_data

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 1500
BRIEFS_DIR = datasets.DATA_DIR / "briefs"
BRIEF_FALLBACKS = {
    "anon-personas": ("anon-personas.md", "syn2.md"),
    "anon-vocab": ("anon-vocab.md", "vocab1.md"),
    "anon-visit": ("anon-visit.md", "visit1.md"),
    "anon-impact": ("anon-impact.md", "impact2.md"),
}


class LabelError(RuntimeError):
    """The dataset cannot be labeled or the target directory is inside a git repository."""


class LabelResult(dict):
    """Dict result that also supports attribute access (`res['malformed']` and `res.malformed`)."""

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError as error:
            raise AttributeError(key) from error


def _is_inside_git_repo(directory: Path) -> bool:
    resolved = directory.resolve()
    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            return True
    proc = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _assert_outside_git_repo(directory: Path) -> None:
    if _is_inside_git_repo(directory):
        raise LabelError(f"refusing to write labels inside a git repository: {directory}")


def load_brief(name: str, briefs_dir: Path = BRIEFS_DIR) -> str:
    if name not in BRIEF_FALLBACKS:
        raise LabelError(
            f"unknown dataset for labeling: {name!r}; expected {list(BRIEF_FALLBACKS)}"
        )
    for filename in BRIEF_FALLBACKS[name]:
        path = briefs_dir / filename
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise LabelError(f"no brief found for {name!r} in {briefs_dir}")


def _read_unlabeled_items(directory: Path) -> list[dict]:
    for candidate in ("items.json", "personas.json", "prompts.json", "fixtures.json"):
        path = directory / candidate
        if path.is_file():
            parsed = json.loads(path.read_text(encoding="utf-8"))
            items = datasets._items(parsed, "items")
            if isinstance(items, list):
                return items
    for candidate in ("unlabeled.jsonl", "items.jsonl", "users.jsonl"):
        path = directory / candidate
        if path.is_file():
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise LabelError(f"no unlabeled items found in {directory}")


def _apply_persona_labels(item: dict, reply: dict, slots: dict) -> dict | None:
    golden = reply.get("golden")
    timeline = reply.get("golden_timeline")
    raw_tags = reply.get("tags", reply.get("turn_tags"))
    if not isinstance(golden, dict) or not isinstance(timeline, list) or not timeline:
        return None
    if raw_tags is None or not isinstance(raw_tags, (dict, list)):
        return None
    for key, spec in golden.items():
        if validate_data._golden_errors("persona", key, spec, slots):
            return None
    persona = copy.deepcopy(item)
    turns = validate_data._persona_turns(persona)
    if not turns:
        return None
    turn_ids = [t.get("id") for t in turns if t.get("id") is not None]
    for entry in timeline:
        if not isinstance(entry, dict):
            return None
        ft = entry.get("from_turn")
        valid_idx = isinstance(ft, int) and not isinstance(ft, bool) and 1 <= ft <= len(turns)
        if not valid_idx and ft not in turn_ids:
            return None
        if validate_data._slot_value_errors(
            "persona golden_timeline", entry.get("slot"), entry.get("value"), slots
        ):
            return None

    if isinstance(raw_tags, dict):
        for tag_list in raw_tags.values():
            if not isinstance(tag_list, list) or any(
                not isinstance(t, str) or t not in validate_data.TURN_TAGS for t in tag_list
            ):
                return None
        for turn in turns:
            t_tags = raw_tags.get(turn.get("id"), turn.get("tags") or [])
            if not isinstance(t_tags, list) or any(
                t not in validate_data.TURN_TAGS for t in t_tags
            ):
                return None
            turn["tags"] = list(t_tags)
    elif isinstance(raw_tags, list):
        if raw_tags and all(isinstance(el, list) for el in raw_tags):
            if len(raw_tags) != len(turns):
                return None
            for turn, tag_list in zip(turns, raw_tags, strict=True):
                if any(
                    not isinstance(t, str) or t not in validate_data.TURN_TAGS for t in tag_list
                ):
                    return None
                turn["tags"] = list(tag_list)
        else:
            if any(
                not isinstance(t, str)
                or t not in (validate_data.TURN_TAGS | set(validate_data.TRAPS))
                for t in raw_tags
            ):
                return None
            persona["tags"] = list(raw_tags)
            for turn in turns:
                existing = turn.get("tags") or []
                if not isinstance(existing, list) or any(
                    t not in validate_data.TURN_TAGS for t in existing
                ):
                    return None
                turn["tags"] = list(existing)

    persona["golden"] = golden
    persona["golden_timeline"] = timeline
    persona.setdefault("checks", [])
    persona.setdefault("stated_events", [])
    return persona


def _apply_vocab_labels(item: dict, reply: dict) -> dict | None:
    lbl = reply.get("label")
    if not isinstance(lbl, str) or lbl not in validate_data.VOCAB_LABELS:
        return None
    return {**item, "label": lbl}


def _apply_visit_labels(item: dict, reply: dict) -> dict | None:
    lang = reply.get("expected_reply_lang")
    if not isinstance(lang, str) or lang not in validate_data.REPLY_LANGS:
        return None
    return {**item, "expected_reply_lang": lang}


def _apply_impact_labels(item: dict, reply: dict, slots: dict) -> dict | None:
    kind = reply.get("kind")
    relevant_to = reply.get("relevant_to")
    many_voices = reply.get("many_voices")
    if kind not in ("relevant", "neutral") or not isinstance(many_voices, bool):
        return None
    if kind == "relevant":
        if not isinstance(relevant_to, str) or relevant_to not in slots:
            return None
    elif relevant_to is not None:
        return None
    return {**item, "kind": kind, "relevant_to": relevant_to, "many_voices": many_voices}


def _apply_labels(name: str, item: dict, reply: dict | None, slots: dict) -> dict | None:
    if not isinstance(reply, dict):
        return None
    if name == "anon-personas":
        return _apply_persona_labels(item, reply, slots)
    if name == "anon-vocab":
        return _apply_vocab_labels(item, reply)
    if name == "anon-visit":
        return _apply_visit_labels(item, reply)
    if name == "anon-impact":
        return _apply_impact_labels(item, reply, slots)
    return None


def _recompute_sha256(directory: Path) -> str:
    files = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name != "sha256")
    sha = datasets.checksum(directory, files)
    (directory / "sha256").write_text(f"{sha}\n", encoding="utf-8")
    return sha


def label_dataset(
    name: str,
    *,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    llm: llm_module.LLMClient | None = None,
    data_root: Path | None = None,
) -> LabelResult:
    brief = load_brief(name)
    root = Path(data_root) if data_root is not None else datasets.data_dir()
    directory = root / name
    if not directory.is_dir():
        raise LabelError(f"dataset directory does not exist: {directory}")
    _assert_outside_git_repo(directory)

    items = _read_unlabeled_items(directory)
    if limit is not None:
        items = items[: max(0, int(limit))]
    slots = validate_data.load_slots()
    client = llm or llm_module.ClaudeCodeLLMClient()

    system = (
        f"{brief.strip()}\n\n"
        "Label the single JSON item in the user message. Reply with one JSON object only."
    )
    labeled_rows: list[dict] = []
    malformed = 0
    for item in items:
        user_prompt = json.dumps(item, ensure_ascii=False)
        try:
            response = client.complete(
                system=system, user=user_prompt, model=model, max_tokens=MAX_TOKENS
            )
            parsed = parse_json_object(response.text)
        except Exception:
            parsed = None
        merged = _apply_labels(name, item, parsed, slots)
        if merged is None:
            malformed += 1
        else:
            labeled_rows.append(merged)

    out_path = directory / "labeled.jsonl"
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labeled_rows)
    out_path.write_text(content, encoding="utf-8")
    sha = _recompute_sha256(directory)
    return LabelResult(
        dataset=name,
        total=len(items),
        labeled=len(labeled_rows),
        malformed=malformed,
        invalid=malformed,
        unlabeled=malformed,
        sha256=sha,
        output=str(out_path),
    )
