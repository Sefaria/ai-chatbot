"""
Tool executor — dispatches Claude's tool calls to SefariaClient methods.

This is the bridge between the Claude Agent SDK and the Sefaria API:

    Claude decides to call a tool (e.g. "get_text")
    → SDK invokes our MCP handler (in claude_service._build_sdk_tools)
    → handler calls SefariaToolExecutor.execute(tool_name, tool_input)
    → _dispatch routes to the matching SefariaClient method
    → result is wrapped in ToolResult and returned to the SDK
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from .catalog_service import CatalogService
from .contracts import MessageContext
from .sefaria_client import SefariaClient
from .tool_schemas import MAX_REFS_PER_VALIDATION, MAX_RESULTS

logger = logging.getLogger("chat.agent")

# Bounds for the validate_response_links tool. Generous for a real answer, which
# rarely carries more than a few dozen links.
MAX_VALIDATED_RESPONSE_CHARS = 100_000
MAX_VALIDATED_LINKS = 50


@dataclass
class ToolResult:
    """Normalized result returned to the SDK. Content is always a list of
    Anthropic-style content blocks (e.g. [{"type": "text", "text": "..."}]).
    """

    content: list[dict[str, Any]]
    is_error: bool = False


class SefariaToolExecutor:
    """Routes tool calls to SefariaClient methods and normalizes results.

    Stateless except for the shared SefariaClient (HTTP connection pool).
    """

    def __init__(self, client: SefariaClient | None = None):
        self.client = client or SefariaClient()
        self.catalog_service = CatalogService(self.client)

    def set_message_context(self, context: MessageContext) -> None:
        """Propagate per-request auth context to the Sefaria client."""
        self.client.set_user_session(context.sefaria_user_id, context.encrypted_user_token)

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> ToolResult:
        """Execute a tool by name. Errors are caught and returned as ToolResult(is_error=True)."""
        try:
            result = await self._dispatch(tool_name, tool_input)
            return self._wrap(result)
        except Exception as e:
            logger.error(f"Tool execution error for {tool_name}: {e}")
            return self._wrap_error(str(e))

    async def _validate_response_links(self, response_text: str) -> dict[str, Any]:
        """Check a draft response's links, as a tool the agent can call itself.

        The hosted agent runs this validation after the fact and asks for a
        repair. Exposing it as a tool lets an agent we do not orchestrate — one
        running on someone's own machine against the MCP server — check its own
        draft before sending it.
        """
        from .response_link_validator import ResponseLinkValidator, extract_hrefs

        # Each distinct link can cost a Sefaria API call, and on the public MCP server
        # the draft comes from anyone, so bound both the input and the fan-out. The
        # draft is parsed once, in linear time, with every link form a client might
        # render (not only the ones our own widget does).
        if len(response_text) > MAX_VALIDATED_RESPONSE_CHARS:
            raise ValueError(
                f"Response is too long to check (over {MAX_VALIDATED_RESPONSE_CHARS:,} characters)"
            )
        hrefs = extract_hrefs(response_text, all_forms=True)
        if len(hrefs) > MAX_VALIDATED_LINKS:
            raise ValueError(
                f"Response has more than {MAX_VALIDATED_LINKS} distinct links; check it in parts"
            )

        result = await ResponseLinkValidator(self.client).validate_hrefs(hrefs)
        return {
            "safe_to_send": result.is_valid,
            "links_checked": len(hrefs),
            "issues": [{"link": issue.href, "problem": issue.reason} for issue in result.issues],
        }

    async def _dispatch(self, tool_name: str, input_data: dict[str, Any]) -> Any:
        """Route a tool call to the corresponding SefariaClient method."""

        if tool_name == "get_text":
            return await self.client.get_text(input_data["reference"])

        elif tool_name == "specific_keyword_search":
            return await self.client.text_search(
                input_data["query"], input_data.get("filters"), _bounded(input_data, "size", 10)
            )

        elif tool_name == "get_current_calendar":
            return await self.client.get_current_calendar()

        elif tool_name == "semantic_search":
            return await self.client.semantic_search(
                input_data["query"], input_data.get("filters"), 10
            )

        elif tool_name == "get_links_between_texts":
            category = input_data.get("category")
            if category is None:
                return await self.client.get_links_between_texts(
                    input_data["reference"],
                    input_data.get("with_text", "0"),
                )
            return await self.client.get_links_between_texts(
                input_data["reference"],
                input_data.get("with_text", "0"),
                category,
            )

        elif tool_name == "search_in_book":
            return await self.client.search_in_book(
                input_data["query"], input_data["book_name"], _bounded(input_data, "size", 10)
            )

        elif tool_name == "search_in_dictionaries":
            return await self.client.search_in_dictionaries(input_data["query"])

        elif tool_name == "get_english_translations":
            return await self.client.get_english_translations(input_data["reference"])

        elif tool_name == "validate_refs":
            # Duplicates cost a call each for no answer; clamp for callers that skip
            # schema validation.
            refs = list(dict.fromkeys(input_data["refs"]))[:MAX_REFS_PER_VALIDATION]
            return await self.client.validate_refs(refs)

        elif tool_name == "validate_response_links":
            return await self._validate_response_links(input_data["response"])

        elif tool_name == "get_topic_details":
            return await self.client.get_topic_details(
                input_data["topic_slug"],
                input_data.get("with_links", False),
                input_data.get("with_refs", False),
            )

        elif tool_name == "clarify_name_argument":
            return await self.client.clarify_name_argument(
                input_data["name"],
                _bounded(input_data, "limit", None),
                input_data.get("type_filter"),
            )

        elif tool_name == "clarify_search_path_filter":
            filter_path = await self.client.clarify_search_path_filter(input_data["book_name"])
            return {"filter_path": filter_path}

        elif tool_name == "catalog_get_node":
            return await self.catalog_service.get_node(
                input_data["identifier"],
                identifier_type=input_data.get("identifier_type", "path"),
                child_limit=input_data.get("child_limit", 20),
            )

        elif tool_name == "catalog_get_children":
            return await self.catalog_service.get_children(
                input_data["path"],
                child_type=input_data.get("child_type", "all"),
                limit=_bounded(input_data, "limit", 50),
                offset=max(0, int(input_data.get("offset", 0))),
            )

        elif tool_name == "catalog_search":
            return await self.catalog_service.search(
                input_data["query"],
                node_type=input_data.get("node_type", "any"),
                category_path=input_data.get("category_path"),
                limit=_bounded(input_data, "limit", 10),
            )

        elif tool_name == "catalog_query":
            return await self.catalog_service.query(
                node_type=input_data.get("node_type", "any"),
                filters=input_data.get("filters"),
                select=input_data.get("select"),
                limit=_bounded(input_data, "limit", 20),
                offset=max(0, int(input_data.get("offset", 0))),
            )

        elif tool_name == "get_available_manuscripts":
            return await self.client.get_available_manuscripts(input_data["reference"])

        elif tool_name == "get_manuscript_image":
            return await self.client.get_manuscript_image(
                input_data["image_url"], input_data.get("manuscript_title")
            )

        elif tool_name == "search_user_source_sheets":
            return await self.client.search_user_source_sheets(
                input_data.get("query"), _bounded(input_data, "limit", 10)
            )

        elif tool_name == "get_source_sheet":
            return await self.client.get_source_sheet(input_data["sheet_id"])

        elif tool_name == "create_source_sheet":
            return await self.client.create_source_sheet(
                input_data["title"],
                input_data.get("summary", ""),
                input_data["sources"],
            )

        elif tool_name == "update_source_sheet":
            return await self.client.update_source_sheet(
                input_data["sheet_id"],
                title=input_data.get("title"),
                summary=input_data.get("summary"),
                sources=input_data.get("sources"),
            )

        elif tool_name == "list_topic_prompts":
            filters = {k: v for k, v in input_data.items() if k != "limit"}
            return await self.client.list_topic_prompts(
                **filters, limit=_bounded(input_data, "limit", 25)
            )

        elif tool_name == "update_topic_prompt":
            fields = {k: v for k, v in input_data.items() if k != "id"}
            return await self.client.update_topic_prompt(input_data["id"], fields)

        elif tool_name == "publish_topic_prompts":
            return await self.client.publish_topic_prompts(
                input_data["items"],
                lang=input_data.get("lang", "en"),
                published=input_data.get("published", True),
                dry_run=input_data.get("dry_run", False),
            )

        elif tool_name == "generate_topic_prompts":
            return await self.client.generate_topic_prompts(
                input_data["topic"], lang=input_data.get("lang", "en"), ids=input_data.get("ids")
            )

        elif tool_name == "get_admin_task":
            return await self.client.get_admin_task(input_data["task_id"])

        elif tool_name == "get_lexicon_entry":
            return await self.client.get_lexicon_entry(input_data["lexicon"], input_data["headword"])

        elif tool_name == "update_lexicon_entry":
            return await self.client.update_lexicon_entry(
                input_data["lexicon"],
                input_data["headword"],
                input_data["content"],
                input_data["base_version"],
                dry_run=input_data.get("dry_run", False),
            )

        elif tool_name == "rename_lexicon_headword":
            return await self.client.rename_lexicon_headword(
                input_data["lexicon"],
                input_data["headword"],
                input_data["new_headword"],
                dry_run=input_data.get("dry_run", False),
            )

        elif tool_name == "get_version_metadata":
            return await self.client.get_version_metadata(
                input_data["title"], input_data["lang"], input_data["version_title"]
            )

        elif tool_name == "update_version_metadata":
            return await self.client.update_version_metadata(
                input_data["title"],
                input_data["lang"],
                input_data["version_title"],
                input_data["fields"],
                input_data["base_version"],
                dry_run=input_data.get("dry_run", False),
            )

        elif tool_name == "find_versions":
            return await self.client.find_versions(input_data["version_title"], input_data.get("language"))

        elif tool_name == "check_index_dependencies":
            return await self.client.check_index_dependencies(input_data["title"])

        elif tool_name == "purge_cache":
            return await self.client.purge_cache(input_data["ref"])

        elif tool_name in ("tag_ref_with_topic", "untag_ref_from_topic"):
            call = getattr(self.client, tool_name)
            return await call(
                input_data["ref"],
                input_data["topic"],
                lang=input_data.get("lang", "en"),
                dry_run=input_data.get("dry_run", False),
            )

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    # -------------------------------------------------------------------
    # Result normalization
    # -------------------------------------------------------------------

    def _wrap(self, payload: Any) -> ToolResult:
        """Wrap a successful result as a text content block.
        Dicts/lists are JSON-serialized so Claude can read them.
        """
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, indent=2, ensure_ascii=False)

        return ToolResult(content=[{"type": "text", "text": text}], is_error=False)

    def _wrap_error(self, message: str) -> ToolResult:
        """Wrap an error as a JSON content block with is_error=True."""
        return ToolResult(
            content=[{"type": "text", "text": json.dumps({"error": message})}], is_error=True
        )


def _bounded(input_data: dict[str, Any], key: str, default: int | None) -> int | None:
    """A size/limit argument clamped to 1..MAX_RESULTS, or ``default`` when absent."""
    value = input_data.get(key)
    if value is None:
        return default
    return max(1, min(int(value), MAX_RESULTS))


# ---------------------------------------------------------------------------
# Ref resolution — maps ref-bearing tools to their ref argument name
# ---------------------------------------------------------------------------

# Tools whose named arg is a Sefaria ref (resolved via /api/ref for the trail link).
REF_TOOL_ARG = {
    "get_text": "reference",
    "get_links_between_texts": "reference",
    "get_available_manuscripts": "reference",
    "get_english_translations": "reference",
}


async def resolve_tool_ref(client, tool_name: str, tool_input: dict) -> dict | None:
    """Resolve the ref arg of a ref-bearing tool to {is_ref, url_ref, en, he}, or None."""
    arg_name = REF_TOOL_ARG.get(tool_name)
    if not arg_name:
        return None
    ref = tool_input.get(arg_name)
    if not ref:
        return None
    return await client.resolve_ref(ref)


# ---------------------------------------------------------------------------
# Human-readable tool call descriptions (used for SSE progress events)
# ---------------------------------------------------------------------------


def describe_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Generate a short label like 'Searching texts for "shabbat"' for the frontend."""

    def q(v: Any, max_len: int = 140) -> str:
        if isinstance(v, str):
            return f'"{v[:max_len]}{"…" if len(v) > max_len else ""}"'
        try:
            s = json.dumps(v)
            return s[:max_len] + ("…" if len(s) > max_len else "")
        except Exception:
            return "[unserializable]"

    descriptions = {
        "specific_keyword_search": lambda: (
            f"Searching texts for {q(tool_input.get('query'))}"
            + (f" in {q(tool_input.get('filters'))}" if tool_input.get("filters") else "")
        ),
        "semantic_search": lambda: f"Semantic search for {q(tool_input.get('query'))}",
        "search_in_book": lambda: (
            f"Searching in {q(tool_input.get('book_name'))} for {q(tool_input.get('query'))}"
        ),
        "search_in_dictionaries": lambda: (
            f"Searching dictionaries for {q(tool_input.get('query'))}"
        ),
        "get_text": lambda: f"Fetching text {q(tool_input.get('reference'))}",
        "validate_refs": lambda: f"Validating refs {q(tool_input.get('refs'))}",
        "get_links_between_texts": lambda: f"Finding links from {q(tool_input.get('reference'))}",
        "get_topic_details": lambda: f"Loading topic details for {q(tool_input.get('topic_slug'))}",
        "get_current_calendar": lambda: "Fetching current Jewish calendar",
        "clarify_name_argument": lambda: f"Clarifying name {q(tool_input.get('name'))}",
        "clarify_search_path_filter": lambda: (
            f"Resolving book filter for {q(tool_input.get('book_name'))}"
        ),
        "catalog_get_node": lambda: f"Loading catalog node for {q(tool_input.get('identifier'))}",
        "catalog_get_children": lambda: f"Loading catalog children for {q(tool_input.get('path'))}",
        "catalog_search": lambda: f"Searching catalog for {q(tool_input.get('query'))}",
        "catalog_query": lambda: "Querying cached catalog metadata",
        "get_available_manuscripts": lambda: (
            f"Checking available manuscripts for {q(tool_input.get('reference'))}"
        ),
        "get_manuscript_image": lambda: "Downloading manuscript image",
        "get_english_translations": lambda: (
            f"Getting English translations for {q(tool_input.get('reference'))}"
        ),
        "search_user_source_sheets": lambda: (
            "Searching the user's source sheets"
            + (f" for {q(tool_input.get('query'))}" if tool_input.get("query") else "")
        ),
        "get_source_sheet": lambda: f"Loading source sheet {q(tool_input.get('sheet_id'))}",
        "create_source_sheet": lambda: f"Creating source sheet {q(tool_input.get('title'))}",
        "update_source_sheet": lambda: f"Updating source sheet {q(tool_input.get('sheet_id'))}",
        "list_topic_prompts": lambda: "Listing topic prompts",
        "update_topic_prompt": lambda: f"Updating topic prompt {q(tool_input.get('id'))}",
        "publish_topic_prompts": lambda: (
            f"{'Unpublishing' if tool_input.get('published') is False else 'Publishing'} "
            f"{len(tool_input.get('items') or [])} topic prompt(s)"
        ),
        "generate_topic_prompts": lambda: f"Generating prompts for {q(tool_input.get('topic'))}",
        "get_admin_task": lambda: f"Checking task {q(tool_input.get('task_id'))}",
        "tag_ref_with_topic": lambda: f"Tagging {q(tool_input.get('ref'))} with {q(tool_input.get('topic'))}",
        "untag_ref_from_topic": lambda: f"Removing {q(tool_input.get('ref'))} from {q(tool_input.get('topic'))}",
        "get_lexicon_entry": lambda: f"Reading {q(tool_input.get('headword'))} in {q(tool_input.get('lexicon'))}",
        "update_lexicon_entry": lambda: f"Editing {q(tool_input.get('headword'))} in {q(tool_input.get('lexicon'))}",
        "get_version_metadata": lambda: f"Reading {q(tool_input.get('version_title'))} of {q(tool_input.get('title'))}",
        "update_version_metadata": lambda: f"Editing {q(tool_input.get('version_title'))} of {q(tool_input.get('title'))}",
        "find_versions": lambda: f"Finding books with {q(tool_input.get('version_title'))}",
        "check_index_dependencies": lambda: f"Checking what depends on {q(tool_input.get('title'))}",
        "purge_cache": lambda: f"Refreshing caches for {q(tool_input.get('ref'))}",
        "rename_lexicon_headword": lambda: (
            f"Renaming {q(tool_input.get('headword'))} to {q(tool_input.get('new_headword'))}"
        ),
    }

    if tool_name in descriptions:
        return descriptions[tool_name]()

    return f"Running tool {q(tool_name)} with {q(tool_input, 220)}"
