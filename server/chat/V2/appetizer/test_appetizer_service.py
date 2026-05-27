"""Tests for the Haiku-first appetizer pipeline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..agent.sefaria_client import SefariaClient
from ..appetizer.appetizer_service import AppetizerService

# ---------------------------------------------------------------------------
# search_topics tests (SefariaClient)
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
async def test_search_topics_slug_fallback():
    """When name API returns no topics, falls back to direct slug lookup."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {"completion_objects": [{"title": "Shabbat 2a", "type": "ref", "key": "Shabbat.2a"}]},
            {"slug": "shabbat", "primaryTitle": {"en": "Shabbat"}},
        ]
        result = await client.search_topics("Shabbat")
        assert result == [{"title": "Shabbat", "slug": "shabbat"}]
        assert mock.call_count == 2


@pytest.mark.asyncio
async def test_search_topics_empty_result():
    """Both name API and slug fallback return nothing."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {"completion_objects": []},
            Exception("404"),
        ]
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
# AppetizerService — Haiku-first flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_haiku_finds_topic():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "Shabbat"
        result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    assert result.topic_slug == "shabbat"
    assert result.topic_title == "Shabbat"
    assert result.topic_url == "https://www.sefaria.org/topics/shabbat"
    mock_haiku.assert_called_once()
    service.sefaria_client.search_topics.assert_called_once_with("Shabbat", limit=3)


@pytest.mark.asyncio
async def test_haiku_extracts_from_hebrew():
    """Hebrew prompts are handled by Haiku (not a brittle regex)."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [{"title": "Sivan", "slug": "sivan"}]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "Sivan"
        result = await service.find_appetizer("תן לי מקורות על סיוון כ'")

    assert result is not None
    assert result.topic_slug == "sivan"
    mock_haiku.assert_called_once()


@pytest.mark.asyncio
async def test_returns_none_when_search_misses():
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

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = None
        result = await service.find_appetizer("hello how are you?")

    assert result is None
    service.sefaria_client.search_topics.assert_not_called()


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
