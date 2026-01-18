"""
Tests for Braintrust logging helper functions.
"""

import pytest

from chat.views import extract_page_type
from chat.agent.claude_service import extract_refs


class TestExtractPageType:
    """Test extract_page_type function for URL parsing."""

    def test_empty_url_returns_unknown(self):
        """Empty URL returns 'unknown'."""
        assert extract_page_type('') == 'unknown'
        assert extract_page_type(None) == 'unknown'

    def test_subdomain_returns_subdomain(self):
        """Subdomain like eval.sefaria.org returns 'eval'."""
        assert extract_page_type('https://eval.sefaria.org/Genesis.1') == 'eval'
        assert extract_page_type('https://staging.sefaria.org/page') == 'staging'

    def test_www_subdomain_not_returned(self):
        """www subdomain should not be returned as page type."""
        assert extract_page_type('https://www.sefaria.org/Genesis.1') == 'reader'

    def test_texts_path_returns_home(self):
        """The /texts path is the Sefaria home page."""
        assert extract_page_type('https://www.sefaria.org/texts') == 'home'
        assert extract_page_type('https://www.sefaria.org/texts/') == 'home'
        assert extract_page_type('https://sefaria.org/texts') == 'home'

    def test_text_reference_returns_reader(self):
        """Text reference paths should return 'reader'."""
        assert extract_page_type('https://www.sefaria.org/Genesis.1') == 'reader'
        assert extract_page_type('https://www.sefaria.org/Rashi_on_Genesis.1.1') == 'reader'
        assert extract_page_type('https://sefaria.org/Shulchan_Arukh') == 'reader'

    def test_root_path_returns_other(self):
        """Root path returns 'other'."""
        assert extract_page_type('https://www.sefaria.org/') == 'other'
        assert extract_page_type('https://sefaria.org') == 'other'

    def test_static_path_returns_other(self):
        """Static paths should return 'other'."""
        assert extract_page_type('https://www.sefaria.org/static/js/app.js') == 'other'

    def test_invalid_url_handled_gracefully(self):
        """Invalid URLs are handled gracefully by urlparse."""
        # urlparse handles these gracefully - they become paths
        assert extract_page_type('not-a-url') == 'reader'
        assert extract_page_type('://invalid') == 'reader'  # parsed as path

    def test_case_insensitive_path(self):
        """Path matching should be case insensitive."""
        assert extract_page_type('https://sefaria.org/TEXTS') == 'home'
        assert extract_page_type('https://sefaria.org/Texts/') == 'home'


class TestExtractRefs:
    """Test extract_refs function for extracting Sefaria references from tool calls."""

    def test_empty_list_returns_empty(self):
        """Empty tool calls list returns empty refs."""
        assert extract_refs([]) == []

    def test_extracts_reference_from_tool_input(self):
        """Extracts reference field from tool_input."""
        tool_calls = [
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
        ]
        assert extract_refs(tool_calls) == ['Genesis 1:1']

    def test_extracts_multiple_refs(self):
        """Extracts multiple unique references."""
        tool_calls = [
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Rashi on Genesis 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert 'Genesis 1:1' in refs
        assert 'Rashi on Genesis 1:1' in refs
        assert len(refs) == 2

    def test_deduplicates_refs(self):
        """Duplicate references should be deduplicated."""
        tool_calls = [
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert refs == ['Genesis 1:1']

    def test_skips_tool_calls_without_reference(self):
        """Tool calls without 'reference' in input are skipped."""
        tool_calls = [
            {'tool_name': 'search_texts', 'tool_input': {'query': 'creation'}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert refs == ['Genesis 1:1']

    def test_handles_missing_tool_input(self):
        """Handles tool calls missing tool_input gracefully."""
        tool_calls = [
            {'tool_name': 'get_text'},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert refs == ['Genesis 1:1']

    def test_skips_empty_reference(self):
        """Empty reference values should be skipped."""
        tool_calls = [
            {'tool_name': 'get_text', 'tool_input': {'reference': ''}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert refs == ['Genesis 1:1']

    def test_preserves_order(self):
        """References should preserve order of first occurrence."""
        tool_calls = [
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Exodus 1:1'}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Genesis 1:1'}},
            {'tool_name': 'get_text', 'tool_input': {'reference': 'Exodus 1:1'}},
        ]
        refs = extract_refs(tool_calls)
        assert refs == ['Exodus 1:1', 'Genesis 1:1']
