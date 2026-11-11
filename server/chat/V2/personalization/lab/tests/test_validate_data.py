"""The dataset validator for real anonymized datasets (anon-personas, anon-vocab, anon-visit, anon-impact)."""

import json
from datetime import UTC, datetime, timedelta

from chat.V2.personalization.lab import __main__ as cli
from chat.V2.personalization.lab import validate_data as vd

TRAPS = list(vd.TRAPS)
HEB = "שלום, אפשר לקבל הסבר קצר על הפרשה הזאת"


def _turn(pid, n, at, user, tags=None):
    turn = {
        "id": f"{pid}-{n}",
        "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": user,
        "assistant": f"Invented answer number {n}.",
    }
    if tags is not None:
        turn["tags"] = tags
    return turn


def make_persona(index: int, trap: str = "traditional", n_turns: int = 8) -> dict:
    pid = f"p{index}"
    hebrew = index < 3
    base = datetime(2026, 1, 5, 9, tzinfo=UTC)
    text = (lambda n: f"{HEB} {n}") if hebrew else (lambda n: f"Question {n} about a text")
    turns = [
        _turn(pid, n, base + timedelta(days=n), text(n), tags=[]) for n in range(1, n_turns + 1)
    ]
    if n_turns >= 3:
        turns[2]["user"] = "yes" if not hebrew else "כן"
        turns[2]["tags"] = ["short_reply"]
    if trap in ("outlier", "correction", "conflict", "injected") and n_turns >= 5:
        turns[4]["tags"] = [trap]
    if trap == "sensitive" and n_turns >= 6:
        turns[4]["tags"], turns[5]["tags"] = ["sensitive"], ["sensitive"]
    if trap == "exclusion" and n_turns >= 6:
        turns[4]["tags"] = ["exclusion", "must_not_store"]
        turns[5]["tags"] = ["instruction"]
    sessions = [
        {"session_id": f"{pid}-s1", "turns": turns[:4]},
        {"session_id": f"{pid}-s2", "turns": turns[4:]},
    ]
    golden = {
        "preferred_language": {"value": "he" if hebrew else "en"},
        "register": {"value": "academic" if trap == "academic" else "traditional"},
        "text_categories": {"must_include": ["Talmud"]},
        "answer_style": None,
    }
    return {
        "id": pid,
        "user_id": f"u:{pid}",
        "sessions": sessions,
        "golden": golden,
        "golden_timeline": [
            {
                "from_turn": f"{pid}-1",
                "slot": "preferred_language",
                "value": golden["preferred_language"]["value"],
            }
        ],
        "checks": [{"type": "available_by_turn", "slot": "preferred_language", "turn": f"{pid}-2"}],
        "stated_events": [],
    }


def make_personas(n_users: int = 8, n_turns: int = 8) -> dict:
    return {
        "slots_version": "v2",
        "personas": [
            make_persona(i, TRAPS[i % len(TRAPS)], n_turns=n_turns) for i in range(n_users)
        ],
    }


def test_anon_personas_valid_with_minimum_counts_and_partial_trap_coverage():
    # 8 users x 8 turns = 64 turns (>= 8 users, >= 60 turns), only some traps present
    data = {
        "slots_version": "v2",
        "personas": [make_persona(i, "traditional", n_turns=8) for i in range(8)],
    }
    errors, report = vd.validate_personas_with_report(data, vd.load_slots())
    assert errors == []
    assert report["users"] == 8 and report["turns"] == 64
    assert isinstance(report["traps"], dict) and report["traps"]["short_reply"] == 8
    assert report["traps"]["injected"] == 0


def test_anon_personas_enforces_minimum_users_and_turns():
    too_few_users = make_personas(n_users=7, n_turns=10)
    errors = vd.validate_personas(too_few_users, vd.load_slots())
    assert any("8" in e and "user" in e for e in errors), errors

    too_few_turns = make_personas(n_users=8, n_turns=6)  # 48 turns < 60
    errors = vd.validate_personas(too_few_turns, vd.load_slots())
    assert any("60" in e and "turn" in e for e in errors), errors


def test_anon_personas_rejects_illegal_slot_value_and_key():
    data = make_personas()
    data["personas"][0]["golden"]["register"] = {"value": "casual"}
    data["personas"][1]["golden"]["favourite_color"] = {"value": "blue"}
    errors = vd.validate_personas(data, vd.load_slots())
    assert any("register" in e and "casual" in e for e in errors)
    assert any("favourite_color" in e for e in errors)


def test_anon_personas_rejects_story_ids_and_bad_references():
    data = make_personas()
    data["personas"][0]["user_id"] = "u:sc-" + "12345"
    data["personas"][1]["checks"].append(
        {"type": "available_by_turn", "slot": "register", "turn": "nope"}
    )
    errors = vd.validate_personas(data, vd.load_slots())
    assert any("story id" in e for e in errors) and any("nope" in e for e in errors)


def make_vocab(n: int = 60) -> list:
    items = []
    for i in range(n):
        label = vd.VOCAB_LABELS[i % len(vd.VOCAB_LABELS)]
        text = f"phrase {i}" if i % 2 else f"ביטוי {i}"
        items.append({"id": f"v-{i}", "text": text, "label": label})
    return items


def test_anon_vocab_minimum_60_items_and_legal_labels():
    assert vd.validate_vocab(make_vocab(60)) == []
    errors = vd.validate_vocab(make_vocab(59))
    assert any("60" in e for e in errors), errors
    bad = make_vocab(60)
    bad[0]["label"] = "slang"
    assert any("label" in e for e in vd.validate_vocab(bad))


def make_visit(n: int = 20) -> list:
    rows = []
    for i in range(n):
        site = "he" if i % 2 else "en"
        hebrew = i % 3 == 0
        rows.append(
            {
                "id": f"v{i}",
                "site": site,
                "browser_lang": "he-IL" if hebrew else "en-US",
                "country": "IL" if hebrew else "US",
                "prompt": "מה זה סוגיה בתלמוד" if hebrew else "What is a sugya in the Talmud",
                "expected_reply_lang": "he" if hebrew else "en",
            }
        )
    return rows


def test_anon_visit_minimum_20_items():
    assert vd.validate_visit(make_visit(20)) == []
    errors = vd.validate_visit(make_visit(19))
    assert any("20" in e for e in errors), errors


def make_impact(n: int = 20):
    prompts = []
    for i in range(n):
        relevant = i < 4
        prompts.append(
            {
                "id": f"i{i}",
                "text": "מה פירוש המילה הזאת" if i % 3 == 0 else f"Prompt {i}",
                "kind": "relevant" if relevant else "neutral",
                "relevant_to": "hebrew_fluency" if relevant else None,
                "many_voices": i % 3 == 0,
            }
        )
    profiles = {
        "P1": {"hebrew_fluency": "none"},
        "P2": {"hebrew_fluency": "fluent"},
        "P3": {"hebrew_fluency": "basic", "register": "academic"},
    }
    return prompts, profiles


def test_anon_impact_minimum_20_items_and_slot_legality():
    prompts, profiles = make_impact(20)
    assert vd.validate_impact(prompts, profiles, vd.load_slots()) == []
    assert vd.validate_impact(prompts, None, vd.load_slots()) == []
    short_prompts, _ = make_impact(19)
    errors = vd.validate_impact(short_prompts, profiles, vd.load_slots())
    assert any("20" in e for e in errors), errors
    profiles["P1"]["hebrew_fluency"] = "native"
    errors = vd.validate_impact(prompts, profiles, vd.load_slots())
    assert any("native" in e for e in errors)


def test_cli_validate_data_exits_0_1_2_3_and_never_prints_item_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    assert cli.main(["validate-data", "syn1"]) == 2  # no validator for syn1
    capsys.readouterr()

    # Exit 3 when blocked in meta.json
    visit_dir = tmp_path / "anon-visit"
    visit_dir.mkdir()
    (visit_dir / "meta.json").write_text(
        json.dumps({"anonymized": True, "blocked": "BigQuery table has no browser_lang column"})
    )
    assert cli.main(["validate-data", "anon-visit"]) == 3
    out = capsys.readouterr().out
    assert "BigQuery table has no browser_lang column" in out

    # Exit 0 when valid, prints numbers and field names only
    vocab_dir = tmp_path / "anon-vocab"
    vocab_dir.mkdir()
    items = make_vocab(60)
    items[0]["text"] = "PRIVATE_UNSCRUBBED_SENTENCE_DO_NOT_PRINT"
    (vocab_dir / "items.json").write_text(json.dumps(items))
    assert cli.main(["validate-data", "anon-vocab"]) == 0
    out = capsys.readouterr().out
    assert "ok anon-vocab" in out
    assert "PRIVATE_UNSCRUBBED_SENTENCE_DO_NOT_PRINT" not in out

    # Exit 1 when invalid, never prints item text
    bad_items = [{"id": "v-0", "text": "SECRET_TEXT_MUST_NEVER_LEAK", "extra_field": 1}]
    (vocab_dir / "items.json").write_text(json.dumps(bad_items))
    assert cli.main(["validate-data", "anon-vocab"]) == 1
    out = capsys.readouterr().out
    assert "INVALID anon-vocab" in out
    assert "SECRET_TEXT_MUST_NEVER_LEAK" not in out


def test_validate_reads_labeled_when_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path))
    vocab_dir = tmp_path / "anon-vocab"
    vocab_dir.mkdir()
    (vocab_dir / "items.json").write_text(json.dumps(make_vocab(60)))
    assert cli.main(["validate-data", "anon-vocab"]) == 0
    capsys.readouterr()

    # labeled.jsonl is invalid (illegal label) while the unlabeled file is valid
    labeled = make_vocab(60)[:50]
    labeled[0]["label"] = "not_a_legal_label"
    (vocab_dir / "labeled.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labeled))
    assert cli.main(["validate-data", "anon-vocab"]) == 1
    out = capsys.readouterr().out
    assert "INVALID anon-vocab" in out
    assert "10 unlabeled" in out

    # fully labeled: valid and no unlabeled count line
    (vocab_dir / "labeled.jsonl").write_text("".join(json.dumps(r) + "\n" for r in make_vocab(60)))
    assert cli.main(["validate-data", "anon-vocab"]) == 0
    assert "unlabeled" not in capsys.readouterr().out


def test_validate_exit_codes(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    server_dir = Path(vd.__file__).resolve().parents[4]
    env = {**os.environ, "LA_LAB_DATA_DIR": str(tmp_path)}

    def run(name):
        return subprocess.run(
            [sys.executable, "-m", "chat.V2.personalization.lab", "validate-data", name],
            cwd=server_dir, env=env, capture_output=True, text=True,
        ).returncode  # fmt: skip

    vocab_dir = tmp_path / "anon-vocab"
    vocab_dir.mkdir()
    (vocab_dir / "items.json").write_text(json.dumps(make_vocab(60)))
    assert run("anon-vocab") == 0
    (vocab_dir / "items.json").write_text(json.dumps([{"id": "v-0", "text": "x", "extra": 1}]))
    assert run("anon-vocab") == 1
    visit_dir = tmp_path / "anon-visit"
    visit_dir.mkdir()
    (visit_dir / "meta.json").write_text(json.dumps({"blocked": "no column"}))
    assert run("anon-visit") == 3
