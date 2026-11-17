"""Judges: Codex (a different model family from the answers) and a fake for tests.

A reply that does not match the judge's schema is retried once and then becomes
`{"invalid": true}`, which a case scores as `invalid`, never as a pass.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


def matches_schema(reply: object, schema: dict) -> bool:
    """A small JSON-schema subset: type object, required keys, property types and enums."""
    if not isinstance(reply, dict):
        return False
    if any(key not in reply for key in schema.get("required", [])):
        return False
    types = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, spec in schema.get("properties", {}).items():
        if key not in reply:
            continue
        value = reply[key]
        kind = spec.get("type")
        if kind and not (
            isinstance(value, types[kind]) and not (kind != "boolean" and isinstance(value, bool))
        ):
            return False
        if "enum" in spec and value not in spec["enum"]:
            return False
    return True


class CodexJudge:
    def __init__(
        self,
        schema: dict,
        *,
        codex_bin: str = "codex",
        timeout: int = 300,
        model: str | None = None,
    ):
        self.schema = schema
        self.codex_bin = codex_bin
        self.timeout = timeout
        self.model = model

    def _run(self, prompt: str) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            schema_path, out_path = Path(tmp) / "schema.json", Path(tmp) / "reply.json"
            schema_path.write_text(json.dumps(self.schema), encoding="utf-8")
            argv = [
                self.codex_bin, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                "-C", tmp, "--output-schema", str(schema_path), "-o", str(out_path),
            ]  # fmt: skip
            if self.model:
                argv += ["-m", self.model]
            try:
                subprocess.run(
                    argv + [prompt], capture_output=True, text=True, timeout=self.timeout, cwd=tmp
                )
                return json.loads(out_path.read_text(encoding="utf-8"))
            except (subprocess.TimeoutExpired, OSError, ValueError):
                return None

    def ask(self, prompt: str) -> dict:
        for _ in range(2):
            reply = self._run(prompt)
            if matches_schema(reply, self.schema):
                return reply
        return {"invalid": True}


class FakeJudge:
    """Test judge: `replies` is a list (used in order, the last repeated) or a callable(prompt)."""

    def __init__(self, replies, schema: dict | None = None):
        self.replies = replies
        self.schema = schema
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        n = len(self.prompts)
        reply = (
            self.replies(prompt)
            if callable(self.replies)
            else self.replies[min(n, len(self.replies)) - 1]
        )
        if self.schema is not None and not matches_schema(reply, self.schema):
            return {"invalid": True}
        return reply
