import json

import pytest

from chat.V2.personalization.lab import results
from chat.V2.personalization.lab.results import RealDataLeak


def real_result(**extra):
    base = {
        "schema": 1, "id": "a/x", "kind": "live", "run": 1, "model": "claude-haiku-4-5",
        "dataset": {"name": "real1", "kind": "real", "sha256": "abc", "n": 200},
        "case_sha": "c", "git_sha": "g", "started_at": "2026-11-10T10:00:00+00:00",
        "metrics": {"fill_rate_at_k@2": 0.4, "x": None}, "ratios": {"fill_rate_at_k@2": [8, 20]},
        "invalid": 0, "cost": {"calls": 3, "in_tokens": 10, "out_tokens": 4, "typed_requests": 0, "usd": 0.1},
        "steps": [{"name": "extract_turn", "n_in": 5, "n_out": 4}],
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip
    return {**base, **extra}


def test_a_clean_real_result_is_written(tmp_path):
    path = results.write(tmp_path, real_result())
    assert path.name == "real_run1.json" and json.loads(path.read_text())["metrics"]["x"] is None


def test_real_result_with_per_item_raises(tmp_path):
    with pytest.raises(RealDataLeak, match="per_item"):
        results.write(tmp_path, real_result(per_item=[{"persona": "p"}]))
    assert not (tmp_path / "results").exists()


def test_real_result_with_free_string_raises(tmp_path):
    for extra in (
        {"note": "what the learner typed"},
        {"metrics": {"a": "text"}},
        {"cost": {"calls": "x"}},
    ):
        with pytest.raises(RealDataLeak, match="string"):
            results.write(tmp_path, real_result(**extra))


def test_real_result_step_name_must_be_in_catalog(tmp_path):
    with pytest.raises(RealDataLeak, match="catalog"):
        results.write(tmp_path, real_result(steps=[{"name": "free text", "n_in": 1, "n_out": 1}]))


def test_real_result_with_a_long_id_raises(tmp_path):
    with pytest.raises(RealDataLeak):
        results.write(tmp_path, real_result(id="x" * 300))


def test_a_real_part_inside_a_synthetic_result_is_checked_too(tmp_path):
    synthetic = real_result(
        dataset={"name": "syn1", "kind": "synthetic", "sha256": "s", "n": 9}, per_item=[{"p": "x"}]
    )
    synthetic["also"] = {
        "real1": {
            "dataset": {"name": "real1", "kind": "real", "sha256": "a", "n": 2},
            "metrics": {},
            "ratios": {},
            "invalid": 0,
        }
    }
    with pytest.raises(RealDataLeak):
        results.write(tmp_path, synthetic)


def test_synthetic_results_may_carry_per_item(tmp_path):
    synthetic = real_result(
        dataset={"name": "syn1", "kind": "synthetic", "sha256": "s", "n": 9},
        per_item=[{"persona": "rachel", "final_profile": {"a": "en"}}],
    )
    assert results.write(tmp_path, synthetic).name == "run1.json"


def test_real_partial_dir_is_outside_repo(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    data = tmp_path / "la-lab-data"
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(data))
    part = results.partial_dir("a/x", tmp_path / "case", real=True)
    assert part == data / "partial" / "a" / "x"
    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        ).stdout.strip()
    )
    assert repo not in part.parents
    assert (
        results.partial_dir("a/x", tmp_path / "case", real=False)
        == tmp_path / "case" / "results" / ".partial"
    )
