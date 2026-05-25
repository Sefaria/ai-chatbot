"""Tests for the appetizer pipeline."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ..agent.sefaria_client import SefariaClient
from ..appetizer.appetizer_service import AppetizerService


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
        mock.assert_called_once_with("api/name/shabbat", {"type": "topic", "limit": "3"})


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
# AppetizerService tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_appetizer_extracts_topic():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [
        {"title": "Divine Attributes", "slug": "divine-attributes"}
    ]

    with patch.object(service, "_extract_concept", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = "divine attributes"
        result = await service.find_appetizer("What passage in Micah relates to the 13 attributes?")

    assert result is not None
    assert result.topic_slug == "divine-attributes"
    assert result.topic_title == "Divine Attributes"
    assert "divine-attributes" in result.topic_url


@pytest.mark.asyncio
async def test_appetizer_returns_none_when_no_topics():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = "some obscure thing"
        result = await service.find_appetizer("tell me about some obscure thing")

    assert result is None


@pytest.mark.asyncio
async def test_appetizer_returns_none_when_concept_is_none():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()

    with patch.object(service, "_extract_concept", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = None
        result = await service.find_appetizer("hello how are you?")

    assert result is None
    service.sefaria_client.search_topics.assert_not_called()


@pytest.mark.asyncio
async def test_appetizer_returns_none_on_timeout():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()

    async def slow_concept(msg):
        await asyncio.sleep(10)
        return "something"

    with patch.object(service, "_extract_concept", side_effect=slow_concept):
        result = await service.find_appetizer("test")

    assert result is None
