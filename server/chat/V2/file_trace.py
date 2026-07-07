"""Opt-in local JSONL tracing for agent-loop latency investigations."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "[redacted]"
DEFAULT_TRACE_PATH = "latency/runs/agent-loop-debug/events.jsonl"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "encrypted_user_token",
    "secret",
    "token",
)

_WRITE_LOCK = threading.Lock()


def file_tracing_enabled() -> bool:
    return os.environ.get("AGENT_FILE_TRACE_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def _trace_path() -> Path:
    configured = os.environ.get("AGENT_FILE_TRACE_PATH") or DEFAULT_TRACE_PATH
    return Path(configured)


def _redact_key(key: Any) -> bool:
    key_text = str(key).lower()
    return any(part in key_text for part in SENSITIVE_KEY_PARTS)


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return repr(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if _redact_key(getattr(value, "name", "")):
        return REDACTED
    if dataclasses.is_dataclass(value):
        return {
            field.name: REDACTED
            if _redact_key(field.name)
            else _jsonable(getattr(value, field.name), depth=depth + 1)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _redact_key(key) else _jsonable(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_jsonable(item, depth=depth + 1) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value), depth=depth + 1)
    return repr(value)


def _new_trace_ids(session_id: str | None, turn_id: str | None) -> tuple[str, str]:
    run_id = os.environ.get("AGENT_FILE_TRACE_RUN_ID") or datetime.now(UTC).strftime(
        "agent-debug-%Y%m%dT%H%M%SZ"
    )
    trace_id = turn_id or session_id or str(uuid.uuid4())
    return run_id, trace_id


class AgentFileTracer:
    """Append-only JSONL tracer for one chat turn."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str | None,
        turn_id: str | None,
        path: Path,
    ):
        self.run_id = run_id
        self.trace_id = trace_id
        self.session_id = session_id
        self.turn_id = turn_id
        self.path = path
        self.start_perf = time.perf_counter()

    @classmethod
    def create(
        cls, *, session_id: str | None = None, turn_id: str | None = None
    ) -> AgentFileTracer | None:
        if not file_tracing_enabled():
            return None
        run_id, trace_id = _new_trace_ids(session_id, turn_id)
        return cls(
            run_id=run_id,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn_id,
            path=_trace_path(),
        )

    def child_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.start_perf) * 1000)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        try:
            json_payload = _jsonable(payload or {})
        except Exception as exc:
            json_payload = {
                "trace_serialization_error": str(exc),
                "payload_repr": repr(payload),
            }
        event = {
            **self.child_payload(),
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "elapsed_ms": self.elapsed_ms(),
            "payload": json_payload,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fout:
                fout.write(line)
                fout.write("\n")

    def exception(
        self, event_type: str, exc: BaseException, payload: dict[str, Any] | None = None
    ) -> None:
        self.emit(
            event_type,
            {
                **(payload or {}),
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            },
        )
