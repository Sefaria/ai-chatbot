"""All reads and writes of a user's Instructions, Profile sections and writer runs.

Every function takes the user id and touches only that user's rows. The id comes from the signed
token and nothing else (see `settings_api`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from chat.models import (
    ChatMessage,
    ChatSession,
    PersonalizationSettings,
    ProfileSection,
    ProfileWriterRun,
)

MAX_INSTRUCTIONS = PersonalizationSettings.MAX_INSTRUCTIONS
TARGETS = ("instructions", "profile")


@dataclass
class SectionData:
    key: str | None
    kind: str
    heading: str
    body: str
    origin: str
    last_supported_at: datetime
    position: int


@dataclass
class SettingsData:
    user_id: str
    instructions_text: str = ""
    instructions_enabled: bool = True
    instructions_updated_at: datetime | None = None
    profile_enabled: bool = True
    profile_updated_at: datetime | None = None
    profile_updated_by: str | None = None
    profile_revision: int = 0
    processed_through: datetime | None = None
    cleared_at: datetime | None = None
    notice_pending: bool = False
    show_plain_response: bool = False
    update_every_message: bool = False


@dataclass
class PromptRow:
    message_id: str
    session_id: str
    text: str
    server_timestamp: datetime


_SETTINGS_FIELDS = [f for f in SettingsData.__dataclass_fields__ if f != "user_id"]


def _settings_data(row: PersonalizationSettings) -> SettingsData:
    return SettingsData(user_id=row.user_id, **{f: getattr(row, f) for f in _SETTINGS_FIELDS})


def _locked_row(user_id: str) -> PersonalizationSettings:
    """The user's settings row, created if missing, locked until the transaction ends."""
    PersonalizationSettings.objects.get_or_create(user_id=user_id)
    return PersonalizationSettings.objects.select_for_update().get(user_id=user_id)


def get_settings(user_id: str) -> SettingsData:
    """The user's settings; defaults when they have none yet. Reading creates nothing."""
    row = PersonalizationSettings.objects.filter(user_id=user_id).first()
    return _settings_data(row) if row else SettingsData(user_id=user_id)


def set_instructions(user_id: str, text: str) -> SettingsData:
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError("too_long")
    with transaction.atomic():
        row = _locked_row(user_id)
        row.instructions_text = text
        row.instructions_updated_at = timezone.now()
        row.save(update_fields=["instructions_text", "instructions_updated_at"])
        return _settings_data(row)


def set_enabled(user_id: str, target: str, enabled: bool) -> SettingsData:
    if target not in TARGETS:
        raise ValueError("bad_target")
    with transaction.atomic():
        row = _locked_row(user_id)
        field = f"{target}_enabled"
        setattr(row, field, bool(enabled))
        row.save(update_fields=[field])
        return _settings_data(row)


def set_testing(
    user_id: str,
    *,
    show_plain_response: bool | None = None,
    update_every_message: bool | None = None,
) -> SettingsData:
    with transaction.atomic():
        row = _locked_row(user_id)
        changed = []
        if show_plain_response is not None:
            row.show_plain_response = bool(show_plain_response)
            changed.append("show_plain_response")
        if update_every_message is not None:
            row.update_every_message = bool(update_every_message)
            changed.append("update_every_message")
        if changed:
            row.save(update_fields=changed)
        return _settings_data(row)


def get_sections(user_id: str) -> list[SectionData]:
    return [
        SectionData(
            key=r.key,
            kind=r.kind,
            heading=r.heading,
            body=r.body,
            origin=r.origin,
            last_supported_at=r.last_supported_at,
            position=r.position,
        )
        for r in ProfileSection.objects.filter(user_id=user_id).order_by("position", "id")
    ]


def render_profile(sections: list[SectionData]) -> str:
    """`# Heading` and body blocks, in order, joined by a blank line."""
    return "\n\n".join(f"# {s.heading}\n{s.body}" for s in sections)


def replace_sections(
    user_id: str,
    base_revision: int,
    sections: list[SectionData],
    *,
    updated_by: str,
    processed_through: datetime | None,
    set_notice: bool,
) -> bool:
    """Replace the user's sections in one transaction. False, with nothing changed, when the
    Profile moved since `base_revision` was read."""
    with transaction.atomic():
        row = _locked_row(user_id)
        if row.profile_revision != base_revision:
            return False
        ProfileSection.objects.filter(user_id=user_id).delete()
        ProfileSection.objects.bulk_create(
            ProfileSection(
                user_id=user_id,
                kind=s.kind,
                key=s.key,
                heading=s.heading,
                body=s.body,
                origin=s.origin,
                last_supported_at=s.last_supported_at,
                position=i,
            )
            for i, s in enumerate(sections)
        )
        row.profile_revision += 1
        row.profile_updated_at = timezone.now()
        row.profile_updated_by = updated_by
        changed = ["profile_revision", "profile_updated_at", "profile_updated_by"]
        if processed_through is not None:
            row.processed_through = processed_through
            changed.append("processed_through")
        if set_notice:
            row.notice_pending = True
            changed.append("notice_pending")
        row.save(update_fields=changed)
        return True


def clear_profile(user_id: str) -> SettingsData:
    """Delete the sections and start over: later runs read only activity after this moment. The
    revision moves too, so a run already in flight cannot write the old Profile back."""
    with transaction.atomic():
        row = _locked_row(user_id)
        now = timezone.now()
        ProfileSection.objects.filter(user_id=user_id).delete()
        ProfileWriterRun.objects.filter(user_id=user_id).update(
            profile_before="", profile_after="", extraction=""
        )
        row.cleared_at = now
        row.processed_through = now
        row.profile_revision += 1
        row.profile_updated_at = now
        row.profile_updated_by = None
        row.notice_pending = False
        row.save(
            update_fields=[
                "cleared_at",
                "processed_through",
                "profile_revision",
                "profile_updated_at",
                "profile_updated_by",
                "notice_pending",
            ]
        )
        return _settings_data(row)


def take_notice(user_id: str) -> bool:
    """True once per pending notice."""
    return (
        PersonalizationSettings.objects.filter(user_id=user_id, notice_pending=True).update(
            notice_pending=False
        )
        > 0
    )


def unprocessed_user_prompts(
    user_id: str, since: datetime | None, until: datetime | None = None
) -> list[PromptRow]:
    """The user's own prompts after `since` (and up to `until`), oldest first.

    Never earlier than the last Clear. Left out: prompts of hidden sessions (the plain arm of the
    dual display is stored with `is_deleted=True`) and of sessions the user deleted, and turns that
    failed, were cancelled or were refused by the guardrail.
    """
    cleared_at = (
        PersonalizationSettings.objects.filter(user_id=user_id)
        .values_list("cleared_at", flat=True)
        .first()
    )
    floor = max((t for t in (since, cleared_at) if t is not None), default=None)
    rows = ChatMessage.objects.filter(user_id=user_id, role=ChatMessage.Role.USER)
    if floor is not None:
        rows = rows.filter(server_timestamp__gt=floor)
    if until is not None:
        rows = rows.filter(server_timestamp__lte=until)
    hidden_sessions = ChatSession.objects.filter(user_id=user_id, is_deleted=True).values(
        "session_id"
    )
    unanswered_turns = (
        ChatMessage.objects.filter(
            user_id=user_id,
            role=ChatMessage.Role.ASSISTANT,
            status__in=[ChatMessage.Status.FAILED, ChatMessage.Status.REFUSED],
        )
        .exclude(turn_id="")
        .values("turn_id")
    )
    rows = (
        rows.exclude(session_id__in=hidden_sessions)
        .exclude(
            processing_state__in=[
                ChatMessage.ProcessingState.FAILED,
                ChatMessage.ProcessingState.CANCELLED,
                ChatMessage.ProcessingState.CANCELLING,
            ]
        )
        .exclude(
            response_message__status__in=[ChatMessage.Status.FAILED, ChatMessage.Status.REFUSED]
        )
        .exclude(turn_id__in=unanswered_turns)
        .order_by("server_timestamp", "id")
    )
    return [
        PromptRow(r.message_id, r.session_id, r.content, r.server_timestamp)
        for r in rows.only("message_id", "session_id", "content", "server_timestamp")
    ]


def record_run(**fields) -> int:
    """Store one job run and return its id. `started_at` defaults to now."""
    fields.setdefault("started_at", timezone.now())
    return ProfileWriterRun.objects.create(**fields).id


def _watermarks():
    return PersonalizationSettings.objects.filter(user_id=OuterRef("user_id")).values(
        "processed_through"
    )[:1]


def users_with_new_prompts() -> list[str]:
    """Users with at least one user prompt after their watermark (cheap; the caller screens)."""
    rows = (
        ChatMessage.objects.filter(role=ChatMessage.Role.USER)
        .exclude(user_id="")
        .annotate(mark=Subquery(_watermarks()))
        .filter(Q(mark__isnull=True) | Q(server_timestamp__gt=F("mark")))
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    return sorted(set(rows))


def users_active_since(since: datetime) -> list[str]:
    """Users with a user prompt after `since`."""
    rows = (
        ChatMessage.objects.filter(role=ChatMessage.Role.USER, server_timestamp__gt=since)
        .exclude(user_id="")
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    return sorted(set(rows))
