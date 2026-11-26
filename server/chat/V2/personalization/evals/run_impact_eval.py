"""Paired-answer evaluation: does the learner profile change the answer, and for the better?

    python -m chat.V2.personalization.evals.run_impact_eval --llm fake --limit 2 --out DIR

Every prompt is answered once with no profile and once per profile. The profile is injected through
the real seam (render_profile, then build_prompt). Judges see the answers blind and in random order.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from chat.V2.prompts.prompt_fragments import build_prompt

from ..llm import ClaudeCodeLLMClient, FakeLLMClient, LLMClient
from ..parsing import parse_json_object
from ..registry import SlotRegistry
from ..render import render_profile
from ..signals import hebrew_ratio
from ..store import InMemoryProfileStore

DATA = Path(__file__).parent / "data"

PAIRWISE_SYSTEM = (
    "You compare two answers from a Jewish text library assistant to the same prompt. You are "
    "given the learner's profile. Decide which answer fits this learner better (language, depth, "
    "format, vocabulary, helpfulness for them). Do not judge factual accuracy. If the answers fit "
    'equally well, say tie. Reply with JSON only: {"better": 1, "reason": "..."} where better is '
    '1, 2 or "tie".'
)
RUBRIC_SYSTEM = (
    "You review one answer from a Jewish text library assistant that was given a learner profile. "
    "Do not judge factual accuracy. Reply with JSON only: "
    '{"uses_profile": 0|1|2, "language_matches_profile": true|false, "narrows_sources": true|false, '
    '"creepy": true|false, "mentions_profile_explicitly": true|false, '
    '"difference_explained_by_slots": true|false, "notes": "..."}. '
    "uses_profile: 0 not at all, 1 a little, 2 clearly. narrows_sources is true when the answer "
    "leaves out or filters voices, traditions or sources because of the profile. creepy is true "
    "when it states personal inferences about the learner in an intrusive way. "
    "difference_explained_by_slots is true when how this answer is shaped can be traced to the "
    "profile lines."
)


def fake_llm() -> FakeLLMClient:
    """Deterministic stand-in so the plumbing is testable without a model."""

    def respond(system: str, user: str) -> str:
        if system.startswith("You compare two answers"):
            first = user.split("Answer 1:\n", 1)[1].split("\n\nAnswer 2:", 1)[0]
            return json.dumps({"better": 1 if "[personalized]" in first else 2, "reason": "fake"})
        if system.startswith("You review one answer"):
            return json.dumps(
                {"uses_profile": 1, "language_matches_profile": True, "narrows_sources": False,
                 "creepy": False, "mentions_profile_explicitly": False,
                 "difference_explained_by_slots": True, "notes": "fake"}
            )  # fmt: skip
        tag = "[personalized]" if "<learner_profile>" in system else "[baseline]"
        return f"{tag} Answer to: {user}"

    return FakeLLMClient(respond)


def load_cases(limit: int | None = None) -> dict:
    data = json.loads((DATA / "impact_cases.json").read_text(encoding="utf-8"))
    if limit:
        data["cases"] = data["cases"][:limit]
    return data


def profile_text(profile: dict, registry: SlotRegistry) -> tuple[str, list[str]]:
    store = InMemoryProfileStore()
    now = datetime.now(UTC)
    for key, value in profile.items():
        store.set_inferred("impact", key, value, [], now)
    return render_profile(store.get_profile("impact"), registry, now)


def system_prompt(core: str, prompt: str, text: str | None) -> str:
    """Everything build_prompt returns before the user message."""
    full, _ = build_prompt(prompt, core_prompt=core, profile_text=text)
    return full[: len(full) - len(prompt)].rstrip("\n")


def _answer(llm: LLMClient, model: str, system: str, prompt: str) -> dict:
    result = llm.complete(system=system, user=prompt, model=model, max_tokens=1200)
    return {
        "text": result.text, "chars": len(result.text), "hebrew_ratio": round(hebrew_ratio(result.text), 3),
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "latency_ms": result.latency_ms,
    }  # fmt: skip


def _judge(llm: LLMClient, model: str, system: str, user: str) -> dict:
    result = llm.complete(system=system, user=user, model=model, max_tokens=400)
    return {
        "parsed": parse_json_object(result.text), "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }  # fmt: skip


def run_case(case, profiles, rendered, core, answer_llm, judge_llm, answer_model, judge_model):
    prompt = case["prompt"]
    baseline = _answer(answer_llm, answer_model, system_prompt(core, prompt, None), prompt)
    out = {"case": case, "baseline": baseline, "profiles": {}}
    for pid in profiles:
        text, slots = rendered[pid]
        answer = _answer(answer_llm, answer_model, system_prompt(core, prompt, text), prompt)
        swapped = random.Random(case["id"] + pid).random() < 0.5
        first, second = (baseline, answer) if swapped else (answer, baseline)
        pair_user = (
            f"Learner profile:\n{text}\n\nPrompt:\n{prompt}\n\nAnswer 1:\n{first['text']}\n\n"
            f"Answer 2:\n{second['text']}"
        )
        pair = _judge(judge_llm, judge_model, PAIRWISE_SYSTEM, pair_user)
        better = (pair["parsed"] or {}).get("better")
        winner = "invalid"
        if better == "tie":
            winner = "tie"
        elif better in (1, 2):
            winner = "personalized" if (better == 2) == swapped else "baseline"
        rubric_user = f"Learner profile:\n{text}\n\nPrompt:\n{prompt}\n\nAnswer:\n{answer['text']}"
        out["profiles"][pid] = {
            "slots_used": slots, "answer": answer,
            "pairwise": {**pair, "answer_1": "baseline" if swapped else "personalized", "winner": winner},
            "rubric": _judge(judge_llm, judge_model, RUBRIC_SYSTEM, rubric_user),
            "delta_input_tokens": answer["input_tokens"] - baseline["input_tokens"],
            "delta_output_tokens": answer["output_tokens"] - baseline["output_tokens"],
        }  # fmt: skip
    return out


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _preference(rows: list[dict]) -> dict:
    wins = sum(r["pairwise"]["winner"] == "personalized" for r in rows)
    ties = sum(r["pairwise"]["winner"] == "tie" for r in rows)
    losses = sum(r["pairwise"]["winner"] == "baseline" for r in rows)
    return {"n": len(rows), "personalized": wins, "tie": ties, "baseline": losses,
            "rate": wins / len(rows) if rows else None}  # fmt: skip


def summarize(cases: list[dict]) -> dict:
    rows = [
        {**p, "profile": pid, "case": c["case"], "baseline": c["baseline"]}
        for c in cases
        for pid, p in c["profiles"].items()
    ]
    rubrics = [(r, r["rubric"]["parsed"]) for r in rows if r["rubric"]["parsed"]]
    flag = lambda key: [bool(p.get(key)) for _, p in rubrics]  # noqa: E731
    return {
        "preference": {
            "overall": _preference(rows),
            "per_profile": {
                pid: _preference([r for r in rows if r["profile"] == pid])
                for pid in sorted({r["profile"] for r in rows})
            },
            "expect_change_true": _preference([r for r in rows if r["case"]["expect_change"]]),
            "expect_change_false": _preference([r for r in rows if not r["case"]["expect_change"]]),
        },  # fmt: skip
        "rubric": {
            "uses_profile_mean": _mean([p.get("uses_profile", 0) for _, p in rubrics]),
            "language_matches_profile": _rate(flag("language_matches_profile")),
            "narrows_sources": _rate(flag("narrows_sources")),
            "creepy": _rate(flag("creepy")),
            "mentions_profile_explicitly": _rate(flag("mentions_profile_explicitly")),
            "difference_explained_by_slots": _rate(flag("difference_explained_by_slots")),
            "judged": len(rubrics),
        },
        "language_match_per_profile": {
            pid: _rate(
                [bool(p.get("language_matches_profile")) for r, p in rubrics if r["profile"] == pid]
            )
            for pid in sorted({r["profile"] for r in rows})
        },
        "narrows_sources_on_multivocality": _rate(
            [bool(p.get("narrows_sources")) for r, p in rubrics if r["case"].get("multivocality")]
        ),
        "avg_chars_personalized": _mean([r["answer"]["chars"] for r in rows]),
        "avg_chars_baseline": _mean([c["baseline"]["chars"] for c in cases]),
        "avg_added_input_tokens": _mean([r["delta_input_tokens"] for r in rows]),
        "avg_added_output_tokens": _mean([r["delta_output_tokens"] for r in rows]),
    }


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def _num(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}"


def render_markdown(results: dict) -> str:
    s, meta = results["summary"], results["meta"]
    pref = s["preference"]

    def line(name, p):
        return (
            f"| {name} | {p['n']} | {p['personalized']} | {p['tie']} | {p['baseline']} "
            f"| {_pct(p['rate'])} |"
        )

    out = [
        "# Impact evaluation",
        f"Date {meta['date']}, llm `{meta['llm']}`, answers `{meta['answer_model']}`, judge "
        f"`{meta['judge_model']}`, {meta['cases']} cases x {meta['profiles']} profiles.",
        "",
        "## Preference for the personalized answer (blind pairwise)",
        "| group | n | personalized | tie | baseline | rate |",
        "|---|---|---|---|---|---|",
        line("overall", pref["overall"]),
        *[line(f"profile {k}", v) for k, v in pref["per_profile"].items()],
        line("expect_change = true", pref["expect_change_true"]),
        line("expect_change = false", pref["expect_change_false"]),
        "",
        "## Rubric (personalized answers)",
        "| metric | value |",
        "|---|---|",
        f"| uses_profile (mean, 0-2) | {_num(s['rubric']['uses_profile_mean'])} |",
        *[f"| {k} | {_pct(s['rubric'][k])} |" for k in (
            "language_matches_profile", "narrows_sources", "creepy",
            "mentions_profile_explicitly", "difference_explained_by_slots")],
        f"| narrows_sources on multivocality cases | {_pct(s['narrows_sources_on_multivocality'])} |",
        "",
        "## Language match per profile",
        *[f"- {k}: {_pct(v)}" for k, v in s["language_match_per_profile"].items()],
        "",
        "## Length and cost",
        f"- Average characters: {_num(s['avg_chars_personalized'])} personalized, {_num(s['avg_chars_baseline'])} baseline",
        f"- Average added input tokens: {_num(s['avg_added_input_tokens'])}; added output tokens: {_num(s['avg_added_output_tokens'])}",
    ]  # fmt: skip
    return "\n".join(out) + "\n"


def run_impact(llm_kind, answer_model, judge_model, workers, limit, client=None) -> dict:
    data = load_cases(limit)
    registry = SlotRegistry.load()
    rendered = {pid: profile_text(p, registry) for pid, p in data["profiles"].items()}
    llm = client if llm_kind == "claude-code" else fake_llm()
    task = lambda case: run_case(  # noqa: E731
        case,
        data["profiles"],
        rendered,
        data["stand_in_core_prompt"],
        llm,
        llm,
        answer_model,
        judge_model,
    )
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        cases = list(pool.map(task, data["cases"]))
    return {
        "meta": {
            "date": date.today().isoformat(), "llm": llm_kind, "answer_model": answer_model,
            "judge_model": judge_model, "cases": len(cases), "profiles": len(data["profiles"]),
            "harness_overhead_tokens": dict(client.overhead) if client else {},
            "rendered_profiles": {k: v[0] for k, v in rendered.items()},
        },
        "cases": cases,
        "summary": summarize(cases),
    }  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", choices=["fake", "claude-code"], default="fake")
    parser.add_argument("--answer-model", default="claude-sonnet-4-6")
    parser.add_argument("--judge-model", default="claude-sonnet-5-5")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    client = None
    if args.llm == "claude-code":
        client = ClaudeCodeLLMClient()
        for model in {args.answer_model, args.judge_model}:
            client.calibrate(model)
    results = run_impact(
        args.llm, args.answer_model, args.judge_model, args.workers, args.limit, client
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "impact_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "impact_results.md").write_text(render_markdown(results), encoding="utf-8")
    print(render_markdown(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
