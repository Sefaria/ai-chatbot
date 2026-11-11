import json
import shutil

import pytest

from chat.V2.personalization.lab import datasets
from chat.V2.personalization.lab.datasets import DatasetChanged, DatasetError, load_dataset


def test_syn1_loads_with_its_recorded_checksum():
    dataset = load_dataset("syn1")
    assert dataset.kind == "synthetic" and len(dataset.items) == 9
    assert dataset.sha256 == datasets.registry()["syn1"]["sha256"]
    assert "impact_cases" in dataset.files


def test_dataset_checksum_mismatch_raises(tmp_path):
    copy = tmp_path / "data"
    shutil.copytree(datasets.DATA_DIR, copy)
    persona_file = copy / "syn1" / "personas.json"
    persona_file.write_text(persona_file.read_text().replace("rachel-anon", "rachel-edited", 1))
    with pytest.raises(DatasetChanged):
        load_dataset("syn1", registry_path=copy / "datasets.json")


def test_unfrozen_synthetic_dataset_is_refused(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "items.json").write_text("[]")
    entry = {"kind": "synthetic", "dir": "x", "files": ["items.json"], "sha256": None}
    (tmp_path / "datasets.json").write_text(json.dumps({"x": entry}))
    with pytest.raises(DatasetError, match="not frozen"):
        load_dataset("x", registry_path=tmp_path / "datasets.json")


@pytest.mark.parametrize("name", ["anon-personas", "anon-vocab", "anon-visit", "anon-impact"])
def test_anon_datasets_registered_as_real_and_read_anonymized_flag(name, tmp_path, monkeypatch):
    reg = datasets.registry()
    assert reg[name] == {"kind": "real"}
    for old in ("syn2", "vocab1", "visit1", "impact2"):
        assert old not in reg
    directory = tmp_path / name
    directory.mkdir()
    (directory / "items.json").write_text(json.dumps({"items": [{"id": "1"}]}))
    (directory / "sha256").write_text("deadbeef\n")
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    ds_default = load_dataset(name)
    assert (ds_default.kind, ds_default.sha256, ds_default.anonymized) == (
        "real",
        "deadbeef",
        False,
    )
    (directory / "meta.json").write_text(json.dumps({"anonymized": True}))
    ds_anon = load_dataset(name)
    assert ds_anon.anonymized is True
    (directory / "meta.json").write_text(json.dumps({"anonymized": False}))
    assert load_dataset(name).anonymized is False


def test_unknown_dataset_is_refused():
    with pytest.raises(DatasetError, match="unknown"):
        load_dataset("nope")


def test_real_dataset_read_from_env_dir_only(tmp_path, monkeypatch):
    monkeypatch.delenv("LA_LAB_DATA_DIR", raising=False)
    with pytest.raises(DatasetError, match="LA_LAB_DATA_DIR"):
        load_dataset("real1")
    directory = tmp_path / "real1"
    directory.mkdir()
    (directory / "items.json").write_text(json.dumps({"items": [{"user_id": "u1"}]}))
    (directory / "sha256").write_text("abc123\n")
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    dataset = load_dataset("real1")
    assert (dataset.kind, dataset.sha256, dataset.items) == ("real", "abc123", [{"user_id": "u1"}])
    # nothing real is in the repository: the registry holds the name and kind only
    assert datasets.registry()["real1"] == {"kind": "real"}
    assert not (datasets.DATA_DIR / "real1").exists()
