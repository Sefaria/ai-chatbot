"""reverify/heldout-personas: the five iteration-1 designs on syn1 and on a seeded sample of anon-personas.

`build` is the ready replay pipeline for a design named by the variant (the runner fills
`config.strategy` from `config.variants`). `select_items` takes the fixed sample of the
anonymized personas (sorted user ids, `random.Random(seed).sample`), so the sample is the same
on every run and is fixed by `case.json` before any live run. `explain_lines` puts, for each
design, iteration 1's reported `slot_accuracy`, the `syn1` figure and the `anon-personas` figure
on one line (the `syn2` column of the test tree).
"""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

from chat.V2.personalization.jev import FakeJevClient
from chat.V2.personalization.lab.datasets import DATA_DIR
from chat.V2.personalization.lab.replay import (
    EMPTY_REPLY,
    fake_jev_responder,
    oracle_jev_responder,
    oracle_responder,
)
from chat.V2.personalization.lab.replay import strategy_pipeline as build  # noqa: F401
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.typed import JevTypedClient

LIVE_RUNS = 3


def select_items(items, config, dataset):
    """All items, except for the sampled dataset: `sample_size` personas chosen by seed, in user id order."""
    if dataset.name != config.get("sample_dataset"):
        return list(items)
    by_id = {item["user_id"]: item for item in items}
    ids = sorted(by_id)
    size = min(int(config["sample_size"]), len(ids))
    chosen = sorted(random.Random(config["sample_seed"]).sample(ids, size))
    return [by_id[user_id] for user_id in chosen]


def _mean(case, read, template: str, design: str):
    values = []
    for run_no in range(1, LIVE_RUNS + 1):
        result = read(template.format(run_no))
        if result is None:
            continue
        values.append(result["metrics"].get(f"slot_accuracy@{design}"))
    if not values or any(v is None for v in values):
        return None
    return statistics.mean(values)


def explain_lines(case, read):
    iter1 = json.loads((DATA_DIR / "iter1_reported.json").read_text(encoding="utf-8"))["designs"]
    show = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    lines = ["slot_accuracy per design (iteration 1 as reported | syn1 | syn2 = anon-personas):"]
    for design in case.config["variants"]:
        lines.append(
            f"  {design}: iter1 {show(iter1[design]['slot_accuracy'])}"
            f" | syn1 {show(_mean(case, read, 'run{}.json', design))}"
            f" | syn2 {show(_mean(case, read, 'real_run{}.json', design))}"
        )
    return lines


# -- the mock: two invented personas, and stand-in models that answer from their golden profiles --


def mock_items():
    return json.loads((Path(__file__).parent / "mock_fixture.json").read_text(encoding="utf-8"))[
        "personas"
    ]


def _owner(text: str):
    return next(
        (
            p
            for p in mock_items()
            if any(t["user"][:20] in text for s in p["sessions"] for t in s["turns"])
        ),
        None,
    )


def mock_llm():
    def respond(system: str, user: str) -> str:
        owner = _owner(user)
        return oracle_responder(owner)(system, user) if owner else EMPTY_REPLY

    return FakeLLMClient(respond)


def mock_typed():
    def respond(state: dict, questions: dict) -> dict:
        owner = _owner(json.dumps(state, ensure_ascii=False))
        return (
            oracle_jev_responder(owner)(state, questions)
            if owner
            else fake_jev_responder(state, questions)
        )

    return JevTypedClient(FakeJevClient(respond))
