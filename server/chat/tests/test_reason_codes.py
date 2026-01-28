"""Tests for reason codes - enumeration, descriptions, filtering."""

import pytest

from chat.V2.router.reason_codes import (
    REASON_CODES,
    ReasonCode,
    filter_reasons_by_category,
    get_reason_description,
)


class TestReasonCodeEnum:
    """Test ReasonCode enumeration."""

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.ROUTE_SEARCH_INTENT,
            ReasonCode.ROUTE_GENERAL_INTENT,
            ReasonCode.ROUTE_FLOW_STICKINESS,
            ReasonCode.ROUTE_DEFAULT_GENERAL,
        ],
    )
    def test_routing_codes_exist(self, code):
        assert code

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.GUARDRAIL_PROMPT_INJECTION,
            ReasonCode.GUARDRAIL_HARASSMENT,
            ReasonCode.GUARDRAIL_HATE_SPEECH,
            ReasonCode.GUARDRAIL_HIGH_RISK_PSAK,
            ReasonCode.GUARDRAIL_MEDICAL_ADVICE,
        ],
    )
    def test_guardrail_codes_exist(self, code):
        assert code

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.TOOLS_ADDED_HALACHIC_SET,
            ReasonCode.TOOLS_ADDED_SEARCH_SET,
            ReasonCode.TOOLS_MINIMAL_GENERAL_SET,
            ReasonCode.TOOLS_NONE_ATTACHED,
        ],
    )
    def test_tool_codes_exist(self, code):
        assert code

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.SESSION_NEW,
            ReasonCode.SESSION_CONTINUE,
            ReasonCode.SESSION_TIMEOUT,
            ReasonCode.SESSION_END_REQUESTED,
        ],
    )
    def test_session_codes_exist(self, code):
        assert code

    def test_enum_is_string_enum(self):
        code = ReasonCode.ROUTE_HALACHIC_INTENT
        assert isinstance(code, str)
        assert isinstance(code.value, str)
        assert code == "ROUTE_HALACHIC_INTENT"


class TestReasonCodesDict:
    """Test REASON_CODES dictionary."""

    def test_all_codes_have_entries(self):
        for code in ReasonCode:
            assert code in REASON_CODES, f"Missing entry for {code}"

    def test_entries_have_required_fields(self):
        valid_categories = {"routing", "guardrail", "tooling", "session"}
        for code, info in REASON_CODES.items():
            assert "description" in info and len(info["description"]) > 0
            assert info.get("category") in valid_categories, f"Invalid category for {code}"


class TestGetReasonDescription:
    """Test get_reason_description function."""

    @pytest.mark.parametrize(
        "code,expected_substring",
        [
            (ReasonCode.ROUTE_HALACHIC_INTENT, "halachic"),
            (ReasonCode.GUARDRAIL_PROMPT_INJECTION, "injection"),
            (ReasonCode.TOOLS_ADDED_SEARCH_SET, "search"),
        ],
    )
    def test_get_description_contains_expected_text(self, code, expected_substring):
        desc = get_reason_description(code)
        assert expected_substring in desc.lower()

    def test_all_descriptions_non_empty(self):
        for code in ReasonCode:
            assert len(get_reason_description(code)) > 0


class TestFilterReasonsByCategory:
    """Test filter_reasons_by_category function."""

    @pytest.mark.parametrize("category", ["routing", "guardrail", "tooling", "session"])
    def test_filter_by_category(self, category):
        all_codes = list(ReasonCode)
        filtered = filter_reasons_by_category(all_codes, category)
        for code in filtered:
            assert REASON_CODES[code]["category"] == category

    def test_filter_empty_list(self):
        assert filter_reasons_by_category([], "routing") == []

    def test_filter_unknown_category(self):
        assert filter_reasons_by_category(list(ReasonCode), "unknown") == []

    def test_filter_subset(self):
        subset = [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.GUARDRAIL_PROMPT_INJECTION,
            ReasonCode.TOOLS_ADDED_SEARCH_SET,
        ]
        assert filter_reasons_by_category(subset, "routing") == [ReasonCode.ROUTE_HALACHIC_INTENT]
        assert filter_reasons_by_category(subset, "guardrail") == [
            ReasonCode.GUARDRAIL_PROMPT_INJECTION
        ]


class TestReasonCodeCategories:
    """Test reason code categorization is consistent."""

    @pytest.mark.parametrize(
        "prefix,expected_category",
        [
            ("ROUTE_", "routing"),
            ("GUARDRAIL_", "guardrail"),
            ("TOOLS_", "tooling"),
            ("SESSION_", "session"),
        ],
    )
    def test_prefix_matches_category(self, prefix, expected_category):
        for code in ReasonCode:
            if code.value.startswith(prefix):
                assert REASON_CODES[code]["category"] == expected_category, (
                    f"{code} should be {expected_category}"
                )


class TestReasonCodeUsageInRouting:
    """Test that reason codes can be used properly in routing context."""

    def test_reason_codes_serializable(self):
        codes = [
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.GUARDRAIL_HIGH_RISK_PSAK,
            ReasonCode.TOOLS_ADDED_HALACHIC_SET,
        ]
        values = [code.value for code in codes]
        assert values == [
            "ROUTE_HALACHIC_INTENT",
            "GUARDRAIL_HIGH_RISK_PSAK",
            "TOOLS_ADDED_HALACHIC_SET",
        ]

    def test_reason_codes_comparable(self):
        code1 = ReasonCode.ROUTE_HALACHIC_INTENT
        code2 = ReasonCode.ROUTE_HALACHIC_INTENT
        code3 = ReasonCode.ROUTE_SEARCH_INTENT
        assert code1 == code2
        assert code1 != code3

    def test_reason_codes_hashable(self):
        codes = {
            ReasonCode.ROUTE_HALACHIC_INTENT,
            ReasonCode.ROUTE_HALACHIC_KEYWORDS,
            ReasonCode.ROUTE_HALACHIC_INTENT,  # Duplicate
        }
        assert len(codes) == 2

    def test_reason_codes_in_list(self):
        codes = [ReasonCode.ROUTE_HALACHIC_INTENT, ReasonCode.ROUTE_HALACHIC_KEYWORDS]
        assert ReasonCode.ROUTE_HALACHIC_INTENT in codes
        assert ReasonCode.ROUTE_SEARCH_INTENT not in codes
