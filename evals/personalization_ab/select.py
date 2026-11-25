"""Pick which users and turns go into the A/B comparison. Pure functions, no database."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Turn:
    user_id: str
    session_id: str
    turn_number: int
    created: datetime
    prompt: str
    original_answer: str


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def dedupe(turns: list[Turn]) -> list[Turn]:
    """A repeated or retried prompt (same normalized text, same user) counts once, keeping the
    later one. The result is in time order."""
    latest: dict[tuple[str, str], Turn] = {}
    for t in sorted(turns, key=lambda t: (t.created, t.turn_number)):
        latest[(t.user_id, _normalize(t.prompt))] = (
            t  # a later one overwrites an earlier one
        )
    return sorted(latest.values(), key=lambda t: (t.created, t.turn_number))


def pick_users(
    turns_by_user: dict[str, list[Turn]], n: int, seed: int, min_turns: int = 20
) -> list[str]:
    """`n` users with at least `min_turns` turns after dedupe, chosen with `random.Random(seed)`.
    The same input and seed give the same list."""
    eligible = sorted(
        u for u, turns in turns_by_user.items() if len(dedupe(turns)) >= min_turns
    )
    rng = random.Random(seed)
    return rng.sample(eligible, min(n, len(eligible)))
