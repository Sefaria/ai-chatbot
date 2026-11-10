"""Metrics the `reverify` aspect adds to the core set."""

from __future__ import annotations

import json

from chat.V2.personalization.lab.datasets import DATA_DIR
from chat.V2.personalization.lab.metrics import Metric, usd_per_1000_turns


def usd_vs_iter1(data, variant=None):
    """How far a design's `usd_per_1000_turns` is from iteration 1's reported figure, as a share of it
    (the absolute difference when iteration 1 reported 0)."""
    reported = json.loads((DATA_DIR / "iter1_reported.json").read_text(encoding="utf-8"))["designs"]
    base = variant.removesuffix("_h55") if variant else variant
    if base not in reported:
        return None
    figure = usd_per_1000_turns(data, variant)
    if figure is None:
        return None
    expected = reported[base]["usd_per_1000_turns_haiku45"]
    return abs(figure - expected) / expected if expected else abs(figure)


METRICS = {
    "usd_vs_iter1": Metric(
        "usd_vs_iter1",
        "fraction",
        "absolute difference between the design's `usd_per_1000_turns` and iteration 1's reported figure "
        "(`iter1_reported.json`) ÷ iteration 1's figure (the absolute difference when iteration 1 reported 0)",
        usd_vs_iter1,
    )
}
