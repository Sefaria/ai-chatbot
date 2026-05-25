"""Tests for the two-tier appetizer pipeline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..agent.sefaria_client import SefariaClient
from ..appetizer.appetizer_service import (
    AppetizerService,
    _extract_query_words,
)

# ---------------------------------------------------------------------------
# _extract_query_words tests
# ---------------------------------------------------------------------------


def test_extract_query_words_strips_common_prefixes():
    assert _extract_query_words("find me sources about Shabbat") == "Shabbat"
    assert _extract_query_words("tell me about the divine attributes") == "the divine attributes"
    assert _extract_query_words("what does the Torah say about time") == "time"


def test_extract_query_words_strips_please_variants():
    assert _extract_query_words("please find sources about Rambam") == "Rambam"
    assert _extract_query_words("can you show me texts on prayer") == "prayer"


def test_extract_query_words_preserves_short_queries():
    assert _extract_query_words("Shabbat") == "Shabbat"
    assert _extract_query_words("Rambam") == "Rambam"
    assert _extract_query_words("divine attributes") == "divine attributes"


def test_extract_query_words_handles_hebrew():
    assert _extract_query_words("מה אומרת התורה על שבת") == "מה אומרת התורה על שבת"


def test_extract_query_words_strips_trailing_punctuation():
    assert _extract_query_words("what is Shabbat?") == "Shabbat"
    assert _extract_query_words("tell me about prayer.") == "prayer"


def test_extract_query_words_empty():
    assert _extract_query_words("") == ""
    assert _extract_query_words("   ") == ""


# ---------------------------------------------------------------------------
# search_topics tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_topics_returns_first_match():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {"title": "Shabbat", "type": "Topic", "key": "shabbat"},
                {"title": "Shabbat HaGadol", "type": "Topic", "key": "shabbat-hagadol"},
            ]
        }
        result = await client.search_topics("shabbat", limit=3)
        assert result == [
            {"title": "Shabbat", "slug": "shabbat"},
            {"title": "Shabbat HaGadol", "slug": "shabbat-hagadol"},
        ]
        mock.assert_called_once_with("api/name/shabbat", {"limit": "3"})


@pytest.mark.asyncio
async def test_search_topics_empty_result():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"completion_objects": []}
        result = await client.search_topics("xyznonexistent")
        assert result == []


@pytest.mark.asyncio
async def test_search_topics_filters_non_topic_types():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {"title": "Shabbat", "type": "Topic", "key": "shabbat"},
                {"title": "Shabbat", "type": "TocCategory", "key": "shabbat-cat"},
                {"title": "Shabbat 2a", "type": "ref", "key": "Shabbat.2a"},
            ]
        }
        result = await client.search_topics("shabbat")
        assert len(result) == 1
        assert result[0]["slug"] == "shabbat"


# ---------------------------------------------------------------------------
# AppetizerService — Tier 1 (direct keyword search)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_finds_topic_directly():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]

    result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    assert result.topic_slug == "shabbat"
    assert result.topic_title == "Shabbat"
    assert result.topic_url == "https://www.sefaria.org/topics/shabbat"
    service.sefaria_client.search_topics.assert_called_once_with("Shabbat", limit=3)


@pytest.mark.asyncio
async def test_tier1_skips_haiku_when_topic_found():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    mock_haiku.assert_not_called()


# ---------------------------------------------------------------------------
# AppetizerService — Tier 2 (Haiku fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_falls_back_to_haiku_when_no_direct_match():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [],
        [{"title": "Torah and Secular Wisdom", "slug": "torah-and-secular-wisdom"}],
    ]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "Torah and secular wisdom"
        result = await service.find_appetizer(
            "if a person mixes Torah thinking with outside wisdom"
        )

    assert result is not None
    assert result.topic_slug == "torah-and-secular-wisdom"
    mock_haiku.assert_called_once()
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_returns_none_when_both_tiers_miss():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "some obscure concept"
        result = await service.find_appetizer("tell me about some obscure thing")

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_haiku_returns_none():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = None
        result = await service.find_appetizer("hello how are you?")

    assert result is None
    service.sefaria_client.search_topics.assert_called_once()


@pytest.mark.asyncio
async def test_returns_none_on_timeout():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(10)
        return [{"title": "X", "slug": "x"}]

    service.sefaria_client.search_topics = slow_search
    result = await service.find_appetizer("test")

    assert result is None
