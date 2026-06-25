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
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [{"title": "Kiddush", "slug": "kiddush"}],
        [{"title": "Havdalah", "slug": "havdalah"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("Shabbat", "concept", "high"),
            Candidate("Kiddush", "concept", "high"),
            Candidate("Havdalah", "concept", "high"),
        ]
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
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [],
        [{"title": "Havdalah", "slug": "havdalah"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("Shabbat", "concept", "high"),
            Candidate("Nonexistent", "concept", "high"),
            Candidate("Havdalah", "concept", "high"),
        ]
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
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [{"title": "Shabbat", "slug": "shabbat"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("Shabbat", "concept", "high"),
            Candidate("Sabbath", "concept", "high"),
        ]
        result = await service.find_appetizer("shabbat")

    assert result is not None
    assert len(result.topics) == 1
    assert result.topics[0].topic_slug == "shabbat"


@pytest.mark.asyncio
async def test_first_candidate_hits():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Shabbat", "slug": "shabbat"}],
        [],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("Shabbat", "concept", "high"),
            Candidate("Sabbath", "concept", "high"),
        ]
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
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [],
        [{"title": "Herod", "slug": "herod"}],
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("Herod the Great", "person", "high"),
            Candidate("Herod", "person", "high"),
        ]
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
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [{"title": "Sivan", "slug": "sivan"}]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Sivan", "concept", "high")]
        result = await service.find_appetizer("תן לי מקורות על סיוון כ'")

    assert result is not None
    assert result.topics[0].topic_slug == "sivan"


@pytest.mark.asyncio
async def test_returns_none_when_all_candidates_miss():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate("candidate1", "concept", "high"),
            Candidate("candidate2", "concept", "high"),
        ]
        result = await service.find_appetizer("some obscure thing")

    assert result is None
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_returns_none_when_llm_returns_empty():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = []
        result = await service.find_appetizer("hello how are you?")

    assert result is None
    service.sefaria_client.search_topics.assert_not_called()


@pytest.mark.asyncio
async def test_hebrew_interface_lang_returns_hebrew_title():
    """When interface_lang='he', TopicInfo.topic_title must be the Hebrew title."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # Sefaria name API returns both English and Hebrew title fields
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat", "he": "שַׁבָּת", "slug": "shabbat"}
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat", "concept", "high")]
        result = await service.find_appetizer("מה זה שבת?", interface_lang="he")

    assert result is not None
    assert result.topics[0].topic_slug == "shabbat"
    assert result.topics[0].topic_title == "שַׁבָּת"


@pytest.mark.asyncio
async def test_english_interface_lang_returns_english_title():
    """Default (no lang or lang='en') keeps English title."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat", "he": "שַׁבָּת", "slug": "shabbat"}
    ]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat", "concept", "high")]
        result = await service.find_appetizer("tell me about shabbat")

    assert result is not None
    assert result.topics[0].topic_title == "Shabbat"


@pytest.mark.asyncio
async def test_returns_none_on_timeout():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(10)
        return [{"title": "X", "slug": "x"}]

    service.sefaria_client.search_topics = slow_search
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Test", "concept", "high")]
        result = await service.find_appetizer("test")

    assert result is None


# ---------------------------------------------------------------------------
# _get_canonical_titles tests (SefariaClient)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_canonical_titles_returns_primary_title():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "slug": "education",
            "primaryTitle": {"en": "Education", "he": "חינוך"},
        }
        result = await client._get_canonical_titles("education")
        assert result == {"en": "Education", "he": "חינוך"}
        mock.assert_called_once_with("api/v2/topics/education")


@pytest.mark.asyncio
async def test_get_canonical_titles_returns_none_when_missing():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"slug": "education"}  # no primaryTitle
        result = await client._get_canonical_titles("education")
        assert result is None


@pytest.mark.asyncio
async def test_get_canonical_titles_returns_none_on_exception():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("network error")
        result = await client._get_canonical_titles("education")
        assert result is None


# ---------------------------------------------------------------------------
# search_topics — pool filtering (topic_pools from name API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_topics_pool_filter_keeps_library_topic():
    """Candidate whose topic_pools include 'library' is kept when pool='library'."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    {
                        "title": "Shabbat",
                        "type": "Topic",
                        "key": "shabbat",
                        "topic_pools": ["torah_tab", "library", "sheets"],
                    }
                ]
            },
            # canonical title carries both English and Hebrew
            {"slug": "shabbat", "primaryTitle": {"en": "Shabbat", "he": "שַׁבָּת"}},
        ]
        result = await client.search_topics("shabbat", pool="library")
        assert result == [{"title": "Shabbat", "slug": "shabbat", "he": "שַׁבָּת"}]


@pytest.mark.asyncio
async def test_search_topics_pool_filter_removes_non_library_topic():
    """Candidate whose topic_pools lack 'library' (e.g. sheets-only) is dropped."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {
                    "title": "Daf Yomi",
                    "type": "Topic",
                    "key": "daf-yomi",
                    "topic_pools": ["sheets"],
                }
            ]
        }
        result = await client.search_topics("daf-yomi", pool="library")
        assert result == []
        # only the name API call — no canonical-title fetch for a dropped candidate
        assert mock.call_count == 1


@pytest.mark.asyncio
async def test_search_topics_no_pool_filter_single_call():
    """When pool=None, only the single name API call is made (no pool filter, no title fetch)."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {
                    "title": "Shabbat",
                    "type": "Topic",
                    "key": "shabbat",
                    "topic_pools": ["sheets"],
                }
            ]
        }
        result = await client.search_topics("shabbat")
        # no filtering on topic_pools, title kept as-is from name API
        assert result == [{"title": "Shabbat", "slug": "shabbat"}]
        assert mock.call_count == 1


@pytest.mark.asyncio
async def test_search_topics_pool_filter_uses_canonical_title():
    """With pool set, the canonical page title overrides the name-API title.

    The name API returns title 'Parenting' for key 'education', but the topic
    page /topics/education has primaryTitle 'Education'.
    """
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    {
                        "title": "Parenting",
                        "type": "Topic",
                        "key": "education",
                        "topic_pools": ["library", "sheets"],
                    }
                ]
            },
            {"slug": "education", "primaryTitle": {"en": "Education", "he": "חינוך"}},
        ]
        result = await client.search_topics("parenting", pool="library")
        assert result == [{"title": "Education", "slug": "education", "he": "חינוך"}]


@pytest.mark.asyncio
async def test_search_topics_pool_filter_falls_back_to_name_title():
    """When the page has no canonical primaryTitle, the name-API title is kept."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    {
                        "title": "Herod",
                        "type": "PersonTopic",
                        "key": "herod",
                        "topic_pools": ["library", "sheets"],
                    }
                ]
            },
            {"slug": "herod"},  # no primaryTitle on page
        ]
        result = await client.search_topics("herod", pool="library")
        # no canonical title → English falls back to name-API title, Hebrew is empty
        assert result == [{"title": "Herod", "slug": "herod", "he": ""}]


@pytest.mark.asyncio
async def test_search_topics_pool_filter_skips_slug_fallback():
    """With pool set and zero library candidates, returns [] without slug fallback.

    This prevents the bare 'parashat' topic doc from leaking in via the
    slug-fallback path.
    """
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"completion_objects": []}  # name API misses
        result = await client.search_topics("parashat", pool="library")
        assert result == []
        # no slug-fallback call to api/v2/topics
        assert mock.call_count == 1


@pytest.mark.asyncio
async def test_appetizer_passes_pool_library_to_search_topics():
    """AppetizerService passes pool='library' to search_topics."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]

    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat", "concept", "high")]
        result = await service.find_appetizer("tell me about shabbat")

    assert result is not None
    service.sefaria_client.search_topics.assert_called_once_with("Shabbat", limit=3, pool="library")


# ---------------------------------------------------------------------------
# Regression: timeout constant
# ---------------------------------------------------------------------------


def test_appetizer_timeout_is_at_most_5_seconds():
    """Hard budget is 5 seconds — changing this requires explicit review."""
    from ..appetizer.appetizer_service import APPETIZER_TIMEOUT_SECONDS

    assert APPETIZER_TIMEOUT_SECONDS <= 5


# ---------------------------------------------------------------------------
# Regression: search_topics primary-over-alias ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_topics_primary_preferred_over_alias():
    """With pool='library', is_primary=True entries are sorted before aliases.

    Simulates the live case where 'Parshat Noah' (non-primary PersonTopic alias
    for slug 'noah') appears before 'Parashat Noach' (primary Topic) in the raw
    autocomplete order, but the primary entry should win as topics[0].
    """
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    # Non-primary alias first in raw autocomplete
                    {
                        "title": "Parshat Noah",
                        "type": "PersonTopic",
                        "key": "noah",
                        "is_primary": False,
                        "topic_pools": ["library", "general_en", "sheets"],
                    },
                    # Primary exact parsha topic second
                    {
                        "title": "Parashat Noach",
                        "type": "Topic",
                        "key": "parashat-noach",
                        "is_primary": True,
                        "topic_pools": ["library", "sheets"],
                    },
                ]
            },
            # Canonical title for parashat-noach (fetched first after sorting)
            {"slug": "parashat-noach", "primaryTitle": {"en": "Parashat Noach", "he": "פרשת נח"}},
            # Canonical title for noah
            {"slug": "noah", "primaryTitle": {"en": "Noah", "he": "נח"}},
        ]
        result = await client.search_topics("parashat noach", pool="library")

    assert result[0]["slug"] == "parashat-noach"
    assert result[1]["slug"] == "noah"


# ---------------------------------------------------------------------------
# AppetizerService._get_calendar_context — daily cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_context_cached_per_day():
    service = AppetizerService.__new__(AppetizerService)
    service._calendar_cache = None
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {
        "Gregorian Date": "2026-06-25T09:00:00",
        "calendar_items": [
            {"title": {"en": "Daf Yomi"}, "displayValue": {"en": "Chullin 56"}},
        ],
    }
    first = await service._get_calendar_context()
    second = await service._get_calendar_context()
    assert "daf_yomi: Chullin 56" in first
    assert first == second
    service.sefaria_client.get_current_calendar.assert_called_once()  # cached


@pytest.mark.asyncio
async def test_calendar_context_unavailable_on_error():
    service = AppetizerService.__new__(AppetizerService)
    service._calendar_cache = None
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.side_effect = Exception("boom")
    result = await service._get_calendar_context()
    assert result == "<calendar_context>unavailable</calendar_context>"


# ---------------------------------------------------------------------------
# _extract_candidates_via_llm — structured Candidate extraction
# ---------------------------------------------------------------------------

from ..appetizer.appetizer_service import Candidate


def _fake_tool_response(candidates):
    resp = MagicMock()
    block = MagicMock()
    block.input = {"candidates": candidates}
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_extract_parses_candidates():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response(
            [
                {"label": "Parenting", "kind": "concept", "confidence_level": "high"},
                {"label": "", "kind": "concept", "confidence_level": "low"},  # dropped: empty label
            ]
        ),
    ):
        result = await service._extract_candidates_via_llm(
            "sources on parenting", "<calendar_context>unavailable</calendar_context>"
        )
    assert result == [Candidate(label="Parenting", kind="concept", confidence_level="high")]


@pytest.mark.asyncio
async def test_extract_empty_is_none():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response([]),
    ):
        result = await service._extract_candidates_via_llm(
            "yes please", "<calendar_context>unavailable</calendar_context>"
        )
    assert result == []


@pytest.mark.asyncio
async def test_extract_returns_empty_on_exception():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        side_effect=Exception("api down"),
    ):
        result = await service._extract_candidates_via_llm(
            "anything", "<calendar_context>unavailable</calendar_context>"
        )
    assert result == []
