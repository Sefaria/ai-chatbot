"""Log the A/B run to a Braintrust experiment in the "On Site Agent" project."""

from __future__ import annotations

import time
from collections import defaultdict

PROJECT = "On Site Agent"


CHAPTERS = (
    "personalization_answering",
    "personalization_profile_used",
    "profile_writer",
    "profile_extraction",
    "profile_dream",
    "profile_edit",
)


def prompt_versions() -> dict:
    """Where each of the six chapters resolves from right now (`braintrust:<slug>@<v>` or `bundled@<sha>`)."""
    from chat.V2.prompts.chapters import chapter_source

    return {name: chapter_source(name) for name in CHAPTERS}


def _rank_of(judgment, key) -> int:
    return judgment.data["ranking"].index(key) + 1


def log_experiment(
    packets, judgments, prompts_mode, *, client=None, seed: int = 0, name=None
) -> str:
    if client is None:
        import braintrust as client
    exp = client.init(
        project=PROJECT, experiment=name or f"personalization-ab-{int(time.time())}"
    )
    meta = {
        "prompt_versions": prompt_versions(),
        "prompts_mode": prompts_mode,
        "seed": seed,
    }
    by_user = defaultdict(list)
    for j in judgments:
        by_user[j.user].append(j)
    for packet in packets:
        mine = by_user[packet["user"]]
        for item in packet["prompts"]:
            ranks = {
                j.model: {k: _rank_of(j, k) for k in ("original", "A", "B")}
                for j in mine
                if j.kind == "ranking" and j.prompt_index == item["index"]
            }
            n = len(ranks) or 1
            exp.log(
                input={
                    "user": packet["user"],
                    "prompt": item["prompt"],
                    "index": item["index"],
                },
                output={"original": item["original"], "A": item["A"], "B": item["B"]},
                scores={
                    "a_beats_b": sum(r["A"] < r["B"] for r in ranks.values()) / n,
                    "a_beats_original": sum(
                        r["A"] < r["original"] for r in ranks.values()
                    )
                    / n,
                    "b_beats_original": sum(
                        r["B"] < r["original"] for r in ranks.values()
                    )
                    / n,
                },
                metadata={
                    **meta,
                    "ranks": ranks,
                    "trace_ids": item.get("trace_ids", {}),
                },
            )
        profile_js = [j for j in mine if j.kind == "profile"]
        n = len(profile_js) or 1
        exp.log(
            input={"user": packet["user"], "kind": "profiles"},
            output={"A": packet["profile_a"], "B": packet["profile_b"],
                    "judgments": {j.model: j.data for j in profile_js}},
            scores={
                "profile_a_accuracy": sum(j.data["scores"]["A"] for j in profile_js) / n / 5,
                "profile_b_accuracy": sum(j.data["scores"]["B"] for j in profile_js) / n / 5,
                "a_wins_profile": sum(j.data["winner"] == "A" for j in profile_js) / n,
            },
            metadata=meta,
        )  # fmt: skip
    summary = exp.summarize()
    return summary.experiment_url
