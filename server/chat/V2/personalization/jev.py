"""Client for Jev, a typed judge: yes/no ("noul") and one-of-N ("choice") questions about a state.

One request carries one state and many questions. Jev cannot generate text. The API key is read
from the environment (and, only when `allow_keychain=True`, from the macOS Keychain, as the
`jev-ask` script does) and never appears in an error message, log line or file.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

URL = "https://api.typesafe.ai/v1/systemone"
KEY_ENV = "TYPESAFE_API_KEY"
KEYCHAIN_ITEM = "typesafe-api-key"
DEFAULT_MODEL = "jev-1.13.0"
ANSWER_TYPES = ("noul", "choice")


class JevError(Exception):
    """A Jev request failed. The message never contains the key (HTTP errors give the status only)."""


@dataclass
class JevQuestion:
    type: str  # "noul" or "choice"
    instructions: str
    criteria: dict[str, str | None] | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


@dataclass
class JevAnswer:
    type: str
    choice: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    noul: float | None = None


@dataclass
class JevResponse:
    answers: dict[str, JevAnswer]
    input_tokens: int
    latency_ms: int
    model: str


class JevClient(Protocol):
    def ask(self, state: dict, questions: dict[str, JevQuestion]) -> JevResponse: ...


def _keychain_key() -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


class HttpJevClient:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: int = 30, allow_keychain: bool = False):
        self.model = model
        self.timeout = timeout
        self.allow_keychain = allow_keychain

    def _key(self) -> str:
        key = os.environ.get(KEY_ENV) or (_keychain_key() if self.allow_keychain else None)
        if not key:
            raise JevError(f"no Jev API key: set {KEY_ENV}")
        return key

    def ask(self, state, questions):
        body = json.dumps(
            {
                "state": state,
                "questions": {k: q.to_json() for k, q in questions.items()},
                "model": self.model,
            }
        ).encode()
        request = urllib.request.Request(
            URL,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:  # status only: the request carries the key
            raise JevError(f"Jev HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise JevError("Jev network or response error") from None
        latency_ms = int((time.perf_counter() - started) * 1000)
        return parse_response(data, latency_ms, self.model)


def _number(value: object, *, unit: bool = False) -> float | None:
    """A finite real number (a bool is not one); with `unit`, within [0, 1]. Else JevError."""
    try:
        ok = (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
        if not ok or (unit and not 0 <= value <= 1):
            raise JevError("Jev response malformed")
        return float(value)
    except (OverflowError, ValueError):  # an integer too large for a float
        raise JevError("Jev response malformed") from None


def parse_response(data: object, latency_ms: int, default_model: str) -> JevResponse:
    """Validate a decoded Jev reply; anything malformed raises JevError (never AttributeError)."""
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise JevError("Jev response malformed")
    answers = {}
    for key, raw in data["answers"].items():
        if (
            not isinstance(key, str)
            or not isinstance(raw, dict)
            or raw.get("type") not in ANSWER_TYPES
        ):
            raise JevError("Jev response malformed")
        choice, probabilities = raw.get("choice"), raw.get("probabilities")
        if choice is not None and not isinstance(choice, str):
            raise JevError("Jev response malformed")
        if probabilities is not None:
            if not isinstance(probabilities, dict):
                raise JevError("Jev response malformed")
            probabilities = {str(k): _number(v, unit=True) for k, v in probabilities.items()}
        answers[key] = JevAnswer(
            type=raw["type"],
            choice=choice,
            confidence=None
            if raw.get("confidence") is None
            else _number(raw["confidence"], unit=True),
            probabilities=probabilities,
            noul=None if raw.get("noul") is None else _number(raw["noul"], unit=True),
        )
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise JevError("Jev response malformed")
    tokens = (usage or {}).get("input_tokens", 0)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise JevError("Jev response malformed")
    model = data.get("model", default_model)
    return JevResponse(
        answers, tokens, latency_ms, model if isinstance(model, str) else default_model
    )


@dataclass
class FakeJevClient:
    """Test client: `responder(state, questions) -> {question key: JevAnswer}`."""

    responder: Callable[[dict, dict[str, JevQuestion]], dict[str, JevAnswer]]
    calls: list[tuple[dict, dict[str, JevQuestion]]] = field(default_factory=list)
    model: str = DEFAULT_MODEL

    def ask(self, state, questions):
        self.calls.append((state, questions))
        text = json.dumps(state, ensure_ascii=False) + json.dumps(
            {k: q.to_json() for k, q in questions.items()}, ensure_ascii=False
        )
        return JevResponse(self.responder(state, questions), len(text) // 4, 0, self.model)
