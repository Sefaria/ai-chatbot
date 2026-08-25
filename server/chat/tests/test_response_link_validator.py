from unittest.mock import AsyncMock

import pytest

from chat.V2.agent.response_link_validator import (
    ResponseLinkValidator,
    extract_hrefs,
    sefaria_text_ref_from_href,
)


def test_extract_hrefs_from_html_and_markdown():
    response = (
        '<a href="https://www.sefaria.org/Genesis.1.1">Genesis</a> '
        "[Nahum](https://www.sefaria.org/Nahum.2)"
    )
    assert extract_hrefs(response) == [
        "https://www.sefaria.org/Genesis.1.1",
        "https://www.sefaria.org/Nahum.2",
    ]


def test_topic_links_are_not_ref_validated():
    tref, issue = sefaria_text_ref_from_href("https://www.sefaria.org/topics/shabbat")
    assert tref is None
    assert issue is None


def test_multi_segment_sefaria_links_are_not_ref_validated():
    tref, issue = sefaria_text_ref_from_href("https://www.sefaria.org/sheets/123")
    assert tref is None
    assert issue is None


def test_external_links_are_rejected():
    tref, issue = sefaria_text_ref_from_href("https://www.deadseascrolls.org.il/")
    assert tref is None
    assert issue == "External, non-Sefaria URL"


@pytest.mark.asyncio
async def test_valid_text_ref_link_passes():
    client = AsyncMock()
    client.strict_resolve_ref.return_value = {
        "is_ref": True,
        "url_ref": "Genesis.1.1",
        "en": "Genesis 1:1",
        "he": "בראשית א׳:א׳",
    }
    validator = ResponseLinkValidator(client)

    result = await validator.validate_response(
        '<a href="https://www.sefaria.org/Genesis.1.1">Genesis</a>'
    )

    assert result.is_valid is True
    client.strict_resolve_ref.assert_awaited_once_with("Genesis.1.1")


@pytest.mark.asyncio
async def test_invalid_text_ref_link_fails():
    client = AsyncMock()
    client.strict_resolve_ref.return_value = None
    validator = ResponseLinkValidator(client)

    result = await validator.validate_response(
        '<a href="https://www.sefaria.org/Not_A_Ref.1">Bad ref</a>'
    )

    assert result.is_valid is False
    assert result.issues[0].href == "https://www.sefaria.org/Not_A_Ref.1"
    assert result.issues[0].reason == "Invalid Sefaria text ref"
