"""reverify/label-agreement: Opus 5.5 re-labels the personas Sonnet (anon-personas) or the author (syn1) labelled.

`select_items` takes the fixed sample of the anonymized personas (sorted user ids,
`random.Random(seed).sample`) and wraps each persona for the `label` runner. `predict` strips every
label from the persona, runs the lab's own labeling prompt (`L/label.py`: `build_system` and
`label_item` with the `anon-personas` brief) on the headless client, and returns one letter per slot
comparing the two golden profiles (see the reverify aspect's `metrics.py`); it never returns or
stores the Opus labels in a result. A live run keeps each Opus profile under
`$LA_LAB_DATA_DIR/partial/` so that a timed-out slice resumes; nothing there is committed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
from pathlib import Path

from chat.V2.personalization.lab import datasets, label, validate_data

CASE_ID = "reverify/label-agreement"
BRIEF = "anon-personas"
CACHED = ("syn1", "anon-personas")  # the live datasets; the mock never touches the partial dir
RESULTS = Path(__file__).parent / "results"

_spec = importlib.util.spec_from_file_location(
    "reverify_label_metrics", Path(__file__).parent.parent / "metrics.py"
)
_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metrics)


def select_items(items, config, dataset):
    """Every persona, except for the sampled dataset: `sample_size` personas chosen by seed."""
    if dataset.name == config.get("sample_dataset"):
        by_id = {item["user_id"]: item for item in items}
        ids = sorted(by_id)
        size = min(int(config["sample_size"]), len(ids))
        items = [by_id[u] for u in sorted(random.Random(config["sample_seed"]).sample(ids, size))]
    return [
        {
            "id": item.get("id", ""),
            "label": "golden",
            "persona": item,
            "dataset": dataset.name if dataset.name in CACHED else "",
        }
        for item in items
    ]


def unlabeled_view(persona: dict) -> dict:
    """What the first labeller saw: the turns, with no golden, timeline, tags, trap or note."""
    keep = ("id", "at", "user", "assistant")
    return {
        "user_id": persona["user_id"],
        "sessions": [
            {
                "session_id": s.get("session_id"),
                "turns": [{k: t[k] for k in keep if k in t} for t in s.get("turns", [])],
            }
            for s in persona.get("sessions", [])
        ],
    }


# -- the live run's progress: Opus profiles of finished items, outside every repository -----------


def _run_no(dataset: str) -> int:
    pattern = "real_run*.json" if dataset == "anon-personas" else "run*.json"
    return len(list(RESULTS.glob(pattern))) + 1 if RESULTS.is_dir() else 1


def _cache_path(dataset: str) -> Path:
    return datasets.data_dir() / "partial" / CASE_ID / f"opus_{dataset}_run{_run_no(dataset)}.json"


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class _Tap:
    """Adds up the tokens of the calls one item takes."""

    def __init__(self, inner):
        self.inner, self.tokens_in, self.tokens_out = inner, 0, 0

    def complete(self, *, system, user, model, max_tokens=600):
        result = self.inner.complete(system=system, user=user, model=model, max_tokens=max_tokens)
        self.tokens_in += result.input_tokens
        self.tokens_out += result.output_tokens
        return result


def predict(item, config, llm):
    """One letter per slot comparing the frozen golden with the Opus golden, or "invalid"."""
    persona = item["persona"]
    key = hashlib.sha1(persona["user_id"].encode()).hexdigest()[:16]
    cache_file = _cache_path(item["dataset"]) if item.get("dataset") else None
    cache = _read_cache(cache_file) if cache_file else {}
    model = config["model"]
    if key in cache:
        entry = cache[key]
        if hasattr(
            llm, "rows"
        ):  # the run's cost counts every call, including those of earlier slices
            llm.rows.append(
                {"step": "assign_vocabulary", "model": model,
                 "in_tokens": entry["in_tokens"], "out_tokens": entry["out_tokens"]}
            )  # fmt: skip
        return _metrics.agreement_codes(persona["golden"], entry["golden"])
    tap = _Tap(llm)
    labeled = label.label_item(
        config.get("brief_dataset", BRIEF),
        unlabeled_view(persona),
        client=tap,
        model=model,
        slots=validate_data.load_slots(),
    )
    if labeled is None:
        return "invalid"
    if cache_file:
        cache[key] = {
            "golden": labeled["golden"],
            "in_tokens": tap.tokens_in,
            "out_tokens": tap.tokens_out,
        }
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return _metrics.agreement_codes(persona["golden"], labeled["golden"])


# -- the mock: two invented personas and a stand-in labeller that answers from `opus_golden` -------


def mock_items():
    return json.loads((Path(__file__).parent / "mock_fixture.json").read_text(encoding="utf-8"))[
        "personas"
    ]


def mock_llm():
    from chat.V2.personalization.llm import FakeLLMClient

    personas = {p["user_id"]: p for p in mock_items()}

    def respond(system: str, user: str) -> str:
        seen = json.loads(user)
        persona = personas[seen["user_id"]]
        turn_ids = [t["id"] for s in seen["sessions"] for t in s["turns"]]
        return json.dumps(
            {
                "golden": persona["opus_golden"],
                "golden_timeline": [
                    {"from_turn": turn_ids[0], "slot": "preferred_language", "value": "en"}
                ],
                "tags": {tid: [] for tid in turn_ids},
            }
        )

    return FakeLLMClient(respond)
