"""Sefaria Link are Valid Scorer - validates all Sefaria links in responses."""

from typing import Any
import re
import json
import requests

NAME = "Sefaria Link are Valid"
SLUG = "link-are-valid-06b8"
DESCRIPTION = "1 if all Sefaria links are valid. 0 if there are any non valid Sefaria links"

# Prefer href extraction when HTML is present
_HREF_URL_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', flags=re.IGNORECASE)
# Plain URL extraction (fallback + union)
_PLAIN_URL_RE = re.compile(r'https?://[^\s"<>]+', flags=re.IGNORECASE)


def _maybe_json_unescape(s: str) -> str:
    """
    If output is a JSON-encoded string (common when model output is serialized),
    decode it so sequences like \\\" become ".
    """
    if not isinstance(s, str):
        return str(s)
    t = s.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        try:
            return json.loads(t)
        except Exception:
            return s
    return s


def _strip_trailing_punct(url: str) -> str:
    url = url.rstrip("\\")
    return url.rstrip(').,;:!?]}>\"\'`')


def _split_url(url: str):
    """
    Minimal URL split: returns (scheme, host, path)
    """
    m = re.match(r"^(https?)://([^/]+)(/[^?#]*)?.*$", url.strip(), flags=re.IGNORECASE)
    if not m:
        return None, None, None
    scheme = (m.group(1) or "https").lower()
    host = (m.group(2) or "").lower()
    path = m.group(3) or "/"
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


def _quote(s: str) -> str:
    """
    Minimal UTF-8 percent-encoder for URL path segments.
    Encodes all non-unreserved chars, using UTF-8 bytes (important for Hebrew, etc).
    """
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out: list[str] = []
    for ch in s:
        if ch in safe:
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append(f"%{b:02X}")
    return "".join(out)


def _unquote(s: str) -> str:
    """
    Minimal percent-decoder (UTF-8 aware).
    """
    bs = bytearray()
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                bs.append(int(s[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        bs.extend(s[i].encode("utf-8"))
        i += 1
    try:
        return bs.decode("utf-8")
    except Exception:
        return bs.decode("utf-8", errors="replace")


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


def _extract_tref_from_url(url: str) -> str | None:
    """
    If this is a Sefaria *text* URL, return tref candidate, else None.

    Important special-case:
    - /texts/... are TOC/category pages (like https://www.sefaria.org/texts/Talmud)
      and should be validated via GET, not /api/texts.
    """
    scheme, host, path = _split_url(url)
    if not scheme or not _is_sefaria_host(host):
        return None

    p = _unquote(path).strip("/")
    if not p:
        return None

    # /texts/... are category/TOC pages, not refs for /api/texts purposes
    if p.startswith("texts/") or p == "texts":
        return None

    # Reject obvious non-text pages
    blocked_prefixes = (
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
    if any(p == b.rstrip("/") or p.startswith(b) for b in blocked_prefixes):
        return None

    return p or None


def _validate_nontext_page_exists(
    session: requests.Session,
    url: str,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """
    Non-text (or TOC-like) Sefaria URLs: just GET and require non-error.
    Note: we don't try to assert semantic correctness, only that the page loads.
    """
    try:
        res = session.get(url, timeout=timeout, allow_redirects=True)
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
    session: requests.Session,
    base_host: str,
    tref: str,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """
    Validation for text-ish refs:
    - Use /api/name to parse and normalize.
    - If it's a TOC/schema node (is_node), validate by GET page exists.
    - Otherwise require /api/texts to return no error and contain content.
    """
    tref = _unquote(tref)

    name_url = f"{base_host}/api/name/{_quote(tref)}"
    name_res = session.get(name_url, timeout=timeout)
    if name_res.status_code != 200:
        return False, f"/api/name status {name_res.status_code}"

    name_data = name_res.json()
    if not name_data.get("is_ref"):
        return False, "not a ref per /api/name"

    normalized_ref = _unquote(name_data.get("url") or name_data.get("ref") or tref)

    # Node refs often have no /api/texts representation; validate by GET page exists
    if name_data.get("is_node"):
        page_url = f"{base_host}/{_quote(normalized_ref)}"
        ok, reason = _validate_nontext_page_exists(session=session, url=page_url, timeout=timeout)
        return ok, f"node ref; page check: {reason}"

    texts_url = f"{base_host}/api/texts/{_quote(normalized_ref)}"
    texts_res = session.get(
        texts_url,
        params={"context": 0, "pad": 0, "commentary": 0},
        timeout=timeout,
    )
    if texts_res.status_code != 200:
        return False, f"/api/texts status {texts_res.status_code}"

    text_data = texts_res.json()
    if text_data.get("error"):
        return False, f"/api/texts error: {text_data.get('error')}"

    has_en = _has_content(text_data.get("text"))
    has_he = _has_content(text_data.get("he"))
    if not (has_en or has_he):
        return False, "no text content at ref"

    return True, "ok"


def _extract_urls(text: str) -> list[str]:
    """
    Robust URL extraction:
    - JSON-unescape if needed (fixes href=\\\"...\\\")
    - Extract both href URLs and plain URLs, then union + dedupe
    """
    text = _maybe_json_unescape(text)

    href_urls = _HREF_URL_RE.findall(text)
    plain_urls = _PLAIN_URL_RE.findall(text)

    seen = set()
    out: list[str] = []
    for u in href_urls + plain_urls:
        u = _strip_trailing_punct(u)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _drop_prefix_urls(urls: list[str]) -> list[str]:
    """
    Remove extracted URLs that are strict prefixes of other extracted URLs.
    This fixes broken HTML like href='...Cohen's...' which can yield both:
      - ...Hermann_Cohen   (truncated)
      - ...Hermann_Cohen's_Religion... (real)
    """
    out: list[str] = []
    for u in urls:
        is_prefix = False
        for v in urls:
            if u == v:
                continue
            if v.startswith(u) and len(v) > len(u):
                nxt = v[len(u) : len(u) + 1]
                if nxt in ("'", "%", "_", "-", ".", ":", ";", ","):
                    is_prefix = True
                    break
        if not is_prefix:
            out.append(u)
    return out


def handler(
    input: Any,
    output: Any,
    expected: Any,
    metadata: dict[str, Any],
):
    if isinstance(output, str):
        text = output
    elif isinstance(output, dict):
        text = output.get("content") or output.get("response") or str(output)
    else:
        text = str(output)

    text = _maybe_json_unescape(text)

    raw_urls = _extract_urls(text)

    # Filter Sefaria hosts
    sefaria_urls: list[str] = []
    for u in raw_urls:
        _, host, _ = _split_url(u)
        if _is_sefaria_host(host):
            sefaria_urls.append(u)

    # Deduplicate preserving order
    seen = set()
    urls = [u for u in sefaria_urls if not (u in seen or seen.add(u))]

    # Drop strict-prefix garbage URLs (Cohen's-style truncations)
    urls = _drop_prefix_urls(urls)

    if not urls:
        return {
            "score": 1.0,
            "name": NAME,
            "metadata": {
                "urls_found": [],
                "reason": "No Sefaria links found - passing by default",
            },
        }

    invalid_urls: list[str] = []
    validation_details: dict[str, str] = {}
    text_urls: list[str] = []
    nontext_urls: list[str] = []

    with requests.Session() as session:
        for url in urls:
            scheme, host, _ = _split_url(url)
            base_host = f"{scheme}://{host}" if scheme and host else "https://www.sefaria.org"

            tref = _extract_tref_from_url(url)
            try:
                if tref:
                    text_urls.append(url)
                    is_valid, reason = _validate_tref_as_existing_text_ref(
                        session=session,
                        base_host=base_host,
                        tref=tref,
                        timeout=5.0,
                    )
                else:
                    nontext_urls.append(url)
                    is_valid, reason = _validate_nontext_page_exists(
                        session=session,
                        url=url,
                        timeout=5.0,
                    )

                if not is_valid:
                    invalid_urls.append(url)
                validation_details[url] = reason
            except Exception as e:
                invalid_urls.append(url)
                validation_details[url] = f"exception: {type(e).__name__}"

    if invalid_urls:
        return {
            "score": 0.0,
            "name": NAME,
            "metadata": {
                "urls_found": urls,
                "text_urls": text_urls,
                "nontext_urls": nontext_urls,
                "invalid_urls": invalid_urls,
                "validation_details": validation_details,
                "reason": f"Found {len(invalid_urls)} invalid Sefaria link(s)",
            },
        }

    return {
        "score": 1.0,
        "name": NAME,
        "metadata": {
            "urls_found": urls,
            "text_urls": text_urls,
            "nontext_urls": nontext_urls,
            "validation_details": validation_details,
            "reason": f"All {len(urls)} Sefaria link(s) are valid",
        },
    }
