"""Tests for origin resolution logic."""

from chat.V2.origin import resolve_origin


class TestResolveOrigin:
    """Test origin resolution from caller-provided value + hardcoded defaults."""

    def test_no_caller_origin_returns_default(self):
        origin, is_prod = resolve_origin(None)
        assert origin == "dev"
        assert is_prod is False

    def test_empty_string_returns_default(self):
        origin, is_prod = resolve_origin("")
        assert origin == "dev"
        assert is_prod is False

    def test_caller_origin_used_as_is(self):
        origin, is_prod = resolve_origin("my-test-tool")
        assert origin == "my-test-tool"
        assert is_prod is False

    def test_prod_origin_detected(self):
        origin, is_prod = resolve_origin("sefaria-prod")
        assert origin == "sefaria-prod"
        assert is_prod is True

    def test_non_prod_origin(self):
        origin, is_prod = resolve_origin("eval")
        assert origin == "eval"
        assert is_prod is False

    def test_whitespace_stripped(self):
        origin, is_prod = resolve_origin("  sefaria-prod  ")
        assert origin == "sefaria-prod"
        assert is_prod is True
