"""Regression test for the merged link_quote_accuracy scorer.

Covers all three failure modes:
- bad links: invalid Sefaria URLs
- false absence: claims Sefaria is missing a work it actually has
- hallucinated quotes: source-language quoted text not at the cited ref

Run from the repo root:
    python evals/scorers/tests/test_link_quote_accuracy.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code_scorers"))
from link_quote_accuracy import handler  # noqa: E402

CASES = [
    # ── Link validity ─────────────────────────────────────────────────────────
    ("No links", "Here is some text with no Sefaria links.", 1.0),
    (
        "Valid text ref",
        '<a class="response-link" href="https://www.sefaria.org/Genesis.1.1">Genesis 1:1</a>',
        1.0,
    ),
    (
        "Invalid text ref",
        '<a class="response-link" href="https://www.sefaria.org/Genesis.999.999">Genesis 999:999</a>',
        0.0,
    ),
    (
        "TOC page (valid non-text)",
        '<a class="response-link" href="https://www.sefaria.org/texts/Talmud">Talmud</a>',
        1.0,
    ),
    # ── Quote matching ────────────────────────────────────────────────────────
    (
        "Rav Kook real quotes (incl. ellipsis + English rendering)",
        '<p>From <a class="response-link" href="https://www.sefaria.org/Orot_HaKodesh.3.1.1.11">Orot HaKodesh</a>:</p>'
        '<ul><li><span class="response-quote">אסור ליראת שמים שתדחק את המוסר הטבעי של האדם, כי אז אינה עוד יראת שמים טהורה.</span></li>'
        '<li><span class="response-quote">סימן ליראת שמים טהורה הוא כשהמוסר הטבעי... הולך ועולה על פיה במעלות יותר גבוהות ממה שהוא עומד מבלעדה.</span></li></ul>'
        '<p>English: <span class="response-quote">"Fear of heaven must not displace natural morality."</span></p>',
        1.0,
    ),
    (
        "Hallucinated Hebrew quote at real ref",
        '<p>From <a class="response-link" href="https://www.sefaria.org/Genesis.1.1">Genesis 1:1</a>:</p>'
        '<ul><li><span class="response-quote">בראשית ברא אלהים מטוס ולא ידע איש</span></li></ul>',
        0.0,
    ),
    # ── False-absence claims ──────────────────────────────────────────────────
    (
        "Sacks - 'are not currently in'",
        "Rabbi Sacks's books are not currently in Sefaria's library.",
        0.0,
    ),
    (
        "Sacks - 'isn't part of'",
        "Rabbi Jonathan Sacks's work isn't part of Sefaria's collection.",
        0.0,
    ),
    (
        "Sacks - 'haven't been added'",
        "Rabbi Sacks's books haven't been added to Sefaria yet.",
        0.0,
    ),
    (
        "Sacks - 'doesn't host'",
        "Sefaria doesn't host the writings of Rabbi Sacks.",
        0.0,
    ),
    # ── True absence (made-up book) ───────────────────────────────────────────
    ("Made-up book", "Sefaria does not have the Squiggle Squoggle Codex.", 1.0),
    # ── Honest 'couldn't find' (no library claim) ─────────────────────────────
    (
        "Honest couldn't find",
        "<p>I searched but couldn't find that quote in Sefaria.</p>",
        1.0,
    ),
    # ── Multiple failures combined ────────────────────────────────────────────
    (
        "Bad link + false absence",
        "Rabbi Sacks's books are not currently in Sefaria's library. "
        'See <a class="response-link" href="https://www.sefaria.org/Genesis.999.999">Genesis 999:999</a>.',
        0.0,
    ),
]


def main() -> int:
    correct = 0
    for desc, output, expected in CASES:
        r = handler(input=None, output=output, expected=None, metadata={})
        actual = r["score"]
        ok = actual == expected
        correct += int(ok)
        status = "✓" if ok else "✗"
        print(f"{status} {desc:<55} expected={expected} actual={actual}")
        checks = r["metadata"].get("checks_failed") or []
        if checks:
            print(f"    checks_failed={checks}")
        if not ok:
            print(f"    reason: {r['metadata'].get('reason', '')[:140]}")
    print(f"\n{correct}/{len(CASES)} correct")
    return 0 if correct == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
