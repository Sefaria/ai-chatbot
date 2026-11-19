"""Three Claude judges score the Profiles and the answers, blind.

Each judge model is a separate headless `claude -p` call (see README for the flags). Answers are
shown under neutral labels "1", "2", "3" in an order shuffled by `random.Random(seed +
prompt_index)`; the code maps labels back to original / A / B. Profiles are shown as "1" and "2",
shuffled by `random.Random(seed)`.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

MODELS = ("opus", "sonnet", "haiku")

PROFILE_SYSTEM = """You are checking two short user Profiles written about the same person.
A Profile is a note an assistant keeps about a user so its answers fit them better.
You get the user's first ten questions (the evidence) and two Profiles, labelled 1 and 2.

For each Profile give an accuracy score from 1 (mostly wrong or empty) to 5 (accurate, complete),
and list reasons in three groups:
- supported: claims in the Profile that the questions support
- missed: interests or habits visible in the questions that the Profile leaves out
- invented: claims the questions do not support

Then say which Profile is better overall ("1", "2" or "tie") and why in one or two sentences.
Judge only against the questions. Do not reward length.

Reply with one JSON object and nothing else:
{"profiles": {"1": {"score": 1-5, "supported": [...], "missed": [...], "invented": [...]},
              "2": {"score": 1-5, "supported": [...], "missed": [...], "invented": [...]}},
 "winner": "1" | "2" | "tie", "why": "..."}"""

RANKING_SYSTEM = """You are comparing three answers to the same question from a Jewish-texts study assistant.
The labels 1, 2 and 3 carry no meaning. You see the user's earlier questions for context.
Rank the answers from best to worst for this user: correct, cites its sources, useful, fitted to
what the earlier questions show about them, and not padded.

Reply with one JSON object and nothing else:
{"ranking": ["best label", "middle label", "worst label"], "why": "one or two sentences"}"""


@dataclass
class Judgment:
    model: str
    kind: str  # "profile" or "ranking"
    prompt_index: int | None
    data: dict = field(default_factory=dict)
    user: str = ""


def label_order(seed: int, prompt_index: int) -> list[str]:
    """Which of original/A/B is shown as label 1, 2, 3 for this prompt."""
    order = ["original", "A", "B"]
    random.Random(seed + prompt_index).shuffle(order)
    return order


def profile_order(seed: int) -> list[str]:
    order = ["A", "B"]
    random.Random(seed).shuffle(order)
    return order


def claude_call(model: str, system: str, user: str, timeout: int = 300) -> str:
    argv = [
        "claude", "-p", user, "--model", model, "--system-prompt", system,
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
        "--setting-sources", "", "--settings", "{}",
        "--output-format", "json", "--no-session-persistence",
    ]  # fmt: skip
    env = dict(os.environ)
    env["MAX_THINKING_TOKENS"] = "0"
    last = "no output"
    for _ in range(2):
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=timeout,
                    cwd=cwd, env=env, stdin=subprocess.DEVNULL, check=False,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                last = "timed out"
                continue
        if proc.returncode == 0:
            for line in reversed(proc.stdout.strip().splitlines()):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and "result" in data:
                    return data["result"]
            last = "unparsable output"
        else:
            last = f"exit code {proc.returncode}"
    raise RuntimeError(f"claude call failed ({model}): {last}")


def _json_object(text: str) -> dict:
    """The first JSON object in a reply, tolerating prose and code fences around it."""
    text = re.sub(r"```(?:json)?", "", text or "")
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object in reply")


def _ask(call, model, system, user, check, attempts: int = 3):
    """Ask, validate, retry. Returns (object, None) or (None, reason): a bad reply never raises."""
    last = "no reply"
    for _ in range(attempts):
        try:
            obj = _json_object(call(model, system, user))
            check(obj)
            return obj, None
        except Exception as exc:  # noqa: BLE001 - any failure of one judge call is a judgment
            last = f"{type(exc).__name__}: {exc}"
    return None, last


def _label(v) -> str:
    return str(v).strip()


def _check_profile(o: dict) -> None:
    profiles = o.get("profiles")
    if not isinstance(profiles, dict) or {_label(k) for k in profiles} != {"1", "2"}:
        raise ValueError("profiles must be labelled 1 and 2")
    if _label(o.get("winner")) not in ("1", "2", "tie"):
        raise ValueError("winner must be 1, 2 or tie")
    for v in profiles.values():
        if not 1 <= int(v["score"]) <= 5:
            raise ValueError("score out of range")


def _check_rank(o: dict) -> None:
    ranking = o.get("ranking")
    if not isinstance(ranking, list) or sorted(_label(x) for x in ranking) != [
        "1",
        "2",
        "3",
    ]:
        raise ValueError("ranking must be a permutation of 1, 2, 3")


def _profile_user(packet: dict, order: list[str]) -> str:
    qs = "\n".join(f"- {t['prompt']}" for t in packet["turns"])
    profiles = {"A": packet["profile_a"], "B": packet["profile_b"]}
    shown = "\n\n".join(
        f"### Profile {i + 1}\n{profiles[k] or '(empty)'}" for i, k in enumerate(order)
    )
    return f"## The user's first ten questions\n{qs}\n\n{shown}"


def _ranking_user(packet: dict, item: dict, order: list[str]) -> str:
    history = "\n".join(f"- {t['prompt']}" for t in packet["turns"])
    shown = "\n\n".join(f"### Answer {i + 1}\n{item[k]}" for i, k in enumerate(order))
    return (
        f"## Earlier questions from this user\n{history}\n\n"
        f"## Question\n{item['prompt']}\n\n{shown}"
    )


def run_judges(
    packet: dict, models=MODELS, seed: int = 0, *, call: Callable | None = None
) -> list[Judgment]:
    call = call or claude_call
    out: list[Judgment] = []
    user = packet.get("user", "")
    p_order = profile_order(seed)
    for model in models:
        o, why_bad = _ask(
            call, model, PROFILE_SYSTEM, _profile_user(packet, p_order), _check_profile
        )
        if o is None:
            out.append(Judgment(model, "profile", None, {"unusable": why_bad}, user))
        else:
            back = {str(i + 1): k for i, k in enumerate(p_order)}
            profs = {_label(n): v for n, v in o["profiles"].items()}
            winner = _label(o["winner"])
            out.append(Judgment(model, "profile", None, {
                "scores": {back[n]: int(v["score"]) for n, v in profs.items()},
                "reasons": {back[n]: {k: v.get(k, []) for k in ("supported", "missed", "invented")}
                            for n, v in profs.items()},
                "winner": winner if winner == "tie" else back[winner],
                "why": o.get("why", ""),
            }, user))  # fmt: skip
        for item in packet["prompts"]:
            order = label_order(seed, item["index"])
            o, why_bad = _ask(
                call,
                model,
                RANKING_SYSTEM,
                _ranking_user(packet, item, order),
                _check_rank,
            )
            if o is None:
                out.append(
                    Judgment(
                        model, "ranking", item["index"], {"unusable": why_bad}, user
                    )
                )
                continue
            out.append(Judgment(model, "ranking", item["index"], {
                "ranking": [order[int(_label(n)) - 1] for n in o["ranking"]],  # best first
                "why": o.get("why", ""),
            }, user))  # fmt: skip
    return out
