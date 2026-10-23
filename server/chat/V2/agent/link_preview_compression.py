"""Compresses a linked source's text into a short, lossy preview for
get_links_between_texts(with_text="1"), replacing flat 500-character
truncation.

Why: flat truncation returns a clean, contiguous excerpt that *looks*
complete, so the agent often answers from it directly without verifying --
silently failing whenever the relevant detail falls past the first 500
characters. A word-length-based preview (drop the shortest/lowest-information
words, keep the rest, mark gaps) is visibly incomplete, which in testing made
the agent verify via get_text before citing specific facts far more often,
while being honest that it is a preview. This measurably eliminates most
of the truncation-based silent-failure mode. See the research writeup for
the underlying benchmark (fetch-recall, accuracy, and cost comparisons across
truncation, this preview, and refs-only).

Each call draws fresh randomness only to break ties between equal-length
words (the selection signal itself -- word length -- is stable); every call
gets an independently generated preview rather than one pinned, reusable
compression, so the same source isn't permanently hidden by one unlucky draw.
"""

import random

GAP = "…"
DEFAULT_RETENTION = 0.3


def _tokenize(text: str) -> list[str]:
    return text.split()


def compress_link_preview(text: str, r_keep: float = DEFAULT_RETENTION) -> str:
    """Return `text` with the longest ~r_keep fraction of words kept and the
    rest replaced by a gap marker, so length/word-count signals survive but
    short, low-information words are dropped first."""
    tokens = _tokenize(text)
    n = len(tokens)
    if n == 0:
        return text
    keep_n = max(1, round(r_keep * n))
    drop_n = n - keep_n
    if drop_n <= 0:
        return text

    rng = random.Random()  # unseeded: a fresh draw every call, see module docstring
    ranked = sorted(range(n), key=lambda i: (len(tokens[i]), rng.random()))
    drop_indices = set(ranked[:drop_n])

    return " ".join(GAP if i in drop_indices else tok for i, tok in enumerate(tokens))
