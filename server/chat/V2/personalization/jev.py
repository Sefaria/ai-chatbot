"""Client for Jev, a typed judge: yes/no ("noul") and one-of-N ("choice") questions about a state.

One request carries one state and many questions. Jev cannot generate text. The API key is read
from the environment (and, only when `allow_keychain=True`, from the macOS Keychain, as the
`jev-ask` script does) and never appears in an error message, log line or file.
"""

from __future__ import annotations

import json
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
        answers = {}
        for key, raw in (data.get("answers") or {}).items():
            answers[key] = JevAnswer(
                type=raw.get("type", ""),
                choice=raw.get("choice"),
                confidence=raw.get("confidence"),
                probabilities=raw.get("probabilities"),
                noul=raw.get("noul"),
            )
        tokens = (data.get("usage") or {}).get("input_tokens", 0)
        return JevResponse(answers, tokens, latency_ms, data.get("model", self.model))


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
