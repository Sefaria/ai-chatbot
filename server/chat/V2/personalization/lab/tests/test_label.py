"""Tests for the labeling runner (L/label.py and LAB label)."""

import json
import subprocess
from pathlib import Path

import pytest

from chat.V2.personalization.lab import __main__ as cli
from chat.V2.personalization.lab import label
from chat.V2.personalization.llm import FakeLLMClient


def _unlabeled_persona() -> dict:
    return {
        "id": "p1",
        "user_id": "u:p1",
        "sessions": [
            {
                "session_id": "s1",
                "turns": [
                    {
                        "id": "p1-1",
                        "at": "2026-02-01T10:00:00Z",
                        "user": "What does Rashi say on Genesis 1:1?",
                        "assistant": "Rashi explains that the Torah begins with Creation...",
                    },
                    {
                        "id": "p1-2",
                        "at": "2026-02-01T10:05:00Z",
                        "user": "yes",
                        "assistant": "Continuing with Ramban's view...",
                    },
                ],
            }
        ],
    }


def test_label_writes_outside_repo_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))

    # 1. anon-personas
    p_dir = tmp_path / "anon-personas"
    p_dir.mkdir()
    (p_dir / "items.json").write_text(json.dumps({"items": [_unlabeled_persona()]}))
    persona_reply = json.dumps(
        {
            "golden": {
                "preferred_language": {"value": "en"},
                "register": {"value": "traditional"},
                "text_categories": {"must_include": ["Tanakh"]},
            },
            "golden_timeline": [
                {"from_turn": "p1-1", "slot": "preferred_language", "value": "en"},
                {"from_turn": "p1-1", "slot": "register", "value": "traditional"},
            ],
            "tags": {"p1-1": [], "p1-2": ["short_reply"]},
        }
    )
    llm_p = FakeLLMClient(lambda system, user: persona_reply)
    res_p = label.label_dataset("anon-personas", llm=llm_p)
    assert res_p["labeled"] == 1 and res_p["malformed"] == 0
    assert "preferred_language" in llm_p.calls[0]["system"]
    lines_p = [json.loads(x) for x in (p_dir / "labeled.jsonl").read_text().splitlines()]
    assert len(lines_p) == 1
    assert lines_p[0]["golden"]["register"] == {"value": "traditional"}
    assert lines_p[0]["golden_timeline"][0]["slot"] == "preferred_language"
    assert lines_p[0]["sessions"][0]["turns"][1]["tags"] == ["short_reply"]

    # 2. anon-vocab
    v_dir = tmp_path / "anon-vocab"
    v_dir.mkdir()
    (v_dir / "items.json").write_text(
        json.dumps([{"id": "v1", "text": "What is the sugya here?"}, {"id": "v2", "text": "Hi"}])
    )
    llm_v = FakeLLMClient(lambda system, user: json.dumps({"label": "transliterated"}))
    assert (
        cli.main(["label", "anon-vocab", "--model", "claude-sonnet-5-5", "--limit", "1"], llm=llm_v)
        == 0
    )
    lines_v = [json.loads(x) for x in (v_dir / "labeled.jsonl").read_text().splitlines()]
    assert len(lines_v) == 1 and lines_v[0]["label"] == "transliterated"

    # 3. anon-visit
    vis_dir = tmp_path / "anon-visit"
    vis_dir.mkdir()
    (vis_dir / "items.json").write_text(
        json.dumps(
            [
                {
                    "id": "f1",
                    "site": "he",
                    "browser_lang": "he-IL",
                    "country": "IL",
                    "prompt": "Hello",
                }
            ]
        )
    )
    llm_vis = FakeLLMClient(lambda system, user: json.dumps({"expected_reply_lang": "en"}))
    res_vis = label.label_dataset("anon-visit", llm=llm_vis)
    assert res_vis["labeled"] == 1
    lines_vis = [json.loads(x) for x in (vis_dir / "labeled.jsonl").read_text().splitlines()]
    assert lines_vis[0]["expected_reply_lang"] == "en"

    # 4. anon-impact
    imp_dir = tmp_path / "anon-impact"
    imp_dir.mkdir()
    (imp_dir / "items.json").write_text(json.dumps([{"id": "i1", "text": "Explain sugya"}]))
    llm_imp = FakeLLMClient(
        lambda system, user: json.dumps(
            {"kind": "relevant", "relevant_to": "terminology_comfort", "many_voices": True}
        )
    )
    res_imp = label.label_dataset("anon-impact", llm=llm_imp)
    assert res_imp["labeled"] == 1
    lines_imp = [json.loads(x) for x in (imp_dir / "labeled.jsonl").read_text().splitlines()]
    assert (
        lines_imp[0]["kind"] == "relevant"
        and lines_imp[0]["relevant_to"] == "terminology_comfort"
        and lines_imp[0]["many_voices"] is True
    )

    # Verify nothing was written inside the git repository
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            check=True,
        ).stdout.strip()
    )
    for d in (p_dir, v_dir, vis_dir, imp_dir):
        assert repo_root not in d.resolve().parents


def test_label_refuses_inside_git_repo(tmp_path, monkeypatch):
    git_dir = tmp_path / "in_repo"
    git_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=git_dir, check=True)
    ds_dir = git_dir / "anon-vocab"
    ds_dir.mkdir()
    (ds_dir / "items.json").write_text(json.dumps([{"id": "v1", "text": "Hello"}]))
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(git_dir))
    llm = FakeLLMClient(lambda s, u: '{"label": "none"}')
    with pytest.raises(label.LabelError, match="git"):
        label.label_dataset("anon-vocab", llm=llm)
    assert not (ds_dir / "labeled.jsonl").exists()
    assert cli.main(["label", "anon-vocab"], llm=llm) == 2


def test_label_malformed_reply_counted_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    v_dir = tmp_path / "anon-vocab"
    v_dir.mkdir()
    items = [
        {"id": "v1", "text": "First"},
        {"id": "v2", "text": "Second"},
        {"id": "v3", "text": "Third"},
        {"id": "v4", "text": "Fourth"},
    ]
    (v_dir / "items.json").write_text(json.dumps(items))
    replies = iter(
        [
            '{"label": "academic"}',
            "not json at all",
            '{"label": "not_a_legal_label"}',
            '{"label": "traditional"}',
        ]
    )
    llm = FakeLLMClient(lambda s, u: next(replies))
    result = label.label_dataset("anon-vocab", llm=llm)
    assert result["total"] == 4
    assert result["labeled"] == 2
    assert result["malformed"] == 2
    written = [json.loads(line) for line in (v_dir / "labeled.jsonl").read_text().splitlines()]
    assert [row["id"] for row in written] == ["v1", "v4"]
    assert [row["label"] for row in written] == ["academic", "traditional"]


def test_label_updates_sha256(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    v_dir = tmp_path / "anon-vocab"
    v_dir.mkdir()
    (v_dir / "items.json").write_text(json.dumps([{"id": "v1", "text": "First"}]))
    (v_dir / "sha256").write_text("0" * 64 + "\n")
    before = (v_dir / "sha256").read_text().strip()

    llm1 = FakeLLMClient(lambda s, u: '{"label": "academic"}')
    res1 = label.label_dataset("anon-vocab", llm=llm1)
    after1 = (v_dir / "sha256").read_text().strip()
    assert len(after1) == 64 and after1 != before and res1["sha256"] == after1

    llm2 = FakeLLMClient(lambda s, u: '{"label": "traditional"}')
    res2 = label.label_dataset("anon-vocab", llm=llm2)
    after2 = (v_dir / "sha256").read_text().strip()
    assert after2 != after1 and res2["sha256"] == after2
