"""HTML Format Scorer - validates response HTML structure and length limits."""

from typing import Any
import re

NAME = "HTML Format"
SLUG = "html-format-dc7d"
DESCRIPTION = "Validates response HTML structure, allowed tags, and length limits"

# Length limits
MAX_CHAR_COUNT = 1600
MAX_WORD_COUNT = 400

# Opening tags with required classes
ALLOWED_OPENING_PATTERNS = [
    r'<p\s+class=["\']response-generic["\'][^>]*>',
    r'<p\s+class=["\']response-signoff["\'][^>]*>',
    r'<ul\s+class=["\']response-list["\'][^>]*>',
    r'<h3\s+class=["\']response-title["\'][^>]*>',
    r'<h4\s+class=["\']response-section["\'][^>]*>',
    r'<a\s+class=["\']response-link["\'][^>]*>',
    r'<span\s+class=["\']response-quote["\'][^>]*>',
]

# Closing tags
ALLOWED_CLOSING_PATTERNS = [
    r"</p>",
    r"</ul>",
    r"</h3>",
    r"</h4>",
    r"</a>",
    r"</span>",
]

# List items (allowed inside ul)
ALLOWED_LIST_PATTERNS = [
    r"<li[^>]*>",
    r"</li>",
]

ALLOWED_PATTERNS = ALLOWED_OPENING_PATTERNS + ALLOWED_CLOSING_PATTERNS + ALLOWED_LIST_PATTERNS

# Required structure patterns
REQUIRED_STRUCTURE_PATTERNS = [
    r'<p\s+class=["\']response-generic["\']',
    r'<p\s+class=["\']response-signoff["\']',
    r'<ul\s+class=["\']response-list["\']',
    r'<h3\s+class=["\']response-title["\']',
    r'<h4\s+class=["\']response-section["\']',
]


def _extract_response(output: Any) -> str:
    """Extract response text from output, handling nested dict structures."""
    content = output.get("content") if isinstance(output, dict) else None
    if isinstance(content, dict):
        return content.get("text", str(content))
    elif content is not None:
        return content
    else:
        return str(output)


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    """Check if response follows HTML formatting guidelines."""
    response = _extract_response(output)

    # Check if response contains any HTML
    if not re.search(r"<[^>]+>", response):
        return {
            "score": None,
            "name": NAME,
            "metadata": {
                "reason": "No HTML tags found - response may be error/clarification",
                "choice": "c",
            },
        }

    errors = []

    # === LENGTH CHECK ===
    text_only = re.sub(r"<[^>]*>", "", response)
    text_only = re.sub(r"\s+", " ", text_only).strip()

    char_count = len(text_only)
    word_count = len(text_only.split()) if text_only else 0

    if char_count > MAX_CHAR_COUNT:
        errors.append(f"Exceeds {MAX_CHAR_COUNT} character limit: {char_count} characters")
    if word_count > MAX_WORD_COUNT:
        errors.append(f"Exceeds {MAX_WORD_COUNT} word limit: {word_count} words")

    # Find all HTML tags in the response
    all_tags = re.findall(r"<[^>]+>", response)

    disallowed_tags = []
    for tag in all_tags:
        is_allowed = any(re.match(pattern, tag, re.IGNORECASE) for pattern in ALLOWED_PATTERNS)
        if not is_allowed:
            if re.match(r"<(p|ul|h3|h4|a|span)\s", tag, re.IGNORECASE):
                disallowed_tags.append(f"{tag} (incorrect or missing class)")
            else:
                disallowed_tags.append(tag)

    if disallowed_tags:
        unique_disallowed = list(set(disallowed_tags))[:5]
        errors.append(f"Disallowed HTML tags: {unique_disallowed}")

    # === CHECK FOR REQUIRED STRUCTURE ===
    has_valid_element = any(
        re.search(pattern, response, re.IGNORECASE)
        for pattern in REQUIRED_STRUCTURE_PATTERNS
    )

    if not has_valid_element:
        errors.append(
            "No valid response building blocks found (missing response-* classes)"
        )

    # === RETURN RESULT ===
    if errors:
        return {
            "score": 0.0,
            "name": NAME,
            "metadata": {
                "choice": "b",
                "errors": errors,
                "char_count": char_count,
                "word_count": word_count,
            },
        }
    else:
        return {
            "score": 1.0,
            "name": NAME,
            "metadata": {
                "choice": "a",
                "char_count": char_count,
                "word_count": word_count,
            },
        }
