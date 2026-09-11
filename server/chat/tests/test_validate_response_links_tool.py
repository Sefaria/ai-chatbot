"""The response-link validator, exposed as a tool.

The hosted agent validates links after the fact and asks itself for a repair.
An agent we do not orchestrate — Claude Code on someone's machine, talking to
the public MCP server — has no such loop, so it needs to be able to run the same
check on its own draft. That is what this tool is for, and why it is on the MCP
surface rather than agent-only.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from chat.V2.agent.tool_executor import SefariaToolExecutor
from chat.V2.agent.tool_schemas import ALL_TOOLS, get_tools_for_surface

TOOL = "validate_response_links"
GOOD_LINK = '<a href="https://www.sefaria.org/Genesis.1.1">Genesis 1:1</a>'
BAD_REF_LINK = '<a href="https://www.sefaria.org/Genesis.999.1">nope</a>'
EXTERNAL_LINK = '<a href="https://evil.example.com/x">elsewhere</a>'


def _executor(resolves: bool = True) -> SefariaToolExecutor:
    executor = SefariaToolExecutor()
    executor.client.strict_resolve_ref = AsyncMock(
        return_value={"is_ref": True, "url_ref": "Genesis.1.1"} if resolves else None
    )
    return executor


async def _validate(executor: SefariaToolExecutor, draft: str) -> dict:
    result = await executor.execute(TOOL, {"response": draft})
    assert not result.is_error
    return json.loads(result.content[0]["text"])


class TestExposure:
    def test_offered_on_the_mcp_surface(self):
        assert TOOL in {tool["name"] for tool in get_tools_for_surface("mcp")}

    def test_not_added_to_our_own_agent(self):
        # The hosted agent already validates links after drafting; a second,
        # manual route to the same check would change its behaviour for no gain.
        assert TOOL not in {tool["name"] for tool in get_tools_for_surface("agent")}

    def test_description_tells_the_agent_when_to_run_it(self):
        # An agent we do not orchestrate only calls this if the description says
        # to, so the instruction is load-bearing, not decoration.
        description = ALL_TOOLS[TOOL]["description"].lower()
        assert "before you send" in description
        assert "fix or remove" in description


class TestValidation:
    @pytest.mark.asyncio
    async def test_clean_draft_is_safe_to_send(self):
        report = await _validate(_executor(), f"<p>See {GOOD_LINK}.</p>")
        assert report["safe_to_send"] is True
        assert report["issues"] == []

    @pytest.mark.asyncio
    async def test_unresolvable_ref_is_reported(self):
        report = await _validate(_executor(resolves=False), f"<p>{BAD_REF_LINK}</p>")
        assert report["safe_to_send"] is False
        assert report["issues"][0]["link"] == "https://www.sefaria.org/Genesis.999.1"

    @pytest.mark.asyncio
    async def test_external_link_is_reported(self):
        report = await _validate(_executor(), f"<p>{EXTERNAL_LINK}</p>")
        assert report["safe_to_send"] is False
        assert "non-Sefaria" in report["issues"][0]["problem"]

    @pytest.mark.asyncio
    async def test_every_bad_link_is_reported_not_just_the_first(self):
        # A draft is fixed in one pass, so reporting one issue at a time would
        # cost a round trip per bad link.
        report = await _validate(_executor(resolves=False), f"<p>{BAD_REF_LINK}{EXTERNAL_LINK}</p>")
        assert len(report["issues"]) == 2

    @pytest.mark.asyncio
    async def test_draft_without_links_is_safe(self):
        report = await _validate(_executor(), "<p>No links here at all.</p>")
        assert report["safe_to_send"] is True

    @pytest.mark.asyncio
    async def test_unreachable_sefaria_is_reported_not_silently_passed(self):
        executor = SefariaToolExecutor()
        executor.client.strict_resolve_ref = AsyncMock(side_effect=RuntimeError("network down"))

        report = await _validate(executor, f"<p>{GOOD_LINK}</p>")

        # Failing open here would let unverified links through as "safe".
        assert report["safe_to_send"] is False
