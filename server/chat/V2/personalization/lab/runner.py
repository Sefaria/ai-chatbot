"""Run one case on one dataset, once, and return the result dict (see README for its shape).

The case's `pipeline.py` is loaded from its directory. Its entry point depends on `case.task`:
`replay` -> `build(config, llm, typed=None) -> Pipeline`; `label` -> `predict(item, config, llm)`.
Optional hooks for `LAB run --mock`: `mock_llm()`, `mock_typed()`, `mock_items()`.
Optional hook for every run: `select_items(items, config, dataset) -> items`, a reproducible
subset of a dataset (for example a seeded sample), applied before the items are replayed.
Optional hook for a `replay` case: `counts(variant_config, replays, llm) -> dict`, the `RunData.counts`
of one variant (for the metrics read from counts, such as `overhead_tokens` and `recount_gap`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..llm import estimate_cost
from ..typed import JevTypedClient
from . import guard
from . import results as results_mod
from .datasets import Dataset
from .manifest import Case
from .metrics import (
    METRICS,
    Metric,
    RunData,
    evaluate,
    load_aspect_metrics,
    sensitive_reached_model,
    split_key,
)
from .replay import PersonaReplay, replay_persona
from .steps import check_step_name

SCHEMA = 1
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class JevOnIdentifiableData(RuntimeError):
    """Jev receives synthetic or anonymized text only; a non-anonymized real dataset never gets a Jev client."""


class RunnerError(RuntimeError):
    pass


def _safe(fn, case_dir) -> str:
    """A git-derived string, or "" when the case is not in a repository (a throwaway test case)."""
    if not case_dir:
        return ""
    try:
        return fn(case_dir)
    except guard.NotPreregistered:
        return ""


def load_pipeline(case_dir):
    path = Path(case_dir) / "pipeline.py"
    if not path.is_file():
        raise RunnerError(f"{case_dir}: no pipeline.py")
    spec = importlib.util.spec_from_file_location(f"lab_case_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the case dir
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


class _CountingLLM:
    """Wraps an LLM client and records one text-free row per call."""

    def __init__(self, inner, step: str):
        self.inner, self.step, self.rows = inner, step, []

    def complete(self, *, system, user, model, max_tokens=600):
        result = self.inner.complete(system=system, user=user, model=model, max_tokens=max_tokens)
        self.rows.append(
            {
                "step": self.step,
                "model": model,
                "in_tokens": result.input_tokens,
                "out_tokens": result.output_tokens,
            }
        )
        return result


# -- progress (resume) ------------------------------------------------------------------------


def _slug(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _save_replay(path: Path, replay: PersonaReplay) -> None:
    data = asdict(replay)
    data.pop("persona")
    data["skips"] = dict(replay.skips)
    data["jobs"] = [j.isoformat() for j in replay.jobs]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def _load_replay(path: Path, persona: dict) -> PersonaReplay | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["skips"] = Counter(data["skips"])
    data["jobs"] = [datetime.fromisoformat(j) for j in data["jobs"]]
    data["step_counts"] = {k: tuple(v) for k, v in data.get("step_counts", {}).items()}
    return PersonaReplay(persona=persona, **data)


# -- expanding the case's metric list ---------------------------------------------------------


def expand_keys(case: Case, variants: list[str | None], known: dict[str, Metric]):
    """[(key, metric, design variant, param)] for every metric key the result carries."""
    keys, seen = [], set()
    for raw in case.metrics:
        name, suffix = split_key(raw)
        metric = known[name]
        if suffix is None:
            designs, params = variants, (metric.params or (None,))
        elif metric.params and "/" in suffix:
            design, _, param = suffix.partition("/")
            designs, params = [design], (param,)
        elif metric.params and suffix in metric.params:
            designs, params = [None], (suffix,)
        else:
            designs, params = [suffix], (None,)
        for design in designs:
            for param in params:
                key = name + (
                    "" if design is None and param is None
                    else "@" + "/".join(x for x in (design, param) if x is not None)
                )  # fmt: skip
                if key not in seen:
                    seen.add(key)
                    keys.append((key, metric, design, param))
    return keys


def _read_prior(case_dir, case_id, dataset: Dataset, run_no: int, real: bool):
    prior_metrics, prior_profiles = [], []
    part = results_mod.partial_dir(case_id, case_dir, real) if case_dir else None
    for n in range(1, run_no):
        name = results_mod.file_name("live", n, real)
        previous = results_mod.read(case_dir, name) if case_dir else None
        if previous:
            source = (
                previous
                if previous["dataset"]["name"] == dataset.name
                else previous.get("also", {}).get(dataset.name)
            )
            if source:
                prior_metrics.append(source["metrics"])
        if part and (part / f"profiles_{dataset.name}_run{n}.json").is_file():
            prior_profiles.append(
                json.loads(
                    (part / f"profiles_{dataset.name}_run{n}.json").read_text(encoding="utf-8")
                )
            )
    return prior_metrics, prior_profiles


def _normalise_persona(item: dict) -> dict:
    return {"golden": {}, "checks": [], "interview": None, **item}


# -- the run ----------------------------------------------------------------------------------


def run_case(
    case: Case, *, kind: str, run_no: int, dataset: Dataset, llm, typed=None, judge=None,
    resume: bool = False, items=None, pipeline_module=None,
) -> dict:  # fmt: skip
    if kind not in ("mock", "live"):
        raise RunnerError(f"kind must be 'mock' or 'live', not {kind!r}")
    real = dataset.kind == "real"
    if (
        real
        and not dataset.anonymized
        and (isinstance(typed, JevTypedClient) or getattr(typed, "jev", None) is not None)
    ):
        raise JevOnIdentifiableData(
            f"dataset {dataset.name} is real and not anonymized; Jev gets synthetic or anonymized text only"
        )
    case_dir = case.dir or None
    if kind == "live" and not case_dir:
        raise RunnerError("a live run needs the case directory; refusing an unguarded live run")
    if kind == "live":
        guard.assert_preregistered(case_dir)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    known = {
        **METRICS,
        **(load_aspect_metrics(Path(case_dir).parent / "metrics.py") if case_dir else {}),
    }
    module = pipeline_module or (load_pipeline(case_dir) if case_dir else None)
    config = {**case.config}
    model = config.get("model", DEFAULT_MODEL)
    if items is None:
        items = list(dataset.items)
        if module is not None and hasattr(module, "select_items"):
            items = list(module.select_items(items, config, dataset))
    items = list(items)
    variants: list[str | None] = list(config.get("variants") or [None])
    part = results_mod.partial_dir(case.id, case_dir, real) if (case_dir or real) else None

    datas: dict[str | None, RunData] = {}
    per_item: list[dict] = []
    steps: Counter = Counter()
    step_in: Counter = Counter()
    step_names: dict[str, None] = {}  # the pipeline's steps, in order
    invalid = 0
    sent_sensitive = 0
    call_rows: list[dict] = []
    profiles: dict[str, dict] = {}
    need_idempotent = any(split_key(k)[0] == "idempotent" for k in case.metrics)

    if case.task == "replay":
        for variant in variants:
            vconfig = {
                **config,
                **(
                    {"variant": variant, "strategy": config.get("strategy", variant)}
                    if variant
                    else {}
                ),
            }
            replays: list[PersonaReplay] = []
            unavailable = False
            for raw in items:
                persona = _normalise_persona(raw)
                saved = (
                    part / f"run{run_no}" / f"{variant or '_'}__{_slug(persona['user_id'])}.json"
                    if (resume and part)
                    else None
                )
                replay = _load_replay(saved, persona) if saved else None
                if replay is None:
                    try:
                        pipeline = module.build(vconfig, llm, typed)
                    except ValueError:  # e.g. a design that needs a Jev client that is not there
                        unavailable = True
                        break
                    for name in pipeline.steps():
                        step_names.setdefault(name)
                    replay = replay_persona(pipeline, persona, check_idempotent=need_idempotent)
                    if part and kind == "live":
                        _save_replay(
                            part
                            / f"run{run_no}"
                            / f"{variant or '_'}__{_slug(persona['user_id'])}.json",
                            replay,
                        )
                replays.append(replay)
                for name, (n_in, n_out) in replay.step_counts.items():
                    step_in[name] += n_in
                    steps[name] += n_out
            if unavailable:
                invalid += len(items)
                datas[variant] = RunData(replays=[])
                datas[variant].counts["unavailable"] = 1
                continue
            for replay in replays:
                call_rows += [
                    {
                        "variant": variant or "",
                        "persona": replay.persona.get("id", "") if not real else "",
                        **c,
                    }
                    for c in replay.calls
                ]
                invalid += replay.diagnostics.get("invalid", 0)
            data = RunData(replays=replays, calls=[c for r in replays for c in r.calls])
            if module is not None and hasattr(module, "counts"):
                data.counts.update(module.counts(vconfig, replays, llm))
            datas[variant] = data
            if not real:
                sent_sensitive += sensitive_reached_model(replays)
                per_item += [
                    {"persona": r.persona.get("id", r.persona["user_id"]), "variant": variant or "", "turns": r.turns,
                     "final_profile": {k: (v or {}).get("value") for k, v in r.final_profile.items()},
                     "checks": _check_rows(r)}
                    for r in replays
                ]  # fmt: skip
        n_turns = sum(d.turns for d in datas.values()) // max(1, len(datas))
    elif case.task == "label":
        counting = _CountingLLM(llm, "assign_vocabulary")
        labels = []
        for item in items:
            predicted = module.predict(item, config, counting)
            invalid += predicted == "invalid"
            labels.append((item["label"], predicted))
            if not real:
                per_item.append(
                    {"id": item.get("id", ""), "label": item["label"], "predicted": predicted}
                )
        data = RunData(labels=labels, calls=counting.rows, counts={"turns": len(items)})
        datas[None] = data
        call_rows = [dict(c, variant="", persona="") for c in counting.rows]
        n_turns = len(items)
        step_names["assign_vocabulary"] = None
        steps["assign_vocabulary"] = sum(p != "invalid" for _l, p in labels)
        step_in["assign_vocabulary"] = len(items)
    else:
        raise RunnerError(f"task {case.task!r} is run by the lab app (see Task 15)")

    # metrics: first the plain ones, then those that read earlier runs
    prior_metrics, prior_profiles = _read_prior(case_dir, case.id, dataset, run_no, real)
    values: dict[str, float | None] = {}
    ratios: dict[str, list[float]] = {}
    expanded = expand_keys(case, variants, known)
    for history in (False, True):
        for key, metric, design, param in expanded:
            if metric.history != history:
                continue
            data = datas.get(design) or next(iter(datas.values()))
            data.prior_metrics = prior_metrics
            data.prior_profiles = [p.get(design or "", {}) for p in prior_profiles]
            data.metrics_so_far = values
            if data.counts.get("unavailable"):
                values[key] = None
                continue
            value, ratio = evaluate(metric, data, param if metric.params else design)
            values[key] = value
            if ratio is not None:
                ratios[key] = [ratio.num, ratio.den]

    # privacy numbers that apply to every case
    privacy = {
        "sensitive_reached_model": None if real else sent_sensitive,
        "stated_overwritten": 0,
        "exclusion_stored": 0,
    }
    for data in datas.values():
        if data.replays:
            privacy["stated_overwritten"] += METRICS["stated_overwritten"].compute(data, None)
            privacy["exclusion_stored"] += METRICS["exclusion_stored"].compute(data, None)

    usd = sum(estimate_cost(c["model"], c["in_tokens"], c["out_tokens"]) for c in call_rows)
    cost = {
        "calls": len(call_rows), "in_tokens": sum(c["in_tokens"] for c in call_rows),
        "out_tokens": sum(c["out_tokens"] for c in call_rows),
        "typed_requests": sum(c["step"] == "typed_questions" for c in call_rows), "usd": usd,
    }  # fmt: skip
    # a count a pipeline did not measure is null, never a guess
    step_rows = [
        {"name": check_step_name(n), "n_in": step_in.get(n), "n_out": steps.get(n)}
        for n in step_names
    ]
    if case.task == "replay":
        step_rows.insert(0, {"name": "load_turns", "n_in": n_turns, "n_out": n_turns})
    step_rows.append({"name": "score", "n_in": len(items), "n_out": len(values)})

    result = {
        "schema": SCHEMA, "id": case.id, "kind": kind, "run": run_no,
        "dataset": {"name": dataset.name, "kind": dataset.kind, "sha256": dataset.sha256, "n": len(items)},
        "case_sha": _safe(guard.case_sha, case_dir),
        "git_sha": _safe(guard.head_sha, case_dir),
        "started_at": started_at, "model": model,
        "metrics": values, "ratios": ratios, "invalid": int(invalid), "cost": cost,
        "steps": step_rows, "privacy": privacy,
    }  # fmt: skip
    if not real:
        result["per_item"] = per_item
    if case.task == "replay":
        profiles = {
            (variant or ""): {
                r.persona["user_id"]: {k: v["value"] for k, v in r.final_profile.items() if v}
                for r in d.replays
            }
            for variant, d in datas.items()
        }
    if part and kind == "live":
        part.mkdir(parents=True, exist_ok=True)
        (part / f"profiles_{dataset.name}_run{run_no}.json").write_text(
            json.dumps(profiles), encoding="utf-8"
        )
    if case.task == "replay" and not real and case_dir:
        name = "calls_mock.json" if kind == "mock" else f"calls_run{run_no}.json"
        out = Path(case_dir) / "results" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(call_rows, indent=1), encoding="utf-8")
    return result


def _check_rows(replay: PersonaReplay):
    from .scoring import evaluate_check

    return [evaluate_check(c, replay) for c in replay.persona.get("checks", [])]
