"""The anonymized real-data pull, exercised with invented rows and a fake model."""

import json
import subprocess
from pathlib import Path

import pytest

from chat.V2.personalization.lab.datasets import checksum
from chat.V2.personalization.lab.real.pull import (
    ExportFileSource,
    FakeSource,
    PullError,
    run,
)
from chat.V2.personalization.lab.real.scrub import scrub_text
from chat.V2.personalization.llm import FakeLLMClient

FIELDS = {
    "user_id": "metadata.user_id",
    "conversation_id": "metadata.session_id",
    "timestamp": "created",
    "user_text": "input",
    "assistant_text": "output",
    "staff_marker": {
        "path": "metadata.is_staff",
        "staff_values": [True],
        "rule": "drop_user_if_any",
    },
    "site": "metadata.site",
    "browser_lang": "metadata.browser_lang",
    "country": "metadata.country",
}


class ExplodingSource:
    def rows(self):
        raise AssertionError("the source must not be read")


def quiet_llm():
    return FakeLLMClient(lambda system, user: '{"spans": []}')


def row(
    user,
    i,
    text,
    assistant,
    *,
    conversation="conv-1",
    is_staff=None,
    site="en",
    browser_lang="en",
    country="US",
    **extra,
):
    data = {
        "created": f"2026-11-01T10:{i:02d}:00Z",
        "input": text,
        "output": assistant,
        "metadata": {
            "user_id": user,
            "session_id": conversation,
            "is_staff": is_staff,
            "site": site,
            "browser_lang": browser_lang,
            "country": country,
        },
    }
    data.update(extra)
    return data


def learner(user, n, conversation="conv-1", is_staff=None, label=None):
    who = label or "sample-learner"
    return [
        row(
            user,
            i,
            f"Question {i} from {who} about Genesis",
            f"Answer {i} for {who} about Genesis",
            conversation=conversation,
            is_staff=is_staff,
        )
        for i in range(n)
    ]


def go(tmp_path, rows, llm=None, fields=None, **kwargs):
    out = kwargs.pop("out", tmp_path / "out")
    run(
        fields=FIELDS if fields is None else fields,
        min_turns=kwargs.pop("min_turns", 4),
        max_turns=kwargs.pop("max_turns", 8),
        target_users=kwargs.pop("target_users", 200),
        out=out,
        source=kwargs.pop("source", FakeSource(rows)),
        llm=quiet_llm() if llm is None else llm,
        **kwargs,
    )
    return out


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def personas(out: Path) -> list[dict]:
    return load_jsonl(out / "anon-personas" / "users.jsonl")


def items(out: Path, name: str) -> list[dict]:
    return load_jsonl(out / name / "items.jsonl")


def read_meta(out: Path, name: str) -> dict:
    return json.loads((out / name / "meta.json").read_text(encoding="utf-8"))


def read_summary(out: Path, name: str) -> dict:
    return json.loads((out / name / "summary.json").read_text(encoding="utf-8"))


def blob(out: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in out.rglob("*") if path.is_file())


def turns_of(persona: dict) -> list[dict]:
    return [turn for session in persona["sessions"] for turn in session["turns"]]


def test_staff_rows_removed(tmp_path):
    out = go(
        tmp_path,
        learner("learner-7", 4, label="kept-user")
        + learner("staff-9", 6, is_staff=True, label="staff-user"),
    )
    written = blob(out)
    assert [p["sessions"][0]["turns"][0]["user"] for p in personas(out)]
    assert "learner-7" not in written
    assert "staff-9" not in written
    assert "Question 0 from kept-user about Genesis" in written
    assert "from staff-user" not in written
    assert len(personas(out)) == 1


def test_staff_user_dropped_entirely(tmp_path):
    # Staff user has 4 rows with is_staff=None and only 1 row with is_staff=True;
    # rule "drop_user_if_any" must drop every row of that user.
    staff_rows = learner("staff-mixed", 4, is_staff=None) + [
        row("staff-mixed", 4, "Staff test query", "Staff test answer", is_staff=True)
    ]
    out = go(tmp_path, learner("learner-7", 4) + staff_rows)
    written = blob(out)
    assert len(personas(out)) == 1
    assert "staff-mixed" not in written
    assert "Staff test query" not in written


def test_missing_staff_marker_refused(tmp_path):
    fields = {key: value for key, value in FIELDS.items() if key != "staff_marker"}
    with pytest.raises(PullError, match="staff_marker"):
        go(tmp_path, learner("learner-7", 4), fields=fields, source=ExplodingSource())
    assert not (tmp_path / "out").exists()


def test_users_outside_4_to_8_turns_removed(tmp_path):
    rows = (
        learner("u3", 3, label="three-turn")
        + learner("u4", 4, label="four-turn")
        + learner("u8", 8, label="eight-turn")
        + learner("u9", 9, label="nine-turn")
    )
    out = go(tmp_path, rows)
    texts = " ".join(turn["user"] for persona in personas(out) for turn in turns_of(persona))
    assert "from four-turn " in texts and "from eight-turn " in texts
    assert "from three-turn " not in texts and "from nine-turn " not in texts
    counts = sorted(len(turns_of(persona)) for persona in personas(out))
    assert counts == [4, 8]


def test_assistant_turn_kept_with_each_user_turn(tmp_path):
    pairs = [
        ("What is a mishnah?", "A mishnah is one paragraph of the Oral Law."),
        ("And a gemara?", "A gemara is the discussion of that paragraph."),
        ("Who is Rashi?", "Rashi is a medieval commentator."),
        ("Where is Genesis 1:1?", "Genesis 1:1 opens the Torah."),
    ]
    rows = [row("learner-7", i, user, assistant) for i, (user, assistant) in enumerate(pairs)]
    out = go(tmp_path, rows)
    got = [(turn["user"], turn["assistant"]) for turn in turns_of(personas(out)[0])]
    assert got == pairs


def test_text_from_string_content_or_messages(tmp_path):
    rows = [
        row("learner-7", 0, "Plain string question", "Plain string answer"),
        row(
            "learner-7",
            1,
            {"content": "Object content question"},
            {"content": "Object content answer"},
        ),
        row(
            "learner-7",
            2,
            {
                "messages": [
                    {"role": "user", "content": "Earlier user question"},
                    {"role": "assistant", "content": "Earlier assistant answer"},
                    {"role": "user", "content": "Last user message in list"},
                ]
            },
            {
                "messages": [
                    {"role": "user", "content": "Last user message in list"},
                    {"role": "assistant", "content": "First assistant draft"},
                    {"role": "assistant", "content": "Last assistant message in list"},
                ]
            },
        ),
        row("learner-7", 3, "Fourth valid question", "Fourth valid answer"),
        # Unreadable rows for the same user should be dropped and counted, leaving 4 valid turns:
        row("learner-7", 4, {"unexpected": 123}, "Valid answer"),
        row(
            "learner-7",
            5,
            "Valid question",
            {"messages": [{"role": "user", "content": "no assistant"}]},
        ),
    ]
    out = go(tmp_path, rows)
    got = [(t["user"], t["assistant"]) for t in turns_of(personas(out)[0])]
    assert got == [
        ("Plain string question", "Plain string answer"),
        ("Object content question", "Object content answer"),
        ("Last user message in list", "Last assistant message in list"),
        ("Fourth valid question", "Fourth valid answer"),
    ]


def test_out_inside_git_repo_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(PullError, match="git"):
        go(tmp_path, learner("learner-7", 4), out=repo / "data", source=ExplodingSource())
    assert not (repo / "data").exists()
    worktree = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).parent,
            text=True,
        ).strip()
    )
    with pytest.raises(PullError, match="git"):
        go(tmp_path, learner("learner-7", 4), out=worktree, source=ExplodingSource())


def test_export_inside_git_repo_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    export_file = repo / "rows.json"
    export_file.write_text(json.dumps({"data": learner("learner-7", 4)}), encoding="utf-8")
    with pytest.raises(PullError, match="git"):
        ExportFileSource(export_file).rows()

    valid_export = tmp_path / "rows.json"
    valid_export.write_text(json.dumps({"data": learner("learner-7", 4)}), encoding="utf-8")
    loaded = ExportFileSource(valid_export).rows()
    assert len(loaded) == 4


def test_sensitive_turns_dropped_and_counted(tmp_path):
    rows = learner("learner-7", 4)
    rows.append(
        row(
            "learner-7",
            4,
            "my mother is sick and I am asking for a psalm",
            "I am sorry to hear that",
        )
    )
    llm = quiet_llm()
    out = go(tmp_path, rows, llm=llm)
    texts = [turn["user"] for turn in turns_of(personas(out)[0])]
    assert len(texts) == 4
    assert all("sick" not in text for text in texts)
    summary = read_summary(out, "anon-personas")
    assert summary["sensitive_dropped_share"] == pytest.approx(0.2)
    assert all("sick" not in call["user"] for call in llm.calls)


def test_summary_has_only_numbers(tmp_path):
    out = go(
        tmp_path,
        learner("learner-7", 4, conversation="c1") + learner("learner-7", 2, conversation="c2"),
    )

    def numbers_only(value):
        if isinstance(value, dict):
            for item in value.values():
                numbers_only(item)
        elif isinstance(value, list):
            for item in value:
                numbers_only(item)
        else:
            assert isinstance(value, (int, float)) and not isinstance(value, bool)

    for name in ("anon-personas", "anon-vocab", "anon-visit", "anon-impact"):
        summary = read_summary(out, name)
        numbers_only(summary)
        for key in (
            "users",
            "turns",
            "turns_per_user",
            "hebrew_prompt_share",
            "short_reply_share",
            "sensitive_dropped_share",
            "scrub_hits",
            "residual_hits",
        ):
            assert key in summary
        assert isinstance(summary["scrub_hits"], dict)
        directory = out / name
        names = sorted(path.name for path in directory.iterdir() if path.name != "sha256")
        assert (directory / "sha256").read_text(encoding="utf-8").strip() == checksum(
            directory, names
        )
        assert "learner-7" not in json.dumps(summary)


def test_ids_replaced_by_fresh_pseudonyms(tmp_path):
    rows = learner("learner-7", 4, conversation="conv-77", label="sample-learner")
    first = go(tmp_path, rows, out=tmp_path / "one")
    second = go(tmp_path, rows, out=tmp_path / "two")
    a = personas(first)[0]
    b = personas(second)[0]
    assert a["user_id"] != "learner-7" and b["user_id"] != "learner-7"
    assert a["user_id"] != b["user_id"]
    assert a["sessions"][0]["session_id"] != "conv-77"
    assert a["sessions"][0]["session_id"] != b["sessions"][0]["session_id"]
    assert "learner-7" not in blob(first) and "conv-77" not in blob(first)


def test_scrub_replaces_email_phone_url_number(tmp_path):
    user = "Email ada@example.com or call 415-555-0134 about ref 123456 and page 12345"
    assistant = "See https://example.com/a?user=123456 and https://example.com/plain"
    rows = [row("learner-7", 0, user, assistant)]
    rows += [
        row("learner-7", i, f"Question {i} about Genesis", f"Answer {i} about Genesis")
        for i in range(1, 4)
    ]
    out = go(tmp_path, rows)
    turn = turns_of(personas(out)[0])[0]
    assert turn["user"] == "Email [email] or call [phone] about ref [number] and page 12345"
    assert turn["assistant"] == "See [url] and https://example.com/plain"
    assert scrub_text(user)[0] == turn["user"]
    hits = read_summary(out, "anon-personas")["scrub_hits"]
    assert hits["email"] == 1 and hits["phone"] == 1 and hits["url"] == 1 and hits["number"] == 1


def test_name_pass_replaces_spans(tmp_path):
    def respond(system, user):
        assert "rabbi" in system.lower()
        assert "biblical" in system.lower()
        if "Dana Cohen" in user:
            return '{"spans": ["Dana Cohen"]}'
        return '{"spans": []}'

    llm = FakeLLMClient(respond)
    rows = [
        row(
            "learner-7",
            0,
            "Dana Cohen asked what Rashi says on Genesis",
            "Rashi reads the verse plainly",
        ),
        row("learner-7", 1, "What is a mishnah?", "A paragraph of the Oral Law."),
        row("learner-7", 2, "And a gemara?", "The discussion of that paragraph."),
        row("learner-7", 3, "Thank you", "Glad to help."),
    ]
    out = go(tmp_path, rows, llm=llm)
    turn = turns_of(personas(out)[0])[0]
    assert turn["user"] == "[name] asked what Rashi says on Genesis"
    assert "Dana Cohen" not in blob(out)
    assert read_meta(out, "anon-personas")["anonymized"] is True


def test_residual_hit_sets_anonymized_false(tmp_path, monkeypatch):
    def leave(text: str):
        return text, {"email": 0, "phone": 0, "url": 0, "number": 0}

    monkeypatch.setattr("chat.V2.personalization.lab.real.scrub.scrub_text", leave)
    rows = [
        row("learner-7", 0, "write to ada@example.com about Genesis", "A commentator reads it so"),
        row("learner-7", 1, "What is a mishnah?", "A paragraph of the Oral Law."),
        row("learner-7", 2, "And a gemara?", "The discussion of that paragraph."),
        row("learner-7", 3, "Thank you", "Glad to help."),
    ]
    out = go(tmp_path, rows)
    assert read_meta(out, "anon-personas")["anonymized"] is False
    assert read_summary(out, "anon-personas")["residual_hits"] >= 1


def test_name_pass_failure_sets_anonymized_false(tmp_path):
    llm = FakeLLMClient(lambda system, user: "not json")
    rows = [
        row("learner-7", i, f"Question {i} about Rashi on Genesis", f"Answer {i} about Genesis")
        for i in range(4)
    ]
    out = go(tmp_path, rows, llm=llm)
    assert read_meta(out, "anon-personas")["anonymized"] is False
    assert "Question 0 about Rashi on Genesis" in turns_of(personas(out)[0])[0]["user"]
    assert read_summary(out, "anon-personas")["residual_hits"] == 0


def test_visit_blocked_when_fields_unmapped(tmp_path):
    fields = {**FIELDS, "site": None, "browser_lang": None, "country": None}
    out = go(tmp_path, learner("learner-7", 4), fields=fields)
    blocked = read_meta(out, "anon-visit")["blocked"]
    assert "site" in blocked and "browser_lang" in blocked and "country" in blocked
    visit = items(out, "anon-visit")
    assert visit and "text" in visit[0]
    assert "site" not in visit[0] and "browser_lang" not in visit[0] and "country" not in visit[0]
    mapped = go(tmp_path, learner("learner-7", 4), out=tmp_path / "mapped")
    row0 = items(mapped, "anon-visit")[0]
    assert row0["site"] == "en" and row0["browser_lang"] == "en" and row0["country"] == "US"
    assert "text" in row0 and "blocked" not in read_meta(mapped, "anon-visit")
