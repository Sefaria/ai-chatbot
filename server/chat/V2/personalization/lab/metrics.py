"""Metrics, defined once in code, each with the plain-language formula the case pages show.

A metric's `compute(data, variant)` returns a number, a `Ratio(num, den)` (kept so a pooled
aggregate can add the numerators and denominators of three runs), or None when the data has no
way to answer (no denominator, not enough runs).

Key grammar of a result's `metrics`: `name`, `name@variant` (a design or arm of the case's
`config.variants`), or, for a metric that declares `params` (such as `fill_rate_at_k`),
`name@param` and `name@variant/param`.

Metrics about answers (pairs, judges, the lab app) are reported by the case's pipeline as
`RunData.counts`: a ratio metric reads `counts[name]` over `counts[name + "_of"]`; a count metric
reads `counts[name]`. Metrics about a replayed history are computed here from the snapshots.
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..llm import estimate_cost
from ..registry import SlotRegistry
from .metric_formulas import FORMULAS
from .scoring import evaluate_check, score_slot

CASES_ROOT = Path(__file__).parent / "cases"
LANGUAGE_SLOT = "preferred_language"
_SKIP = object()  # no golden value applies yet


@dataclass(frozen=True)
class Ratio:
    num: float
    den: float

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None


@dataclass
class RunData:
    """Everything a metric may read for one run, one dataset and one variant."""

    replays: list = field(default_factory=list)  # replay.PersonaReplay
    labels: list[tuple[str, str]] = field(default_factory=list)  # (label, predicted)
    calls: list[dict] = field(default_factory=list)  # {step, model, in_tokens, out_tokens}
    counts: dict[str, float] = field(default_factory=dict)
    prior_profiles: list[dict] = field(
        default_factory=list
    )  # {user: {slot: value}} of earlier runs
    prior_metrics: list[dict] = field(default_factory=list)  # earlier runs' `metrics`
    metrics_so_far: dict = field(
        default_factory=dict
    )  # this run's metrics computed before this one

    @property
    def turns(self) -> int:
        return sum(r.turns for r in self.replays) or int(self.counts.get("turns", 0))


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    formula: str
    compute: Callable[[RunData, str | None], float | Ratio | None]
    params: tuple[str, ...] = ()
    history: bool = False  # computed after the others, from earlier runs and this run's metrics
    cross_run: bool = False  # defined only over all three runs: a bar reads run 3's value


_REGISTRY: SlotRegistry | None = None


def _slots() -> list[str]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SlotRegistry.load()
    return [s.key for s in _REGISTRY.all()]


# -- golden profile helpers -------------------------------------------------------------------


def _turn_number(persona: dict, from_turn) -> int:
    """The 1-based turn number a `golden_timeline` `from_turn` names: an int is the number itself;
    a string is a turn id, found in the persona's own turn order (real labels use `<user>-t<N>`,
    the form `real/pull.py` builds, so a trailing `-t<N>` is the fallback)."""
    if isinstance(from_turn, int) and not isinstance(from_turn, bool):
        return from_turn
    ids = [
        t.get("id")
        for s in persona.get("sessions") or []
        for t in s.get("turns") or []
        if isinstance(t, dict)
    ]
    if from_turn in ids:
        return ids.index(from_turn) + 1
    match = re.search(r"-t(\d+)$", str(from_turn))
    if match:
        return int(match.group(1))
    raise ValueError(f"golden_timeline from_turn {from_turn!r} is not a turn of the persona")


def golden_at(persona: dict, slot: str, turn_no: int):
    """The golden form of `slot` after turn `turn_no` (1-based): the last `golden_timeline` entry
    at or before it, or the final golden value from turn 1 when the persona has no timeline for
    the slot. `_SKIP` when a timeline exists but has not started. `from_turn` may be a turn number
    or a turn id."""
    timeline = sorted(
        (
            (_turn_number(persona, e["from_turn"]), e)
            for e in persona.get("golden_timeline") or []
            if e["slot"] == slot
        ),
        key=lambda pair: pair[0],
    )
    if not timeline:
        return persona["golden"].get(slot)
    applicable = [e for n, e in timeline if n <= turn_no]
    return {"value": applicable[-1]["value"]} if applicable else _SKIP


def _final_verdicts(replay) -> dict[str, str]:
    final = replay.final_profile
    return {
        slot: score_slot(replay.persona["golden"].get(slot), final.get(slot)) for slot in _slots()
    }


# -- replay metrics ---------------------------------------------------------------------------


def slot_accuracy(data: RunData, variant=None):
    correct = scored = 0
    for replay in data.replays:
        # a slot "has a golden value" unless the golden is empty (None) or optional and unset
        for slot, verdict in _final_verdicts(replay).items():
            if replay.persona["golden"].get(slot) is None or verdict == "not_applicable":
                continue
            scored += 1
            correct += verdict == "correct"
    return Ratio(correct, scored)


def over_inferred(data: RunData, variant=None):
    filled = empty = 0
    for replay in data.replays:
        for slot in _slots():
            if replay.persona["golden"].get(slot) is None:
                empty += 1
                filled += replay.final_profile.get(slot) is not None
    return Ratio(filled, empty)


def checks_passed(data: RunData, variant=None):
    passed = total = 0
    for replay in data.replays:
        for check in replay.persona.get("checks", []):
            total += 1
            passed += evaluate_check(check, replay)["passed"]
    return Ratio(passed, total)


def first_session_fill(data: RunData, variant=None):
    hit = 0
    for replay in data.replays:
        first = {t["id"] for t in replay.persona["sessions"][0]["turns"]}
        last = next((s for s in reversed(replay.turn_snapshots()) if s["turn_id"] in first), None)
        if last is None:
            continue
        for slot in _slots():
            golden = replay.persona["golden"].get(slot)
            if slot != LANGUAGE_SLOT and golden is not None:
                if score_slot(golden, last["profile"].get(slot)) == "correct":
                    hit += 1
                    break
    return Ratio(hit, len(data.replays))


def turns_to_fill(data: RunData, variant=None):
    numbers = []
    for replay in data.replays:
        snaps = replay.turn_snapshots()
        for slot, final in replay.final_profile.items():
            if final is None:
                continue
            at = next(
                (i + 1 for i, s in enumerate(snaps) if (s["profile"].get(slot) or {}).get("value") == final["value"]),
                len(snaps),
            )  # fmt: skip
            numbers.append(at)
    return statistics.median(numbers) if numbers else None


def wrong_slot_turns(data: RunData, variant=None):
    wrong = turns = 0
    for replay in data.replays:
        for number, snap in enumerate(replay.turn_snapshots(), 1):
            turns += 1
            for slot, held in snap["profile"].items():
                if held is None:
                    continue
                golden = golden_at(replay.persona, slot, number)
                if golden is _SKIP:
                    continue
                if score_slot(golden, held) in ("wrong", "over_inferred"):
                    wrong += 1
                    break
    return Ratio(wrong, turns)


def _changes(replay):
    """(snapshot, slot, before, after) for every value that differs from the snapshot before it."""
    previous: dict = {}
    for snap in replay.snapshots:
        for slot in set(previous) | set(snap["profile"]):
            before = (previous.get(slot) or {}).get("value")
            after = (snap["profile"].get(slot) or {}).get("value")
            if before != after:
                yield snap, slot, before, after
        previous = snap["profile"]


def flips_per_10_turns(data: RunData, variant=None):
    flips = sum(1 for r in data.replays for _s, _k, before, _a in _changes(r) if before is not None)
    return Ratio(flips * 10, data.turns)


def _caused_by(replay, tag: str, *, flips_only: bool) -> int:
    """Value changes caused by a turn carrying `tag`: a change in that turn's own snapshot, or in
    a later batch snapshot whose slot cites that turn as evidence."""
    tagged = {
        t["id"] for s in replay.persona["sessions"] for t in s["turns"] if tag in t.get("tags", [])
    }
    count = 0
    for snap, slot, before, _after in _changes(replay):
        if flips_only and before is None:
            continue
        if snap["kind"] == "turn" and snap["turn_id"] in tagged:
            count += 1
        elif snap["kind"] == "batch":
            cited = {
                e.get("message_id") for e in (snap["profile"].get(slot) or {}).get("evidence", [])
            }
            count += bool(cited & tagged)
    return count


def outlier_flips(data: RunData, variant=None):
    return sum(_caused_by(r, "outlier", flips_only=True) for r in data.replays)


def injection_writes(data: RunData, variant=None):
    return sum(_caused_by(r, "injected", flips_only=False) for r in data.replays)


def fill_rate_at_k(data: RunData, variant=None):
    k = int(variant)
    filled = pairs = 0
    for replay in data.replays:
        snaps = replay.turn_snapshots()
        if len(snaps) < k:
            continue
        for slot in _slots():
            if replay.persona["golden"].get(slot) is not None:
                pairs += 1
                filled += snaps[k - 1]["profile"].get(slot) is not None
    return Ratio(filled, pairs)


def _final_values(replay) -> dict:
    return {s: p["value"] for s, p in replay.final_profile.items() if p is not None}


def run_agreement(data: RunData, variant=None):
    runs = [*data.prior_profiles, {r.persona["user_id"]: _final_values(r) for r in data.replays}]
    if len(runs) < CROSS_RUNS:  # defined over all three runs only
        return None
    agree = filled = 0
    for user in runs[-1]:
        slots = {s for run in runs for s in run.get(user, {})}
        for slot in slots:
            filled += 1
            values = [json.dumps(run.get(user, {}).get(slot)) for run in runs]
            agree += len(set(values)) == 1 and values[0] != "null"
    return Ratio(agree, filled)


def calls_per_turn(data: RunData, variant=None):
    return Ratio(len(data.calls), data.turns)


def usd_per_1000_turns(data: RunData, variant=None):
    usd = sum(estimate_cost(c["model"], c["in_tokens"], c["out_tokens"]) for c in data.calls)
    return usd / data.turns * 1000 if data.turns else None


def stated_overwritten(data: RunData, variant=None):
    count = 0
    for replay in data.replays:
        for event in replay.persona.get("stated_events", []):
            seen = False
            held = None
            for snap in replay.snapshots:
                if snap["kind"] == "turn" and snap["turn_id"] == event["after_turn"]:
                    seen = True
                    held = (snap["profile"].get(event["slot"]) or {}).get("value")
                    continue
                if not seen:
                    continue
                now = (snap["profile"].get(event["slot"]) or {}).get("value")
                if held == event["value"] and now != held:
                    count += 1
                held = now
    return count


def idempotent(data: RunData, variant=None):
    flags = [r.idempotent for r in data.replays if r.idempotent is not None]
    return float(all(flags)) if flags else None


def conflict_marked(data: RunData, variant=None):
    planted = sum(
        "conflict" in t.get("tags", [])
        for r in data.replays for s in r.persona["sessions"] for t in s["turns"]
    )  # fmt: skip
    return Ratio(sum(r.conflicts_marked for r in data.replays), planted)


def exclusion_stored(data: RunData, variant=None):
    count = 0
    for replay in data.replays:
        tags = {
            g for s in replay.persona["sessions"] for t in s["turns"] for g in t.get("tags", [])
        }
        if tags & {"exclusion", "must_not_store"}:
            count += sum(
                not evaluate_check(c, replay)["passed"]
                for c in replay.persona.get("checks", [])
                if c["type"] == "never_stored"
            )
    return count


def sensitive_dropped_share(data: RunData, variant=None):
    return Ratio(sum(r.skips["sensitive"] for r in data.replays), data.turns)


def sensitive_reached_model(replays) -> int:
    """Turns tagged `sensitive` in the dataset whose text appears in any model call. The tag is
    independent of the screen under test."""
    count = 0
    for replay in replays:
        for session in replay.persona["sessions"]:
            for turn in session["turns"]:
                if "sensitive" in turn.get("tags", []):
                    count += any(turn["user"] in text for text in replay.sent_texts)
    return count


# -- label metrics ----------------------------------------------------------------------------


def label_accuracy(data: RunData, variant=None):
    return Ratio(sum(label == pred for label, pred in data.labels), len(data.labels))


def label_wrong(data: RunData, variant=None):
    return sum(pred != label and pred != "unknown" for label, pred in data.labels)


def coverage(data: RunData, variant=None):
    return Ratio(sum(pred != "unknown" for _l, pred in data.labels), len(data.labels))


# -- across runs ------------------------------------------------------------------------------


CROSS_RUNS = 3  # runs a cross-run metric compares
_HISTORY = ("run_agreement", "runs_identical", "lead_over_next")


def split_key(key: str) -> tuple[str, str | None]:
    name, _, rest = key.partition("@")
    return name, rest or None


def runs_identical(data: RunData, variant=None):
    if len(data.prior_metrics) < CROSS_RUNS - 1:  # defined over all three runs only
        return None
    # metrics that read the other runs differ between runs by construction, so they are left out
    plain = lambda m: {k: v for k, v in m.items() if split_key(k)[0] not in _HISTORY}  # noqa: E731
    current = plain(data.metrics_so_far)
    for prior in data.prior_metrics:
        other = plain(prior)
        if other.keys() != current.keys():
            return 0.0
        for key, value in current.items():
            a, b = value, other[key]
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-12):
                return 0.0
    return 1.0


def lead_over_next(data: RunData, variant=None):
    runs = [*data.prior_metrics, data.metrics_so_far]
    series: dict[str, list[float]] = {}
    for run in runs:
        for key, value in run.items():
            if key.startswith("slot_accuracy@") and value is not None:
                series.setdefault(key.split("@", 1)[1], []).append(value)
    if variant not in series or len(series) < 2:
        return None
    best_other = max(max(v) for k, v in series.items() if k != variant)
    return min(series[variant]) - best_other


# -- metrics read from the pipeline's counts --------------------------------------------------


def _counted_ratio(name: str):
    def compute(data: RunData, variant=None):
        if name not in data.counts or f"{name}_of" not in data.counts:
            return None
        return Ratio(data.counts[name], data.counts[f"{name}_of"])

    return compute


def _counted(name: str):
    def compute(data: RunData, variant=None):
        return data.counts.get(name)

    return compute


_REPLAY = {
    "slot_accuracy": ("fraction", slot_accuracy),
    "over_inferred": ("fraction", over_inferred),
    "checks_passed": ("fraction", checks_passed),
    "first_session_fill": ("fraction", first_session_fill),
    "turns_to_fill": ("turns", turns_to_fill),
    "wrong_slot_turns": ("fraction", wrong_slot_turns),
    "flips_per_10_turns": ("flips", flips_per_10_turns),
    "outlier_flips": ("count", outlier_flips),
    "calls_per_turn": ("calls", calls_per_turn),
    "usd_per_1000_turns": ("usd", usd_per_1000_turns),
    "stated_overwritten": ("count", stated_overwritten),
    "idempotent": ("flag", idempotent),
    "conflict_marked": ("fraction", conflict_marked),
    "injection_writes": ("count", injection_writes),
    "label_accuracy": ("fraction", label_accuracy),
    "label_wrong": ("count", label_wrong),
    "coverage": ("fraction", coverage),
    "sensitive_dropped_share": ("fraction", sensitive_dropped_share),
    "exclusion_stored": ("count", exclusion_stored),
}
_COUNTED_RATIOS = [
    "lang_correct_t1", "prefers_personalized", "neutral_changed", "injected_on_relevant",
    "injected_on_neutral", "other_voice_kept", "preferred_share", "byte_identical_off",
    "marker_coverage", "override_honoured", "paragraph_agreement", "tool_called_on_relevant",
    "tool_called_on_neutral", "prefers_always_over_arm", "mentions_not_hidden",
    "both_answers_arrived", "off_prompt_matches_plain", "notices_per_learner",
    "edited_value_in_next_prompt", "marked_active_next_turn", "style_adherence",
    "confirmed_style_applied",
]  # fmt: skip
_COUNTED = {
    "intrusive": "count", "store_writes": "count", "slots_changed_by_country": "count",
    "paragraph_violations": "count", "style_override_effect": "count",
    "declined_style_stored": "count", "other_profile_reads": "count",
    "writes_after_opt_out": "count", "unmarked_single_active": "count",
    "marked_sensitive_stored": "count", "notice_on_plain": "count",
    "overhead_tokens": "tokens", "recount_gap": "fraction", "preferred_share_lift": "fraction",
}  # fmt: skip

# The formulas below are quoted from the test-tree rows `notices_per_learner` and `label_accuracy`.
FORMULAS_EXTRA = {
    "notice_on_plain": "notices shown on plain answers (from the `notices_per_learner` row)",
    "label_wrong": 'items whose predicted label differs from the label, not counting "unknown" (from the `label_accuracy` row)',
}


def _base_metrics() -> dict[str, Metric]:
    formulas = {**FORMULAS, **FORMULAS_EXTRA}
    found: dict[str, Metric] = {}
    for name, (unit, fn) in _REPLAY.items():
        found[name] = Metric(name, unit, formulas[name], fn)
    found["fill_rate_at_k"] = Metric(
        "fill_rate_at_k", "fraction", formulas["fill_rate_at_k"], fill_rate_at_k, params=("2", "4", "8")
    )  # fmt: skip
    found["run_agreement"] = Metric("run_agreement", "fraction", formulas["run_agreement"], run_agreement, history=True, cross_run=True)  # fmt: skip
    found["runs_identical"] = Metric("runs_identical", "flag", formulas["runs_identical"], runs_identical, history=True, cross_run=True)  # fmt: skip
    found["lead_over_next"] = Metric("lead_over_next", "fraction", formulas["lead_over_next"], lead_over_next, history=True)  # fmt: skip
    for name in _COUNTED_RATIOS:
        found[name] = Metric(name, "fraction", formulas[name], _counted_ratio(name))
    for name, unit in _COUNTED.items():
        found[name] = Metric(name, unit, formulas[name], _counted(name))
    return found


def load_aspect_metrics(path: Path) -> dict[str, Metric]:
    """Metrics an aspect adds in `cases/<aspect>/metrics.py`: a module-level `METRICS` dict of
    `Metric`, each with a formula. A metric that shadows a core one is refused."""
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location(f"lab_aspect_metrics_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    added = dict(getattr(module, "METRICS", {}))
    for name, metric in added.items():
        if not metric.formula:
            raise ValueError(f"{path}: metric {name} has no formula")
        if name in BASE:
            raise ValueError(f"{path}: metric {name} is already defined in the core set")
    return added


def load_all_aspect_metrics(root: Path = CASES_ROOT) -> dict[str, Metric]:
    found: dict[str, Metric] = {}
    for path in sorted(root.glob("*/metrics.py")):
        found.update(load_aspect_metrics(path))
    return found


BASE: dict[str, Metric] = _base_metrics()
METRICS: dict[str, Metric] = {**BASE, **load_all_aspect_metrics()}


def base_name(key: str) -> str:
    return key.split("@", 1)[0]


def metric_for(key: str, extra: dict[str, Metric] | None = None) -> Metric | None:
    return {**METRICS, **(extra or {})}.get(base_name(key))


def evaluate(
    metric: Metric, data: RunData, variant: str | None = None
) -> tuple[float | None, Ratio | None]:
    """(value, ratio) of one metric; `ratio` is set when the metric is a fraction of counts."""
    result = metric.compute(data, variant)
    if isinstance(result, Ratio):
        return result.value, result
    return result, None
