"""reverify/cost-recount: the cost of the five designs, recomputed from heldout-personas' saved per-call rows.

No extraction call is made. Run n reads `calls_run<n>.json` and `run<n>.json` (the ledger's total) of
`reverify/heldout-personas` on `origin/lab/reverify/heldout-personas` with `git show`. Each variant is
a view of those rows: a design's own rows (priced as saved, Haiku 4.5), the same rows priced at Haiku 5.5
(`<design>_h55`), or `total`, every design's rows together (for `recount_gap`). The only live model
calls are `overhead_calls` empty prompts per run through the run's own client, to re-measure the
headless overhead. Rows are saved net of that overhead (the client subtracts it once per call), so the
recount does not subtract it again. Run the three runs in order: run n is the count of existing
`results/run*.json` files plus one.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

from chat.V2.personalization.lab.datasets import DATA_DIR
from chat.V2.personalization.lab.guard import repo_root
from chat.V2.personalization.llm import LLMResult, estimate_cost

CASE_DIR = Path(__file__).parent
LIVE_RUNS = 3
PRICE_SUFFIX = "_h55"  # the same rows at Haiku 5.5 prices
TOTAL = "total"  # every design's rows together
HAIKU_45, HAIKU_55 = "claude-haiku-4-5", "claude-haiku-5-5"
STEPS = ["extract_turn", "extract_batch", "typed_questions"]


class Source:
    """The rows of one run of the sibling case, and the ledger total they were saved with."""

    def __init__(self, run, rows, ledger_usd, dataset_sha=None, persona_ids=None):
        self.run, self.rows, self.ledger_usd, self.dataset_sha = run, rows, ledger_usd, dataset_sha
        self.persona_ids: dict[str, str] = dict(persona_ids or {})  # user_id -> persona id

    def rows_for(self, design: str, persona: str) -> list[dict]:
        return [
            r for r in self.rows
            if r["persona"] == persona and (design == TOTAL or r["variant"] == design)
        ]  # fmt: skip


# -- reading the sibling case's results (live) ------------------------------------------------


def _git_json(ref: str, case_id: str, name: str):
    top = repo_root(CASE_DIR).resolve()
    rel = (CASE_DIR.parent.parent / case_id / "results" / name).resolve().relative_to(top)
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel.as_posix()}"], cwd=top, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read {name} of {case_id} on {ref}: run heldout-personas first")
    return json.loads(proc.stdout)


def current_run(results_dir: Path) -> int:
    n = 1 + sum((results_dir / f"run{k}.json").is_file() for k in range(1, LIVE_RUNS + 1))
    if n > LIVE_RUNS:
        raise RuntimeError("runs 1 to 3 already exist")
    return n


_LIVE: dict[int, Source] = {}


def live_source(config: dict) -> Source:
    n = current_run(CASE_DIR / "results")
    if n not in _LIVE:
        ref, case_id = config["source_ref"], config["source_case"]
        ledger = _git_json(ref, case_id, f"run{n}.json")
        _LIVE[n] = Source(
            n, _git_json(ref, case_id, f"calls_run{n}.json"), ledger["cost"]["usd"],
            ledger["dataset"]["sha256"],
        )  # fmt: skip
    return _LIVE[n]


def source_for(config: dict, llm) -> Source:
    """The fixture the mock model carries, else the sibling case's rows for this run."""
    return getattr(llm, "source", None) or live_source(config)


def select_items(items, config, dataset):
    """All items. On the dataset the rows were saved on: refuses a changed checksum and learns user ids."""
    if dataset.name != config["source_dataset"]:
        return list(items)  # the mock's fixture dataset
    source = live_source(config)
    if dataset.sha256 != source.dataset_sha:
        raise RuntimeError(
            f"{dataset.name} ({dataset.sha256[:12]}) is not the dataset the rows were saved on "
            f"({str(source.dataset_sha)[:12]})"
        )
    source.persona_ids.update({i["user_id"]: i["id"] for i in items})
    return list(items)


# -- the pipeline: no model, only the saved rows ----------------------------------------------


class RecountPipeline:
    def __init__(self, variant: str, source: Source):
        self.design = variant.removesuffix(PRICE_SUFFIX)
        self.priced = HAIKU_55 if variant.endswith(PRICE_SUFFIX) else None
        self.source = source
        self.persona: str | None = None

    def on_turn(self, user_id, turn) -> None:
        if self.persona is None:
            self.persona = self.source.persona_ids[user_id]

    def run_job(self, user_id, now) -> None:
        pass

    def profile(self, user_id) -> dict:
        return {}

    def steps(self) -> list[str]:
        return list(STEPS)

    def calls(self) -> list[dict]:
        rows = []
        for r in self.source.rows_for(self.design, self.persona) if self.persona else []:
            model = r["model"]
            if self.priced and model.startswith(HAIKU_45):
                model = self.priced
            rows.append({"step": r["step"], "model": model, "in_tokens": r["in_tokens"],
                         "out_tokens": r["out_tokens"]})  # fmt: skip
        return rows

    def step_counts(self) -> dict:
        calls = self.calls()
        return {s: (n, n) for s in STEPS if (n := sum(c["step"] == s for c in calls))}


def build(config, llm, typed=None):
    return RecountPipeline(config["variant"], source_for(config, llm))


# -- the numbers a replay cannot compute: overhead and recount --------------------------------


def measure_overhead(llm, config, source) -> float:
    """Mean input tokens of `overhead_calls` empty headless calls, once per run (the same trivial
    prompt as `ClaudeCodeLLMClient.calibrate`)."""
    done = vars(llm).setdefault("_lab_overhead", {})
    if source.run not in done:
        tokens = []
        for _ in range(int(config["overhead_calls"])):
            result = llm.complete(system="x", user="x", model=config["model"])
            tokens.append(
                result.raw_input_tokens
                if result.raw_input_tokens is not None
                else result.input_tokens
            )
        done[source.run] = statistics.mean(tokens)
    return done[source.run]


def counts(vconfig, replays, llm) -> dict:
    source = source_for(vconfig, llm)
    out = {"overhead_tokens": measure_overhead(llm, vconfig, source)}
    if vconfig["variant"] == TOTAL and source.ledger_usd:
        summed = sum(
            estimate_cost(c["model"], c["in_tokens"], c["out_tokens"])
            for r in replays for c in r.calls
        )  # fmt: skip
        out["recount_gap"] = abs(summed - source.ledger_usd) / source.ledger_usd
    return out


def explain_lines(case, read):
    iter1 = json.loads((DATA_DIR / "iter1_reported.json").read_text(encoding="utf-8"))["designs"]

    def mean(key):
        values = [(read(f"run{n}.json") or {}).get("metrics", {}).get(key) for n in range(1, 4)]
        return None if not values or any(v is None for v in values) else statistics.mean(values)

    show = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    lines = ["usd per 1,000 turns per design (iteration 1 | recount at Haiku 4.5 | at Haiku 5.5):"]
    for design in case.config["designs"]:
        lines.append(
            f"  {design}: iter1 {show(iter1[design]['usd_per_1000_turns_haiku45'])}"
            f" | haiku45 {show(mean(f'usd_per_1000_turns@{design}'))}"
            f" | haiku55 {show(mean(f'usd_per_1000_turns@{design}{PRICE_SUFFIX}'))}"
        )
    lines.append(f"overhead_tokens (mean of the runs): {show(mean('overhead_tokens@total'))}")
    return lines


# -- the mock: a fixed fixture of per-call rows with known token counts -----------------------


def _fixture() -> dict:
    return json.loads((CASE_DIR / "mock_fixture.json").read_text(encoding="utf-8"))


def mock_items():
    return _fixture()["personas"]


class FixtureLLM:
    """Stands in for the headless client: carries the fixture rows and answers each empty call with
    the next scripted raw input-token count."""

    def __init__(self, fixture: dict | None = None):
        fixture = fixture or _fixture()
        self.source = Source(
            "mock", fixture["rows"], fixture["ledger_usd"], fixture["dataset_sha"],
            {p["user_id"]: p["id"] for p in fixture["personas"]},
        )  # fmt: skip
        self.raw = list(fixture["overhead_raw"])
        self.calls: list[dict] = []

    def complete(self, *, system, user, model, max_tokens=600):
        self.calls.append({"system": system, "user": user, "model": model})
        raw = self.raw[(len(self.calls) - 1) % len(self.raw)]
        return LLMResult("ok", raw, 1, 0.0, 0, raw)


def mock_llm():
    return FixtureLLM()
