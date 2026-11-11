import pytest

from chat.V2.personalization.lab import guard
from chat.V2.personalization.lab.datasets import load_dataset
from chat.V2.personalization.lab.guard import NotPreregistered
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.runner import run_case
from chat.V2.personalization.llm import FakeLLMClient

from .conftest import commit_and_push, git, write_case


def live(case):
    return run_case(
        case,
        kind="live",
        run_no=1,
        dataset=load_dataset("syn1"),
        llm=FakeLLMClient(lambda s, u: "{}"),
    )


def test_case_sha_is_the_git_tree_hash_without_results(repo):
    work, cases = repo
    directory = write_case(cases)
    (directory / "results").mkdir()
    (directory / "results" / "mock.json").write_text("{}")
    commit_and_push(work)
    expected = git(work, "rev-parse", "HEAD:lab/cases/a/x").strip()
    # results/ is committed too, so git's tree has a results entry; the lab's hash leaves it out
    assert guard.case_sha(directory) != expected
    git(work, "rm", "-rq", "lab/cases/a/x/results")
    commit_and_push(work, "drop results")
    assert guard.case_sha(directory) == git(work, "rev-parse", "HEAD:lab/cases/a/x").strip()


def test_case_sha_changes_with_any_file_and_not_with_results(repo):
    work, cases = repo
    directory = write_case(cases)
    before = guard.case_sha(directory)
    (directory / "results").mkdir()
    (directory / "results" / "run1.json").write_text("{}")
    assert guard.case_sha(directory) == before
    (directory / "pipeline.py").write_text("# changed\n")
    assert guard.case_sha(directory) != before


def test_preregistered_case_passes(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work)
    (directory / "results").mkdir()
    (directory / "results" / "mock.json").write_text("{}")  # results may change freely
    assert guard.assert_preregistered(directory) == guard.case_sha(directory)


def test_live_run_refused_when_case_dir_dirty(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work)
    (directory / "pipeline.py").write_text("# edited after the commit\n")
    with pytest.raises(NotPreregistered, match="uncommitted"):
        live(load_case(directory))


def test_an_untracked_file_in_the_case_dir_is_dirty(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work)
    (directory / "extra.py").write_text("x = 1\n")
    with pytest.raises(NotPreregistered, match="uncommitted"):
        guard.assert_preregistered(directory)


def test_live_run_refused_when_case_unpushed(repo):
    work, cases = repo
    directory = write_case(cases)
    commit_and_push(work, push=False)
    with pytest.raises(NotPreregistered, match="not on any origin"):
        live(load_case(directory))


def test_a_case_never_committed_is_refused(repo):
    work, cases = repo
    directory = write_case(cases)
    with pytest.raises(NotPreregistered):
        guard.assert_preregistered(directory)


def test_jev_refused_on_non_anonymized_real(tmp_path):
    from chat.V2.personalization.jev import FakeJevClient
    from chat.V2.personalization.lab.datasets import Dataset
    from chat.V2.personalization.lab.runner import JevOnIdentifiableData
    from chat.V2.personalization.typed import JevTypedClient

    case = load_case(write_case(tmp_path))
    typed = JevTypedClient(FakeJevClient(lambda state, questions: {}))
    real = Dataset("real1", "real", "sha", load_dataset("syn1").items[:1], anonymized=False)
    with pytest.raises(JevOnIdentifiableData):
        run_case(
            case,
            kind="mock",
            run_no=0,
            dataset=real,
            llm=FakeLLMClient(lambda s, u: "{}"),
            typed=typed,
        )


def test_jev_allowed_on_anonymized_real(tmp_path, monkeypatch):
    from chat.V2.personalization.jev import FakeJevClient
    from chat.V2.personalization.lab.datasets import Dataset
    from chat.V2.personalization.typed import JevTypedClient

    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    case = load_case(write_case(tmp_path))
    typed = JevTypedClient(FakeJevClient(lambda state, questions: {}))
    real = Dataset("anon-personas", "real", "sha", load_dataset("syn1").items[:1], anonymized=True)
    result = run_case(
        case, kind="mock", run_no=0, dataset=real, llm=FakeLLMClient(lambda s, u: "{}"), typed=typed
    )
    assert result["dataset"]["name"] == "anon-personas" and "per_item" not in result
