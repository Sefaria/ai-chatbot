"""Plain data types shared by the personalization package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

SlotValueType = Literal["enum", "list", "text"]
SlotStatus = Literal["inferred", "stated"]
ProposalStatus = Literal["pending", "accepted", "rejected"]
Value = str | list[str]

MAX_EVIDENCE = 3


@dataclass(frozen=True)
class SlotDef:
    key: str
    label: str
    value_type: SlotValueType
    instruction: str
    allowed: tuple[str, ...] = ()
    max_items: int | None = None
    max_chars: int | None = None
    shelf_life_days: int | None = None
    min_sessions: int = 1
    deterministic: bool = False
    impacts: str = ""


@dataclass
class Evidence:
    quote: str
    session_id: str
    message_id: str
    at: datetime
    item: str | None = None  # for a list slot: the item this prompt supports (None: all of them)


@dataclass
class SlotValue:
    slot_key: str
    value: Value
    status: SlotStatus
    evidence: list[Evidence]
    updated_at: datetime
    expires_at: datetime | None = None
    slot_set_version: str = "v1"


class InstructionVerdict(StrEnum):
    """What a classifier decided about one standing instruction in a learner's message.

    Only SAFE_STYLE keeps the turn's text; EXCLUSION and UNKNOWN quarantine it."""

    SAFE_STYLE = "safe_style"
    EXCLUSION = "exclusion"
    UNKNOWN = "unknown"


@dataclass
class Observation:
    user_id: str
    session_id: str
    message_id: str
    text: str
    at: datetime
    signals: dict[str, Any] = field(default_factory=dict)
    consumed_at: datetime | None = None
    id: int | str | None = None  # set by the store
    quarantined: bool = False  # holds a standing instruction not cleared as safe: text is empty


@dataclass
class Proposal:
    id: str
    user_id: str
    slot_key: str
    value: Value
    quote: str
    status: ProposalStatus
    created_at: datetime
    source_message_id: str = ""  # the observation (same user, this message id) it came from


@dataclass
class Turn:
    user_id: str
    session_id: str
    message_id: str
    user_message: str
    assistant_response: str
    at: datetime


@dataclass
class ProfileSettingsData:
    personalize_enabled: bool = True
    store_enabled: bool = True


WriteReason = Literal[
    "stale_revision", "store_disabled", "duplicate", "stated_wins", "pending_proposal", "sensitive"
]


@dataclass(frozen=True)
class WriteResult:
    """What a store write did. `revision` is the user's revision after the call.

    It has no truth value on purpose: write `result.applied`, never `if result`.
    `proposal` is set by `add_proposal` and `resolve_proposal`.
    """

    applied: bool
    reason: WriteReason | None
    revision: int
    proposal: Proposal | None = None

    def __bool__(self) -> bool:
        raise TypeError("WriteResult has no truth value; use .applied")
