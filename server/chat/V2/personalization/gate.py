"""The one server-side gate for personalization: who may use it, and where.

Everything stays behind the lab-deployment flags. It is on only when `PERSONALIZATION_OVERRIDE_ENABLED`
and `PERSONALIZATION_OVERRIDE_ALLOW_ALL` are set, `ENVIRONMENT` names a non-production deployment
and the user is signed in. Production and beta are refused whatever the flags say.
"""

from __future__ import annotations

import os

from django.conf import settings

# Values of the ENVIRONMENT variable on user-facing deployments: prod sets `prod` and beta sets
# `beta` (wiki runbooks/verify-first-party-user-agent-locally.md, from
# AppliedAI-Infrastructure#170). `production` is the spelling sefaria-eval uses.
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "beta"})

ANON_PREFIX = "anon:"


def _non_production_environment() -> bool:
    """True only when ENVIRONMENT names a deployment that is not production; unset is refused."""
    environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    return bool(environment) and environment not in PRODUCTION_ENVIRONMENTS


def enabled_for(user_id: str) -> bool:
    """Whether personalization (settings, Profile, Instructions) is on for this user id."""
    if not user_id or user_id.startswith(ANON_PREFIX):
        return False
    if getattr(settings, "PERSONALIZATION_OVERRIDE_ENABLED", False) is not True:
        return False
    if not _non_production_environment():
        return False
    return os.environ.get("PERSONALIZATION_OVERRIDE_ALLOW_ALL") == "true"


def lab_enabled() -> bool:
    """Whether the lab-only controls (testing switches, run now, health) are available."""
    return os.environ.get("PERSONALIZATION_LAB_SEED") == "true" and _non_production_environment()
