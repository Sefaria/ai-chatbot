"""Tests that the agent's SDK options expose only the Sefaria MCP tools.

The Claude Code CLI enables its full built-in toolset (Bash, Read, Write, WebFetch,
Task, ...) unless `tools` is set; `allowed_tools` only auto-approves tools, it does
not remove the others. These tests lock in the restriction at both the options
level and the CLI command line the SDK generates from them.
"""

from unittest.mock import patch

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from chat.V2.agent.sdk_options_builder import SDKOptionsBuilder

MCP_TOOLS = ["mcp__sefaria__text_search", "mcp__sefaria__get_text"]


def _build_options(allowed_tools=MCP_TOOLS):
    builder = SDKOptionsBuilder(
        options_cls=ClaudeAgentOptions,
        model="claude-test",
        max_tokens=1000,
        temperature=0.5,
        braintrust_api_key="bt-key",
        braintrust_project="bt-project",
        mcp_server_name="sefaria",
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        options, _ = builder.build(
            system_prompt="test",
            mcp_server={"type": "stdio", "command": "true"},
            allowed_tools=list(allowed_tools),
        )
    return options


def _flag_value(cmd: list[str], flag: str) -> str:
    assert flag in cmd, f"{flag} missing from CLI command"
    return cmd[cmd.index(flag) + 1]


class TestAgentToolRestriction:
    def test_builtin_tools_disabled(self):
        assert _build_options().tools == []

    def test_unlisted_tools_denied_rather_than_auto_approved(self):
        assert _build_options().permission_mode == "dontAsk"

    def test_only_passed_mcp_servers_are_loaded(self):
        options = _build_options()
        assert options.strict_mcp_config is True
        assert list(options.mcp_servers) == ["sefaria"]

    def test_allowed_tools_are_only_sefaria_mcp_tools(self):
        options = _build_options()
        assert options.allowed_tools == MCP_TOOLS
        assert all(name.startswith("mcp__sefaria__") for name in options.allowed_tools)

    def test_cli_command_disables_builtins_and_bypass(self):
        options = _build_options()
        options.cli_path = "/nonexistent/claude"
        cmd = SubprocessCLITransport(prompt="", options=options)._build_command()

        assert _flag_value(cmd, "--tools") == ""
        assert _flag_value(cmd, "--permission-mode") == "dontAsk"
        assert "--strict-mcp-config" in cmd
        assert "bypassPermissions" not in cmd
        assert _flag_value(cmd, "--allowedTools") == ",".join(MCP_TOOLS)
