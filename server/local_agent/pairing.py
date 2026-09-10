"""Pairing: how a browser tab earns the right to drive this daemon.

The daemon prints a short code at startup. A page can only present that code if
whoever is driving it can see this machine's terminal — which is the proof we
want, since the daemon runs as the user and spends their Claude subscription.

Pairing therefore needs no server round trip. What it produces is a long random
runner token the page stores and sends on every later request.

The record lives in a 0600 file in the runner's data directory rather than the
database, so it survives ``migrate`` and never mixes with conversation data.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from .paths import data_dir

RECORD_NAME = "agent-pairing.json"

# Six digits, shown to a human and typed back within one session. The entropy that
# matters is the runner token; this only has to resist a blind guess during the
# short window a page is being paired.
CODE_DIGITS = 6
TOKEN_BYTES = 32


@dataclass(frozen=True)
class Pairing:
    """A paired browser, and the Sefaria identity it authenticated as."""

    runner_token: str
    user_id: str
    sefaria_user_id: str | None
    paired_at: str
    # The browser's current userId token. Stored because the daemon authenticates
    # to our server as the user when proxying prompts and the guardrail, and
    # refreshed on each turn because these tokens expire.
    encrypted_user_token: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def new_code() -> str:
    """Generate the code shown in the terminal."""
    return "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def record_path():
    return data_dir() / RECORD_NAME


def save(pairing: Pairing) -> None:
    """Write the pairing record with owner-only permissions."""
    path = record_path()
    path.write_text(json.dumps(pairing.to_json(), indent=2))
    os.chmod(path, 0o600)


def load() -> Pairing | None:
    """Return the stored pairing, or None if this daemon is unpaired."""
    path = record_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return Pairing(**data)
    except (ValueError, TypeError):
        # A corrupt record means unpaired, not a crash: the user can re-pair.
        return None


def clear() -> None:
    record_path().unlink(missing_ok=True)


def pair(
    *,
    user_id: str,
    sefaria_user_id: str | None,
    encrypted_user_token: str | None = None,
) -> Pairing:
    """Create and persist a new pairing, replacing any existing one."""
    pairing = Pairing(
        runner_token=new_token(),
        user_id=user_id,
        sefaria_user_id=sefaria_user_id,
        paired_at=datetime.now(UTC).isoformat(),
        encrypted_user_token=encrypted_user_token,
    )
    save(pairing)
    return pairing


def refresh_user_token(encrypted_user_token: str | None) -> None:
    """Store a newer userId token, if the browser sent one.

    These tokens expire, so the one captured at pairing goes stale. Writes only
    when the value actually changed, so this costs nothing on the common path.
    """
    if not encrypted_user_token:
        return
    record = load()
    if record is None or record.encrypted_user_token == encrypted_user_token:
        return
    save(replace(record, encrypted_user_token=encrypted_user_token))


def token_matches(presented: str | None) -> bool:
    """Constant-time check of a presented runner token against the stored one."""
    if not presented:
        return False
    pairing = load()
    if pairing is None:
        return False
    return secrets.compare_digest(presented, pairing.runner_token)
