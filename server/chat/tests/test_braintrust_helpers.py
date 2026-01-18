"""Tests for Braintrust logging helper functions."""

import pytest

from chat.agent.claude_service import extract_refs
from chat.views import extract_page_type


class TestExtractPageType:
    """Test extract_page_type function for URL parsing."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("", "unknown"),
            (None, "unknown"),
            ("https://eval.sefaria.org/Genesis.1", "eval"),
            ("https://staging.sefaria.org/page", "staging"),
            ("https://www.sefaria.org/Genesis.1", "reader"),
            ("https://www.sefaria.org/texts", "home"),
            ("https://www.sefaria.org/texts/", "home"),
            ("https://sefaria.org/texts", "home"),
            ("https://sefaria.org/TEXTS", "home"),
            ("https://sefaria.org/Texts/", "home"),
            ("https://www.sefaria.org/Genesis.1", "reader"),
            ("https://www.sefaria.org/Rashi_on_Genesis.1.1", "reader"),
            ("https://sefaria.org/Shulchan_Arukh", "reader"),
            ("https://www.sefaria.org/", "other"),
            ("https://sefaria.org", "other"),
            ("https://www.sefaria.org/static/js/app.js", "other"),
            ("not-a-url", "reader"),
            ("://invalid", "reader"),
        ],
    )
    def test_extract_page_type(self, url: str, expected: str) -> None:
        assert extract_page_type(url) == expected


class TestExtractRefs:
    """Test extract_refs function for extracting Sefaria references from tool calls."""

    def test_empty_list_returns_empty(self) -> None:
        assert extract_refs([]) == []

    def test_extracts_reference_from_tool_input(self) -> None:
        tool_calls = [{"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}}]
        assert extract_refs(tool_calls) == ["Genesis 1:1"]

    def test_extracts_multiple_refs(self) -> None:
        tool_calls = [
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
            {"tool_name": "get_text", "tool_input": {"reference": "Rashi on Genesis 1:1"}},
        ]
        refs = extract_refs(tool_calls)
        assert set(refs) == {"Genesis 1:1", "Rashi on Genesis 1:1"}

    def test_deduplicates_refs(self) -> None:
        tool_calls = [
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
        ]
        assert extract_refs(tool_calls) == ["Genesis 1:1"]

    def test_skips_tool_calls_without_reference(self) -> None:
        tool_calls = [
            {"tool_name": "search_texts", "tool_input": {"query": "creation"}},
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
        ]
        assert extract_refs(tool_calls) == ["Genesis 1:1"]

    def test_handles_missing_tool_input(self) -> None:
        tool_calls = [
            {"tool_name": "get_text"},
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
        ]
        assert extract_refs(tool_calls) == ["Genesis 1:1"]

    def test_skips_empty_reference(self) -> None:
        tool_calls = [
            {"tool_name": "get_text", "tool_input": {"reference": ""}},
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
        ]
        assert extract_refs(tool_calls) == ["Genesis 1:1"]

    def test_preserves_order(self) -> None:
        tool_calls = [
            {"tool_name": "get_text", "tool_input": {"reference": "Exodus 1:1"}},
            {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
            {"tool_name": "get_text", "tool_input": {"reference": "Exodus 1:1"}},
        ]
        assert extract_refs(tool_calls) == ["Exodus 1:1", "Genesis 1:1"]
