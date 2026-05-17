"""Tests for the cached catalog service backed by api/index."""

from unittest.mock import AsyncMock, Mock

import pytest

from chat.V2.agent.catalog_service import CatalogService


@pytest.fixture
def sample_catalog():
    return [
        {
            "category": "Tanakh",
            "heCategory": 'תנ"ך',
            "enDesc": "Hebrew Bible",
            "heDesc": 'תנ"ך',
            "enShortDesc": "Root category",
            "heShortDesc": "שורש",
            "contents": [
                {
                    "category": "Torah",
                    "heCategory": "תורה",
                    "enDesc": "Five Books of Moses",
                    "heDesc": "חמשה חומשי תורה",
                    "enShortDesc": "Torah",
                    "heShortDesc": "תורה",
                    "contents": [
                        {
                            "title": "Genesis",
                            "heTitle": "בראשית",
                            "categories": ["Tanakh", "Torah"],
                            "primary_category": "Tanakh",
                            "corpus": "Tanakh",
                            "enShortDesc": "Creation and the patriarchs",
                            "heShortDesc": "בריאה והאבות",
                            "order": 1,
                            "authors": [
                                {
                                    "en": "Moses",
                                    "he": "משה",
                                    "slug": "moses",
                                }
                            ],
                        },
                        {
                            "title": "Rashi on Genesis",
                            "heTitle": 'רש"י על בראשית',
                            "categories": ["Tanakh", "Torah", "Commentary"],
                            "primary_category": "Commentary",
                            "dependence": "Commentary",
                            "commentator": "Rashi",
                            "heCommentator": 'רש"י',
                            "base_text_titles": ["Genesis"],
                            "base_text_order": 1,
                            "enShortDesc": "Classic medieval commentary",
                            "heShortDesc": "פירוש קלאסי",
                            "authors": [
                                {
                                    "en": "Rashi",
                                    "he": 'רש"י',
                                    "slug": "rashi",
                                }
                            ],
                        },
                        {
                            "title": "Nechama Leibowitz",
                            "heTitle": "נחמה ליבוביץ",
                            "categories": ["Tanakh", "Torah", "Commentary"],
                            "primary_category": "Commentary",
                            "nodeType": "TocCollectionNode",
                            "enShortDesc": "Collection node with slug",
                            "heShortDesc": "אוסף עם סלאג",
                        },
                    ],
                }
            ],
        }
    ]


@pytest.fixture
def service(sample_catalog):
    CatalogService._raw_catalog = None
    CatalogService._raw_catalog_ts = 0.0
    CatalogService._compiled_index = None
    CatalogService._compiled_index_ts = 0.0
    client = Mock()
    client.get_library_index = AsyncMock(return_value=sample_catalog)
    return CatalogService(client=client, cache_ttl_seconds=60 * 60 * 24)


@pytest.mark.asyncio
async def test_get_node_by_path_returns_category_with_children(service):
    result = await service.get_node("Tanakh/Torah")

    assert result["found"] is True
    node = result["node"]
    assert node["title"] == "Torah"
    assert node["child_count"] == 3
    assert node["children_preview"][0]["title"] == "Genesis"


@pytest.mark.asyncio
async def test_get_node_by_title_returns_ambiguous_matches(service, sample_catalog):
    sample_catalog[0]["contents"][0]["contents"].append(
        {
            "title": "Genesis",
            "heTitle": "בראשית ב",
            "categories": ["Tanakh", "Appendix"],
            "primary_category": "Tanakh",
            "enShortDesc": "Duplicate title for ambiguity testing",
            "heShortDesc": "כפילות",
        }
    )

    result = await service.get_node("Genesis", identifier_type="title")

    assert result["found"] is False
    assert result["ambiguous"] is True
    assert len(result["matches"]) == 2


@pytest.mark.asyncio
async def test_get_children_filters_by_node_type(service):
    result = await service.get_children("Tanakh/Torah", child_type="book")

    assert result["found"] is True
    assert result["total_children"] == 3
    assert all(child["type"] == "book" for child in result["children"])


@pytest.mark.asyncio
async def test_catalog_search_matches_creator_and_description(service):
    result = await service.search("rashi", node_type="book")

    assert result["results"]
    top = result["results"][0]
    assert top["node"]["title"] == "Rashi on Genesis"
    assert "authors" in top["matched_fields"]


@pytest.mark.asyncio
async def test_catalog_get_node_preserves_author_schema(service):
    result = await service.get_node("Genesis", identifier_type="title")

    assert result["found"] is True
    assert result["node"]["authors"] == [{"en": "Moses", "he": "משה", "slug": "moses"}]


@pytest.mark.asyncio
async def test_catalog_search_matches_hebrew_author_name(service):
    result = await service.search('רש"י', node_type="book")

    assert result["results"]
    assert result["results"][0]["node"]["title"] == "Rashi on Genesis"
    assert "authors" in result["results"][0]["matched_fields"]


@pytest.mark.asyncio
async def test_catalog_search_handles_null_order_values(service, sample_catalog):
    sample_catalog[0]["contents"][0]["contents"].append(
        {
            "title": "Rav Shagar on Pesach",
            "heTitle": 'רב שג"ר על פסח',
            "categories": ["Tanakh", "Torah", "Commentary"],
            "primary_category": "Commentary",
            "enShortDesc": "Teachings by Rav Shagar",
            "heShortDesc": 'שיעורים של הרב שג"ר',
            "order": None,
            "authors": [
                {
                    "en": "Rav Shagar",
                    "he": 'שג"ר',
                    "slug": "rav-shagar",
                }
            ],
        }
    )
    sample_catalog[0]["contents"][0]["contents"].append(
        {
            "title": "Collected Writings of Rav Shagar",
            "heTitle": 'כתבי הרב שג"ר',
            "categories": ["Tanakh", "Torah", "Commentary"],
            "primary_category": "Commentary",
            "enShortDesc": "Collected writings",
            "heShortDesc": "כתבים",
            "order": 2,
            "authors": [
                {
                    "en": "Rav Shagar",
                    "he": 'שג"ר',
                    "slug": "rav-shagar",
                }
            ],
        }
    )

    result = await service.search("Rav Shagar", node_type="book")

    assert [item["node"]["title"] for item in result["results"][:2]] == [
        "Rav Shagar on Pesach",
        "Collected Writings of Rav Shagar",
    ]


@pytest.mark.asyncio
async def test_catalog_query_filters_by_creator_and_base_text(service):
    result = await service.query(
        node_type="book",
        filters={"creator": "Rashi", "base_text_title": "Genesis"},
        select=["title", "creators", "base_text_titles"],
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["title"] == "Rashi on Genesis"
    assert result["results"][0]["base_text_titles"] == ["Genesis"]


@pytest.mark.asyncio
async def test_catalog_query_filters_by_author_slug_and_selects_authors(service):
    result = await service.query(
        node_type="book",
        filters={"author_slug": "rashi"},
        select=["title", "authors"],
    )

    assert result["total_matches"] == 1
    assert result["results"][0]["title"] == "Rashi on Genesis"
    assert result["results"][0]["authors"] == [{"en": "Rashi", "he": 'רש"י', "slug": "rashi"}]


@pytest.mark.asyncio
async def test_catalog_is_cached_between_calls(service):
    first = await service.get_node("Tanakh", child_limit=1)
    second = await service.get_children("Tanakh")

    assert first["found"] is True
    assert second["found"] is True
    service.client.get_library_index.assert_awaited_once()
