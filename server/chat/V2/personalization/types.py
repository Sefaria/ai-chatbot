"""Plain data types shared by the personalization package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass
class SlotValue:
    slot_key: str
    value: Value
    status: SlotStatus
    evidence: list[Evidence]
    updated_at: datetime
    expires_at: datetime | None = None
    slot_set_version: str = "v1"


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


@dataclass
class Proposal:
    id: str
    user_id: str
    slot_key: str
    value: Value
    quote: str
    status: ProposalStatus
    created_at: datetime


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
