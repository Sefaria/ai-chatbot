"""Validator for the real anonymized datasets (anon-personas, anon-vocab, anon-visit, anon-impact).

It checks schema, minimum counts, and that every slot key and value is legal in `slots.json`.
Trap coverage is reported as numbers, not required. It prints numbers and field names only, never
item text, and exits 3 with the reason when `meta.json` marks the dataset blocked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import datasets

SLOTS_PATH = Path(__file__).parent.parent / "slots.json"
HEBREW = re.compile(r"[\u0590-\u05FF]")
LATIN = re.compile(r"[A-Za-z]")
STORY_ID = re.compile(r"\bsc-\d+", re.IGNORECASE)
ISO_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")

TRAPS = (
    "outlier", "correction", "conflict", "stated", "injected", "sensitive", "exclusion",
    "traditional", "academic", "mixed", "first_visit", "stale_interest",
)  # fmt: skip
TURN_TAGS = {
    "outlier", "correction", "conflict", "injected", "sensitive", "exclusion", "instruction",
    "must_not_store", "short_reply",
}  # fmt: skip
CHECK_TYPES = {
    "available_by_turn", "not_flipped", "stated_locked", "changed_fact", "never_stored",
    "instruction_captured", "expired",
}  # fmt: skip
VOCAB_LABELS = ("traditional", "academic", "transliterated", "yeshivish", "none")
GOLDEN_KEYS = {"value", "accept", "status", "must_include", "must_not_include", "any_include",
               "any_of", "optional"}  # fmt: skip
REPLY_LANGS = {"he", "en", "mixed"}
SHORT_REPLY_MAX_WORDS = 4
STALE_DAYS = 60

MIN_PERSONA_USERS = 8
MIN_PERSONA_TURNS = 60
MIN_VOCAB_ITEMS = 60
MIN_VISIT_ITEMS = 20
MIN_IMPACT_ITEMS = 20

VALIDATORS = ("anon-personas", "anon-vocab", "anon-visit", "anon-impact")


class ValidationUnavailable(RuntimeError):
    """There is no validator or no registered dataset of that name."""


class DatasetBlocked(RuntimeError):
    """The dataset's meta.json marks it blocked with a reason."""


def load_slots() -> dict[str, dict]:
    parsed = json.loads(SLOTS_PATH.read_text(encoding="utf-8"))
    return {slot["key"]: slot for slot in parsed["slots"]}


def _has_hebrew(text) -> bool:
    return isinstance(text, str) and bool(HEBREW.search(text))


def _script(text: str) -> str:
    """'he', 'en', 'mixed' or '' from the letters of a text."""
    he, en = len(HEBREW.findall(text)), len(LATIN.findall(text))
    if he and not en:
        return "he"
    if en and not he:
        return "en"
    return "mixed" if he and en else ""


def _safe_token(value) -> str:
    if isinstance(value, str) and len(value) <= 30 and " " not in value:
        return repr(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    return "<invalid>"


def _slot_value_errors(where: str, key: str, value, slots: dict) -> list[str]:
    slot = slots.get(key)
    if slot is None:
        return [f"{where}: slot key {_safe_token(key)} is not in slots.json"]
    kind = slot["value_type"]
    if kind == "enum":
        if not isinstance(value, str) or value not in slot["allowed"]:
            return [f"{where}: {key} value {_safe_token(value)} not in {slot['allowed']}"]
    elif kind == "list":
        if not isinstance(value, list) or not value:
            return [f"{where}: {key} value must be a non-empty list"]
        bad = [_safe_token(v) for v in value if v not in slot["allowed"]]
        if bad:
            return [f"{where}: {key} value {bad} not in {slot['allowed']}"]
        if len(value) > slot["max_items"]:
            return [f"{where}: {key} has more than {slot['max_items']} items"]
    elif kind == "text":
        if not isinstance(value, str) or not value or len(value) > slot["max_chars"]:
            return [f"{where}: {key} must be text of at most {slot['max_chars']} characters"]
    return []


def _golden_errors(where: str, key: str, golden, slots: dict) -> list[str]:
    if golden is None:
        return (
            [] if key in slots else [f"{where}: golden key {_safe_token(key)} is not in slots.json"]
        )
    if key not in slots:
        return [f"{where}: golden key {_safe_token(key)} is not in slots.json"]
    if not isinstance(golden, dict) or not (set(golden) & {"value", "must_include", "any_of"}):
        return [f"{where}: golden {key} needs value, must_include or any_of"]
    errors = [
        f"{where}: golden {key} has unknown field {_safe_token(f)}"
        for f in set(golden) - GOLDEN_KEYS
    ]
    slot = slots[key]
    if slot["value_type"] == "list":
        for field in ("must_include", "must_not_include", "any_include"):
            for item in golden.get(field, []):
                if item not in slot["allowed"]:
                    errors.append(
                        f"{where}: {key} {field} value {_safe_token(item)} not in {slot['allowed']}"
                    )
    elif slot["value_type"] == "enum":
        for field in ("value", "accept"):
            for item in (
                [golden[field]] if field == "value" and field in golden else golden.get(field, [])
            ):
                if item not in slot["allowed"]:
                    errors.append(
                        f"{where}: {key} {field} value {_safe_token(item)} not in {slot['allowed']}"
                    )
    return errors


def _story_ids(data) -> list[str]:
    return list(STORY_ID.findall(json.dumps(data, ensure_ascii=False)))


def _story_id_errors(data) -> list[str]:
    return [f"story id {m!r} found in data" for m in _story_ids(data)]


def _persona_turns(persona: dict) -> list[dict]:
    return [t for s in persona.get("sessions", []) for t in s.get("turns", [])]


def _check_persona_shape(idx: int, p: dict, slots: dict) -> list[str]:
    where = f"persona #{idx}"
    if not isinstance(p, dict):
        return [f"{where}: expected an object"]
    errors = []
    for field in ("user_id", "sessions", "golden", "golden_timeline"):
        if field not in p:
            errors.append(f"{where}: missing field {field!r}")
    if errors:
        return errors
    if p.get("trap") is not None and p["trap"] not in TRAPS:
        errors.append(f"{where}: trap {p['trap']!r} is not one of {list(TRAPS)}")
    if "tags" in p and p["tags"] is not None and not isinstance(p["tags"], list):
        errors.append(f"{where}: tags must be a list")
    if not isinstance(p["golden"], dict):
        return [f"{where}: golden must be an object"]
    for key, golden in p["golden"].items():
        errors += _golden_errors(where, key, golden, slots)
    if p.get("interview"):
        for key, value in p["interview"].items():
            errors += _slot_value_errors(f"{where} interview", key, value, slots)
    turns = _persona_turns(p)
    if not turns:
        return errors + [f"{where}: no turns"]
    ids = [t.get("id") for t in turns if t.get("id") is not None]
    previous = None
    for t_idx, turn in enumerate(turns):
        if not isinstance(turn, dict):
            errors.append(f"{where}: turn #{t_idx} is not an object")
            continue
        for field in ("at", "user", "assistant"):
            if not isinstance(turn.get(field), str) or not turn[field].strip():
                errors.append(f"{where}: turn #{t_idx} has no {field}")
        at = turn.get("at", "")
        if not isinstance(at, str) or not ISO_TIME.match(at):
            errors.append(f"{where}: turn #{t_idx} time is not ISO-8601")
            continue
        parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if previous and parsed < previous:
            errors.append(f"{where}: turn #{t_idx} is earlier than the turn before it")
        previous = parsed
        tags = turn.get("tags", [])
        if not isinstance(tags, list):
            errors.append(f"{where}: turn #{t_idx} tags must be a list")
        else:
            for tag in tags:
                if tag not in TURN_TAGS:
                    errors.append(
                        f"{where}: turn #{t_idx} tag {tag!r} is not one of {sorted(TURN_TAGS)}"
                    )
    if len(set(ids)) != len(ids):
        errors.append(f"{where}: turn ids are not unique")
    for event in p.get("stated_events") or []:
        if ids and event.get("after_turn") not in ids:
            errors.append(f"{where}: stated event after unknown turn {event.get('after_turn')!r}")
        errors += _slot_value_errors(
            f"{where} stated event", event.get("slot"), event.get("value"), slots
        )
    if not isinstance(p["golden_timeline"], list):
        errors.append(f"{where}: golden_timeline must be a list")
    else:
        for entry in p["golden_timeline"]:
            if not isinstance(entry, dict):
                errors.append(f"{where}: golden_timeline entry is not an object")
                continue
            ft = entry.get("from_turn")
            valid_turn_idx = (
                isinstance(ft, int) and not isinstance(ft, bool) and 1 <= ft <= len(turns)
            )
            if not valid_turn_idx and ft not in ids:
                errors.append(f"{where}: golden_timeline from unknown turn {ft!r}")
            errors += _slot_value_errors(
                f"{where} golden_timeline", entry.get("slot"), entry.get("value"), slots
            )
    user_text = " ".join(t.get("user", "") for t in turns if isinstance(t, dict)).lower()
    for check in p.get("checks") or []:
        if not isinstance(check, dict) or check.get("type") not in CHECK_TYPES:
            errors.append(
                f"{where}: check type {check.get('type') if isinstance(check, dict) else None!r} is not one of {sorted(CHECK_TYPES)}"
            )
            continue
        if "slot" in check and check["slot"] not in slots:
            errors.append(f"{where}: check slot {check['slot']!r} is not in slots.json")
        for ref in ("turn", "after_turn"):
            if ref in check and check[ref] not in ids:
                errors.append(f"{where}: check refers to unknown turn {check[ref]!r}")
        for phrase in check.get("phrases", []):
            if str(phrase).lower() not in user_text:
                errors.append(f"{where}: never_stored phrase is in no user turn")
    return errors


def _extract_personas(data) -> tuple[list | None, list[str]]:
    if isinstance(data, list):
        return data, []
    if isinstance(data, dict):
        errs = []
        if "slots_version" in data and data.get("slots_version") != "v2":
            errs.append("anon-personas: slots_version must be 'v2'")
        for key in ("personas", "items"):
            if isinstance(data.get(key), list):
                return data[key], errs
    return None, ["anon-personas: expected a list of personas or an object with 'personas'"]


def report_personas(personas: list[dict]) -> dict:
    turns = [
        t for p in personas if isinstance(p, dict) for t in _persona_turns(p) if isinstance(t, dict)
    ]
    trap_counts: dict[str, int] = dict.fromkeys((*TURN_TAGS, *TRAPS), 0)
    for p in personas:
        if not isinstance(p, dict):
            continue
        if p.get("trap") in trap_counts:
            trap_counts[p["trap"]] += 1
        if p.get("stated_events"):
            trap_counts["stated"] += 1
        if len(p.get("sessions") or []) == 1:
            trap_counts["first_visit"] += 1
        p_turns = [t for t in _persona_turns(p) if isinstance(t, dict)]
        previous = None
        for t in p_turns:
            for tag in t.get("tags") or []:
                if tag in trap_counts:
                    trap_counts[tag] += 1
            if (
                previous is not None
                and "short_reply" not in (t.get("tags") or [])
                and 0 < len(str(t.get("user", "")).split()) <= SHORT_REPLY_MAX_WORDS
            ):
                trap_counts["short_reply"] += 1
            previous = t
    hebrew_users = sum(
        1
        for p in personas
        if isinstance(p, dict)
        and sum(_has_hebrew(t.get("user", "")) for t in _persona_turns(p)) * 2
        > len(_persona_turns(p))
    )
    return {
        "users": len(personas),
        "turns": len(turns),
        "hebrew_users": hebrew_users,
        "traps": trap_counts,
    }


def validate_personas_with_report(data, slots: dict | None = None) -> tuple[list[str], dict]:
    slots = slots or load_slots()
    errors = _story_id_errors(data)
    personas, shape_errors = _extract_personas(data)
    errors += shape_errors
    if personas is None:
        return errors, {"users": 0, "turns": 0, "hebrew_users": 0, "traps": {}}
    if len(personas) < MIN_PERSONA_USERS:
        errors.append(
            f"anon-personas: needs at least {MIN_PERSONA_USERS} users, has {len(personas)}"
        )
    shape = []
    for idx, p in enumerate(personas):
        shape += _check_persona_shape(idx, p, slots)
    errors += shape
    report = report_personas(personas)
    if shape:
        return errors, report
    for field in ("id", "user_id"):
        values = [p[field] for p in personas if field in p and p[field] is not None]
        if len(set(values)) != len(values):
            errors.append(f"anon-personas: persona {field} values are not unique")
    all_ids = [t["id"] for p in personas for t in _persona_turns(p) if "id" in t]
    if len(set(all_ids)) != len(all_ids):
        errors.append("anon-personas: turn ids are not unique across personas")
    if report["turns"] < MIN_PERSONA_TURNS:
        errors.append(
            f"anon-personas: needs at least {MIN_PERSONA_TURNS} turns, has {report['turns']}"
        )
    return errors, report


def validate_personas(data, slots: dict | None = None) -> list[str]:
    errors, _ = validate_personas_with_report(data, slots)
    return errors


validate_syn2 = validate_personas


def validate_vocab_with_report(items) -> tuple[list[str], dict]:
    if isinstance(items, dict) and isinstance(items.get("items"), list):
        items = items["items"]
    errors = _story_id_errors(items)
    if not isinstance(items, list):
        return errors + ["anon-vocab: expected a list"], {"items": 0}
    if len(items) < MIN_VOCAB_ITEMS:
        errors.append(f"anon-vocab: needs at least {MIN_VOCAB_ITEMS} items, has {len(items)}")
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"id", "text", "label"}:
            found = sorted(item.keys()) if isinstance(item, dict) else type(item).__name__
            errors.append(
                f"anon-vocab: item #{idx} has fields {found}, expected ['id', 'label', 'text']"
            )
            return errors, {"items": len(items)}
        if not isinstance(item["text"], str) or not item["text"].strip():
            errors.append(f"anon-vocab: item #{idx} has empty text")
        if item["label"] not in VOCAB_LABELS:
            errors.append(
                f"anon-vocab: item #{idx} has label {item['label']!r} not in {list(VOCAB_LABELS)}"
            )
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        errors.append("anon-vocab: ids are not unique")
    report = {
        "items": len(items),
        "hebrew": sum(_has_hebrew(i.get("text", "")) for i in items),
        "labels": {label: sum(i.get("label") == label for i in items) for label in VOCAB_LABELS},
    }
    return errors, report


def validate_vocab(items) -> list[str]:
    errors, _ = validate_vocab_with_report(items)
    return errors


validate_vocab1 = validate_vocab


def validate_visit_with_report(rows) -> tuple[list[str], dict]:
    if isinstance(rows, dict):
        rows = rows.get("fixtures") if isinstance(rows.get("fixtures"), list) else rows.get("items")
    errors = _story_id_errors(rows)
    if not isinstance(rows, list):
        return errors + ["anon-visit: expected a list"], {"items": 0}
    if len(rows) < MIN_VISIT_ITEMS:
        errors.append(f"anon-visit: needs at least {MIN_VISIT_ITEMS} items, has {len(rows)}")
    fields = {"id", "site", "browser_lang", "country", "prompt", "expected_reply_lang"}
    differs = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != fields:
            found = sorted(row.keys()) if isinstance(row, dict) else type(row).__name__
            errors.append(f"anon-visit: item #{idx} has fields {found}, expected {sorted(fields)}")
            return errors, {"items": len(rows)}
        where = f"anon-visit: item #{idx}"
        if row["site"] not in ("he", "en"):
            errors.append(f"{where}: site must be 'he' or 'en'")
        if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", str(row["browser_lang"])):
            errors.append(f"{where}: browser_lang is not a language tag")
        if not re.fullmatch(r"[A-Z]{2}", str(row["country"])):
            errors.append(f"{where}: country must be a two-letter upper-case code")
        if row["expected_reply_lang"] not in REPLY_LANGS:
            errors.append(f"{where}: expected_reply_lang must be one of {sorted(REPLY_LANGS)}")
        script = _script(str(row["prompt"]))
        if not script:
            errors.append(f"{where}: prompt has no letters")
        elif script != "mixed" and row["expected_reply_lang"] != script:
            errors.append(f"{where}: a prompt written in {script!r} must expect a {script!r} reply")
        if script in ("he", "en") and script != row["site"]:
            differs += 1
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        errors.append("anon-visit: ids are not unique")
    return errors, {"items": len(rows), "site_differs": differs}


def validate_visit(rows) -> list[str]:
    errors, _ = validate_visit_with_report(rows)
    return errors


validate_visit1 = validate_visit


def validate_impact_with_report(
    prompts, profiles: dict | None = None, slots: dict | None = None
) -> tuple[list[str], dict]:
    if slots is None and isinstance(profiles, dict) and "preferred_language" in profiles:
        slots, profiles = profiles, None
    slots = slots or load_slots()
    if isinstance(prompts, dict):
        prompts = (
            prompts.get("prompts")
            if isinstance(prompts.get("prompts"), list)
            else prompts.get("items")
        )
    errors = _story_id_errors([prompts, profiles] if profiles is not None else prompts)
    if not isinstance(prompts, list):
        return errors + ["anon-impact: prompts must be a list"], {"items": 0}
    if profiles is not None:
        if not isinstance(profiles, dict) or set(profiles) != {"P1", "P2", "P3"}:
            return errors + ["anon-impact: profiles must be an object with exactly P1, P2, P3"], {
                "items": len(prompts)
            }
        for name, profile in profiles.items():
            if not isinstance(profile, dict) or not profile:
                errors.append(f"anon-impact: profile {name} must be a non-empty slot dictionary")
                continue
            for key, value in profile.items():
                errors += _slot_value_errors(f"anon-impact profile {name}", key, value, slots)
        if len({json.dumps(p, sort_keys=True) for p in profiles.values()}) != 3:
            errors.append("anon-impact: the three profiles must differ")
    if len(prompts) < MIN_IMPACT_ITEMS:
        errors.append(f"anon-impact: needs at least {MIN_IMPACT_ITEMS} prompts, has {len(prompts)}")
    fields = {"id", "text", "kind", "relevant_to", "many_voices"}
    relevant = neutral = voices = hebrew = 0
    for idx, p in enumerate(prompts):
        if not isinstance(p, dict) or set(p) != fields:
            found = sorted(p.keys()) if isinstance(p, dict) else type(p).__name__
            errors.append(
                f"anon-impact: prompt #{idx} has fields {found}, expected {sorted(fields)}"
            )
            return errors, {"items": len(prompts)}
        where = f"anon-impact: prompt #{idx}"
        if not isinstance(p["text"], str) or not p["text"].strip():
            errors.append(f"{where}: empty text")
        if p["kind"] == "relevant":
            relevant += 1
            if p["relevant_to"] not in slots:
                errors.append(
                    f"{where}: relevant_to {p['relevant_to']!r} is not a slot key in slots.json"
                )
            elif profiles is not None:
                values = {json.dumps(pr.get(p["relevant_to"])) for pr in profiles.values()}
                if len(values) < 2:
                    errors.append(f"{where}: profiles do not differ on {p['relevant_to']!r}")
        elif p["kind"] == "neutral":
            neutral += 1
            if p["relevant_to"] is not None:
                errors.append(f"{where}: a neutral prompt must have relevant_to null")
        else:
            errors.append(f"{where}: kind must be 'relevant' or 'neutral'")
        if not isinstance(p["many_voices"], bool):
            errors.append(f"{where}: many_voices must be true or false")
        voices += p["many_voices"] is True
        hebrew += _has_hebrew(p["text"])
    ids = [p["id"] for p in prompts]
    if len(set(ids)) != len(ids):
        errors.append("anon-impact: ids are not unique")
    report = {
        "items": len(prompts),
        "relevant": relevant,
        "neutral": neutral,
        "many_voices": voices,
        "hebrew": hebrew,
    }
    return errors, report


def validate_impact(prompts, profiles: dict | None = None, slots: dict | None = None) -> list[str]:
    errors, _ = validate_impact_with_report(prompts, profiles, slots)
    return errors


validate_impact2 = validate_impact


def _resolve_dataset_dir(name: str, data_root: Path | None) -> Path:
    if data_root is not None:
        return Path(data_root) / name
    return datasets.data_dir() / name


def _check_blocked(directory: Path) -> None:
    for meta_file in ("meta.json", "summary.json"):
        path = directory / meta_file
        if path.is_file():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(meta, dict) and meta.get("blocked"):
                raise DatasetBlocked(str(meta["blocked"]))


def _load_dataset_items(directory: Path):
    labeled = directory / "labeled.jsonl"
    if labeled.is_file():
        return [
            json.loads(line)
            for line in labeled.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for candidate in ("personas.json", "items.json", "fixtures.json", "prompts.json"):
        path = directory / candidate
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    for candidate in ("items.jsonl", "users.jsonl"):
        path = directory / candidate
        if path.is_file():
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise FileNotFoundError("items.json")


def validate_with_report(name: str, data_root: Path | None = None) -> tuple[list[str], dict]:
    if name not in VALIDATORS:
        raise ValidationUnavailable(f"no validator for {name!r}; validators: {list(VALIDATORS)}")
    reg_file = (
        Path(data_root) / "datasets.json"
        if data_root is not None and (Path(data_root) / "datasets.json").is_file()
        else datasets.REGISTRY_PATH
    )
    entries = json.loads(reg_file.read_text(encoding="utf-8"))
    if name not in entries:
        raise ValidationUnavailable(f"{name!r} is not registered in datasets.json")
    try:
        directory = _resolve_dataset_dir(name, data_root)
    except datasets.DatasetError as error:
        return [str(error)], {}
    if not directory.is_dir():
        return [f"{name}: missing directory"], {}
    _check_blocked(directory)
    try:
        raw = _load_dataset_items(directory)
    except FileNotFoundError:
        return [f"{name}: missing data file"], {}
    except json.JSONDecodeError as error:
        return [f"{name}: invalid JSON at line {error.lineno}"], {}
    slots = load_slots()
    if name == "anon-personas":
        return validate_personas_with_report(raw, slots)
    if name == "anon-vocab":
        return validate_vocab_with_report(raw)
    if name == "anon-visit":
        return validate_visit_with_report(raw)
    profiles = None
    if (directory / "profiles.json").is_file():
        try:
            profiles = json.loads((directory / "profiles.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            return [f"{name}: invalid profiles.json at line {error.lineno}"], {}
    return validate_impact_with_report(raw, profiles, slots)


def validate(name: str, data_root: Path | None = None) -> list[str]:
    """Validate the files of a registered dataset; the list of problems is empty when it is valid."""
    problems, _ = validate_with_report(name, data_root=data_root)
    return problems
