import json

import pytest

from chat.V2.personalization.lab.manifest import ManifestError, case_dirs, load_case

from .conftest import write_case


def test_valid_case_loads(tmp_path):
    case = load_case(write_case(tmp_path))
    assert case.id == "a/x" and case.keep_if[0].dataset == "syn1" and case.status == "planned"


def test_manifest_rejects_unknown_metric(tmp_path):
    with pytest.raises(ManifestError, match="unknown metric"):
        load_case(write_case(tmp_path, metrics=["slot_accuracy", "made_up_metric"]))


def test_manifest_rejects_unknown_metric_in_a_bar(tmp_path):
    bad = [{"metric": "nope@x", "value": 1}]
    with pytest.raises(ManifestError, match="unknown metric"):
        load_case(write_case(tmp_path, keep_if=bad))


def test_manifest_rejects_unknown_dataset(tmp_path):
    with pytest.raises(ManifestError, match="unknown dataset"):
        load_case(write_case(tmp_path, datasets=["nope"]))


def test_manifest_rejects_empty_hypothesis(tmp_path):
    with pytest.raises(ManifestError, match="hypothesis"):
        load_case(write_case(tmp_path, hypothesis="  "))


def test_manifest_rejects_planned_case_without_bars(tmp_path):
    with pytest.raises(ManifestError, match="keep_if"):
        load_case(write_case(tmp_path, keep_if=[]))


def test_manifest_rejects_draft(tmp_path):
    with pytest.raises(ManifestError, match="status"):
        load_case(write_case(tmp_path, status="draft"))


def test_blocked_case_needs_reason(tmp_path):
    with pytest.raises(ManifestError, match="blocked_reason"):
        load_case(write_case(tmp_path, status="blocked", keep_if=[]))
    case = load_case(
        write_case(tmp_path, status="blocked", keep_if=[], blocked_reason="needs the lab app")
    )
    assert case.blocked_reason == "needs the lab app"


def test_a_bar_needs_a_value_or_a_ref(tmp_path):
    with pytest.raises(ManifestError, match="neither"):
        load_case(write_case(tmp_path, keep_if=[{"metric": "slot_accuracy"}]))


def test_a_bar_with_ref_loads(tmp_path):
    ref = {"case": "a/y", "metric": "slot_accuracy", "factor": 0.5}
    case = load_case(
        write_case(tmp_path, keep_if=[{"metric": "slot_accuracy", "op": "<=", "ref": ref}])
    )
    assert case.keep_if[0].ref["factor"] == 0.5


def test_an_aspect_may_add_a_metric_with_a_formula(tmp_path):
    aspect = tmp_path / "a"
    aspect.mkdir()
    (aspect / "metrics.py").write_text(
        "from chat.V2.personalization.lab.metrics import Metric\n"
        "METRICS = {'my_metric': Metric('my_metric', 'count', 'things counted', lambda d, v: 1)}\n"
    )
    case = load_case(
        write_case(tmp_path, metrics=["my_metric"], keep_if=[{"metric": "my_metric", "value": 1}])
    )
    assert case.metrics == ["my_metric"]


def test_case_dirs_finds_every_case(tmp_path):
    write_case(tmp_path, "a", "x")
    write_case(tmp_path, "b", "y")
    assert [p.name for p in case_dirs(tmp_path)] == ["x", "y"]
    assert json.loads((tmp_path / "a" / "x" / "case.json").read_text())["hypothesis"] == "h"
