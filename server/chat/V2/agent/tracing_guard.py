"""Thread-local guard to suppress Braintrust span creation.

The braintrust SDK wrapper (`setup_claude_agent_sdk`) patches the Claude Agent
SDK globally — once per process, irreversible.  To prevent load-test requests
from generating spans we intercept `braintrust.logger.start_span` with a
thread-local check: when the calling thread has `suppress=True`, every
`start_span` call returns `NOOP_SPAN` instead of creating a real span.

Usage:
    from .tracing_guard import install_tracing_guard, suppress_tracing

    install_tracing_guard()          # once at process startup

    with suppress_tracing():         # in a load-test thread
        ...                          # all start_span calls → NOOP_SPAN
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager

from braintrust.logger import NOOP_SPAN
from braintrust.logger import start_span as _original_start_span

_thread_state = threading.local()
_installed = False


def _guarded_start_span(*args, **kwargs):
    """Drop-in replacement for `braintrust.logger.start_span`.

    Returns NOOP_SPAN when tracing is suppressed for the current thread.
    """
    if getattr(_thread_state, "suppress", False):
        return NOOP_SPAN
    return _original_start_span(*args, **kwargs)


def install_tracing_guard() -> None:
    """Monkey-patch `start_span` in the braintrust logger and wrapper modules.

    Safe to call multiple times — only patches once.
    """
    global _installed
    if _installed:
        return

    import braintrust.logger as _bt_logger_mod

    _bt_logger_mod.start_span = _guarded_start_span

    # The SDK wrapper imports start_span directly — patch it there too.
    try:
        import braintrust.wrappers.claude_agent_sdk._wrapper as _wrapper_mod

        _wrapper_mod.start_span = _guarded_start_span
    except (ImportError, AttributeError):
        pass

    _installed = True


@contextmanager
def suppress_tracing() -> Generator[None, None, None]:
    """Context manager that suppresses all Braintrust span creation in this thread."""
    _thread_state.suppress = True
    try:
        yield
    finally:
        _thread_state.suppress = False
