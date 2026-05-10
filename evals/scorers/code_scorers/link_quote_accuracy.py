"""Link Quote Accuracy Scorer - validates Sefaria links, quoted source text, and absence claims."""

from typing import Any
from urllib.parse import quote, unquote, urlsplit
import re
import requests

NAME = "Link Quote Accuracy"
SLUG = "links-are-valid-06b8"
DESCRIPTION = (
    "0 if any Sefaria links are broken, quoted source-language text doesn't appear "
    "at the cited ref, or the response falsely claims a book isn't in Sefaria's library. "
    "Failure metadata identifies which check(s) failed."
)

MAX_URLS_TO_VALIDATE = 20
MAX_REFS_TO_FETCH = 10
MIN_QUOTE_LEN = 8
API_BASE = "https://www.sefaria.org"
TIMEOUT = 5.0

# ── Regexes ───────────────────────────────────────────────────────────────────

_PLAIN_URL_RE = re.compile(r'https?://[^\s"<>]+', flags=re.IGNORECASE)
_RESPONSE_LINK_HREF_RE = re.compile(
    r'<a\s+class=["\']response-link["\'][^>]*href=["\']([^"\']+)["\']',
    flags=re.IGNORECASE,
)
_QUOTE_RE = re.compile(
    r'<span\s+class=["\']response-quote["\'][^>]*>(.*?)</span>',
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_NIKUD_RE = re.compile(r"[֑-ׇ]")
_HEBREW_RE = re.compile(r"[֐-׿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WS_RE = re.compile(r"\s+")
_ELLIPSIS_RE = re.compile(r"\.{2,}|…")

_ABSENCE_PATTERNS = [
    re.compile(
        r"([\w''.\- ]{2,60}?)\s+"
        r"(?:is|are|isn[''']?t|aren[''']?t|is\s+not|are\s+not|hasn[''']?t\s+been|haven[''']?t\s+been)\s+"
        r"(?:currently\s+|yet\s+)?"
        r"(?:in|part\s+of|included\s+in|available\s+(?:in|on)|added\s+to)\s+"
        r"(?:Sefaria|Sefaria[''']?s\s+(?:library|collection|database))",
        re.IGNORECASE,
    ),
    re.compile(
        r"Sefaria\s+(?:does\s+not|doesn[''']?t|do(?:es)n[''']?t)\s+(?:currently\s+|yet\s+)?"
        r"(?:have|include|contain|carry|host)\s+([\w''.\- ]{2,60})",
        re.IGNORECASE,
    ),
]

_TRAILING_POSSESSIVE_RE = re.compile(r"['']s\b", re.IGNORECASE)
_TRAILING_NOUN_RE = re.compile(
    r"\s+(?:books?|writings?|works?|teachings?|texts?|commentar(?:y|ies)?)$",
    re.IGNORECASE,
)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an|any|all|most|many)\s+", re.IGNORECASE)
_OF_PHRASE_RE = re.compile(
    r"^(?:writings?|books?|works?|teachings?)\s+of\s+(.+)$", re.IGNORECASE
)

# ── Shared helpers ────────────────────────────────────────────────────────────


def _strip_trailing_punct(url: str) -> str:
    url = url.rstrip("\\")
    return url.rstrip(").,;:!?]}>\"'`")


def _quote_path(path: str) -> str:
    return quote(path, safe="")


def _strip_html(s: str) -> str:
    return _TAG_RE.sub(" ", s or "")


def _normalize(s: str) -> str:
    s = _strip_html(s)
    s = _NIKUD_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    if not _HEBREW_RE.search(s):
        s = s.lower()
    return s


def _response_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        return (
            output.get("content")
            or output.get("response")
            or output.get("text")
            or str(output)
        )
    return str(output)


# ── Check 1: link validity ────────────────────────────────────────────────────


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in _PLAIN_URL_RE.findall(text):
        u = _strip_trailing_punct(u)
        if u and "sefaria.org" in u.lower() and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _url_is_valid(session: requests.Session, url: str) -> bool:
    try:
        res = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        return res.status_code < 400
    except Exception:
        return False


# ── Check 2: hallucinated quotes ─────────────────────────────────────────────


def _extract_response_link_trefs(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for href in _RESPONSE_LINK_HREF_RE.findall(text):
        if "sefaria.org" not in href.lower():
            continue
        parts = urlsplit(href.strip())
        tref = unquote(parts.path).strip("/")
        if tref and tref not in seen:
            seen.add(tref)
            out.append(tref)
    return out


def _fetch_ref_text(session: requests.Session, tref: str) -> tuple[str, str]:
    """Return (normalized_text, language). Empty strings on failure."""
    try:
        res = session.get(
            f"{API_BASE}/api/v3/texts/{_quote_path(tref)}", timeout=TIMEOUT
        )
    except Exception:
        return "", ""
    if res.status_code != 200:
        return "", ""
    try:
        data = res.json()
    except Exception:
        return "", ""
    if data.get("error"):
        return "", ""
    versions = data.get("versions") or []
    version = next(
        (v for v in versions if v.get("isSource")),
        next(
            (v for v in versions if v.get("isPrimary")),
            versions[0] if versions else None,
        ),
    )
    if not version:
        return "", ""

    chunks: list[str] = []

    def _walk(v: Any) -> None:
        if isinstance(v, str):
            chunks.append(v)
        elif isinstance(v, list):
            for item in v:
                _walk(item)

    _walk(version.get("text"))
    lang = (version.get("actualLanguage") or version.get("language") or "").lower()
    return _normalize(" ".join(chunks)), lang


def _quote_language(s: str) -> str | None:
    he = len(_HEBREW_RE.findall(s))
    en = len(_LATIN_RE.findall(s))
    if he > en * 2:
        return "he"
    if en > he * 2:
        return "en"
    return None


def _extract_quotes(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in _QUOTE_RE.findall(text):
        n = _normalize(raw)
        if len(n) < MIN_QUOTE_LEN or n in seen:
            continue
        seen.add(n)
        lang = _quote_language(n)
        if lang is not None:
            out.append((n, lang))
    return out


def _quote_in_corpus(q: str, corpus: str) -> bool:
    """Split on ellipses; each fragment must appear in corpus."""
    fragments = [f.strip(" .,;:!?\"'") for f in _ELLIPSIS_RE.split(q)]
    fragments = [f for f in fragments if len(f) >= 4]
    if not fragments:
        return q in corpus
    return all(f in corpus for f in fragments)


# ── Check 3: false absences ───────────────────────────────────────────────────


def _clean_capture(phrase: str) -> str:
    phrase = phrase.strip().strip(".,;:'\"")
    phrase = _TRAILING_NOUN_RE.sub("", phrase).strip()
    phrase = _TRAILING_POSSESSIVE_RE.sub("", phrase).strip()
    phrase = _LEADING_ARTICLE_RE.sub("", phrase).strip()
    m = _OF_PHRASE_RE.match(phrase)
    if m:
        phrase = m.group(1).strip()
    return phrase


def _detect_absence_claims(text: str) -> list[str]:
    plain = _strip_html(text)
    out: list[str] = []
    seen: set[str] = set()
    for pat in _ABSENCE_PATTERNS:
        for m in pat.finditer(plain):
            phrase = _clean_capture(m.group(1))
            n = _normalize(phrase)
            if phrase and n and n not in seen:
                seen.add(n)
                out.append(phrase)
    return out


_RABBINICAL_PREFIX_RE = re.compile(r"^(?:rabbi|rav|rebbe)\s+(.+)$", re.IGNORECASE)


def _name_api_resolves(session: requests.Session, claim: str) -> str | None:
    try:
        res = session.get(f"{API_BASE}/api/name/{_quote_path(claim)}", timeout=TIMEOUT)
    except Exception:
        return None
    if res.status_code != 200:
        return None
    try:
        data = res.json()
    except Exception:
        return None
    if data.get("is_ref"):
        return data.get("ref") or claim
    claim_words = [w for w in _normalize(claim).split() if len(w) > 2]
    for obj in data.get("completion_objects") or []:
        if obj.get("type") not in {"ref", "Book", "AuthorTopic"}:
            continue
        title_norm = _normalize(obj.get("title") or "")
        if claim_words and all(w in title_norm for w in claim_words):
            return obj.get("title") or claim
    return None


def _claim_is_false_absence(session: requests.Session, claim: str) -> str | None:
    if result := _name_api_resolves(session, claim):
        return result
    # strip rabbinical prefix and retry (e.g. "Rabbi Jonathan Sacks" → "Jonathan Sacks")
    m = _RABBINICAL_PREFIX_RE.match(claim)
    if m:
        if result := _name_api_resolves(session, m.group(1).strip()):
            return result
    # fall back to index API
    try:
        res = session.get(
            f"{API_BASE}/api/v2/index/{_quote_path(claim)}", timeout=TIMEOUT
        )
        if res.status_code == 200:
            data = res.json()
            if not data.get("error"):
                return data.get("title") or claim
    except Exception:
        pass
    return None


# ── Handler ───────────────────────────────────────────────────────────────────


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    text = _response_text(output)

    if not text:
        return {"score": 1.0, "name": NAME, "metadata": {"reason": "Empty response"}}

    # Check 1: link validity
    urls = _extract_urls(text)
    truncated = len(urls) > MAX_URLS_TO_VALIDATE
    urls = urls[:MAX_URLS_TO_VALIDATE]

    # Check 2: hallucinated quotes
    quotes = _extract_quotes(text)
    trefs = _extract_response_link_trefs(text)

    # Check 3: false absences
    absence_claims = _detect_absence_claims(text)

    invalid_urls: list[str] = []
    unmatched_quotes: list[dict[str, str]] = []
    false_absences: list[dict[str, str]] = []

    with requests.Session() as session:
        # Check 1
        for url in urls:
            if not _url_is_valid(session, url):
                invalid_urls.append(url)

        # Check 2
        if quotes and trefs:
            ref_corpus = ""
            ref_source_langs: set[str] = set()
            for tref in trefs[:MAX_REFS_TO_FETCH]:
                src_text, src_lang = _fetch_ref_text(session, tref)
                if src_text:
                    ref_corpus += " " + src_text
                if src_lang:
                    ref_source_langs.add(src_lang)
            if ref_corpus.strip():
                for q, q_lang in quotes:
                    if q_lang not in ref_source_langs:
                        continue
                    if not _quote_in_corpus(q, ref_corpus):
                        unmatched_quotes.append({"quote": q, "lang": q_lang})

        # Check 3
        for claim in absence_claims:
            matched = _claim_is_false_absence(session, claim)
            if matched:
                false_absences.append({"claim": claim, "matched_title": matched})

    # ── Collect failures ──────────────────────────────────────────────────────

    failures: list[str] = []

    if invalid_urls:
        detail = f"bad links: {len(invalid_urls)} invalid Sefaria link(s)"
        if truncated:
            detail += f" (validated first {MAX_URLS_TO_VALIDATE})"
        failures.append(detail)

    if unmatched_quotes:
        previews = [
            q["quote"][:80] + ("..." if len(q["quote"]) > 80 else "")
            for q in unmatched_quotes
        ]
        failures.append(
            f"hallucinated quotes: {len(unmatched_quotes)} source-language quote(s) not found at cited refs: "
            + "; ".join(f"'{p}'" for p in previews)
        )

    if false_absences:
        failures.append(
            "false absence: "
            + "; ".join(
                f"'{f['claim']}' → in library as '{f['matched_title']}'"
                for f in false_absences
            )
        )

    if failures:
        return {
            "score": 0.0,
            "name": NAME,
            "metadata": {
                "reason": " | ".join(failures),
                "checks_failed": [f.split(":")[0] for f in failures],
                "invalid_urls": invalid_urls,
                "unmatched_quotes": unmatched_quotes,
                "false_absences": false_absences,
            },
        }

    reason_parts = []
    if urls:
        suffix = f" (first {MAX_URLS_TO_VALIDATE})" if truncated else ""
        reason_parts.append(f"all {len(urls)} link(s) valid{suffix}")
    if quotes:
        reason_parts.append(f"{len(quotes)} quote(s) matched source")
    if not reason_parts:
        reason_parts.append("no links or quotes to check")

    return {
        "score": 1.0,
        "name": NAME,
        "metadata": {"reason": "; ".join(reason_parts)},
    }
