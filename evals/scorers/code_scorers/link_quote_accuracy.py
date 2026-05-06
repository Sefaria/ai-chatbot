"""Link Quote Accuracy Scorer - validates Sefaria links, quoted source text, and absence claims."""

from typing import Any
from urllib.parse import quote, unquote
import re
import json
import requests

NAME = "Link Quote Accuracy"
SLUG = "links-are-valid-06b8"
DESCRIPTION = (
    "0 if any Sefaria links are invalid, quoted source-language text doesn't match "
    "the cited ref, or the response falsely claims Sefaria lacks a work it has. "
    "Failure metadata identifies which check(s) failed."
)

MAX_URLS_TO_VALIDATE = 20
MAX_REFS_TO_FETCH = 10
MIN_QUOTE_LEN = 8
API_BASE = "https://www.sefaria.org"
TIMEOUT = 5.0

# ── URL extraction ────────────────────────────────────────────────────────────

_HREF_URL_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', flags=re.IGNORECASE)
_PLAIN_URL_RE = re.compile(r'https?://[^\s"<>]+', flags=re.IGNORECASE)
_RESPONSE_LINK_HREF_RE = re.compile(
    r'<a\s+class=["\']response-link["\'][^>]*href=["\']([^"\']+)["\']',
    flags=re.IGNORECASE,
)

# ── Quote / text extraction ───────────────────────────────────────────────────

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

# ── Absence-claim detection ───────────────────────────────────────────────────

_ABSENCE_PATTERNS = [
    # "X is/are not [in/part of/available in/added to] Sefaria('s library/collection)"
    re.compile(
        r"([\w''.\- ]{2,60}?)\s+"
        r"(?:is|are|isn'?t|aren'?t|is\s+not|are\s+not|hasn'?t\s+been|haven'?t\s+been)\s+"
        r"(?:currently\s+|yet\s+)?"
        r"(?:in|part\s+of|included\s+in|available\s+(?:in|on)|added\s+to)\s+"
        r"(?:Sefaria|Sefaria'?s\s+(?:library|collection|database))",
        re.IGNORECASE,
    ),
    # "Sefaria (does not|doesn't) (have|include|contain|carry|host) X"
    re.compile(
        r"Sefaria\s+(?:does\s+not|doesn'?t|do(?:es)n'?t)\s+(?:currently\s+|yet\s+)?"
        r"(?:have|include|contain|carry|host)\s+([\w''.\- ]{2,60})",
        re.IGNORECASE,
    ),
]

_TRAILING_POSSESSIVE_RE = re.compile(r"['']s\b", re.IGNORECASE)
_TRAILING_NOUN_RE = re.compile(
    r"\s+(?:books?|writings?|works?|teachings?|texts?|commentaries?)$", re.IGNORECASE
)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an|any|all|most|many)\s+", re.IGNORECASE)
_RABBINICAL_TITLE_RE = re.compile(r"^(?:rabbi|rebbe)\b", re.IGNORECASE)
_RABBINICAL_PREFIX_RE = re.compile(r"^(?:rabbi|rav|rebbe)\s+(.+)$", re.IGNORECASE)
_OF_PHRASE_RE = re.compile(
    r"^(?:writings?|books?|works?|teachings?)\s+of\s+(.+)$", re.IGNORECASE
)


# ── Shared helpers ────────────────────────────────────────────────────────────


def _maybe_json_unescape(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        try:
            return json.loads(stripped)
        except Exception:
            return text
    return text


def _strip_trailing_punct(url: str) -> str:
    url = url.rstrip("\\")
    return url.rstrip(").,;:!?]}>\"'`")


def _split_url(url: str):
    match = re.match(
        r"^(https?)://([^/]+)(/[^?#]*)?.*$", url.strip(), flags=re.IGNORECASE
    )
    if not match:
        return None, None, None
    scheme = (match.group(1) or "https").lower()
    host = (match.group(2) or "").lower()
    path = match.group(3) or "/"
    return scheme, host, path


def _is_sefaria_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.lower()
    return (
        h == "sefaria.org"
        or h.endswith(".sefaria.org")
        or h == "sefaria.org.il"
        or h.endswith(".sefaria.org.il")
    )


def _quote_path(path: str) -> str:
    return quote(path, safe="")


def _unquote_path(path: str) -> str:
    return unquote(path, encoding="utf-8", errors="replace")


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, list):
        return any(_has_content(v) for v in value)
    if isinstance(value, dict):
        return any(_has_content(v) for v in value.values())
    return bool(value)


def _strip_html(s: str) -> str:
    return _TAG_RE.sub(" ", s or "")


def _normalize(s: str) -> str:
    """Strip HTML + nikud, collapse whitespace, lowercase if no Hebrew."""
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


# ── URL / link validation ─────────────────────────────────────────────────────


def _extract_tref_from_url(url: str) -> str | None:
    scheme, host, path = _split_url(url)
    if not scheme or not _is_sefaria_host(host):
        return None
    p = _unquote_path(path).strip("/")
    if not p:
        return None
    if p.startswith("texts/") or p == "texts":
        return None
    blocked = (
        "sheets/",
        "topics/",
        "collections/",
        "search",
        "api/",
        "about",
        "help",
        "community",
        "donate",
        "account",
        "my/",
    )
    if any(p == b.rstrip("/") or p.startswith(b) for b in blocked):
        return None
    return p or None


def _validate_nontext_page_exists(
    session: requests.Session, url: str
) -> tuple[bool, str]:
    try:
        res = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except Exception as e:
        return False, f"exception: {type(e).__name__}"
    if res.status_code >= 400:
        return False, f"GET status {res.status_code}"
    ct = (res.headers.get("Content-Type") or "").lower()
    if "text/html" in ct:
        body = (res.text or "").lower()
        if ("page not found" in body) or ("404" in body and "not found" in body):
            return False, "GET returned 'page not found' content"
    return True, "ok"


def _validate_tref_as_existing_text_ref(
    session: requests.Session, base_host: str, tref: str
) -> tuple[bool, str]:
    tref = _unquote_path(tref)
    name_res = session.get(f"{base_host}/api/name/{_quote_path(tref)}", timeout=TIMEOUT)
    if name_res.status_code != 200:
        return False, f"/api/name status {name_res.status_code}"
    name_data = name_res.json()
    if not name_data.get("is_ref"):
        return False, "not a ref per /api/name"
    normalized_ref = _unquote_path(name_data.get("url") or name_data.get("ref") or tref)
    if name_data.get("is_node"):
        ok, reason = _validate_nontext_page_exists(
            session, f"{base_host}/{_quote_path(normalized_ref)}"
        )
        return ok, f"node ref; page check: {reason}"
    texts_res = session.get(
        f"{base_host}/api/texts/{_quote_path(normalized_ref)}",
        params={"context": 0, "pad": 0, "commentary": 0},
        timeout=TIMEOUT,
    )
    if texts_res.status_code != 200:
        return False, f"/api/texts status {texts_res.status_code}"
    text_data = texts_res.json()
    if text_data.get("error"):
        return False, f"/api/texts error: {text_data.get('error')}"
    if not (_has_content(text_data.get("text")) or _has_content(text_data.get("he"))):
        return False, "no text content at ref"
    return True, "ok"


def _extract_urls(text: str) -> list[str]:
    text = _maybe_json_unescape(text)
    seen = set()
    out: list[str] = []
    for u in _HREF_URL_RE.findall(text) + _PLAIN_URL_RE.findall(text):
        u = _strip_trailing_punct(u)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _drop_prefix_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    for u in urls:
        is_prefix = any(
            v != u
            and v.startswith(u)
            and v[len(u) : len(u) + 1] in ("'", "%", "_", "-", ".", ":", ";", ",")
            for v in urls
        )
        if not is_prefix:
            out.append(u)
    return out


# ── Quote matching ────────────────────────────────────────────────────────────


def _quote_language(s: str) -> str:
    he = len(_HEBREW_RE.findall(s))
    en = len(_LATIN_RE.findall(s))
    if he > en * 2:
        return "he"
    if en > he * 2:
        return "en"
    return "mixed"


def _extract_quotes(text: str) -> list[tuple[str, str]]:
    """Return [(normalized_quote, language), ...]."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in _QUOTE_RE.findall(text):
        n = _normalize(raw)
        if len(n) < MIN_QUOTE_LEN or n in seen:
            continue
        seen.add(n)
        out.append((n, _quote_language(n)))
    return out


def _extract_response_link_urls(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in _RESPONSE_LINK_HREF_RE.findall(text):
        if "sefaria.org" in u.lower() and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _quote_in_corpus(q: str, corpus: str) -> bool:
    """Ellipsis-aware: split on '...'/'…', require each fragment to appear in corpus."""
    fragments = [f.strip(" .,;:!?\"'") for f in _ELLIPSIS_RE.split(q)]
    fragments = [f for f in fragments if len(f) >= 4]
    if not fragments:
        return q in corpus
    return all(f in corpus for f in fragments)


def _fetch_ref_source(session: requests.Session, tref: str) -> tuple[str, str]:
    """Return (normalized_source_text, source_language). Empty strings on failure."""
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


# ── Absence-claim detection ───────────────────────────────────────────────────


def _clean_capture(phrase: str) -> str:
    phrase = phrase.strip().strip(".,;:'\"")
    phrase = _TRAILING_NOUN_RE.sub("", phrase).strip()
    phrase = _TRAILING_POSSESSIVE_RE.sub("", phrase).strip()
    phrase = _LEADING_ARTICLE_RE.sub("", phrase).strip()
    m = _OF_PHRASE_RE.match(phrase)
    if m:
        phrase = m.group(1).strip()
    return phrase


def _candidate_variants(phrase: str) -> list[str]:
    variants = [phrase]
    m = _RABBINICAL_PREFIX_RE.match(phrase)
    if m:
        variants.append(m.group(1).strip())
    elif not _RABBINICAL_TITLE_RE.match(phrase):
        variants.append(f"Rabbi {phrase}")
    seen, out = set(), []
    for v in variants:
        k = v.lower()
        if v and k not in seen:
            seen.add(k)
            out.append(v)
    return out


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
    claim_norm = _normalize(claim)
    completions = data.get("completion_objects") or []
    for obj in completions:
        if obj.get("type") not in {"ref", "Book", "AuthorTopic"}:
            continue
        if _normalize(obj.get("title") or "") == claim_norm:
            return obj.get("title")
    claim_words = [w for w in claim_norm.split() if len(w) > 2]
    if not claim_words:
        return None
    for obj in completions:
        if obj.get("type") != "AuthorTopic":
            continue
        if "library" not in (obj.get("topic_pools") or []):
            continue
        if all(w in _normalize(obj.get("title") or "") for w in claim_words):
            return obj.get("title")
    for obj in completions:
        if obj.get("type") != "Book":
            continue
        if all(w in _normalize(obj.get("title") or "") for w in claim_words):
            return obj.get("title")
    return None


def _index_api_resolves(session: requests.Session, claim: str) -> str | None:
    try:
        res = session.get(
            f"{API_BASE}/api/v2/index/{_quote_path(claim)}", timeout=TIMEOUT
        )
    except Exception:
        return None
    if res.status_code != 200:
        return None
    try:
        data = res.json()
    except Exception:
        return None
    if data.get("error"):
        return None
    return data.get("title") or claim


def _claim_is_false_absence(session: requests.Session, claim: str) -> str | None:
    for variant in _candidate_variants(claim):
        match = _name_api_resolves(session, variant)
        if match:
            return match
    return _index_api_resolves(session, claim)


# ── Handler ───────────────────────────────────────────────────────────────────


def handler(
    input: Any,
    output: Any,
    expected: Any,
    metadata: dict[str, Any],
):
    text = _response_text(output)
    text = _maybe_json_unescape(text)

    if not text:
        return {"score": 1.0, "name": NAME, "metadata": {"reason": "Empty response"}}

    # ── Check 1: link validity ────────────────────────────────────────────────

    raw_urls = _extract_urls(text)
    sefaria_urls = [u for u in raw_urls if _is_sefaria_host(_split_url(u)[1])]
    seen: set[str] = set()
    urls = [u for u in sefaria_urls if not (u in seen or seen.add(u))]
    urls = _drop_prefix_urls(urls)
    truncated = len(urls) > MAX_URLS_TO_VALIDATE
    urls = urls[:MAX_URLS_TO_VALIDATE]

    invalid_urls: list[str] = []
    validation_details: dict[str, str] = {}
    text_urls: list[str] = []
    nontext_urls: list[str] = []

    # ── Check 2: quote matching + Check 3: absence claims ────────────────────

    quotes = _extract_quotes(text)
    ref_link_urls = _extract_response_link_urls(text)
    false_absences: list[dict[str, str]] = []
    unmatched_quotes: list[dict[str, str]] = []
    ref_source_langs: set[str] = set()
    refs_with_text = 0

    with requests.Session() as session:
        for url in urls:
            scheme, host, _ = _split_url(url)
            base_host = f"{scheme}://{host}" if scheme and host else API_BASE
            tref = _extract_tref_from_url(url)
            try:
                if tref:
                    text_urls.append(url)
                    is_valid, reason = _validate_tref_as_existing_text_ref(
                        session, base_host, tref
                    )
                else:
                    nontext_urls.append(url)
                    is_valid, reason = _validate_nontext_page_exists(session, url)
                if not is_valid:
                    invalid_urls.append(url)
                validation_details[url] = reason
            except Exception as e:
                invalid_urls.append(url)
                validation_details[url] = f"exception: {type(e).__name__}"

        for claim in _detect_absence_claims(text):
            matched = _claim_is_false_absence(session, claim)
            if matched:
                false_absences.append({"claim": claim, "matched_title": matched})

        if quotes and ref_link_urls:
            ref_corpus = ""
            for url in ref_link_urls[:MAX_REFS_TO_FETCH]:
                tref = _extract_tref_from_url(url)
                if not tref:
                    continue
                src_text, src_lang = _fetch_ref_source(session, tref)
                if src_text:
                    ref_corpus += " " + src_text
                    refs_with_text += 1
                if src_lang:
                    ref_source_langs.add(src_lang)
            if ref_corpus.strip():
                for q, q_lang in quotes:
                    if q_lang == "mixed" or q_lang not in ref_source_langs:
                        continue
                    if not _quote_in_corpus(q, ref_corpus):
                        unmatched_quotes.append({"quote": q, "lang": q_lang})

    # ── Collect failures ──────────────────────────────────────────────────────

    failures: list[str] = []

    if invalid_urls:
        detail = f"bad links: {len(invalid_urls)} invalid Sefaria link(s)"
        if truncated:
            detail += f" (validated first {MAX_URLS_TO_VALIDATE})"
        failures.append(detail)

    if false_absences:
        failures.append(
            "false absence: "
            + "; ".join(
                f"'{f['claim']}' → in library as '{f['matched_title']}'"
                for f in false_absences
            )
        )

    if unmatched_quotes:
        previews = [
            q["quote"][:80] + ("..." if len(q["quote"]) > 80 else "")
            for q in unmatched_quotes
        ]
        failures.append(
            f"hallucinated quotes: {len(unmatched_quotes)} source-language quote(s) not found at cited refs: "
            + "; ".join(f"'{p}'" for p in previews)
        )

    if failures:
        return {
            "score": 0.0,
            "name": NAME,
            "metadata": {
                "reason": " | ".join(failures),
                "checks_failed": [f.split(":")[0] for f in failures],
                "invalid_urls": invalid_urls,
                "validation_details": validation_details,
                "false_absences": false_absences,
                "unmatched_quotes": unmatched_quotes,
                "urls_checked": len(urls),
                "quotes_checked": len(quotes),
                "refs_checked": len(ref_link_urls[:MAX_REFS_TO_FETCH]),
                "refs_with_text": refs_with_text,
                "ref_source_langs": sorted(ref_source_langs),
                "truncated": truncated,
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
        "metadata": {
            "reason": "; ".join(reason_parts),
            "urls_checked": len(urls),
            "text_urls": text_urls,
            "nontext_urls": nontext_urls,
            "validation_details": validation_details,
            "quotes_checked": len(quotes),
            "refs_checked": len(ref_link_urls[:MAX_REFS_TO_FETCH]),
            "refs_with_text": refs_with_text,
            "ref_source_langs": sorted(ref_source_langs),
            "truncated": truncated,
        },
    }
