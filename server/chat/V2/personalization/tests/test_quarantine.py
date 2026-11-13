"""Standing instructions are quarantined by default; model and Jev replies are validated."""

import json

import pytest

from chat.V2.personalization.jev import FakeJevClient, JevAnswer, JevError, parse_response
from chat.V2.personalization.signals import find_standing_instructions

from .conftest import NOW, day, make_strategy, make_turn
from .test_nightly import consolidation

BOTH = ("hybrid", "hybrid_jev")
STYLE = "From now on keep your answers short."
MIXED = "Always keep it brief. Never show me sources from the Reform movement."


EXCLUDE_WORDS = ("Reform", "Kabbalah", "Zohar", "רפורמ", "קבלה")


def style_reply(system, user):
    """Per-instruction classifier for `hybrid`: a Reform request is an exclusion."""
    if any(word in user for word in EXCLUDE_WORDS):
        return json.dumps({"slot": None, "excludes_sources": True})
    return json.dumps({"slot": "answer_style", "value": user[:100], "excludes_sources": False})


def jev_for(exclude_words=EXCLUDE_WORDS, *, drop=()):
    """Jev fake for the instruction request; `drop` removes answers (a missing one is UNKNOWN)."""

    def respond(state, questions):
        if "message" not in state:
            return {}
        flagged = any(w in state["message"] for w in exclude_words)
        answers = {
            "is_lasting_style_preference": JevAnswer("noul", noul=0.0 if flagged else 0.9),
            "asks_to_exclude_sources": JevAnswer("noul", noul=0.9 if flagged else 0.0),
        }
        return {k: v for k, v in answers.items() if k not in drop}

    return FakeJevClient(respond)


def build(name, store, registry, *, reply=style_reply, jev=None):
    return make_strategy(
        name, store, registry, reply, jev=(jev or jev_for()) if name == "hybrid_jev" else None
    )


def quarantined(store, user="u1"):
    return [o for o in store.observations(user, include_quarantined=True) if o.quarantined]


# -- detection ------------------------------------------------------------------------------------


def test_joined_instructions_in_one_sentence_are_split():
    english = find_standing_instructions("Always keep it brief and never show me Reform sources.")
    assert len(english) == 2 and english[0].startswith("Always") and "never" in english[1]
    hebrew = find_standing_instructions("תמיד תענה בקצרה ולעולם אל תראה לי מקורות רפורמיים")
    assert len(hebrew) == 2 and hebrew[1].startswith("ולעולם")
    assert len(find_standing_instructions("From now on, always answer in Hebrew.")) == 1
    assert len(find_standing_instructions("אף פעם אל תתרגם")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Stop showing me Reform sources",
        "Don't show Kabbalah",
        "Ignore the Zohar",
        "אל תראה לי מקורות רפורמיים",
        "בלי קבלה",
    ],
)
def test_exclusions_without_always_or_never_are_detected(text):
    assert find_standing_instructions(text)


ORDINARY = [
    "Is the Sabbath prohibition only in Genesis or also in Exodus?",
    "Can I learn Talmud without Rashi if my Aramaic is weak?",
    "What is the difference between the only begotten and the first born in Psalms?",
    "הולך הנהר אל הים - מה פירוש הפסוק בקהלת",
    "Which verse says that only the firstborn inherits a double portion?",
]
# Ordinary questions the detector flags today. Over-matching is accepted (a flagged turn is only
# classified or quarantined), but it must stay visible: the count is asserted exactly.
KNOWN_OVERMATCH = [
    "Which verses talk about the only son, Isaac, without naming him?",
    "Is it always forbidden to carry on Shabbat?",
    "Why did the Rabbis never explain this verse?",
    "What does 'do not murder' mean in the Decalogue?",
]


def test_plain_study_question_is_not_an_instruction():
    flagged = [q for q in ORDINARY if find_standing_instructions(q)]
    print(f"over-matching: {len(flagged)} of {len(ORDINARY)} ordinary questions flagged")
    assert flagged == []


def test_known_overmatch_is_exactly_the_listed_questions():
    flagged = [q for q in KNOWN_OVERMATCH if find_standing_instructions(q)]
    print(f"known over-match: {len(flagged)} of {len(KNOWN_OVERMATCH)}")
    assert flagged == KNOWN_OVERMATCH and len(flagged) == 4


@pytest.mark.parametrize(
    "text",
    [
        "Hide Reform sources",
        "Please leave out Reform commentaries",
        "Keep away from Reform sources",
        "Omit the Zohar",
        "Filter out Kabbalah",
        "Remove Reform sources",
        "I'd rather not see Reform sources",
        "הסתר מקורות רפורמיים",
        "תסתיר את הזוהר",
        "תסתירי מקורות רפורמיים",
        "השמט את הקבלה",
        "סנן מקורות רפורמיים",
    ],
)
def test_hiding_verbs_are_detected(text):
    assert find_standing_instructions(text)


# -- the turn rule --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", BOTH)
def test_second_instruction_sentence_is_classified(store, registry, name):
    assert len(find_standing_instructions(MIXED)) == 2
    strategy, _, _ = build(name, store, registry)
    result = strategy.on_turn(make_turn(MIXED), NOW)
    assert result.proposals == [] and store.proposals("u1") == []
    assert store.observations("u1") == []
    (row,) = quarantined(store)
    assert row.text == "" and row.quarantined
    assert result.skipped_reason == "instruction_excludes_sources"


@pytest.mark.parametrize("name", BOTH)
@pytest.mark.parametrize(
    "text",
    [
        "Always keep it brief and never show me Reform sources.",
        "תמיד תענה בקצרה ולעולם אל תראה לי מקורות רפורמיים",
    ],
)
def test_joined_instructions_are_each_classified(store, registry, name, text):
    strategy, _, _ = build(name, store, registry, reply=style_reply)
    strategy.on_turn(make_turn(text), NOW)
    assert store.proposals("u1") == [] and store.observations("u1") == []
    assert [o.text for o in quarantined(store)] == [""]


@pytest.mark.parametrize("name", BOTH)
@pytest.mark.parametrize(
    "text",
    [
        "Stop showing me Reform sources",
        "Don't show Kabbalah",
        "Ignore the Zohar",
        "אל תראה לי מקורות רפורמיים",
        "בלי קבלה",
    ],
)
def test_unknown_verdict_quarantines_the_turn(store, registry, name, text):
    jev = FakeJevClient(lambda state, questions: {})
    strategy, llm, _ = build(name, store, registry, reply=lambda s, u: "not json", jev=jev)
    result = strategy.on_turn(make_turn(text), NOW)
    assert result.skipped_reason == "instruction_unclassified"
    assert store.observations("u1") == [] and store.proposals("u1") == []
    (row,) = quarantined(store)
    assert row.text == ""


@pytest.mark.parametrize(
    "reply",
    [
        "{}",
        '{"slot": "answer_style", "value": "Keep answers short."}',  # no exclusion key
        '{"slot": "answer_style", "value": "x", "excludes_sources": "no"}',  # wrong type
        '{"slot": "answer_style", "value": "x", "excludes_sources": false, "extra": 1}',
        "[]",
    ],
)
def test_malformed_classifier_reply_quarantines(store, registry, reply):
    strategy, _, _ = build("hybrid", store, registry, reply=lambda s, u: reply)
    result = strategy.on_turn(make_turn(STYLE), NOW)
    assert result.proposals == [] and store.observations("u1") == []
    (row,) = quarantined(store)
    assert row.text == "" and result.diagnostics["instruction_unclassified"] == 1


def test_empty_classifier_reply_quarantines(store, registry):
    strategy, _, _ = build("hybrid", store, registry, reply=lambda s, u: "{}")
    strategy.on_turn(make_turn("Never show me Reform sources."), NOW)
    assert store.observations("u1") == [] and len(quarantined(store)) == 1


def test_reply_missing_exclusion_key_quarantines(store, registry):
    reply = lambda s, u: json.dumps({"slot": "answer_style", "value": "Keep answers short."})  # noqa: E731
    strategy, _, _ = build("hybrid", store, registry, reply=reply)
    result = strategy.on_turn(make_turn(STYLE), NOW)
    assert result.proposals == [] and len(quarantined(store)) == 1


@pytest.mark.parametrize("drop", [("asks_to_exclude_sources",), ("is_lasting_style_preference",)])
def test_jev_missing_answer_is_unknown(store, registry, drop):
    strategy, _, _ = build("hybrid_jev", store, registry, jev=jev_for(drop=drop))
    result = strategy.on_turn(make_turn(STYLE), NOW)
    assert result.proposals == [] and store.proposals("u1") == []
    assert result.skipped_reason == "instruction_unclassified"
    assert store.observations("u1") == [] and len(quarantined(store)) == 1


def test_jev_uncertain_exclusion_score_is_unknown(store, registry):
    def respond(state, questions):
        return {
            "is_lasting_style_preference": JevAnswer("noul", noul=0.9),
            "asks_to_exclude_sources": JevAnswer("noul", noul=0.5),
        }

    strategy, _, _ = build("hybrid_jev", store, registry, jev=FakeJevClient(respond))
    result = strategy.on_turn(make_turn(STYLE), NOW)
    assert result.proposals == [] and len(quarantined(store)) == 1


@pytest.mark.parametrize("name", BOTH)
def test_clean_style_instruction_still_becomes_proposal(store, registry, name):
    strategy, _, _ = build(name, store, registry)
    result = strategy.on_turn(make_turn(STYLE), NOW)
    (proposal,) = result.proposals
    assert proposal.slot_key == "answer_style" and proposal.source_message_id == "m1"
    (row,) = store.observations("u1")
    assert row.text == STYLE and not row.quarantined and quarantined(store) == []
    assert "answer_style" not in store.get_profile("u1")


# -- windows --------------------------------------------------------------------------------------


def recording(calls):
    def respond(system, user):
        calls.append(user)
        return json.dumps({"updates": [], "clear": []})

    return respond


def test_rejected_proposal_text_never_reaches_batch(store, registry):
    seen = []

    def respond(system, user):
        if "Slots:" in system and "Recent prompts" in user:
            seen.append(user)
            return json.dumps(
                {
                    "updates": [
                        {
                            "slot": "answer_style",
                            "value": "Keep answers short.",
                            "evidence": [{"message_id": "m1", "quote": STYLE}],
                        }
                    ],
                    "clear": [],
                }
            )
        return style_reply(system, user)

    strategy, _, _ = make_strategy("hybrid", store, registry, respond)
    (proposal,) = strategy.on_turn(make_turn(STYLE), day(-1)).proposals
    assert store.resolve_proposal("u1", proposal.id, False, day(-1)).applied
    (row,) = quarantined(store)
    assert (row.message_id, row.text) == ("m1", "")
    strategy.on_turn(make_turn("Explain Berakhot 2a", message="m2", session="s2"), NOW)
    strategy.run_batch(NOW)
    assert seen and all("short" not in text for text in seen)
    assert "answer_style" not in store.get_profile("u1")


def test_pending_proposal_source_is_excluded_from_windows(store, registry):
    seen = []

    def respond(system, user):
        if "Recent prompts" in user:
            seen.append(user)
            return json.dumps({"updates": [], "clear": []})
        return style_reply(system, user)

    strategy, _, _ = make_strategy("hybrid", store, registry, respond)
    (proposal,) = strategy.on_turn(make_turn(STYLE, at=day(-2)), day(-2)).proposals
    strategy.on_turn(
        make_turn("Explain Berakhot 2a", message="m2", session="s2", at=day(-1)), day(-1)
    )
    strategy.run_batch(NOW)
    assert seen and all("short" not in text for text in seen)
    assert store.resolve_proposal("u1", proposal.id, True, NOW).applied
    seen.clear()
    strategy.on_turn(make_turn("Explain Shabbat 3a", message="m3", session="s3"), NOW)
    strategy.run_batch(NOW)
    assert seen and any("short" in text for text in seen)  # accepted: usable again


def test_superseded_proposal_source_is_quarantined(store, registry):
    strategy, _, _ = make_strategy("hybrid", store, registry, style_reply)
    strategy.on_turn(make_turn(STYLE, message="m1"), day(-1))
    strategy.on_turn(make_turn("Always answer in a formal tone.", message="m2"), NOW)
    assert [o.message_id for o in quarantined(store)] == ["m1"]  # replaced, never accepted


@pytest.mark.parametrize("name", ["realtime", "nightly"])
def test_strategies_without_a_classifier_quarantine_instruction_turns(store, registry, name):
    calls = []
    strategy, llm, _ = make_strategy(name, store, registry, recording(calls))
    strategy.on_turn(make_turn("Never show me Reform sources.", at=day(-1)), day(-1))
    assert store.observations("u1") == [] and llm.calls == []
    (row,) = quarantined(store)
    assert row.text == ""
    strategy.on_turn(make_turn("Explain Berakhot 2a", message="m2", session="s2"), NOW)
    strategy.run_batch(NOW)
    assert all("Reform" not in c["user"] for c in llm.calls)


def test_realtime_quarantines_instruction_turns(store, registry):
    strategy, llm, _ = make_strategy("realtime", store, registry, recording([]))
    result = strategy.on_turn(make_turn("Always hide the Zohar from me."), NOW)
    assert llm.calls == [] and result.skipped_reason == "instruction_quarantined"
    assert len(quarantined(store)) == 1


def test_nightly_quarantines_instruction_turns(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry, consolidation())
    strategy.on_turn(make_turn("Always hide the Zohar from me."), NOW)
    strategy.run_batch(NOW)
    assert llm.calls == [] and len(quarantined(store)) == 1
    assert store.users_with_pending() == []


# -- envelopes ------------------------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["{}", '{"updates": "invalid"}', '{"updates": [], "clear": 3}'])
def test_empty_consolidation_envelope_is_a_failure(store, registry, reply):
    strategy, _, _ = make_strategy("nightly", store, registry, lambda s, u: reply)
    strategy.on_turn(make_turn("Explain Berakhot 2a", at=day(-1)), day(-1))
    result = strategy.run_batch(NOW)
    assert result.diagnostics["invalid_envelope"] == 1 and result.errors == 1
    assert result.users_processed == 0 and store.users_with_pending() == ["u1"]


def test_valid_empty_consolidation_is_consumed(store, registry):
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation())
    strategy.on_turn(make_turn("Explain Berakhot 2a", at=day(-1)), day(-1))
    result = strategy.run_batch(NOW)
    assert result.users_processed == 1 and store.users_with_pending() == []


def test_jev_overflow_becomes_jev_error():
    data = {"answers": {"k": {"type": "noul", "noul": 10**400}}}
    with pytest.raises(JevError):
        parse_response(data, 0, "m")
    with pytest.raises(JevError):
        parse_response(
            {"answers": {"k": {"type": "choice", "choice": "a", "confidence": 10**400}}}, 0, "m"
        )


# -- the store ------------------------------------------------------------------------------------


def test_store_hides_quarantined_rows_unless_asked(store):
    from chat.V2.personalization.types import Observation

    store.add_observation(Observation("u1", "s1", "m1", "", NOW, {}, NOW, quarantined=True))
    store.add_observation(Observation("u1", "s1", "m2", "hello", NOW, {}))
    assert [o.message_id for o in store.observations("u1")] == ["m2"]
    assert [o.message_id for o in store.observations("u1", include_quarantined=True)] == [
        "m1",
        "m2",
    ]


def test_rejecting_a_proposal_quarantines_its_source_in_the_same_write(store):
    from chat.V2.personalization.types import Observation

    store.add_observation(Observation("u1", "s1", "m1", STYLE, NOW, {"language": "en"}))
    proposal = store.add_proposal(
        "u1", "answer_style", "Keep answers short.", STYLE, NOW, source_message_id="m1"
    ).proposal
    revision = store.revision("u1")
    assert store.resolve_proposal("u1", proposal.id, False, NOW).applied
    assert store.revision("u1") == revision + 1
    (row,) = store.observations("u1", include_quarantined=True)
    assert row.quarantined and row.text == "" and row.signals == {}
    assert store.observations("u1") == []


# -- only cleared clauses are released -----------------------------------------------------------

LEAKS = [
    "Always keep it brief. Keep away from Reform sources",
    "תמיד תקצר. הסתר מקורות רפורמיים",
    "I'd rather not see Reform sources. Always be brief.",
    "Please keep answers short and leave out Reform commentaries",
]
HIDDEN = ("Reform", "רפורמ")


def everything_sent(store, strategy, llm, jev):
    """All text that reached a store row, a proposal, a model call or a Jev request."""
    strategy.run_batch(NOW)
    parts = [o.text for o in store.observations("u1", include_quarantined=True)]
    parts += [p.value if isinstance(p.value, str) else "" for p in store.proposals("u1")]
    parts += [p.quote for p in store.proposals("u1")]
    parts += [c["user"] for c in llm.calls if "Recent prompts" in c["user"]]
    if jev is not None:
        # the classification request must carry the clause it judges; the others never may
        parts += [json.dumps(s, ensure_ascii=False) for s, _ in jev.calls if "message" not in s]
        parts += [o.text for o in strategy.window_observations("u1", NOW)]
    return " ".join(parts)


@pytest.mark.parametrize("name", BOTH)
@pytest.mark.parametrize("text", LEAKS)
def test_no_hiding_text_reaches_a_window_proposal_or_request(store, registry, name, text):
    jev = jev_for()
    strategy, llm, _ = make_strategy(
        name, store, registry, style_reply_or_plan, jev=jev if name == "hybrid_jev" else None
    )
    strategy.on_turn(make_turn(text), NOW)
    strategy.on_turn(make_turn("Explain Berakhot 2a", message="m2", session="s2"), NOW)
    sent = everything_sent(store, strategy, llm, jev if name == "hybrid_jev" else None)
    assert not any(word in sent for word in HIDDEN)


def style_reply_or_plan(system, user):
    if "Recent prompts" in user:
        return json.dumps({"updates": [], "clear": []})
    return style_reply(system, user)


@pytest.mark.parametrize("name", BOTH)
def test_an_undetected_sentence_is_not_released_with_the_cleared_clause(store, registry, name):
    text = "Always keep it brief. Please steer clear of Reform sources."
    assert find_standing_instructions(text) == ["Always keep it brief."]
    jev = jev_for()
    strategy, llm, _ = make_strategy(
        name, store, registry, style_reply_or_plan, jev=jev if name == "hybrid_jev" else None
    )
    result = strategy.on_turn(make_turn(text), NOW)
    assert result.skipped_reason != "instruction_excludes_sources"
    (row,) = store.observations("u1")
    assert row.text == "Always keep it brief." and row.signals["language"] == "en"
    strategy.on_turn(make_turn("Explain Berakhot 2a", message="m2", session="s2"), NOW)
    sent = everything_sent(store, strategy, llm, jev if name == "hybrid_jev" else None)
    assert "Reform" not in sent


@pytest.mark.parametrize("name", BOTH)
def test_more_than_five_clauses_are_quarantined(store, registry, name):
    def many(n):
        return " ".join(f"Always be brief number {i}." for i in range(n))

    strategy, _, _ = build(name, store, registry)
    strategy.on_turn(make_turn(many(5), message="m1"), NOW)
    assert [o.message_id for o in store.observations("u1")] == ["m1"]
    result = strategy.on_turn(make_turn(many(6), message="m2"), NOW)
    assert result.skipped_reason == "instruction_unclassified"
    assert [o.message_id for o in quarantined(store)] == ["m2"]


@pytest.mark.parametrize("error", [RecursionError, OSError])
def test_jev_body_read_errors_become_jev_error(monkeypatch, error):
    from unittest.mock import patch

    from chat.V2.personalization.jev import HttpJevClient

    monkeypatch.setenv("JEV_API_KEY", "k")
    client = HttpJevClient()
    import chat.V2.personalization.jev as jev_module

    monkeypatch.setenv(jev_module.KEY_ENV, "k")
    with (
        patch("chat.V2.personalization.jev.json.load", side_effect=error),
        patch("chat.V2.personalization.jev.urllib.request.urlopen"),
    ):
        with pytest.raises(JevError):
            client.ask({}, {})
