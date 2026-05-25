"""Tests for the appetizer pipeline."""

from unittest.mock import AsyncMock, patch

import pytest

from ..agent.sefaria_client import SefariaClient


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
