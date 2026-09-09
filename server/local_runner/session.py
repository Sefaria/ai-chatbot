"""The pairing code for this daemon process.

Held in memory, never on disk: a code is only valid for the run of the daemon
that printed it, so a stale code from a previous run can never pair.

Three limits make presenting the code meaningful proof rather than a guessable
secret with unlimited retries:

* **Single use.** A successful pairing consumes it.
* **Time limited.** It expires ``TTL_SECONDS`` after being issued.
* **Attempt limited.** ``MAX_ATTEMPTS`` wrong guesses close pairing entirely.

Once pairing is closed, the user restarts the daemon to get a new code. That is
the intended cost: re-pairing should require terminal access again.
"""

from __future__ import annotations

import secrets
import threading
import time

TTL_SECONDS = 600
MAX_ATTEMPTS = 5

_lock = threading.Lock()
_code: str | None = None
_issued_at: float = 0.0
_failed_attempts: int = 0


def set_code(code: str | None) -> None:
    """Issue a code, starting its TTL and resetting the attempt counter."""
    global _code, _issued_at, _failed_attempts
    with _lock:
        _code = code
        _issued_at = time.monotonic()
        _failed_attempts = 0


def _expired() -> bool:
    return time.monotonic() - _issued_at > TTL_SECONDS


def pairing_open() -> bool:
    """Whether this daemon will still accept a pairing attempt."""
    with _lock:
        return _code is not None and not _expired()


def verify(presented: str | None) -> bool:
    """Check a presented code without spending it.

    Separate from :func:`spend` so a recoverable failure later in pairing — the
    server being unreachable, say — does not burn the code and force a restart.
    A wrong code still counts an attempt, so guessing is bounded either way.
    """
    global _code, _failed_attempts
    with _lock:
        if _code is None or _expired():
            return False

        if presented and secrets.compare_digest(presented, _code):
            return True

        _failed_attempts += 1
        if _failed_attempts >= MAX_ATTEMPTS:
            _code = None
        return False


def spend() -> None:
    """Discard the code after a pairing that actually succeeded."""
    global _code
    with _lock:
        _code = None
