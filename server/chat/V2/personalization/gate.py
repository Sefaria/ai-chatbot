"""The one server-side gate between a chat request and the personalization feature.

`resolve` is pure: it reads the settings, the environment and the persistent user id, and
returns what this request may do. A request field can only ask; it is honoured when the
override flag is on, `ENVIRONMENT` names a non-production deployment, and the user is on the
allowlist. `context.isStaff` and `context.labs` are client-supplied and never open anything.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .registry import SlotRegistry

logger = logging.getLogger(__name__)

MODES = frozenset({"off", "realtime", "nightly", "hybrid", "hybrid_jev", "explicit"})
INJECTIONS = frozenset({"always", "router_gate", "tool"})

# Values of the ENVIRONMENT variable on user-facing deployments: prod sets `prod` and beta sets
# `beta` (wiki runbooks/verify-first-party-user-agent-locally.md, from
# AppliedAI-Infrastructure#170). `production` is the spelling sefaria-eval uses.
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "beta"})

MAX_STORE_USER_ID = 100  # the user id column
SUBJECT_PATTERN = re.compile(r"[a-z0-9-]{1,40}")
MAX_FIXTURE_SLOTS = 20


@lru_cache(maxsize=1)
def _registry() -> SlotRegistry:
    return SlotRegistry.load()


@dataclass(frozen=True)
class RequestedPersonalization:
    """What the request body asked for; nothing here is trusted."""

    mode: str | None = None
    injection: str | None = None
    profile: Any = None
    subject: str | None = None

    @classmethod
    def from_context(cls, context: dict | None) -> RequestedPersonalization:
        context = context or {}
        return cls(
            mode=context.get("personalizationMode"),
            injection=context.get("personalizationInjection"),
            profile=context.get("personalizationProfile"),
            subject=context.get("personalizationSubject"),
        )

    @property
    def any_sent(self) -> bool:
        return any(v is not None for v in (self.mode, self.injection, self.profile, self.subject))


@dataclass(frozen=True)
class PersonalizationConfig:
    mode: str
    injection: str
    source: str  # "off" | "setting" | "override"
    override_refused: bool
    fixture_profile: dict | None
    store_user_id: str


def _allowlist(raw: Any) -> frozenset[str]:
    items = raw.split(",") if isinstance(raw, str) else (raw or [])
    return frozenset(str(i).strip() for i in items if str(i).strip())


def _server_injection(settings) -> str:
    value = getattr(settings, "PERSONALIZATION_INJECTION", "always")
    return value if value in INJECTIONS else "always"


def _non_production_environment() -> bool:
    """True only when ENVIRONMENT names a deployment that is not production; unset is refused."""
    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    return bool(environment) and environment not in PRODUCTION_ENVIRONMENTS


def _production_environment() -> bool:
    return (os.environ.get("ENVIRONMENT") or "").strip().lower() in PRODUCTION_ENVIRONMENTS


def override_allowed(settings, persistent_user_id: str) -> bool:
    if getattr(settings, "PERSONALIZATION_OVERRIDE_ENABLED", False) is not True:
        return False
    if not _non_production_environment():
        return False
    allowed = _allowlist(getattr(settings, "PERSONALIZATION_OVERRIDE_USER_IDS", ""))
    return bool(persistent_user_id) and persistent_user_id in allowed


def _valid_fixture(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or len(raw) > MAX_FIXTURE_SLOTS:
        return None
    registry = _registry()
    kept = {}
    for key, value in raw.items():
        validated = registry.validate(key, value) if isinstance(key, str) else None
        if validated is not None:
            kept[key] = validated
    return kept


def resolve(settings, persistent_user_id: str, requested: RequestedPersonalization):
    server_mode = getattr(settings, "PERSONALIZATION_MODE", "off")
    if server_mode not in MODES:
        server_mode = "off"
    server = PersonalizationConfig(
        mode=server_mode,
        injection=_server_injection(settings),
        source="off" if server_mode == "off" else "setting",
        override_refused=False,
        fixture_profile=None,
        store_user_id=persistent_user_id,
    )
    if not requested.any_sent:
        return server
    refused = PersonalizationConfig(**{**server.__dict__, "override_refused": True})
    if not override_allowed(settings, persistent_user_id):
        return refused

    mode = server_mode if requested.mode is None else requested.mode
    injection = server.injection if requested.injection is None else requested.injection
    if mode not in MODES or injection not in INJECTIONS:
        return refused
    store_user_id = persistent_user_id
    if requested.subject is not None:
        subject = requested.subject
        if not isinstance(subject, str) or not SUBJECT_PATTERN.fullmatch(subject):
            return refused
        store_user_id = f"{persistent_user_id}:eval:{subject}"
        if len(store_user_id) > MAX_STORE_USER_ID:
            return refused
    fixture = None
    if requested.profile is not None:
        fixture = _valid_fixture(requested.profile)
        if fixture is None:
            return refused
    return PersonalizationConfig(
        mode=mode,
        injection=injection,
        source="off" if mode == "off" else "override",
        override_refused=False,
        fixture_profile=fixture,
        store_user_id=store_user_id,
    )


def memory_enabled(settings) -> bool:
    """Whether stored profiles can matter at all (the claim request carries no override)."""
    if getattr(settings, "PERSONALIZATION_MODE", "off") != "off":
        return True
    # The override flag only counts on a non-production deployment, as in `override_allowed`.
    return (
        getattr(settings, "PERSONALIZATION_OVERRIDE_ENABLED", False) is True
        and _non_production_environment()
    )


def warn_if_mode_set_outside_production(settings) -> None:
    """A global PERSONALIZATION_MODE stores memory for every user. On a preview, use the override
    and the allowlist instead."""
    mode = getattr(settings, "PERSONALIZATION_MODE", "off")
    if mode != "off" and mode in MODES and not _production_environment():
        logger.warning(
            "PERSONALIZATION_MODE=%s is set on a non-production deployment (ENVIRONMENT=%r): "
            "it stores memory for every user. Never set it on a PR preview; use "
            "PERSONALIZATION_OVERRIDE_ENABLED with PERSONALIZATION_OVERRIDE_USER_IDS.",
            mode,
            os.environ.get("ENVIRONMENT", ""),
        )


def trace_metadata(config: PersonalizationConfig, profile, model: str | None = None) -> dict:
    """Root-span metadata for this turn. `profile` is a ProfileContext or None."""
    if config.source == "off":
        meta: dict[str, Any] = {"personalized": False, "personalization_source": "off"}
        if config.override_refused:
            meta["personalization_override_refused"] = True
        return meta
    meta = {
        "personalized": bool(profile and profile.text),
        "personalization_mode": config.mode,
        "personalization_injection": config.injection,
        "personalization_source": config.source,
        "personalization_override_refused": config.override_refused,
        "slots_used": list(profile.slots_used) if profile else [],
        "slot_set_version": profile.slot_set_version if profile else None,
        "profile_revision": profile.revision if profile else None,
        "personalization_model": None if config.mode == "explicit" else model,
    }
    # Profile text is personal data: it goes on a trace only for an allowlisted override user.
    meta["profile_text"] = profile.text if profile and config.source == "override" else None
    return meta
