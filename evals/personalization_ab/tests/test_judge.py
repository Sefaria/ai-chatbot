import json
import random

from personalization_ab import judge

PACKET = {
    "user": "u-1",
    "turns": [{"prompt": f"p{i}", "answer": f"a{i}"} for i in range(10)],
    "profile_a": "PROFILE-A-TEXT",
    "profile_b": "PROFILE-B-TEXT",
    "prompts": [
        {
            "index": 11 + i,
            "prompt": f"q{i}",
            "original": f"orig{i}",
            "A": f"ansA{i}",
            "B": f"ansB{i}",
        }
        for i in range(2)
    ],
}


def fake_call(model, system, user):
    """Ranks whatever is labelled 1 first, then 2, then 3; scores profiles 4 and 2."""
    if system == judge.PROFILE_SYSTEM:
        return json.dumps(
            {
                "profiles": {
                    "1": {"score": 4, "supported": ["x"], "missed": [], "invented": []},
                    "2": {
                        "score": 2,
                        "supported": [],
                        "missed": ["y"],
                        "invented": ["z"],
                    },
                },
                "winner": "1",
                "why": "first is better",
            }
        )
    return json.dumps({"ranking": ["1", "2", "3"], "why": "by label"})


def ranking(judgments, model, index):
    return next(
        j
        for j in judgments
        if j.kind == "ranking" and j.model == model and j.prompt_index == index
    )


def test_shuffle_is_stable_for_a_seed_and_differs_between_prompts():
    one = judge.run_judges(PACKET, models=("opus",), seed=7, call=fake_call)
    two = judge.run_judges(PACKET, models=("opus",), seed=7, call=fake_call)
    assert [j.data for j in one] == [j.data for j in two]
    assert judge.label_order(7, 11) == judge.label_order(7, 11)
    order = random.Random(7 + 11)
    expected = ["original", "A", "B"]
    order.shuffle(expected)
    assert judge.label_order(7, 11) == expected


def test_labels_map_back_to_the_right_ranks():
    js = judge.run_judges(PACKET, models=("opus", "haiku"), seed=3, call=fake_call)
    for index in (11, 12):
        order = judge.label_order(3, index)  # label "1" is order[0]
        got = ranking(js, "opus", index).data["ranking"]
        assert got == order  # fake judge ranked labels 1,2,3 in that order
        assert sorted(got) == ["A", "B", "original"]


def test_judge_sees_answers_blind_and_profile_judgment_maps_back():
    seen = []

    def spy(model, system, user):
        seen.append(user)
        return fake_call(model, system, user)

    js = judge.run_judges(PACKET, models=("sonnet",), seed=5, call=spy)
    ranking_prompts = [u for u in seen if "orig0" in u]
    assert ranking_prompts and all(
        "original" not in u.lower().split("orig0")[0][-40:] for u in ranking_prompts
    )
    prof = next(j for j in js if j.kind == "profile")
    assert set(prof.data["scores"]) == {"A", "B"}
    assert sorted(prof.data["scores"].values()) == [2, 4]
    assert prof.data["winner"] in ("A", "B")


def test_default_call_flags_isolate_the_model(monkeypatch):
    captured = {}

    class Proc:
        returncode = 0
        stdout = json.dumps({"result": "hi"})

    def fake_run(argv, **kw):
        captured["argv"], captured["kw"] = argv, kw
        return Proc()

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    assert judge.claude_call("haiku", "sys", "usr") == "hi"
    argv = captured["argv"]
    for flag in (
        "--output-format",
        "--setting-sources",
        "--settings",
        "--strict-mcp-config",
        "--tools",
    ):
        assert flag in argv
    assert captured["kw"]["env"]["MAX_THINKING_TOKENS"] == "0"
