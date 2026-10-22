"""The agent's in-process SDK MCP server, end to end through the SDK's own bridge.

fastmcp 4 (for the public MCP server) moved the whole image to ``mcp`` 2.x, which
``claude-agent-sdk`` also uses to serve the agent's tools to the Claude Code CLI. The
other agent tests mock the SDK, so nothing else would catch an incompatibility between
the two before it reached the hosted Library Assistant.

``SdkMcpBridge`` is the SDK's private router for CLI -> in-process server messages. If
an SDK upgrade moves it, update the import rather than deleting this test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge

from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.tool_executor import SefariaToolExecutor
from chat.V2.agent.tool_runtime import ToolRuntime
from chat.V2.agent.tool_schemas import get_tools_for_labs


def _server():
    client = MagicMock()
    client.get_text = AsyncMock(return_value={"versions": [{"text": "In the beginning"}]})
    client.resolve_ref = AsyncMock(return_value=None)
    client.strict_resolve_ref = AsyncMock(return_value=None)
    runtime = ToolRuntime(tool_executor=SefariaToolExecutor(client=client), decorator_fn=tool)
    sdk_tools = runtime.build_sdk_tools(
        tool_schemas=get_tools_for_labs(False),
        context=MessageContext(),
        emit=lambda update: None,
        tool_calls_list=[],
    )
    return create_sdk_mcp_server(name="sefaria", version="1.0.0", tools=sdk_tools)


async def _request(bridge, request_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    response = await bridge.handle(message)
    assert "error" not in response, response
    return response["result"]


async def test_agent_tools_round_trip_through_the_sdk_bridge():
    bridge = SdkMcpBridge("sefaria", _server()["instance"])
    try:
        init = await _request(
            bridge,
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "claude-code", "version": "test"},
            },
        )
        assert init["serverInfo"]["name"] == "sefaria"
        await bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

        listed = await _request(bridge, 2, "tools/list")
        assert {t["name"] for t in listed["tools"]} == {
            t["name"] for t in get_tools_for_labs(False)
        }

        called = await _request(
            bridge, 3, "tools/call", {"name": "get_text", "arguments": {"reference": "Genesis 1:1"}}
        )
        assert not called.get("isError")
        assert "In the beginning" in called["content"][0]["text"]
    finally:
        await bridge.aclose()
