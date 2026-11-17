import copy

import pytest

from chat.V2.personalization.lab import render
from chat.V2.personalization.lab.__main__ import main
from chat.V2.personalization.lab.metric_formulas import FORMULAS
from chat.V2.personalization.lab.results import RealDataLeak
from chat.V2.personalization.lab.steps import STEPS


def _run(kind="live", run=1, real=False):
    return {
        "id": "a/x", "kind": kind, "run": run,
        "dataset": {"name": "real1" if real else "syn1", "kind": "real" if real else "synthetic", "n": 4},
        "metrics": {"slot_accuracy": 0.5, "over_inferred": 0.1},
        "ratios": {"slot_accuracy": [2, 4]},
        "steps": [{"name": "extract_turn", "n_in": 4, "n_out": 3}, {"name": "score", "n_in": 3, "n_out": 2}],
        "cost": {"calls": 3, "in_tokens": 10, "out_tokens": 5, "typed_requests": 0, "usd": 0.01},
    }  # fmt: skip


def _tree(question="q?"):
    case = {
        "id": "a/x", "branch": "lab/a/x", "pr_url": "https://github.com/o/r/pull/1",
        "manifest": {
            "question": question, "why": "because", "source": "src", "hypothesis": "it works",
            "keep_if": [{"metric": "slot_accuracy", "agg": "mean", "op": ">=", "value": 0.4}],
            "datasets": ["syn1"], "metrics": ["slot_accuracy", "over_inferred"], "status": "planned",
        },
        "mock": _run("mock", 0), "runs": [_run(run=1), _run(run=2), _run(run=3)], "real_runs": [],
        "verdict": {
            "verdict": "keep",
            "bars": [{"metric": "slot_accuracy", "agg": "mean", "op": ">=", "target": 0.4, "observed": 0.5, "state": "holds"}],
        },
        "status": "done", "case_sha": "abc123",
    }  # fmt: skip
    blocked = {**copy.deepcopy(case), "id": "a/y", "branch": "lab/a/y", "mock": None, "runs": [],
               "verdict": None, "status": "blocked", "case_sha": "def456", "pr_url": None}  # fmt: skip
    return {
        "generated_at": "2026-11-10T00:00:00Z", "core_sha": "c0de", "repo_url": "https://github.com/o/r",
        "aspects": [{"id": "a", "question": "aq", "why": "aw", "source": "as", "branch": "lab/a/aspect",
                     "cases": [case, blocked]}],
    }  # fmt: skip


@pytest.fixture
def out(tmp_path):
    html = tmp_path / "tree.html"
    wiki = tmp_path / "wiki"
    render.render(_tree(), html, wiki)
    return html.read_text(encoding="utf-8"), wiki


def test_render_html_is_self_contained(out):
    html, _ = out
    assert "<title>LA Lab Tree</title>" in html
    assert "prefers-color-scheme: dark" in html
    assert ":root" in html and "viewport" in html
    for forbidden in ("src=", "<link", "@import", "url(http", 'href="http'):
        assert forbidden not in html


def test_render_escapes_text(tmp_path):
    html_path = tmp_path / "t.html"
    render.render(_tree('<script>alert(1)</script> & "q"'), html_path, tmp_path / "w")
    html = html_path.read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html
    assert html.count("<script") == 2  # the data block and the viewer, nothing from the text
    md = (tmp_path / "w" / "a.md").read_text(encoding="utf-8")
    assert "alert(1)" in md  # markdown keeps the words


def test_render_wiki_has_one_section_per_case_with_case_sha(out):
    _, wiki = out
    page = (wiki / "a.md").read_text(encoding="utf-8")
    assert page.count("\n## ") == 2
    assert "\ncase_sha: abc123\n" in page and "\ncase_sha: def456\n" in page
    index = (wiki / "tree.md").read_text(encoding="utf-8")
    assert index.startswith("---") and "type: project" in index
    assert "generated, do not edit" in index.lower()
    assert "[!warning]" in index
    assert "a/x" in index and "keep" in index


def test_render_refuses_real_per_item(tmp_path):
    data = _tree()
    real = _run("live", 1, real=True)
    real["per_item"] = [{"text": "x"}]
    data["aspects"][0]["cases"][0]["real_runs"] = [real]
    with pytest.raises(RealDataLeak):
        render.render(data, tmp_path / "t.html", tmp_path / "w")
    assert not (tmp_path / "t.html").exists()


def test_render_cli_exits_2_on_real_per_item(tmp_path):
    import json

    data = _tree()
    real = _run("live", 1, real=True)
    real["per_item"] = []
    data["aspects"][0]["cases"][0]["real_runs"] = [real]
    path = tmp_path / "tree.json"
    path.write_text(json.dumps(data))
    args = [
        "render",
        "--tree",
        str(path),
        "--html",
        str(tmp_path / "t.html"),
        "--wiki-dir",
        str(tmp_path / "w"),
    ]
    assert main(args) == 2


def test_render_shows_formula_for_every_metric(out):
    html, wiki = out
    page = (wiki / "a.md").read_text(encoding="utf-8")
    for name in ("slot_accuracy", "over_inferred"):
        assert FORMULAS[name] in html
        assert FORMULAS[name] in page


def test_render_shows_step_descriptions(out):
    html, wiki = out
    page = (wiki / "a.md").read_text(encoding="utf-8")
    for name in ("extract_turn", "score"):
        assert STEPS[name] in html
        assert STEPS[name] in page


def test_render_refuses_real_per_item_in_mock_and_also(tmp_path):
    for place in ("mock", "also"):
        data = _tree()
        case = data["aspects"][0]["cases"][0]
        if place == "mock":
            case["mock"] = {**_run("mock", 0, real=True), "per_item": [{"text": "x"}]}
        else:
            run = case["runs"][0]
            run["also"] = {"real1": {"dataset": {"name": "real1", "kind": "real"}, "per_item": [1]}}
        with pytest.raises(RealDataLeak):
            render.view_model(data)
        with pytest.raises(RealDataLeak):
            render.render(data, tmp_path / "t.html", tmp_path / "w")
