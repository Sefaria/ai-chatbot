"""HTML Format Scorer - validates response HTML structure and length limits."""

from typing import Any
import re

NAME = "HTML Format"
SLUG = "html-format-dc7d"
DESCRIPTION = "Validates response HTML structure, allowed tags, and length limits"


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    """Check if response follows HTML formatting guidelines."""
    # Extract response
    if isinstance(output, dict):
        if "content" in output:
            content = output["content"]
            if isinstance(content, dict) and "text" in content:
                response = content["text"]
            else:
                response = content
        else:
            response = str(output)
    else:
        response = str(output)

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

    if char_count > 1600:
        errors.append(f"Exceeds 1600 character limit: {char_count} characters")
    if word_count > 400:
        errors.append(f"Exceeds 400 word limit: {word_count} words")

    # === ALLOWED HTML PATTERNS ===
    allowed_patterns = [
        # Opening tags with required classes
        r'<p\s+class=["\']response-generic["\'][^>]*>',
        r'<p\s+class=["\']response-signoff["\'][^>]*>',
        r'<ul\s+class=["\']response-list["\'][^>]*>',
        r'<h3\s+class=["\']response-title["\'][^>]*>',
        r'<h4\s+class=["\']response-section["\'][^>]*>',
        r'<a\s+class=["\']response-link["\'][^>]*>',
        r'<span\s+class=["\']response-quote["\'][^>]*>',
        # Closing tags
        r"</p>",
        r"</ul>",
        r"</h3>",
        r"</h4>",
        r"</a>",
        r"</span>",
        # List items (allowed inside ul)
        r"<li[^>]*>",
        r"</li>",
    ]

    # Find all HTML tags in the response
    all_tags = re.findall(r"<[^>]+>", response)

    disallowed_tags = []
    for tag in all_tags:
        is_allowed = False
        for pattern in allowed_patterns:
            if re.match(pattern, tag, re.IGNORECASE):
                is_allowed = True
                break

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
        for pattern in [
            r'<p\s+class=["\']response-generic["\']',
            r'<p\s+class=["\']response-signoff["\']',
            r'<ul\s+class=["\']response-list["\']',
            r'<h3\s+class=["\']response-title["\']',
            r'<h4\s+class=["\']response-section["\']',
        ]
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
