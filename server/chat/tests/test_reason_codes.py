"""
Tests for reason codes - enumeration, descriptions, filtering.
"""

import pytest
from chat.router.reason_codes import (
    ReasonCode,
    REASON_CODES,
    get_reason_description,
    filter_reasons_by_category,
)


class TestReasonCodeEnum:
    """Test ReasonCode enumeration."""

    def test_routing_codes_exist(self):
        """Test that routing reason codes exist."""
        assert ReasonCode.ROUTE_HALACHIC_INTENT
        assert ReasonCode.ROUTE_SEARCH_INTENT
        assert ReasonCode.ROUTE_GENERAL_INTENT
        assert ReasonCode.ROUTE_FLOW_STICKINESS
        assert ReasonCode.ROUTE_DEFAULT_GENERAL

    def test_guardrail_codes_exist(self):
        """Test that guardrail reason codes exist."""
        assert ReasonCode.GUARDRAIL_PROMPT_INJECTION
        assert ReasonCode.GUARDRAIL_HARASSMENT
        assert ReasonCode.GUARDRAIL_HATE_SPEECH
        assert ReasonCode.GUARDRAIL_HIGH_RISK_PSAK
        assert ReasonCode.GUARDRAIL_MEDICAL_ADVICE

    def test_tool_codes_exist(self):
        """Test that tool reason codes exist."""
        assert ReasonCode.TOOLS_ADDED_HALACHIC_SET
        assert ReasonCode.TOOLS_ADDED_SEARCH_SET
        assert ReasonCode.TOOLS_MINIMAL_GENERAL_SET
        assert ReasonCode.TOOLS_NONE_ATTACHED

    def test_session_codes_exist(self):
        """Test that session reason codes exist."""
        assert ReasonCode.SESSION_NEW
        assert ReasonCode.SESSION_CONTINUE
        assert ReasonCode.SESSION_TIMEOUT
        assert ReasonCode.SESSION_END_REQUESTED

    def test_enum_values_are_strings(self):
        """Test that enum values are strings."""
        assert isinstance(ReasonCode.ROUTE_HALACHIC_INTENT.value, str)
        assert ReasonCode.ROUTE_HALACHIC_INTENT.value == "ROUTE_HALACHIC_INTENT"

    def test_enum_is_string_enum(self):
        """Test that ReasonCode inherits from str."""
        code = ReasonCode.ROUTE_HALACHIC_INTENT
        assert isinstance(code, str)
        assert code == "ROUTE_HALACHIC_INTENT"


class TestReasonCodesDict:
    """Test REASON_CODES dictionary."""

    def test_all_codes_have_entries(self):
        """Test that all enum values have dictionary entries."""
        for code in ReasonCode:
            assert code in REASON_CODES, f"Missing entry for {code}"

    def test_entries_have_description(self):
        """Test that all entries have descriptions."""
        for code, info in REASON_CODES.items():
            assert "description" in info, f"Missing description for {code}"
            assert len(info["description"]) > 0

    def test_entries_have_category(self):
        """Test that all entries have categories."""
        for code, info in REASON_CODES.items():
            assert "category" in info, f"Missing category for {code}"

    def test_valid_categories(self):
        """Test that categories are valid."""
        valid_categories = {"routing", "guardrail", "tooling", "session"}
        for code, info in REASON_CODES.items():
            assert info["category"] in valid_categories, f"Invalid category for {code}"


class TestGetReasonDescription:
    """Test get_reason_description function."""

    def test_get_known_description(self):
        """Test getting description for known code."""
        desc = get_reason_description(ReasonCode.ROUTE_HALACHIC_INTENT)
        assert "halachic" in desc.lower()

    def test_get_guardrail_description(self):
        """Test getting guardrail description."""
        desc = get_reason_description(ReasonCode.GUARDRAIL_PROMPT_INJECTION)
        assert "injection" in desc.lower()

    def test_get_tool_description(self):
        """Test getting tool description."""
        desc = get_reason_description(ReasonCode.TOOLS_ADDED_SEARCH_SET)
        assert "search" in desc.lower().replace("search", "search")

    def test_all_descriptions_non_empty(self):
        """Test all descriptions are non-empty."""
        for code in ReasonCode:
            desc = get_reason_description(code)
            assert len(desc) > 0


class TestFilterReasonsByCategory:
    """Test filter_reasons_by_category function."""

    def test_filter_routing_codes(self):
        """Test filtering routing codes."""
        all_codes = list(ReasonCode)
        routing_codes = filter_reasons_by_category(all_codes, "routing")

        for code in routing_codes:
            assert REASON_CODES[code]["category"] == "routing"

    def test_filter_guardrail_codes(self):
        """Test filtering guardrail codes."""
        all_codes = list(ReasonCode)
        guardrail_codes = filter_reasons_by_category(all_codes, "guardrail")

        for code in guardrail_codes:
            assert REASON_CODES[code]["category"] == "guardrail"

    def test_filter_tooling_codes(self):
        """Test filtering tooling codes."""
        all_codes = list(ReasonCode)
        tool_codes = filter_reasons_by_category(all_codes, "tooling")

        for code in tool_codes:
            assert REASON_CODES[code]["category"] == "tooling"

    def test_filter_session_codes(self):
        """Test filtering session codes."""
        all_codes = list(ReasonCode)
        session_codes = filter_reasons_by_category(all_codes, "session")

        for code in session_codes:
            assert REASON_CODES[code]["category"] == "session"

    def test_filter_empty_list(self):
        """Test filtering empty list."""
        result = filter_reasons_by_category([], "routing")
        assert result == []

    def test_filter_unknown_category(self):
        """Test filtering with unknown category."""
        all_codes = list(ReasonCode)
        result = filter_reasons_by_category(all_codes, "unknown")
        assert result == []

    def test_filter_subset(self):
        """Test filtering a subset of codes."""
        subset = [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.GUARDRAIL_PROMPT_INJECTION,
            ReasonCode.TOOLS_ADDED_SEARCH_SET,
        ]
        routing = filter_reasons_by_category(subset, "routing")
        assert routing == [ReasonCode.ROUTE_HALACHIC_INTENT]

        guardrails = filter_reasons_by_category(subset, "guardrail")
        assert guardrails == [ReasonCode.GUARDRAIL_PROMPT_INJECTION]


class TestReasonCodeCategories:
    """Test reason code categorization is consistent."""

    def test_route_codes_are_routing(self):
        """Test ROUTE_ codes are in routing category."""
        for code in ReasonCode:
            if code.value.startswith("ROUTE_"):
                assert REASON_CODES[code]["category"] == "routing", f"{code} should be routing"

    def test_guardrail_codes_are_guardrail(self):
        """Test GUARDRAIL_ codes are in guardrail category."""
        for code in ReasonCode:
            if code.value.startswith("GUARDRAIL_"):
                assert REASON_CODES[code]["category"] == "guardrail", f"{code} should be guardrail"

    def test_tool_codes_are_tooling(self):
        """Test TOOLS_ codes are in tooling category."""
        for code in ReasonCode:
            if code.value.startswith("TOOLS_"):
                assert REASON_CODES[code]["category"] == "tooling", f"{code} should be tooling"

    def test_session_codes_are_session(self):
        """Test SESSION_ codes are in session category."""
        for code in ReasonCode:
            if code.value.startswith("SESSION_"):
                assert REASON_CODES[code]["category"] == "session", f"{code} should be session"


class TestReasonCodeUsageInRouting:
    """Test that reason codes can be used properly in routing context."""

    def test_reason_codes_serializable(self):
        """Test reason codes can be serialized to JSON-compatible format."""
        codes = [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.GUARDRAIL_HIGH_RISK_PSAK,
            ReasonCode.TOOLS_ADDED_HALACHIC_SET,
        ]

        # Should be convertible to string values
        values = [code.value for code in codes]
        assert values == [
            "ROUTE_HALACHIC_INTENT",
            "GUARDRAIL_HIGH_RISK_PSAK",
            "TOOLS_ADDED_HALACHIC_SET",
        ]

    def test_reason_codes_comparable(self):
        """Test reason codes can be compared."""
        code1 = ReasonCode.ROUTE_HALACHIC_INTENT
        code2 = ReasonCode.ROUTE_HALACHIC_INTENT
        code3 = ReasonCode.ROUTE_SEARCH_INTENT

        assert code1 == code2
        assert code1 != code3

    def test_reason_codes_in_list(self):
        """Test reason codes work in lists."""
        codes = [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.ROUTE_HALACHIC_KEYWORDS,
        ]

        assert ReasonCode.ROUTE_HALACHIC_INTENT in codes
        assert ReasonCode.ROUTE_SEARCH_INTENT not in codes

    def test_reason_codes_hashable(self):
        """Test reason codes can be used in sets."""
        codes = {
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.ROUTE_HALACHIC_KEYWORDS,
            ReasonCode.ROUTE_HALACHIC_INTENT,  # Duplicate
        }

        assert len(codes) == 2  # Duplicate removed
