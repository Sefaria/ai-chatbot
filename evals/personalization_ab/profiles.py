"""Build one user's Profile two ways from the same ten turns, with the real profile writer.

A: one turn at a time (a session-window run after each turn).
B: all ten at once (one dream-window run over an empty Profile).

Must run inside a Django process with a database (the tests set this up). Quota is switched off
here only, by patching `profile_job._take_quota` for the length of a build; product code is
untouched. Real user text must never be committed; see README.md.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from .select import Turn

N_TURNS = 10


@dataclass
class ProfileBuild:
    text: str
    revisions: int
    dropped: list = field(default_factory=list)


@contextmanager
def quota_off():
    from chat.V2.personalization import profile_job

    with patch.object(profile_job, "_take_quota", lambda *a, **k: None):
        yield


def _start_empty(user_id: str) -> None:
    from chat.V2.personalization import profile_store as store

    store.set_enabled(user_id, "profile", True)
    store.clear_profile(
        user_id
    )  # no sections, and later runs read only what is added now


def _add_turn(t: Turn) -> None:
    from chat.models import ChatMessage, ChatSession

    ChatSession.objects.get_or_create(
        session_id=t.session_id, defaults={"user_id": t.user_id}
    )
    key = f"ab-{t.user_id}-{t.session_id}-{t.turn_number}-{ChatMessage.objects.count()}"
    user_msg = ChatMessage.objects.create(
        message_id=f"{key}-u",
        session_id=t.session_id,
        user_id=t.user_id,
        turn_id=key,
        role=ChatMessage.Role.USER,
        content=t.prompt,
        processing_state=ChatMessage.ProcessingState.COMPLETED,
    )
    answer = ChatMessage.objects.create(
        message_id=f"{key}-a",
        session_id=t.session_id,
        user_id=t.user_id,
        turn_id=key,
        role=ChatMessage.Role.ASSISTANT,
        content=t.original_answer,
        status=ChatMessage.Status.SUCCESS,
    )
    ChatMessage.objects.filter(pk=user_msg.pk).update(response_message=answer)


def _result(user_id: str, dropped: list) -> ProfileBuild:
    from chat.V2.personalization import profile_store as store

    return ProfileBuild(
        text=store.render_profile(store.get_sections(user_id)),
        revisions=store.get_settings(user_id).profile_revision,
        dropped=dropped,
    )


def build_profile_a(user_id: str, turns: list[Turn], llm, jev) -> ProfileBuild:
    from chat.V2.personalization.profile_job import run_profile_job

    _start_empty(user_id)
    base = _result(user_id, []).revisions
    dropped: list = []
    with quota_off():
        for t in turns[:N_TURNS]:
            _add_turn(t)
            result = run_profile_job(
                user_id, "ab_test", window="session", llm=llm, jev=jev
            )
            if result.status != "applied":
                dropped.append((t.turn_number, result.reason or result.status))
    build = _result(user_id, dropped)
    build.revisions -= base  # revisions this build made, not the clear that began it
    return build


def build_profile_b(user_id: str, turns: list[Turn], llm, jev) -> ProfileBuild:
    from chat.models import ProfileWriterRun
    from chat.V2.personalization.profile_job import run_profile_job

    _start_empty(user_id)
    base = _result(user_id, []).revisions
    for t in turns[:N_TURNS]:
        _add_turn(t)
    with quota_off():
        result = run_profile_job(user_id, "ab_test", window="dream", llm=llm, jev=jev)
    counts: dict = {}
    if result.run_id:
        row = ProfileWriterRun.objects.filter(pk=result.run_id).first()
        try:
            summary = json.loads(row.extraction) if row and row.extraction else {}
        except ValueError:
            summary = {}
        counts = {
            k: summary[k] for k in ("prompts_read", "screened_out") if k in summary
        }
        if "prompts_read" in counts and "screened_out" in counts:
            counts["kept_count"] = counts["prompts_read"] - counts["screened_out"]
    dropped = [{"status": result.status, "reason": result.reason, **counts}]
    build = _result(user_id, dropped)
    build.revisions -= base
    return build
