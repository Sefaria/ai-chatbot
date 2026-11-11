"""Pure scorers for the memory evaluation.

A "slot value" here is the plain-dict form used in snapshots: {"value": ..., "status": ...} or None.
A "snapshot" is {"index", "kind": "turn"|"batch", "turn_id", "at", "profile", "proposals"}.
"""

from __future__ import annotations

import json
import re
from typing import Any

VERDICTS = ("correct", "wrong", "missing", "over_inferred", "not_applicable")


def _as_list(value: Any) -> list[str]:
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    return (
        value.lower() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).lower()
    )


def score_slot(golden: dict | None, slot_value: dict | None) -> str:
    """Compare one stored slot with its golden form (see personas.json)."""
    if golden is None:
        return "correct" if slot_value is None else "over_inferred"
    if slot_value is None:
        return "not_applicable" if golden.get("optional") else "missing"
    value = slot_value["value"]
    if "value" in golden:
        accepted = golden.get("accept") or [golden["value"]]
        if isinstance(value, list) or (value not in accepted and value != golden["value"]):
            return "wrong"
        if golden.get("status") and slot_value.get("status") != golden["status"]:
            return "wrong"
        return "correct"
    if "must_include" in golden:
        items = _as_list(value)
        ok = all(m in items for m in golden["must_include"])
        any_include = golden.get("any_include")
        ok = ok and (not any_include or any(a in items for a in any_include))
        ok = ok and not any(n in items for n in golden.get("must_not_include", []))
        return "correct" if ok else "wrong"
    haystack = _text(value)
    return "correct" if any(t.lower() in haystack for t in golden["any_of"]) else "wrong"


def _find(snapshots: list[dict], turn_id: str) -> dict | None:
    return next((s for s in snapshots if s["kind"] == "turn" and s["turn_id"] == turn_id), None)


def _slot(snapshot: dict, slot: str) -> dict | None:
    return snapshot["profile"].get(slot)


def _result(check: dict, passed: bool, detail: str) -> dict:
    return {"type": check["type"], "passed": passed, "detail": detail}


def check_available_by_turn(check, run) -> dict:
    snap = _find(run.snapshots, check["turn"])
    if snap is None:
        return _result(check, False, f"no snapshot for turn {check['turn']}")
    verdict = score_slot(run.persona["golden"].get(check["slot"]), _slot(snap, check["slot"]))
    return _result(check, verdict == "correct", f"{check['slot']} after {check['turn']}: {verdict}")


def check_not_flipped(check, run) -> dict:
    start = _find(run.snapshots, check["after_turn"])
    if start is None:
        return _result(check, False, f"no snapshot for turn {check['after_turn']}")
    for snap in run.snapshots[start["index"] :]:
        current = _slot(snap, check["slot"])
        if current is None:
            if check.get("if_set"):
                continue
            return _result(check, False, f"unset in snapshot {snap['index']} ({snap['kind']})")
        if current["value"] not in check["allowed"]:
            return _result(
                check, False, f"flipped to {current['value']!r} in snapshot {snap['index']}"
            )
    return _result(check, True, f"stayed within {check['allowed']}")


def check_stated_locked(check, run) -> dict:
    start = 0
    for event in run.persona.get("stated_events", []):
        if event["slot"] == check["slot"]:
            snap = _find(run.snapshots, event["after_turn"])
            start = snap["index"] if snap else 0
    for snap in run.snapshots[start:]:
        current = _slot(snap, check["slot"])
        if not current or current["value"] != check["value"] or current["status"] != "stated":
            return _result(
                check, False, f"not stated {check['value']!r} in snapshot {snap['index']}"
            )
    return _result(check, True, f"{check['slot']} stayed stated {check['value']!r}")


def check_changed_fact(check, run) -> dict:
    final = _slot(run.snapshots[-1], check["slot"]) if run.snapshots else None
    value = final["value"] if final else None
    earlier_old = any(
        (s := _slot(snap, check["slot"])) and s["value"] in check["old"] for snap in run.snapshots
    )
    detail = f"final {value!r}; earlier snapshot in old {check['old']}: {earlier_old}"
    return _result(check, value in check["new"], detail)


def _phrase_regex(phrase: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


def profile_texts(run) -> list[tuple[str, str]]:
    """(where, text) for every slot value, evidence quote and proposal in every snapshot."""
    found = []
    for snap in run.snapshots:
        for key, slot in snap["profile"].items():
            found.append((f"slot {key}", _text(slot["value"])))
            found += [(f"evidence of {key}", e["quote"]) for e in slot.get("evidence", [])]
        for proposal in snap["proposals"]:
            found.append(("proposal value", _text(proposal["value"])))
            found.append(("proposal quote", proposal["quote"]))
    return found


def check_never_stored(check, run) -> dict:
    scope = check.get("scope", "profile")
    places = profile_texts(run)
    if scope == "all":
        places += [("observation", t) for t in run.observations]
        places += [("sent to model", t) for t in run.sent_texts]
    hits = []
    for phrase in check["phrases"]:
        regex = _phrase_regex(phrase)
        hits += [f"{phrase!r} in {where}" for where, text in places if regex.search(text)]
    detail = f"scope {scope}: " + ("; ".join(sorted(set(hits))[:5]) if hits else "no phrase found")
    return _result(check, not hits, detail)


def check_instruction_captured(check, run) -> dict:
    def matches(snapshot):
        current = _slot(snapshot, check["slot"]) if snapshot else None
        return bool(current) and any(t.lower() in _text(current["value"]) for t in check["any_of"])

    if matches(_find(run.snapshots, check["turn"])):
        captured = "turn"
    elif run.snapshots and matches(run.snapshots[-1]):
        captured = "final"
    else:
        captured = "never"
    result = _result(check, captured != "never", f"captured_at: {captured}")
    result["captured_at"] = captured
    return result


def check_expired(check, run) -> dict:
    final = _slot(run.snapshots[-1], check["slot"]) if run.snapshots else None
    if final is None:
        return _result(check, True, f"{check['slot']} absent at the end")
    present = [v for v in _as_list(final["value"]) if v in check["must_not_include"]]
    return _result(check, not present, f"final {final['value']!r}; stale values left: {present}")


CHECKS = {
    "available_by_turn": check_available_by_turn,
    "not_flipped": check_not_flipped,
    "stated_locked": check_stated_locked,
    "changed_fact": check_changed_fact,
    "never_stored": check_never_stored,
    "instruction_captured": check_instruction_captured,
    "expired": check_expired,
}


def evaluate_check(check: dict, run) -> dict:
    result = CHECKS[check["type"]](check, run)
    if "turn" in check:
        result["turn"] = check["turn"]
    if "slot" in check:
        result["slot"] = check["slot"]
    return result
