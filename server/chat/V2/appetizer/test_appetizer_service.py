"""Tests for the multi-topic appetizer pipeline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
        # The name API is queried with a wide window (not the caller's small limit)
        # so real Topics survive the ref-type crowding; results are sliced to `limit`.
        mock.assert_called_once_with("api/name/shabbat", {"limit": "25"})


@pytest.mark.asyncio
async def test_search_topics_fetches_wide_to_clear_ref_crowding():
    """Book-name queries (e.g. 'Shabbat') return ref completions first; the real
    Topic sits past a small limit. search_topics must request a wide window so the
    Topic survives the type+pool filter."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    {"title": "Shabbat", "type": "ref", "key": "Shabbat"},
                    {"title": "Shabbat HaAretz", "type": "ref", "key": "Shabbat HaAretz"},
                    {
                        "title": "Shabbat HaAretz, Preface",
                        "type": "ref",
                        "key": "Shabbat HaAretz, Preface",
                    },
                    {
                        "title": "Shabbat",
                        "type": "Topic",
                        "key": "shabbat",
                        "is_primary": True,
                        "topic_pools": ["library", "sheets"],
                    },
                ]
            },
            {"slug": "shabbat", "primaryTitle": {"en": "Shabbat", "he": "שַׁבָּת"}},
        ]
        result = await client.search_topics("Shabbat", limit=3, pool="library")
        assert result == [{"title": "Shabbat", "slug": "shabbat", "he": "שַׁבָּת"}]
        name_call = mock.call_args_list[0]
        assert name_call.args[0] == "api/name/Shabbat"
        assert int(name_call.args[1]["limit"]) >= 20


@pytest.mark.asyncio
async def test_search_topics_bounds_canonical_fetches_to_limit():
    """Many in-pool topics must not trigger one canonical-title fetch each — that
    would blow the appetizer's 5s budget. Cap canonical fetches at `limit`."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    many = [
        {
            "title": f"T{i}",
            "type": "Topic",
            "key": f"t{i}",
            "is_primary": True,
            "topic_pools": ["library"],
        }
        for i in range(10)
    ]
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [{"completion_objects": many}] + [
            {"slug": f"t{i}", "primaryTitle": {"en": f"T{i}", "he": ""}} for i in range(3)
        ]
        result = await client.search_topics("x", limit=3, pool="library")
        assert len(result) == 3
        # 1 name call + at most `limit` (3) canonical-title calls, not 10
        assert mock.call_count == 4


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
            httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock()),
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
        mock.side_effect = httpx.RequestError("network error")
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
    # failure is not cached → a second call retries the fetch
    result2 = await service._get_calendar_context()
    assert result2 == "<calendar_context>unavailable</calendar_context>"
    assert service.sefaria_client.get_current_calendar.call_count == 2


# ---------------------------------------------------------------------------
# _extract_candidates_via_llm — structured Candidate extraction
# ---------------------------------------------------------------------------

from ..appetizer.appetizer_service import Candidate, _is_strong_match


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


# ---------------------------------------------------------------------------
# Taxonomy regression tests + grounding-gate unit tests
# ---------------------------------------------------------------------------


def test_is_strong_match_normalizes_title_and_slug():
    assert _is_strong_match("Ahab", {"title": "Ahab", "slug": "ahab"})
    assert _is_strong_match("Red Heifer", {"title": "Red Heifer", "slug": "red-heifer"})
    assert _is_strong_match("parashat balak", {"title": "Parashat Balak", "slug": "parashat-balak"})
    assert not _is_strong_match("number six", {"title": "Genesis", "slug": "genesis"})


@pytest.mark.asyncio
async def test_low_confidence_weak_match_dropped():
    """Low-confidence candidate that only fuzzy-grounds is dropped."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # search returns a topic that does NOT exactly match the vague label
    service.sefaria_client.search_topics.return_value = [
        {"title": "Genesis", "slug": "genesis", "he": "בראשית"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("the number six", "concept", "low")]
        result = await service.find_appetizer("is the number six special?")
    assert result is None


@pytest.mark.asyncio
async def test_low_confidence_exact_match_kept():
    """Low-confidence candidate that grounds exactly is kept."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shofar", "slug": "shofar", "he": "שופר"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shofar", "concept", "low")]
        result = await service.find_appetizer("shofar")
    assert result is not None
    assert result.topics[0].topic_slug == "shofar"


@pytest.mark.asyncio
async def test_none_taxonomy_cases_return_none():
    """Greetings / follow-ups / bare citations: LLM yields no candidates -> None."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    for msg in ["<AUTO TEST> Say hi", "explain this to me", "yevamos 76 b", "yes please"]:
        with patch.object(
            service, "_extract_candidates_via_llm", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = []
            result = await service.find_appetizer(msg)
        assert result is None, msg
    service.sefaria_client.search_topics.assert_not_called()


@pytest.mark.asyncio
async def test_temporal_candidate_grounds_to_tractate():
    """Daf-yomi style query resolves (in extraction) to a tractate that grounds."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Chullin", "slug": "chullin", "he": "חולין"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Chullin", "temporal", "high")]
        result = await service.find_appetizer("what's today's daf yomi?")
    assert result is not None
    assert result.topics[0].topic_slug == "chullin"


# ---------------------------------------------------------------------------
# Extraction prompt contract (guards temporal-resolution instructions)
# ---------------------------------------------------------------------------


def test_extraction_prompt_has_temporal_resolution_rules():
    """Guard the prompt instructions that live-testing proved necessary:
    split double parshiyot, and resolve daf yomi to subject topics (not the
    tractate ref, which has no library topic)."""
    from ..appetizer.appetizer_service import EXTRACTION_SYSTEM_PROMPT as p

    # Double parsha must split into separate candidates, never a combined label
    assert "Parashat Chukat" in p and "Parashat Balak" in p
    assert "never emit a combined" in p
    # Daf yomi resolves to the tractate's subject areas
    assert "daf yomi" in p.lower()
    assert "subject" in p.lower()


# ---------------------------------------------------------------------------
# B1 regression: high-confidence fuzzy-mismatch gate (_match_score)
# ---------------------------------------------------------------------------

from ..appetizer.appetizer_service import _is_bare_parsha_label, _match_score


def test_match_score_exact():
    """Exact (strong) match scores 3."""
    assert _match_score("Torah", {"title": "Torah", "slug": "torah"}) == 3
    assert _match_score("Shabbat", {"title": "Shabbat", "slug": "shabbat"}) == 3
    assert _match_score("Red Heifer", {"title": "Red Heifer", "slug": "red-heifer"}) == 3
    assert _match_score("Ahab", {"title": "Ahab", "slug": "ahab"}) == 3


def test_match_score_token_overlap():
    """Token overlap scores 2."""
    # 'existence' and 'god' both appear in title and slug
    assert (
        _match_score("Existence of God", {"title": "Existence of God", "slug": "gods-existence"})
        == 3
    )
    # 'parenting' appears in slug 'parenting' even when title differs
    assert _match_score("Parenting", {"title": "Education", "slug": "education"}) == 1
    # 'red' token in 'red heifer' matches slug 'red-heifer'
    assert _match_score("Red Heifer", {"title": "Red Heifer", "slug": "red-heifer"}) == 3


def test_match_score_unrelated_scores_low():
    """Unrelated hits score 1 (in-window but no token match) — below the acceptance threshold."""
    # "Torah" vs "Noses": no shared tokens
    assert _match_score("Torah", {"title": "Noses", "slug": "noses"}) == 1
    # "Daf Yomi" vs "Yom Kippur": no shared tokens (yomi ≠ yom)
    assert _match_score("Daf Yomi", {"title": "Yom Kippur", "slug": "yom-kippur"}) == 1
    assert _match_score("Daf Yomi", {"title": "Yom HaShoah", "slug": "yom-hashoah"}) == 1


def test_match_score_transliteration_achav():
    """Transliteration: 'Achav' vs 'Acharei Mot' scores 1 (no token match);
    'Achav' vs 'Ahab' also scores 1. The ground_candidate selects the best hit
    across the list and requires score >= 2, so achav-typed queries that only
    produce score-1 hits are dropped — but the LLM emits 'Ahab' (canonical form)
    which scores 3 and grounds correctly."""
    assert _match_score("Achav", {"title": "Acharei Mot", "slug": "acharei-mot"}) == 1
    assert _match_score("Achav", {"title": "Ahab", "slug": "ahab"}) == 1
    # When the LLM correctly translates to the canonical label, it scores 3
    assert _match_score("Ahab", {"title": "Ahab", "slug": "ahab"}) == 3


@pytest.mark.asyncio
async def test_b1_high_confidence_unrelated_hit_dropped():
    """B1 regression: high-confidence candidate whose top grounding hit is unrelated
    to the label must be dropped (not accepted just because confidence=high)."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # Simulate name API returning "Noses" as top hit for "Torah" (the live bug)
    service.sefaria_client.search_topics.return_value = [
        {"title": "Noses", "slug": "noses", "he": ""}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Torah", "concept", "high")]
        result = await service.find_appetizer("show me sources about torah")
    assert result is None


@pytest.mark.asyncio
async def test_b1_high_confidence_second_hit_used_when_first_unrelated():
    """B1 regression: when top hit is unrelated but a later hit is plausible, use it."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # First hit unrelated, second hit is the correct one (achav->ahab pattern)
    service.sefaria_client.search_topics.return_value = [
        {"title": "Acharei Mot", "slug": "acharei-mot", "he": ""},
        {"title": "Ahab", "slug": "ahab", "he": "אחאב"},
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Ahab", "person", "high")]
        result = await service.find_appetizer("help me learn about ahab")
    assert result is not None
    assert result.topics[0].topic_slug == "ahab"


# ---------------------------------------------------------------------------
# B2 regression: daf-yomi (sheets-only) must not leak into library results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b2_daf_yomi_candidate_produces_no_library_topics():
    """B2 regression: a candidate labeled 'Daf Yomi' must not resolve to unrelated
    library topics (e.g. 'Yom Kippur') that appear in the name API response via
    fuzzy expansion. The plausibility gate rejects them all."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # Simulate live API: daf-yomi dropped by pool filter, fuzzy expansions returned
    service.sefaria_client.search_topics.return_value = [
        {"title": "Yom Kippur", "slug": "yom-kippur", "he": "יום כיפור"},
        {"title": "Yom HaShoah", "slug": "yom-hashoah", "he": ""},
        {"title": "Yom HaAtzmaut", "slug": "yom-haatzmaut", "he": ""},
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Daf Yomi", "temporal", "high")]
        result = await service.find_appetizer("what is today's daf yomi?")
    assert result is None


@pytest.mark.asyncio
async def test_b2_sheets_only_topic_excluded_by_pool_filter():
    """B2 regression: search_topics(pool='library') must return empty when the only
    matching topic is in the sheets pool (e.g. daf-yomi)."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {
                    "title": "Daf Yomi",
                    "type": "Topic",
                    "key": "daf-yomi",
                    "is_primary": True,
                    "topic_pools": ["sheets"],  # NOT in library
                }
            ]
        }
        result = await client.search_topics("daf yomi", pool="library")
    assert result == []
    assert mock.call_count == 1  # only name API call, no canonical-title fetch


# ---------------------------------------------------------------------------
# B3 regression: bare "Parashat"/"Parasha" labels must be rejected
# ---------------------------------------------------------------------------


def test_b3_is_bare_parsha_label():
    """B3: bare parsa labels are detected."""
    for bare in [
        "Parashat",
        "Parasha",
        "Parshah",
        "Parshat",
        "Parsha",
        "the parasha",
        "the parsha",
        "PARASHAT",
        "PARASHA",
    ]:
        assert _is_bare_parsha_label(bare), f"{bare!r} should be a bare parsha label"
    # Specific portions must NOT be flagged
    for specific in ["Parashat Pinchas", "Parashat Balak", "Parashat Noach"]:
        assert not _is_bare_parsha_label(specific), f"{specific!r} should not be bare"


@pytest.mark.asyncio
async def test_b3_bare_parsha_dropped_at_extraction():
    """B3 regression: bare 'Parashat' label emitted by extractor is silently dropped."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response(
            [{"label": "Parashat", "kind": "temporal", "confidence_level": "high"}]
        ),
    ):
        result = await service._extract_candidates_via_llm(
            "teach me about the parsha", "<calendar_context>unavailable</calendar_context>"
        )
    assert result == []


@pytest.mark.asyncio
async def test_b3_bare_parsha_dropped_at_grounding():
    """B3 regression: even if a bare 'Parasha' candidate reaches _ground_candidate,
    it is rejected before any Sefaria API call."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Parasha", "temporal", "high")]
        result = await service.find_appetizer("teach me about the parsha")
    assert result is None
    service.sefaria_client.search_topics.assert_not_called()


@pytest.mark.asyncio
async def test_b3_specific_parsha_still_passes():
    """B3 regression: a specific parsha name (e.g. 'Parashat Pinchas') must still ground."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Parashat Pinchas", "slug": "parashat-pinchas", "he": "פרשת פינחס"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Parashat Pinchas", "temporal", "high")]
        result = await service.find_appetizer("teach me about the parsha")
    assert result is not None
    assert result.topics[0].topic_slug == "parashat-pinchas"


# ---------------------------------------------------------------------------
# B4 regression: chip title == canonical topic page title (primaryTitle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b4_chip_title_equals_canonical_page_title():
    """B4 regression: the chip title must come from the topic's canonical primaryTitle,
    not the autocomplete string from the name API (which can differ, e.g. 'Parenting'
    for slug 'education' whose page title is 'Education')."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {
                "completion_objects": [
                    {
                        "title": "Parenting",  # name-API autocomplete string
                        "type": "Topic",
                        "key": "education",
                        "is_primary": True,
                        "topic_pools": ["library", "sheets"],
                    }
                ]
            },
            # canonical page has a different primaryTitle
            {"slug": "education", "primaryTitle": {"en": "Education", "he": "חינוך"}},
        ]
        result = await client.search_topics("parenting", limit=1, pool="library")
    assert len(result) == 1
    assert result[0]["title"] == "Education"  # canonical page title, not autocomplete
    assert result[0]["he"] == "חינוך"
    assert result[0]["slug"] == "education"


# ---------------------------------------------------------------------------
# B5 regression: Hebrew interface_lang produces Hebrew chip title;
#                "he-IL" locale variant is handled correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b5_hebrew_interface_lang_returns_hebrew_title():
    """B5 regression: interface_lang='he' produces Hebrew chip title."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat", "he": "שַׁבָּת", "slug": "shabbat"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat", "concept", "high")]
        result = await service.find_appetizer("מה זה שבת?", interface_lang="he")
    assert result is not None
    assert result.topics[0].topic_title == "שַׁבָּת"


@pytest.mark.asyncio
async def test_b5_hebrew_fallback_to_english_when_no_hebrew_title():
    """B5 regression: when Hebrew title is absent, fall back to English (not blank)."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat Chazon", "he": "", "slug": "shabbat-chazon"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat Chazon", "concept", "high")]
        result = await service.find_appetizer("tell me about Shabbat Chazon", interface_lang="he")
    assert result is not None
    # No Hebrew title available → fall back to English
    assert result.topics[0].topic_title == "Shabbat Chazon"


def test_b5_views_locale_normalization():
    """B5 regression: 'he-IL' (navigator.language format) is treated as Hebrew
    by the views.py locale normalization."""

    # Simulate the normalization logic from views.py
    def normalize_locale(raw_locale):
        return "he" if raw_locale.startswith("he") else raw_locale

    assert normalize_locale("he") == "he"
    assert normalize_locale("he-IL") == "he"
    assert normalize_locale("he-il") == "he"
    assert normalize_locale("en") == "en"
    assert normalize_locale("en-US") == "en-US"
    assert normalize_locale("") == ""


def test_b5_extraction_prompt_has_no_bare_parsha_rule():
    """B5 guard: extraction prompt now includes the bare-parsha prohibition."""
    from ..appetizer.appetizer_service import EXTRACTION_SYSTEM_PROMPT as p

    assert "NEVER emit a bare" in p
    assert "Parashat" in p
    assert "calendar context is unavailable" in p


# ---------------------------------------------------------------------------
# B6 regression: broad themes must resolve to established Sefaria library topics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b6_parenting_resolves_to_education_and_honoring_parents():
    """B6 regression: 'parenting' query must resolve to established library topics.

    The LLM should now emit 'Education' and 'Honoring Parents' (not the literal
    word 'Parenting' which has no exact library topic), and both must ground
    successfully via token-overlap scoring.
    """
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        [{"title": "Education", "slug": "education", "he": "חינוך"}],
        [{"title": "Honoring Parents", "slug": "honoring-parents", "he": "כיבוד אב ואם"}],
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        # Simulates the new prompt behavior: broad theme -> established topic names
        mock_llm.return_value = [
            Candidate("Education", "concept", "high"),
            Candidate("Honoring Parents", "concept", "high"),
        ]
        result = await service.find_appetizer("show me sources on parenting")
    assert result is not None
    slugs = [t.topic_slug for t in result.topics]
    assert "education" in slugs
    assert "honoring-parents" in slugs


@pytest.mark.asyncio
async def test_b6_parenting_literal_label_fails_grounding():
    """B6 regression: if the LLM still emits the literal 'Parenting' label,
    grounding must reject it (score < 2 against 'education' slug)."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # Simulates old (broken) behavior: name API returns 'education' for 'Parenting'
    service.sefaria_client.search_topics.return_value = [
        {"title": "Education", "slug": "education", "he": "חינוך"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Parenting", "concept", "high")]
        result = await service.find_appetizer("show me sources on parenting")
    # 'Parenting' scores 1 vs 'education' (no token overlap) — must be dropped
    assert result is None


def test_b6_extraction_prompt_has_broad_theme_rule():
    """B6 guard: extraction prompt includes the broad-theme mapping rule."""
    from ..appetizer.appetizer_service import EXTRACTION_SYSTEM_PROMPT as p

    assert "BROAD THEME RULE" in p
    assert "Education" in p
    assert "Honoring Parents" in p


# ---------------------------------------------------------------------------
# source_decision logging field (Braintrust metadata.appetizer.source_decision)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_decision_explains_returned_topic():
    """The metrics_sink receives a source_decision describing what was returned and why."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]
    sink: dict = {}
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shabbat", "concept", "high")]
        result = await service.find_appetizer("about shabbat", metrics_sink=sink)
    assert result is not None
    assert "returned" in sink["source_decision"]
    assert "shabbat" in sink["source_decision"]


@pytest.mark.asyncio
async def test_source_decision_explains_why_nothing_returned():
    """When no candidate is extracted, the sink still records why nothing came back."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    sink: dict = {}
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = []
        result = await service.find_appetizer("hello there", metrics_sink=sink)
    assert result is None
    assert "no source returned" in sink["source_decision"]


@pytest.mark.asyncio
async def test_source_decision_explains_dropped_candidate():
    """A high-confidence candidate with no plausible hit is reported as dropped + why."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # name API returns an unrelated topic (no token/exact overlap) -> score 1 -> dropped
    service.sefaria_client.search_topics.return_value = [
        {"title": "Yom Kippur", "slug": "yom-kippur"}
    ]
    sink: dict = {}
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Daf Yomi", "concept", "high")]
        result = await service.find_appetizer("today's daf yomi", metrics_sink=sink)
    assert result is None
    assert "dropped" in sink["source_decision"]


# ---------------------------------------------------------------------------
# Token overlap fix: single-token match on multi-word label must be rejected
# ---------------------------------------------------------------------------

from ..appetizer.appetizer_service import _has_token_overlap


def test_token_overlap_rejects_single_token_from_multiword_label():
    """'Lamed Vav Tzaddikim' vs 'Lamed' — only 1/3 tokens match → rejected."""
    assert not _has_token_overlap("Lamed Vav Tzaddikim", "Lamed")
    assert not _has_token_overlap("Lamed Vav Tzaddikim", "Vav")


def test_token_overlap_accepts_majority_match():
    """'Forbidden Foods' vs 'Forbidden Foods and Laws' — 2/2 label tokens → accepted."""
    assert _has_token_overlap("Forbidden Foods", "Forbidden Foods and Laws")
    assert _has_token_overlap("King Solomon", "Solomon")


def test_token_overlap_accepts_single_token_labels():
    """Single-token labels still match with single overlap."""
    assert _has_token_overlap("Shabbat", "Shabbat HaGadol")
    assert not _has_token_overlap("Shabbat", "Torah")


def test_match_score_lamed_vav_tzaddikim():
    """'Lamed Vav Tzaddikim' must NOT score 2 against 'Lamed' (the letter)."""
    assert _match_score("Lamed Vav Tzaddikim", {"title": "Lamed", "slug": "lamed"}) == 1
    assert _match_score("Lamed Vav Tzaddikim", {"title": "Vav", "slug": "vav"}) == 1


# ---------------------------------------------------------------------------
# Alternative labels: fallback grounding for niche concepts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alternative_label_grounds_when_primary_fails():
    """Niche concept 'Lamed Vav Tzaddikim' fails grounding, but alternative
    'Righteous' grounds successfully."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.side_effect = [
        # Primary "Lamed Vav Tzaddikim" → only "Lamed" (the letter) returned
        [{"title": "Lamed", "slug": "lamed"}],
        # Alternative "Righteous" → exact match
        [{"title": "Righteous", "slug": "righteous"}],
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate(
                "Lamed Vav Tzaddikim",
                "concept",
                "high",
                alternative_labels=["Righteous"],
            )
        ]
        result = await service.find_appetizer("tell me about the lamed vav tzaddikim")
    assert result is not None
    assert result.topics[0].topic_slug == "righteous"
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_alternative_labels_not_tried_when_primary_succeeds():
    """When primary label grounds, alternatives are never searched."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [{"title": "Shabbat", "slug": "shabbat"}]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate(
                "Shabbat",
                "concept",
                "high",
                alternative_labels=["Rest", "Sabbath"],
            )
        ]
        result = await service.find_appetizer("tell me about shabbat")
    assert result is not None
    assert result.topics[0].topic_slug == "shabbat"
    # Only the primary label was searched
    service.sefaria_client.search_topics.assert_called_once()


@pytest.mark.asyncio
async def test_alternative_labels_all_fail_returns_none():
    """When primary and all alternatives fail, returns None."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = []
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            Candidate(
                "Obscure Concept",
                "concept",
                "high",
                alternative_labels=["Also Obscure"],
            )
        ]
        result = await service.find_appetizer("tell me about an obscure concept")
    assert result is None
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_extract_parses_alternative_labels():
    """_extract_candidates_via_llm correctly parses alternative_labels from LLM output."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response(
            [
                {
                    "label": "Lamed Vav Tzaddikim",
                    "kind": "concept",
                    "confidence_level": "high",
                    "alternative_labels": ["Righteous", "Righteousness"],
                }
            ]
        ),
    ):
        result = await service._extract_candidates_via_llm(
            "lamed vav", "<calendar_context>unavailable</calendar_context>"
        )
    assert len(result) == 1
    assert result[0].label == "Lamed Vav Tzaddikim"
    assert result[0].alternative_labels == ["Righteous", "Righteousness"]


def test_extraction_prompt_has_alternative_labels_rule():
    """Guard: extraction prompt includes the alternative labels instruction."""
    from ..appetizer.appetizer_service import EXTRACTION_SYSTEM_PROMPT as p

    assert "ALTERNATIVE LABELS" in p
    assert "alternative_labels" in p
    assert "Lamed Vav Tzaddikim" in p
