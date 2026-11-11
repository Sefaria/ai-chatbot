"""Mock of aggressiveness/none: the seam on a recorded request, the replay of two invented personas
with no model call, the bars, and two negative tests (a one-byte difference and a single store
write must be caught). No real text and no model call."""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.prompt_pipeline import build_turn_prompt
from chat.V2.personalization import runtime
from chat.V2.personalization.lab import runner
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.metrics import RunData, runs_identical
from chat.V2.prompts.prompt_fragments import build_prompt

CASE_DIR = Path(__file__).parent
TURNS = 9  # 5 + 4 turns in the fixture

# A recorded (synthetic) request and the prompt it must produce, written out by hand.
RECORDED = {
    "core": "You are the Library Assistant.",
    "summary": "They studied Berakhot.",
    "page": "https://www.sefaria.org/Genesis.1",
    "messages": [("user", "What is a sugya?"), ("assistant", "A unit of talmudic discussion."),
                 ("user", "Show me an example")],
}  # fmt: skip
RECORDED_PROMPT = (
    "You are the Library Assistant."
    "\n\nConversation summary:\nThey studied Berakhot."
    "\n\nPage context:\nThe user is currently on the Sefaria page: https://www.sefaria.org/Genesis.1. "
    "If the context is relevant, use that information in your response."
    "\n\nUser: What is a sugya?\nAssistant: A unit of talmudic discussion.\nUser: Show me an example"
)


def module():
    return runner.load_pipeline(CASE_DIR)


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository."""
    dest = tmp_path / "aggressiveness" / "none"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    shutil.copy(CASE_DIR.parent / "aspect.json", dest.parent)
    return dest


def mock_run(tmp_path, mod=None):
    case = load_case(case_copy(tmp_path))
    mod = mod or runner.load_pipeline(case.dir)
    dataset = Dataset("mock", "synthetic", "sha", mod.mock_items())
    llm = mod.mock_llm()
    result = runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=llm, pipeline_module=mod
    )
    return case, result, llm


# -- the seam on a recorded request -------------------------------------------------------------


def test_the_seam_on_a_recorded_request_matches_main_byte_for_byte():
    mod = module()
    messages = [ConversationMessage(role, text) for role, text in RECORDED["messages"]]
    context = MessageContext(summary_text=RECORDED["summary"], page_url=RECORDED["page"])
    ours = build_turn_prompt(messages=messages, core_prompt=RECORDED["core"], context=context)
    theirs, _ = mod.main_build_prompt("origin/main")(
        ours.conversation_text, core_prompt=RECORDED["core"],
        summary_text=RECORDED["summary"], page_url=RECORDED["page"],
    )  # fmt: skip
    assert ours.full_prompt == theirs == RECORDED_PROMPT
    assert mod.sha256(ours.full_prompt) == hashlib.sha256(RECORDED_PROMPT.encode()).hexdigest()


def test_main_s_build_prompt_is_loaded_in_its_own_namespace():
    mod = module()
    main = mod.main_build_prompt("origin/main")
    assert main.__globals__["__name__"] == "main_prompt_fragments"
    assert main is not build_prompt and main.__globals__ is not build_prompt.__globals__


def test_an_unknown_ref_is_an_error_not_a_pass():
    mod = module()
    with pytest.raises(mod.MainPromptError):
        mod.main_build_prompt("origin/no-such-branch")


# -- the replay ---------------------------------------------------------------------------------


def test_replay_of_two_personas_is_byte_identical_with_no_write_and_no_model_call(tmp_path):
    _case, result, llm = mock_run(tmp_path)
    assert result["metrics"] == {
        "byte_identical_off": 1.0, "store_writes": 0, "runs_identical": None,
    }  # fmt: skip
    assert result["ratios"]["byte_identical_off"] == [TURNS, TURNS]
    assert result["dataset"]["n"] == 2 and result["invalid"] == 0
    assert llm.calls == []
    assert result["cost"]["calls"] == 0 and result["cost"]["usd"] == 0
    assert result["privacy"]["sensitive_reached_model"] == 0


def test_each_turn_records_both_prompt_hashes_and_no_prompt_text():
    from chat.V2.personalization.lab.replay import replay_persona

    mod = module()
    pipeline = mod.build({"main_ref": "origin/main"}, mod.mock_llm())
    replay = replay_persona(pipeline, mod.mock_items()[0])
    hashes = replay.diagnostics["prompt_hashes"]
    assert [h["turn"] for h in hashes] == ["noa-1", "noa-2", "noa-3", "noa-4", "noa-5"]
    for h in hashes:
        assert len(h["lab"]) == len(h["main"]) == 64 and h["lab"] == h["main"]
    assert len({h["lab"] for h in hashes}) == 5  # five different requests, five different prompts
    saved = json.dumps(replay.diagnostics)
    assert all(t["user"][:12] not in saved for s in mod.mock_items()[0]["sessions"] for t in s["turns"])  # fmt: skip


def test_the_prompts_cover_every_branch_of_the_builder():
    mod = module()
    pipeline = mod.OffPipeline()
    seen = set()
    turn = lambda n: __import__("chat.V2.personalization.types", fromlist=["Turn"]).Turn(  # noqa: E731
        "u", "s", f"m{n}", "hi", "ok", datetime(2026, 11, 4, tzinfo=UTC)
    )
    for position in range(6):
        ours, theirs = pipeline.request_prompts("u", turn(position), position, [])
        assert ours == theirs
        seen.add(("Conversation summary:" in ours, "Page context:" in ours))
    assert seen == {(True, True), (False, False), (False, True), (True, False)}


def test_two_runs_are_identical():
    from chat.V2.personalization.lab.replay import replay_persona

    def metrics():
        mod = module()
        replays = [replay_persona(mod.build({}, None), p) for p in mod.mock_items()]
        return mod.counts({}, replays, None)

    assert metrics() == metrics() == {
        "byte_identical_off": TURNS, "byte_identical_off_of": TURNS, "store_writes": 0,
    }  # fmt: skip
    same = {"byte_identical_off": 1.0, "store_writes": 0}
    assert runs_identical(RunData(prior_metrics=[same, same], metrics_so_far=same)) == 1.0


# -- the negative tests: the metrics must be able to fail ----------------------------------------


def test_one_changed_byte_in_one_prompt_is_caught(tmp_path):
    mod = runner.load_pipeline(CASE_DIR)
    real = mod.main_build_prompt("origin/main")
    calls = []

    def one_byte_off_on_the_fourth(*args, **kwargs):
        text, included = real(*args, **kwargs)
        calls.append(1)
        return (text + " " if len(calls) == 4 else text), included

    mod.main_build_prompt = lambda ref: one_byte_off_on_the_fourth
    _case, result, _llm = mock_run(tmp_path, mod)
    assert result["ratios"]["byte_identical_off"] == [TURNS - 1, TURNS]
    assert result["metrics"]["byte_identical_off"] == (TURNS - 1) / TURNS
    assert result["metrics"]["byte_identical_off"] != 1.0


def test_a_profile_block_in_the_prompt_is_caught(tmp_path, monkeypatch):
    class Profile:
        text, slots_used = "- Hebrew reading level: basic", ["hebrew_fluency"]

    monkeypatch.setattr(runtime, "profile_for_request", lambda config, now: Profile())
    _case, result, _llm = mock_run(tmp_path)
    assert result["metrics"]["byte_identical_off"] == 0.0


def test_a_single_store_write_is_caught(tmp_path, monkeypatch):
    done = []

    def leaky(config, now):
        if not done:
            done.append(1)
            runtime.get_store().set_stated(
                "mock:noa", "answer_style", "concise", datetime(2026, 11, 4, tzinfo=UTC)
            )
        return None

    monkeypatch.setattr(runtime, "profile_for_request", leaky)
    _case, result, _llm = mock_run(tmp_path)
    assert result["metrics"]["store_writes"] == 1
    assert result["metrics"]["byte_identical_off"] == 1.0  # the prompt was untouched; the write alone is caught  # fmt: skip


def test_a_store_read_alone_is_not_a_write():
    store = module().CountingStore()
    store.get_profile("u")
    store.observations("u")
    assert store.writes() == 0
    store.add_proposal  # noqa: B018  (looked up, not called)
    assert store.writes() == 0


# -- the manifest -------------------------------------------------------------------------------


def test_manifest_copies_the_test_tree_row_and_names_a2():
    case = load_case(CASE_DIR)
    assert case.task == "replay" and case.engine == "offline" and case.status == "planned"
    assert case.datasets == ["syn1", "anon-personas"]
    assert case.metrics == ["byte_identical_off", "store_writes", "runs_identical"]
    assert [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if] == [
        ("byte_identical_off", "each", "==", 1.0, "anon-personas"),
        ("store_writes", "each", "==", 0, "anon-personas"),
        ("runs_identical", "min", "==", 1, "anon-personas"),
    ]  # fmt: skip
    raw = json.loads((CASE_DIR / "case.json").read_text(encoding="utf-8"))
    assert any("A2" in d.get("amendment", "") and "188" in d["what"] for d in raw["deviations"])
    assert not {"sample_dataset", "sample_size", "sample_seed"} & set(case.config)
    assert not hasattr(module(), "select_items")  # deterministic: every persona is replayed
