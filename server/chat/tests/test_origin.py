"""Tests for origin resolution logic."""

from chat.V2.origin import resolve_origin


class TestResolveOrigin:
    """Test origin resolution from caller-provided value + hardcoded defaults."""

    def test_no_caller_origin_returns_default(self):
        assert resolve_origin(None) == "dev"

    def test_empty_string_returns_default(self):
        assert resolve_origin("") == "dev"

    def test_caller_origin_used_as_is(self):
        assert resolve_origin("my-test-tool") == "my-test-tool"

    def test_prod_origin_returned(self):
        assert resolve_origin("sefaria-prod") == "sefaria-prod"

    def test_non_prod_origin(self):
        assert resolve_origin("eval") == "eval"

    def test_whitespace_stripped(self):
        assert resolve_origin("  sefaria-prod  ") == "sefaria-prod"
