from datetime import UTC, datetime, timedelta

from personalization_ab.select import Turn, dedupe, pick_users

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def turn(user, n, prompt, minutes=None):
    return Turn(
        user,
        "s1",
        n,
        T0 + timedelta(minutes=n if minutes is None else minutes),
        prompt,
        "a",
    )


def test_dedupe_keeps_the_later_repeat_in_time_order():
    turns = [
        turn("u", 1, "Tell me about otters"),
        turn("u", 2, "other"),
        turn("u", 3, " tell  me about OTTERS "),
    ]
    out = dedupe(turns)
    assert [t.turn_number for t in out] == [2, 3]


def test_dedupe_is_per_user():
    out = dedupe([turn("u1", 1, "same"), turn("u2", 2, "same")])
    assert len(out) == 2


def make(users):
    return {
        u: [turn(u, i, f"{u} question {i}") for i in range(n)] for u, n in users.items()
    }


def test_pick_users_is_stable_and_excludes_short_users():
    data = make({f"u{i}": 25 for i in range(10)} | {"short": 19})
    a = pick_users(data, 4, seed=7)
    assert a == pick_users(data, 4, seed=7)
    assert len(a) == 4 and "short" not in a
    assert pick_users(data, 50, seed=7).count("short") == 0
