"""Guards that the CI workflow lints the server with the pinned ruff version."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yaml"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"


def test_ci_runs_ruff_check_and_format_check():
    workflow = CI_WORKFLOW.read_text()

    assert "ruff check" in workflow
    assert "ruff format --check" in workflow


def test_ci_ruff_version_matches_pre_commit_pin():
    workflow = CI_WORKFLOW.read_text()
    pre_commit = PRE_COMMIT_CONFIG.read_text()

    assert "rev: v0.8.6" in pre_commit
    assert "ruff==0.8.6" in workflow
