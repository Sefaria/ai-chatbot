import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_ab.py"
REPO = Path(__file__).resolve().parents[3]


def dry(data_dir):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--users",
            "1",
            "--seed",
            "1",
            "--data-dir",
            str(data_dir),
            "--dry-run",
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def test_dry_run_refuses_a_dir_inside_a_git_repository():
    proc = dry(REPO)
    assert proc.returncode == 2
    assert "inside a git repository" in proc.stderr + proc.stdout


def test_dry_run_accepts_a_dir_outside_git():
    with tempfile.TemporaryDirectory() as d:
        proc = dry(d)
    assert proc.returncode == 0, proc.stderr


def test_report_logs_a_row_per_prompt_and_one_per_user():
    from personalization_ab import judge, report
    from personalization_ab.tests.test_judge import PACKET, fake_call

    class Exp:
        def __init__(self):
            self.rows = []

        def log(self, **kw):
            self.rows.append(kw)

        def summarize(self):
            return type("S", (), {"experiment_url": "https://bt.example/exp"})()

    exp = Exp()
    client = type("C", (), {"init": staticmethod(lambda **kw: exp)})
    js = judge.run_judges(PACKET, models=("opus",), seed=1, call=fake_call)
    url = report.log_experiment([PACKET], js, "bundled", client=client, seed=1)
    assert url == "https://bt.example/exp"
    assert len(exp.rows) == 3  # two prompts + one profile row
    assert exp.rows[0]["metadata"]["seed"] == 1
    assert set(exp.rows[0]["metadata"]["prompt_versions"]) == set(report.CHAPTERS)


SLUGS = (
    "PERSONALIZATION_PROMPT_SLUG_ANSWERING",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_USED",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EXTRACTION",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EDIT",
)


def test_prompts_bundled_clears_the_six_slug_settings(settings):
    from chat.V2.prompts.chapters import chapter_source

    from personalization_ab.run_ab import apply_prompt_source

    for n in SLUGS:
        setattr(settings, n, "some-slug")
    apply_prompt_source("bundled")
    assert all(getattr(settings, n) == "" for n in SLUGS)
    assert chapter_source("profile_writer").startswith("bundled@")


def test_prompts_braintrust_needs_all_six_and_names_the_missing_one(settings):
    import pytest

    from personalization_ab.run_ab import apply_prompt_source

    env = {n: f"slug-{i}" for i, n in enumerate(SLUGS)}
    del env["PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM"]
    with pytest.raises(RuntimeError, match="PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM"):
        apply_prompt_source("braintrust", env)
    env["PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM"] = "slug-dream"
    apply_prompt_source("braintrust", env)
    assert settings.PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM == "slug-dream"
    assert all(getattr(settings, n) for n in SLUGS)


def test_report_prompt_versions_has_all_six_chapters_from_chapter_source(settings):
    from personalization_ab import report

    for n in SLUGS:
        setattr(settings, n, "")
    versions = report.prompt_versions()
    assert set(versions) == set(report.CHAPTERS) and len(versions) == 6
    assert all(v.startswith("bundled@") for v in versions.values())


def test_run_env_turns_engine_traces_and_cli_debug_off():
    from personalization_ab.run_ab import configure_run_env

    env = {"BRAINTRUST_LOGGING_ENABLED": "true"}
    configure_run_env(env)
    assert env["BRAINTRUST_LOGGING_ENABLED"] == "false"
    assert env["CLAUDE_SDK_DEBUG"] == ""


def _run_real(data_dir, *extra, env_drop=("SEMANTIC_SEARCH_API_TOKEN",)):
    import os

    env = {k: v for k, v in os.environ.items() if k not in env_drop}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--users", "1", "--seed", "1",
         "--data-dir", str(data_dir), *extra],
        capture_output=True, check=False, text=True, env=env,
    )  # fmt: skip


def test_real_run_without_semantic_search_token_refuses():
    with tempfile.TemporaryDirectory() as d:
        proc = _run_real(d)
    assert proc.returncode == 2
    assert "SEMANTIC_SEARCH_API_TOKEN" in proc.stderr


def test_write_private_is_mode_600():
    import stat

    from personalization_ab.run_ab import write_private

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.json"
        write_private(p, {"a": 1})
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_judge_only_reuses_saved_packets_and_survives_bad_judge_replies(monkeypatch):
    import json
    import os
    import stat
    from argparse import Namespace

    from personalization_ab import run_ab
    from personalization_ab.tests.test_judge import PACKET, fake_call

    class Exp:
        def log(self, **kw):
            pass

        def summarize(self):
            return type("S", (), {"experiment_url": "https://bt.example/exp"})()

    client = type("C", (), {"init": staticmethod(lambda **kw: Exp())})
    monkeypatch.delenv("SEMANTIC_SEARCH_API_TOKEN", raising=False)
    with tempfile.TemporaryDirectory() as d:
        pilot = Path(d) / "pilot"
        pilot.mkdir()
        packet = {**PACKET, "seed": 1, "mode": "pilot"}
        (pilot / "user-u-1.json").write_text(json.dumps(packet))

        def half_bad(model, system, user):
            return "not json" if model == "haiku" else fake_call(model, system, user)

        args = Namespace(
            users=1,
            seed=1,
            data_dir=d,
            prompts="bundled",
            dry_run=False,
            judge_only=True,
        )
        rc = run_ab.run(args, judge_call=half_bad, client=client)
        saved = json.loads((pilot / "user-u-1.json").read_text())
        mode = stat.S_IMODE(os.stat(pilot / "user-u-1.json").st_mode)
    assert rc == 0
    assert mode == 0o600
    assert len(saved["judgments"]) == 9
    assert sum("unusable" in j["data"] for j in saved["judgments"]) == 3
