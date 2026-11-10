"""Replay synthetic personas through the memory strategies and score the result.

python -m chat.V2.personalization.evals.run_memory_eval --llm fake --out DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ..jev import FakeJevClient, HttpJevClient
from ..llm import ClaudeCodeLLMClient, FakeLLMClient, estimate_cost
from ..registry import SlotRegistry
from ..strategies import jev_questions
from .oracle import fake_jev_responder, fake_responder, oracle_jev_responder, oracle_responder
from .runner import PersonaRun, run_persona
from .scoring import VERDICTS, evaluate_check, score_slot

DATA = Path(__file__).parent / "data"
ALL_STRATEGIES = ["realtime", "nightly", "hybrid", "hybrid_jev", "explicit"]
JEV_MODEL = jev_questions.JEV_MODEL
PRICE_MODELS = ["claude-haiku-4-5", "claude-haiku-5-5"]
ASSUMPTIONS = [
    "Hybrid: every pending proposal is auto-accepted right after the turn that created it, "
    "simulating the learner tapping yes. The count is reported as auto_accepted.",
    "Batches run at 03:00 UTC on the day after each day boundary and once after the last turn.",
    "Explicit: the persona's interview answers are written as stated slots before the first turn.",
    "Stated events (a learner setting a slot) apply to every strategy after the named turn.",
    "Personas are synthetic; accuracy is against a golden profile written by the dataset author.",
    "hybrid_jev: Jev requests are priced at the Jev rate in both cost scenarios; Jev never writes "
    "free text, so answer_style and learning_habits come only from the LLM batch or an accepted "
    "proposal. Auto-accept applies here too.",
    "Oracle runs answer with golden values on every prompt: scores show the pipeline ceiling.",
]


def load_personas(ids: list[str] | None = None) -> list[dict]:
    personas = json.loads((DATA / "personas.json").read_text(encoding="utf-8"))["personas"]
    return [p for p in personas if not ids or p["id"] in ids]


def _persona_result(run: PersonaRun, registry: SlotRegistry) -> dict:
    golden = run.persona["golden"]
    verdicts = {}
    for slot in registry.all():
        actual = run.final_profile.get(slot.key)
        verdicts[slot.key] = {
            "verdict": score_slot(golden.get(slot.key), actual),
            "golden": golden.get(slot.key),
            "actual": actual["value"] if actual else None,
            "status": actual["status"] if actual else None,
        }
    checks = [evaluate_check(c, run) for c in run.persona["checks"]]
    return {
        "user_id": run.persona["user_id"], "trap": run.persona["trap"], "turns": run.turns,
        "verdicts": verdicts, "checks": checks, "final_profile": run.final_profile,
        "skips": dict(run.skips), "auto_accepted": run.auto_accepted, "snapshots": run.snapshots,
    }  # fmt: skip


def _accuracy(counts: dict) -> float | None:
    scored = sum(counts[v] for v in ("correct", "wrong", "missing", "over_inferred"))
    return counts["correct"] / scored if scored else None


def _is_jev(entry) -> bool:
    return entry.model.startswith("jev")


def _cost(runs: list[PersonaRun]) -> dict:
    entries = [e for r in runs for e in r.ledger_entries]
    llm_entries = [e for e in entries if not _is_jev(e)]
    jev_entries = [e for e in entries if _is_jev(e)]
    turns = sum(r.turns for r in runs) or 1
    tokens_in = sum(e.input_tokens for e in llm_entries)
    tokens_out = sum(e.output_tokens for e in llm_entries)
    jev_usd = sum(estimate_cost(e.model, e.input_tokens, e.output_tokens) for e in jev_entries)
    usd = {m: estimate_cost(m, tokens_in, tokens_out) + jev_usd for m in PRICE_MODELS}
    return {
        "llm_calls": len(llm_entries),
        "calls_hot": sum(e.phase == "turn" for e in llm_entries),
        "calls_batch": sum(e.phase == "batch" for e in llm_entries),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "raw_input_tokens": sum(r.raw_input_tokens for r in runs),
        "calls_per_turn": len(llm_entries) / turns,
        "tokens_per_turn": (tokens_in + tokens_out) / turns,
        "tokens_per_user": (tokens_in + tokens_out) / (len(runs) or 1),
        "avg_latency_ms": sum(e.latency_ms for e in llm_entries) / len(llm_entries)
        if llm_entries
        else 0,
        "jev_requests": len(jev_entries),
        "jev_requests_per_turn": len(jev_entries) / turns,
        "jev_input_tokens": sum(e.input_tokens for e in jev_entries),
        "jev_input_tokens_per_turn": sum(e.input_tokens for e in jev_entries) / turns,
        "jev_avg_latency_ms": sum(e.latency_ms for e in jev_entries) / len(jev_entries)
        if jev_entries
        else 0,
        "jev_usd": jev_usd,
        "turns": turns,
        "usd": usd,
        "usd_per_1000_turns": {m: v / turns * 1000 for m, v in usd.items()},
    }


def summarize_strategy(runs: list[PersonaRun], registry: SlotRegistry) -> dict:
    personas = {r.persona["id"]: _persona_result(r, registry) for r in runs}
    overall = Counter()
    per_slot: dict[str, Counter] = {s.key: Counter() for s in registry.all()}
    by_type: dict[str, dict] = {}
    in_session = []
    for pid, result in personas.items():
        for key, v in result["verdicts"].items():
            overall[v["verdict"]] += 1
            per_slot[key][v["verdict"]] += 1
        for check in result["checks"]:
            bucket = by_type.setdefault(check["type"], {"passed": 0, "total": 0, "per_persona": []})
            bucket["total"] += 1
            bucket["passed"] += check["passed"]
            bucket["per_persona"].append({"persona": pid, **check})
            if check["type"] == "available_by_turn":
                in_session.append({"persona": pid, **check})
    counts = {v: overall[v] for v in VERDICTS}
    return {
        "slot_accuracy": {
            **counts,
            "accuracy": _accuracy(counts),
            "per_slot": {
                k: {
                    **{v: c[v] for v in VERDICTS},
                    "accuracy": _accuracy({v: c[v] for v in VERDICTS}),
                }
                for k, c in per_slot.items()
            },
        },
        "checks": by_type,
        "in_session": in_session,
        "cost": _cost(runs),
        "skips": dict(sum((r.skips for r in runs), Counter())),
        "auto_accepted": sum(r.auto_accepted for r in runs),
        "personas": personas,
    }


def run_eval(
    strategies: list[str],
    personas: list[dict],
    llm_kind: str,
    model: str,
    workers: int = 4,
    claude_client: ClaudeCodeLLMClient | None = None,
    jev_kind: str = "fake",
    jev_client: HttpJevClient | None = None,
) -> dict:
    registry = SlotRegistry.load()

    def llm_for(persona):
        if llm_kind == "claude-code":
            return claude_client
        responder = oracle_responder(persona) if llm_kind == "oracle" else fake_responder
        return FakeLLMClient(responder)

    def jev_for(persona):
        if jev_kind == "http":
            return jev_client
        return FakeJevClient(
            oracle_jev_responder(persona) if jev_kind == "oracle" else fake_jev_responder
        )

    jobs = [(s, p) for s in strategies for p in personas]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        runs = list(
            pool.map(
                lambda j: run_persona(j[0], j[1], llm_for(j[1]), model, registry, jev_for(j[1])),
                jobs,
            )
        )
    by_strategy = {s: [r for r in runs if r.strategy == s] for s in strategies}
    return {
        "meta": {
            "date": date.today().isoformat(),
            "model": model,
            "llm": llm_kind,
            "harness_overhead_tokens": dict(claude_client.overhead) if claude_client else {},
            "slot_set_version": registry.version,
            "jev": jev_kind,
            "jev_model": JEV_MODEL,
            "jev_thresholds": jev_questions.thresholds(),
            "personas": [p["id"] for p in personas],
            "assumptions": ASSUMPTIONS,
        },
        "strategies": {s: summarize_strategy(rs, registry) for s, rs in by_strategy.items()},
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def summary_table(results: dict) -> str:
    rows = [
        "| strategy | slot accuracy | checks passed | in-session | model calls/turn "
        "| Jev requests/turn | Jev input tokens/turn | Jev avg latency ms | model tokens/turn "
        "| $/1k turns (haiku-4-5) | $/1k turns (haiku-5-5) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in results["strategies"].items():
        passed = sum(c["passed"] for c in s["checks"].values())
        total = sum(c["total"] for c in s["checks"].values())
        ins = sum(c["passed"] for c in s["in_session"])
        cost = s["cost"]
        per_1k = cost["usd_per_1000_turns"]
        rows.append(
            f"| {name} | {_pct(s['slot_accuracy']['accuracy'])} | {passed}/{total} "
            f"| {ins}/{len(s['in_session'])} | {cost['calls_per_turn']:.2f} "
            f"| {cost['jev_requests_per_turn']:.2f} | {cost['jev_input_tokens_per_turn']:.0f} "
            f"| {cost['jev_avg_latency_ms']:.0f} | {cost['tokens_per_turn']:.0f} "
            f"| ${per_1k['claude-haiku-4-5']:.3f} | ${per_1k['claude-haiku-5-5']:.3f} |"
        )
    return "\n".join(rows)


def render_markdown(results: dict) -> str:
    meta, strategies = results["meta"], results["strategies"]
    out = [
        "# Memory evaluation",
        f"Date {meta['date']}, llm `{meta['llm']}`, model `{meta['model']}`, slot set "
        f"{meta['slot_set_version']}.",
        "",
        "## Summary",
        summary_table(results),
        "",
        "## Assumptions",
        *[f"- {a}" for a in meta["assumptions"]],
        "",
        "## Slot accuracy by slot",
    ]
    slots = list(next(iter(strategies.values()))["slot_accuracy"]["per_slot"]) if strategies else []
    out += ["| slot | " + " | ".join(strategies) + " |", "|---|" + "---|" * len(strategies)]
    for slot in slots:
        cells = [
            _pct(s["slot_accuracy"]["per_slot"][slot]["accuracy"]) for s in strategies.values()
        ]
        out.append(f"| {slot} | " + " | ".join(cells) + " |")
    out += ["", "## Failed checks", "| strategy | persona | check | detail |", "|---|---|---|---|"]
    for name, s in strategies.items():
        for pid, p in s["personas"].items():
            for c in p["checks"]:
                if not c["passed"]:
                    label = c["type"] + (f" ({c['slot']})" if "slot" in c else "")
                    out.append(f"| {name} | {pid} | {label} | {c['detail']} |")
    out += ["", "## Per persona (slot verdicts, correct/total scored)"]
    out += [
        "| persona | trap | " + " | ".join(strategies) + " |",
        "|---|---|" + "---|" * len(strategies),
    ]
    for pid in meta["personas"]:
        cells = []
        for s in strategies.values():
            verdicts = [v["verdict"] for v in s["personas"][pid]["verdicts"].values()]
            scored = [v for v in verdicts if v != "not_applicable"]
            cells.append(f"{verdicts.count('correct')}/{len(scored)}")
        trap = next(iter(strategies.values()))["personas"][pid]["trap"]
        out.append(f"| {pid} | {trap} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategies", default=",".join(ALL_STRATEGIES))
    parser.add_argument("--llm", choices=["fake", "oracle", "claude-code"], default="fake")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--personas", default="")
    parser.add_argument("--jev", choices=["fake", "oracle", "http"], default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    client = None
    if args.llm == "claude-code":
        client = ClaudeCodeLLMClient()
        client.calibrate(args.model)
    jev_kind = args.jev or {"fake": "fake", "oracle": "oracle", "claude-code": "http"}[args.llm]
    jev_client = HttpJevClient(allow_keychain=True) if jev_kind == "http" else None
    results = run_eval(
        args.strategies.split(","),
        load_personas([p for p in args.personas.split(",") if p]),
        args.llm,
        args.model,
        args.workers,
        client,
        jev_kind,
        jev_client,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "memory_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "memory_results.md").write_text(render_markdown(results), encoding="utf-8")
    print(summary_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
