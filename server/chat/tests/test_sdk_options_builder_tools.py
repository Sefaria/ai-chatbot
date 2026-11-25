"""The answering agent is offered only the Sefaria MCP tools."""

from claude_agent_sdk import ClaudeAgentOptions

from chat.V2.agent.sdk_options_builder import SDKOptionsBuilder

MCP_TOOLS = ["mcp__sefaria__get_text", "mcp__sefaria__search"]


def _build(**overrides):
    builder = SDKOptionsBuilder(
        options_cls=ClaudeAgentOptions,
        model="claude-test",
        max_tokens=1000,
        temperature=0.0,
        braintrust_api_key="",
        braintrust_project="",
        braintrust_logging_enabled=False,
        mcp_server_name="sefaria",
        **overrides,
    )
    options, _ = builder.build(system_prompt="test", mcp_server={}, allowed_tools=MCP_TOOLS)
    return options


def test_builtin_tools_are_switched_off():
    # tools == [] makes the CLI run with --tools "" (only the MCP tools remain)
    assert _build().tools == []


def test_allowed_tools_still_hold_the_mcp_names():
    assert _build().allowed_tools == MCP_TOOLS


def test_builtin_removal_does_not_depend_on_disallowed_tools():
    assert _build().disallowed_tools == []
