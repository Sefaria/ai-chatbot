"""Model clients and the usage ledger.

`AnthropicLLMClient` is the production path. `ClaudeCodeLLMClient` runs headless Claude Code and
is for offline evaluation only. `FakeLLMClient` is for tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# USD per million input / output tokens; matched by model-id prefix (longest wins).
# Haiku 5.5: $0.10 in / $0.50 out up to 100K-token prompts (Anthropic pricing page, cached
# 2026-11-03; `main`'s model_pricing.json has no entry for it). Haiku 4.5: $1 / $5.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "jev": (0.042, 0.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prefixes = [p for p in PRICES if model.startswith(p)]
    if not prefixes:
        return 0.0
    price_in, price_out = PRICES[max(prefixes, key=len)]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    raw_input_tokens: int | None = None


class LLMClient(Protocol):
    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 600, timeout: float = 20.0
    ) -> LLMResult: ...


class FakeLLMClient:
    """Test client: `responder(system, user) -> str`; tokens are len(text) // 4."""

    def __init__(self, responder: Callable[[str, str], str]):
        self.responder = responder
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system, user, model, max_tokens=600, timeout=20.0):
        self.calls.append({"system": system, "user": user, "model": model, "timeout": timeout})
        text = self.responder(system, user)
        tokens_in = (len(system) + len(user)) // 4
        tokens_out = len(text) // 4
        return LLMResult(
            text, tokens_in, tokens_out, estimate_cost(model, tokens_in, tokens_out), 0
        )


class AnthropicLLMClient:
    """Production path: the Anthropic Messages API through the repo's tracked wrapper."""

    def __init__(self, client: Any = None):
        self._client = client

    def complete(self, *, system, user, model, max_tokens=600, timeout=20.0):
        from chat.V2.pricing import tracked_messages_create
        from chat.V2.utils import get_anthropic_client

        client = self._client or get_anthropic_client()
        started = time.perf_counter()
        response = tracked_messages_create(
            client,
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            timeout=timeout,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        usage = response.usage
        tokens_in = (
            usage.input_tokens
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return LLMResult(
            text,
            tokens_in,
            usage.output_tokens,
            estimate_cost(model, tokens_in, usage.output_tokens),
            latency_ms,
        )


class LLMCallError(RuntimeError):
    """A headless model call failed. The message is a fixed phrase and an exit code at most:
    never the command line, the prompts or the process's stderr."""


class ClaudeCodeLLMClient:
    """Evaluation-only client: headless `claude -p` with no tools, no MCP, no settings.

    Claude Code adds its own fixed input overhead; `calibrate(model)` measures it with a
    trivial call so `input_tokens` reports only what the strategy sent.
    """

    def __init__(self, claude_bin: str = "claude", timeout: int = 180):
        self.claude_bin = claude_bin
        self.timeout = timeout
        self.overhead: dict[str, int] = {}

    def calibrate(self, model: str) -> int:
        _, raw_in, _ = self._run(system="x", user="x", model=model)
        self.overhead[model] = raw_in
        return raw_in

    def complete(self, *, system, user, model, max_tokens=600, timeout=None):
        started = time.perf_counter()
        text, raw_in, tokens_out = self._run(system=system, user=user, model=model, timeout=timeout)
        latency_ms = int((time.perf_counter() - started) * 1000)
        tokens_in = max(0, raw_in - self.overhead.get(model, 0))
        return LLMResult(
            text,
            tokens_in,
            tokens_out,
            estimate_cost(model, tokens_in, tokens_out),
            latency_ms,
            raw_in,
        )

    def _run(
        self, *, system: str, user: str, model: str, timeout: float | None = None
    ) -> tuple[str, int, int]:
        argv = [
            self.claude_bin, "-p", user, "--model", model, "--system-prompt", system,
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
            "--setting-sources", "", "--output-format", "json", "--no-session-persistence",
        ]  # fmt: skip
        env = dict(os.environ)
        # The production path is a plain Messages API call. Headless Claude Code turns extended
        # thinking on by default (Haiku 5.5: adaptive thinking), which multiplies output tokens and
        # latency, so switch it off. MAX_THINKING_TOKENS=0 alone is not enough on Haiku 5.5: with
        # stdin inherited, "Reply with OK" still spent 75-190 thinking tokens in about 2 of 3 calls
        # (measured 2026-11-11); with stdin=DEVNULL it spent 0 in 20 of 20. So stdin is closed too.
        env["MAX_THINKING_TOKENS"] = "0"
        if config_dir := os.environ.get("PERSONALIZATION_CLAUDE_CONFIG_DIR"):
            env["CLAUDE_CONFIG_DIR"] = config_dir
        last_error = "no output"
        for _ in range(2):
            with tempfile.TemporaryDirectory() as cwd:
                try:
                    proc = subprocess.run(
                        argv,
                        capture_output=True,
                        text=True,
                        timeout=timeout or self.timeout,
                        cwd=cwd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                    )
                except subprocess.TimeoutExpired:
                    last_error = "timed out"
                    continue
            if proc.returncode == 0:
                data = _last_json_line(proc.stdout)
                if data is not None and "result" in data:
                    usage = data.get("usage") or {}
                    raw_in = sum(
                        usage.get(k, 0) or 0
                        for k in (
                            "input_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                        )
                    )
                    return data["result"], raw_in, usage.get("output_tokens", 0) or 0
                last_error = "unparsable output"
            else:
                last_error = f"exit code {proc.returncode}"
        raise LLMCallError(f"claude -p failed: {last_error}")


def _last_json_line(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


@dataclass
class LedgerEntry:
    strategy: str
    phase: str
    user_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class UsageLedger:
    """Every model call, with strategy, phase and user, so costs can be compared."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def record(self, strategy: str, phase: str, user_id: str, model: str, result: LLMResult):
        self.entries.append(
            LedgerEntry(
                strategy,
                phase,
                user_id,
                model,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
                result.latency_ms,
            )  # fmt: skip
        )

    def totals(self, group_by: str | None = None) -> dict[str, dict[str, float]]:
        """Calls, tokens and cost grouped by an entry field (None groups everything as 'all')."""
        groups: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        for entry in self.entries:
            bucket = groups[str(getattr(entry, group_by)) if group_by else "all"]
            bucket["calls"] += 1
            bucket["input_tokens"] += entry.input_tokens
            bucket["output_tokens"] += entry.output_tokens
            bucket["cost_usd"] += entry.cost_usd
        return dict(groups)
