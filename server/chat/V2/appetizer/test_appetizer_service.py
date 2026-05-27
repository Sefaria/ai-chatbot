"""Tests for the multi-topic appetizer pipeline."""

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
# AppetizerService — multi-topic flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_multiple_topics():
    """All 3 candidates match → returns 3 topics."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [{"title": "Kiddush", "slug": "kiddush"}],
        [{"title": "Havdalah", "slug": "havdalah"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Shabbat", "Kiddush", "Havdalah"]
        result = await service.find_appetizer("tell me about shabbat rituals")

    assert result is not None
    assert len(result.topics) == 3
    assert result.topics[0].topic_slug == "shabbat"
    assert result.topics[1].topic_slug == "kiddush"
    assert result.topics[2].topic_slug == "havdalah"
    assert service.sefaria_client.search_topics.call_count == 3


@pytest.mark.asyncio
async def test_partial_topics_on_mixed_hits():
    """2 of 3 candidates match → returns 2 topics."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [],
        [{"title": "Havdalah", "slug": "havdalah"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Shabbat", "Nonexistent", "Havdalah"]
        result = await service.find_appetizer("shabbat stuff")

    assert result is not None
    assert len(result.topics) == 2
    assert result.topics[0].topic_slug == "shabbat"
    assert result.topics[1].topic_slug == "havdalah"


@pytest.mark.asyncio
async def test_deduplicates_topics_by_slug():
    """Two candidates resolve to the same slug → only one topic returned."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [{"title": "Shabbat", "slug": "shabbat"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Shabbat", "Sabbath"]
        result = await service.find_appetizer("shabbat")

    assert result is not None
    assert len(result.topics) == 1
    assert result.topics[0].topic_slug == "shabbat"


@pytest.mark.asyncio
async def test_first_candidate_hits():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Shabbat", "Sabbath"]
        result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    assert result.topics[0].topic_slug == "shabbat"
    mock_llm.assert_called_once()
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_fallback_to_second_candidate():
    """First candidate misses, second hits — models Herod the Great → Herod."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.side_effect = [
        [],
        [{"title": "Herod", "slug": "herod"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Herod the Great", "Herod"]
        result = await service.find_appetizer(
            "are there any sources in the yerushalmi about king herod the great"
        )

    assert result is not None
    assert result.topics[0].topic_slug == "herod"
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_hebrew_prompt():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [{"title": "Sivan", "slug": "sivan"}]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["Sivan"]
        result = await service.find_appetizer("תן לי מקורות על סיוון כ'")

    assert result is not None
    assert result.topics[0].topic_slug == "sivan"


@pytest.mark.asyncio
async def test_returns_none_when_all_candidates_miss():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ["candidate1", "candidate2"]
        result = await service.find_appetizer("some obscure thing")

    assert result is None
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_returns_none_when_llm_returns_empty():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = []
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
