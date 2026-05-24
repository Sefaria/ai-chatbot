"""Tests for agent tool schema selection."""

from chat.V2.agent.tool_schemas import get_all_tools, get_tools_for_labs

SOURCE_SHEET_TOOL_NAMES = {
    "search_user_source_sheets",
    "get_source_sheet",
    "create_source_sheet",
}


def _tool_names(tools):
    return {tool["name"] for tool in tools}


def test_default_tools_exclude_labs_source_sheet_tools():
    assert SOURCE_SHEET_TOOL_NAMES.isdisjoint(_tool_names(get_all_tools()))
    assert SOURCE_SHEET_TOOL_NAMES.isdisjoint(_tool_names(get_tools_for_labs(False)))


def test_labs_tools_include_source_sheet_tools():
    assert SOURCE_SHEET_TOOL_NAMES.issubset(_tool_names(get_tools_for_labs(True)))
