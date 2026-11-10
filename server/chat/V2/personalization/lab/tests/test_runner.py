import json
import types

import pytest

from chat.V2.personalization.jev import FakeJevClient
from chat.V2.personalization.lab import __main__ as cli
from chat.V2.personalization.lab import judge, results
from chat.V2.personalization.lab.datasets import Dataset, load_dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.replay import StrategyPipeline, oracle_responder, strategy_pipeline
from chat.V2.personalization.lab.runner import JevOnRealData, run_case
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.typed import JevTypedClient

from .conftest import commit_and_push, write_case

MODULE = types.SimpleNamespace(build=strategy_pipeline)


def persona():
    return next(p for p in load_dataset("syn1").items if p["id"] == "rachel-anon")


def oracle_llm():
    return FakeLLMClient(oracle_responder(persona()))


def mock_case(tmp_path, **overrides):
    return load_case(write_case(tmp_path, **overrides))


def run(case, dataset=None, kind="mock", run_no=0, llm=None, typed=None, **kw):
    dataset = dataset or Dataset("fixture", "synthetic", "sha", [persona()])
    return run_case(
        case, kind=kind, run_no=run_no, dataset=dataset, llm=llm or oracle_llm(), typed=typed, **kw
    )


def test_mock_run_of_a_replay_case_scores_the_oracle_and_records_the_shape(tmp_path):
    case = mock_case(tmp_path, metrics=["slot_accuracy", "checks_passed", "calls_per_turn"])
    result = run(case)
    assert result["schema"] == 1 and result["id"] == "a/x" and result["kind"] == "mock"
    assert result["dataset"] == {"name": "fixture", "kind": "synthetic", "sha256": "sha", "n": 1}
    assert result["metrics"]["slot_accuracy"] >= 0.5 and result["metrics"]["checks_passed"] == 1.0
    assert result["ratios"]["slot_accuracy"][1] > 0
    assert set(result["cost"]) == {"calls", "in_tokens", "out_tokens", "typed_requests", "usd"}
    assert [s["name"] for s in result["steps"]][0] == "load_turns" and result["steps"][-1][
        "name"
    ] == "score"
    assert result["per_item"][0]["persona"] == "rachel-anon"
    assert result["privacy"]["sensitive_reached_model"] == 0
    calls = json.loads((tmp_path / "a" / "x" / "results" / "calls_mock.json").read_text())
    assert calls and set(calls[0]) >= {"step", "model", "in_tokens", "out_tokens"}
    assert all("text" not in row and "user" not in row for row in calls)


def test_variants_give_one_metric_key_per_design(tmp_path):
    case = mock_case(
        tmp_path, config={"variants": ["realtime", "explicit"]},
        metrics=["slot_accuracy", "fill_rate_at_k"],
        keep_if=[{"metric": "slot_accuracy@realtime", "value": 0.1}],
    )  # fmt: skip
    result = run(case)
    assert {"slot_accuracy@realtime", "slot_accuracy@explicit"} <= set(result["metrics"])
    assert {"fill_rate_at_k@realtime/2", "fill_rate_at_k@explicit/8"} <= set(result["metrics"])
    assert result["metrics"]["slot_accuracy@realtime"] > result["metrics"]["slot_accuracy@explicit"]


def test_a_design_without_its_jev_client_is_recorded_invalid(tmp_path):
    case = mock_case(
        tmp_path, config={"variants": ["realtime", "hybrid_jev"]}, metrics=["slot_accuracy"]
    )
    result = run(case)
    assert result["metrics"]["slot_accuracy@hybrid_jev"] is None and result["invalid"] == 1
    assert result["metrics"]["slot_accuracy@realtime"] is not None


def test_sensitive_turns_that_reach_a_model_are_counted_from_the_dataset_tags(tmp_path):
    p = persona()
    p["sessions"][0]["turns"][0]["user"] = "My father passed away, secretword"
    p["sessions"][0]["turns"][0]["tags"] = ["sensitive"]
    case = mock_case(tmp_path, metrics=["slot_accuracy"])
    clean = run(case, Dataset("f", "synthetic", "s", [p]))
    assert clean["privacy"]["sensitive_reached_model"] == 0  # the screen dropped it

    # a screen that fails: the tag says sensitive but the text is innocuous to the keyword list
    p2 = json.loads(json.dumps(persona()))
    p2["sessions"][0]["turns"][0]["tags"] = ["sensitive"]
    leaked = run(case, Dataset("f", "synthetic", "s", [p2]))
    assert leaked["privacy"]["sensitive_reached_model"] == 1


def test_jev_on_real_dataset_refused(tmp_path):
    case = mock_case(tmp_path)
    typed = JevTypedClient(FakeJevClient(lambda state, questions: {}))
    real = Dataset("real1", "real", "sha", [persona()])
    with pytest.raises(JevOnRealData):
        run(case, real, typed=typed)


def test_a_real_run_has_numbers_only_and_progress_outside_the_repo(tmp_path, monkeypatch):
    data = tmp_path / "la-lab-data"
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(data))
    real_persona = {"user_id": "u:real", "sessions": persona()["sessions"]}
    case = load_case(
        write_case(tmp_path / "repo-cases", metrics=["calls_per_turn", "sensitive_dropped_share"])
    )
    real = Dataset("real1", "real", "sha", [real_persona])
    # the guard needs a pushed case, so run the same call through a case with no directory
    import dataclasses

    free = dataclasses.replace(case, dir="")
    result = run_case(
        free, kind="live", run_no=1, dataset=real, llm=oracle_llm(), pipeline_module=MODULE
    )
    results.check_real(result)  # raises on per_item or free text
    assert "per_item" not in result and result["privacy"]["sensitive_reached_model"] is None
    assert (data / "partial" / "a" / "x" / "profiles_real1_run1.json").is_file()
    assert not (tmp_path / "repo-cases" / "a" / "x" / "results").exists()


def test_label_task_scores_predictions(tmp_path):
    case_dir = write_case(tmp_path, task="label", datasets=["syn1"], metrics=["label_accuracy", "label_wrong", "coverage"],
                          config={}, keep_if=[{"metric": "label_accuracy", "value": 0.5}])  # fmt: skip
    (case_dir / "pipeline.py").write_text(
        "def predict(item, config, llm):\n    return 'unknown' if item['label'] == 'none' else item['label']\n"
    )
    items = [
        {"id": "1", "text": "t", "label": "academic"},
        {"id": "2", "text": "t", "label": "none"},
    ]
    result = run(load_case(case_dir), Dataset("vocab-fixture", "synthetic", "s", items))
    assert result["metrics"] == {"label_accuracy": 0.5, "label_wrong": 0, "coverage": 0.5}


def test_idempotent_metric_triggers_the_second_job_check(tmp_path):
    case = mock_case(tmp_path, metrics=["idempotent"])
    assert run(case)["metrics"]["idempotent"] in (0.0, 1.0)


def test_unknown_task_pairs_is_left_to_the_lab_app(tmp_path):
    from chat.V2.personalization.lab.runner import RunnerError

    case = mock_case(tmp_path, task="pairs", engine="app")
    with pytest.raises(RunnerError, match="lab app"):
        run(case)


def test_live_runs_three_times_and_history_metrics_read_the_earlier_runs(repo):
    work, cases = repo
    directory = write_case(cases, metrics=["slot_accuracy", "run_agreement", "runs_identical"])
    commit_and_push(work)
    case = load_case(directory)
    dataset = load_dataset("syn1")
    deps = {"llm": oracle_llm()}
    seen = []
    for n in (1, 2, 3):
        llm = FakeLLMClient(lambda system, user: '{"updates": []}')
        result = run_case(
            case,
            kind="live",
            run_no=n,
            dataset=Dataset("syn1", "synthetic", dataset.sha256, [persona()]),
            llm=llm,
        )
        results.write(directory, result)
        seen.append(result["metrics"])
    assert seen[0]["run_agreement"] is None and seen[1]["run_agreement"] is not None
    assert seen[2]["runs_identical"] == 1.0
    assert deps


def test_codex_judge_retries_once_then_scores_invalid():
    schema = {
        "type": "object",
        "required": ["better"],
        "properties": {"better": {"type": "integer", "enum": [1, 2]}},
    }
    j = judge.CodexJudge(schema)
    replies = iter([{"better": 7}, {"better": 2}])
    j._run = lambda prompt: next(replies)
    assert j.ask("p") == {"better": 2}  # one retry, then valid
    bad = iter([{"better": 7}, "not json", {"better": 1}])
    j._run = lambda prompt: next(bad)
    assert j.ask("p") == {"invalid": True}  # two bad replies in a row


def test_invalid_judge_reply_scores_invalid():
    schema = {
        "type": "object",
        "required": ["better"],
        "properties": {"better": {"type": "integer"}},
    }
    fake = judge.FakeJudge([{"nope": 1}, {"better": 1}], schema)
    assert fake.ask("a") == {"invalid": True} and fake.ask("b") == {"better": 1}


# -- the command line -------------------------------------------------------------------------


def test_validate_all_fails_below_min_cases(tmp_path, capsys):
    write_case(tmp_path, "a", "x")
    assert cli.main(["--cases-root", str(tmp_path), "validate", "--all", "--min-cases", "1"]) == 0
    assert cli.main(["--cases-root", str(tmp_path), "validate", "--all", "--min-cases", "2"]) == 1
    assert "fewer than" in capsys.readouterr().out


def test_validate_reports_an_invalid_case(tmp_path, capsys):
    write_case(tmp_path, "a", "x", status="draft")
    assert cli.main(["--cases-root", str(tmp_path), "validate", "a/x"]) == 1
    assert cli.main(["--cases-root", str(tmp_path), "validate", "--all"]) == 1


def test_explain_shows_the_formulas_and_bars(tmp_path, capsys):
    write_case(tmp_path)
    assert cli.main(["--cases-root", str(tmp_path), "explain", "a/x"]) == 0
    out = capsys.readouterr().out
    assert (
        "slots whose final value equals the golden value" in out
        and "slot_accuracy [mean] >= 0.5" in out
    )


def test_run_mock_writes_mock_json_and_blocked_case_exits_3(tmp_path):
    write_case(tmp_path, "a", "x", datasets=["syn1"])
    write_case(tmp_path, "a", "b", status="blocked", keep_if=[], blocked_reason="needs the lab")
    assert cli.main(["--cases-root", str(tmp_path), "run", "a/x", "--mock"]) == 0
    assert (tmp_path / "a" / "x" / "results" / "mock.json").is_file()
    assert cli.main(["--cases-root", str(tmp_path), "run", "a/b", "--mock"]) == 3
    assert cli.main(["--cases-root", str(tmp_path), "check", "a/b"]) == 3


def test_live_refused_before_the_case_is_pushed(repo, capsys):
    work, cases = repo
    write_case(cases)
    commit_and_push(work, push=False)
    code = cli.main(
        ["--cases-root", str(cases), "run", "a/x", "--live", "--runs", "1"], llm=oracle_llm()
    )
    assert code == 2 and "NotPreregistered" in capsys.readouterr().out


def test_check_fails_when_pipeline_changed_after_run(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work)
    from chat.V2.personalization.lab import guard

    sha = guard.case_sha(directory)
    sha256 = load_dataset("syn1").sha256
    stub = {"case_sha": sha, "dataset": {"name": "syn1", "sha256": sha256}}
    for name in ("mock.json", "run1.json", "run2.json", "run3.json", "verdict.json"):
        results.write(directory, {**stub, "kind": "mock", "run": 0}, name)
    args = ["--cases-root", str(cases), "check", "a/x"]
    assert cli.main(args) == 0
    (directory / "pipeline.py").write_text("# changed after the runs\n")
    assert cli.main(args) == 1
    (directory / "pipeline.py").write_text(open(directory / "pipeline.py").read())  # still changed
    assert cli.main(args) == 1


def test_check_fails_when_a_dataset_changed(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work)
    from chat.V2.personalization.lab import guard

    stub = {"case_sha": guard.case_sha(directory), "dataset": {"name": "syn1", "sha256": "0" * 64}}
    for name in ("mock.json", "run1.json", "run2.json", "run3.json", "verdict.json"):
        results.write(directory, {**stub, "kind": "mock", "run": 0}, name)
    assert cli.main(["--cases-root", str(cases), "check", "a/x"]) == 1


def test_strategy_pipeline_is_a_pipeline():
    pipeline = strategy_pipeline({"strategy": "realtime"}, FakeLLMClient(lambda s, u: "{}"))
    assert isinstance(pipeline, StrategyPipeline) and "screen_sensitive" in pipeline.steps()


def test_verdict_command_writes_verdict_json_and_keeps_the_note(tmp_path, capsys):
    directory = write_case(tmp_path)
    for n in (1, 2, 3):
        row = {
            "kind": "live", "run": n, "dataset": {"name": "syn1", "kind": "synthetic", "sha256": "s", "n": 9},
            "metrics": {"slot_accuracy": 0.8}, "ratios": {}, "invalid": 0,
            "privacy": {"sensitive_reached_model": 0, "stated_overwritten": 0, "exclusion_stored": 0},
        }  # fmt: skip
        results.write(directory, row)
    args = ["--cases-root", str(tmp_path), "verdict", "a/x"]
    assert cli.main(args) == 0
    path = directory / "results" / "verdict.json"
    data = json.loads(path.read_text())
    assert data["verdict"] == "keep" and data["runs"] == 3 and data["id"] == "a/x"
    data["note"] = "read by Yotam"
    data["verdict"] = "drop"  # a person cannot change the computed verdict
    path.write_text(json.dumps(data))
    assert cli.main(args) == 0
    again = json.loads(path.read_text())
    assert again["verdict"] == "keep" and again["note"] == "read by Yotam"
    assert cli.main(["--cases-root", str(tmp_path), "explain", "a/x"]) == 0
    assert "Verdict: keep" in capsys.readouterr().out
