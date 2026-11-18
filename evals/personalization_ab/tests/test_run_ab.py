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
    assert (
        exp.rows[0]["metadata"]["seed"] == 1
        and exp.rows[0]["metadata"]["prompt_versions"] == "bundled"
    )
