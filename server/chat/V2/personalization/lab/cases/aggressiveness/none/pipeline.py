"""aggressiveness/none: the control. Every turn's prompt is built with personalization off and
compared, byte for byte, with the prompt `main`'s `build_prompt` builds for the same request.

For each turn the pipeline runs the production off-path: `gate.resolve` with the server mode `off`,
then `runtime.profile_for_request` (with the profile store swapped for `CountingStore`, which counts
every write call and every row, so a change that reached the store on the off path would show), then
the branch's `build_turn_prompt` with the `MessageContext` the chat view builds. The same request
goes to `build_prompt` loaded from `git show <main_ref>:server/chat/V2/prompts/prompt_fragments.py`
in an isolated module namespace (never imported over the branch's module). It makes no model call
and reads no store.

Hashes: the SHA-256 of both prompts per turn is kept in `stats()["diagnostics"]["prompt_hashes"]`
(the replay's saved progress). Prompt text is never stored. The core prompt, summary and page url
are fixed synthetic strings; summary and page url are switched on at fixed turn positions so each
branch of the builder is compared.
"""

from __future__ import annotations

import functools
import hashlib
import subprocess
import types
from collections import Counter
from pathlib import Path
from unittest import mock

from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.prompt_pipeline import build_turn_prompt
from chat.V2.personalization import gate, runtime
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.store import InMemoryProfileStore
from chat.V2.personalization.types import Turn

CORE_PROMPT = (
    "You are the Library Assistant."  # synthetic stand-in; the real one lives in Braintrust
)
SUMMARY = "They studied Berakhot."
PAGE_URL = "https://www.sefaria.org/Genesis.1"
MAIN_FILE = "server/chat/V2/prompts/prompt_fragments.py"
OFF_SETTINGS = types.SimpleNamespace(
    PERSONALIZATION_MODE="off",
    PERSONALIZATION_OVERRIDE_ENABLED=False,
    PERSONALIZATION_OVERRIDE_USER_IDS="",
    PERSONALIZATION_INJECTION="always",
)
_READS = frozenset(
    {"get_profile", "revision", "get_candidate", "observations", "users_with_pending",
     "proposals", "get_settings"}
)  # fmt: skip


class MainPromptError(RuntimeError):
    pass


@functools.lru_cache(maxsize=4)
def main_build_prompt(ref: str):
    """`build_prompt` as `ref` has it, executed in its own namespace."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{MAIN_FILE}"],
        cwd=Path(runtime.__file__).parent, capture_output=True, text=True,
    )  # fmt: skip
    if proc.returncode != 0:
        raise MainPromptError(f"git show {ref}:{MAIN_FILE} failed: {proc.stderr.strip()[:200]}")
    namespace: dict = {"__name__": "main_prompt_fragments"}
    exec(compile(proc.stdout, f"{ref}:{MAIN_FILE}", "exec"), namespace)  # noqa: S102
    return namespace["build_prompt"]


class CountingStore:
    """An in-memory profile store that counts every call that is not a read, and its rows."""

    def __init__(self):
        self._inner = InMemoryProfileStore()
        self.write_calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name.startswith("_") or name in _READS or not callable(attr):
            return attr

        def counted(*args, **kwargs):
            self.write_calls.append(name)
            return attr(*args, **kwargs)

        return counted

    def rows(self) -> int:
        inner = self._inner
        return sum(
            len(v)
            for table in (inner._slots, inner._observations, inner._proposals, inner._settings, inner._candidates)
            for v in table.values()
        )  # fmt: skip

    def writes(self) -> int:
        return max(len(self.write_calls), self.rows())


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OffPipeline:
    def __init__(self, main_ref: str = "origin/main"):
        self.main_build = main_build_prompt(main_ref)
        self.store = CountingStore()
        self._history: dict[tuple, list[ConversationMessage]] = {}
        self._turns = 0
        self._identical = 0
        self._hashes: list[dict] = []

    # one request, as the chat view makes it with personalization off
    def request_prompts(self, user_id: str, turn: Turn, position: int, history) -> tuple[str, str]:
        messages = [*history, ConversationMessage("user", turn.user_message)]
        summary = SUMMARY if position % 3 == 0 else None
        page_url = PAGE_URL if position % 2 == 0 else None
        config = gate.resolve(OFF_SETTINGS, user_id, gate.RequestedPersonalization())
        with mock.patch.object(runtime, "_store", self.store):
            # the view skips this read for an off request; making it here puts the store in reach
            # of the off path, so a read or a write that slipped in would be counted and compared
            profile = runtime.profile_for_request(config, turn.at)
        context = MessageContext(
            summary_text=summary,
            page_url=page_url,
            user_id=user_id,
            profile_text=(profile.text or None) if profile else None,
        )
        ours = build_turn_prompt(messages=messages, core_prompt=CORE_PROMPT, context=context)
        theirs, _ = self.main_build(
            ours.conversation_text, core_prompt=CORE_PROMPT, summary_text=summary, page_url=page_url
        )
        return ours.full_prompt, theirs

    def on_turn(self, user_id: str, turn: Turn) -> None:
        key = (user_id, turn.session_id)
        history = self._history.setdefault(key, [])
        ours, theirs = self.request_prompts(user_id, turn, self._turns, history)
        self._turns += 1
        self._identical += ours.encode("utf-8") == theirs.encode("utf-8")
        self._hashes.append({"turn": turn.message_id, "lab": sha256(ours), "main": sha256(theirs)})
        history += [
            ConversationMessage("user", turn.user_message),
            ConversationMessage("assistant", turn.assistant_response),
        ]
        # the view's after-turn guard (`_submit_personalization_update`): never for an off request
        # (`config.source == "off"`), so nothing is queued and nothing reaches the store

    def run_job(self, user_id, now) -> None:  # no nightly job
        return None

    def profile(self, user_id) -> dict:
        return {}

    def steps(self) -> list[str]:
        return ["render_profile", "write_profile"]

    def step_counts(self) -> dict:
        return {"render_profile": (self._turns, 0), "write_profile": (0, 0)}

    def stats(self) -> dict:
        return {
            "skips": {},
            "diagnostics": {
                "prompts_compared": self._turns,
                "prompts_identical": self._identical,
                "store_writes": self.store.writes(),
                "prompt_hashes": self._hashes,
            },
        }


def build(config, llm, typed=None) -> OffPipeline:
    return OffPipeline(config.get("main_ref", "origin/main"))


def counts(variant_config, replays, llm) -> dict:
    """byte_identical_off (identical prompts over compared prompts) and store_writes, summed."""
    total = Counter()
    for replay in replays:
        for name in ("prompts_compared", "prompts_identical", "store_writes"):
            total[name] += replay.diagnostics.get(name, 0)
    return {
        "byte_identical_off": total["prompts_identical"],
        "byte_identical_off_of": total["prompts_compared"],
        "store_writes": total["store_writes"],
    }


# -- the mock: two invented personas, and a model that must never be called ---------------------


def mock_items():
    import json

    return json.loads((Path(__file__).parent / "mock_fixture.json").read_text(encoding="utf-8"))[
        "personas"
    ]


def mock_llm():
    def respond(system: str, user: str) -> str:
        raise AssertionError("aggressiveness/none makes no model call")

    return FakeLLMClient(respond)
