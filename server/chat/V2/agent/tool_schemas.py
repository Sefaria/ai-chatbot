"""
Tool schemas — JSON Schema definitions for every Sefaria tool the agent can call.

Each TOOL_* constant is a dict with {name, description, input_schema} matching
the format expected by create_sdk_mcp_server(). The schemas are also used by
SefariaToolExecutor._dispatch to validate/extract parameters.

Adding a new tool:
1. Define a TOOL_* constant here
2. Add it to ALL_TOOLS
3. Add a matching handler in SefariaToolExecutor._dispatch
4. Add a matching method in SefariaClient
5. Add a description in describe_tool_call (tool_executor.py)
"""

from typing import Any

# ============================================================================
# Individual Tool Schemas
# ============================================================================

TOOL_GET_TEXT = {
    "name": "get_text",
    "description": "Retrieves the actual text content from a specific reference in the Jewish library.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": 'Specific text reference (e.g. "Genesis 1:1", "Berakhot 2a")',
            },
            "version_language": {
                "type": "string",
                "enum": ["source", "english", "both"],
                "description": "Which language version to retrieve. Omit for all versions.",
            },
        },
        "required": ["reference"],
    },
}

TOOL_TEXT_SEARCH = {
    "name": "text_search",
    "description": "Searches across the entire Jewish library for passages containing specific terms. Hebrew/Aramaic searches are more reliable than English translations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "filters": {"type": "array", "items": {"type": "string"}},
            "size": {"type": "number", "default": 10},
        },
        "required": ["query"],
    },
}

TOOL_GET_CURRENT_CALENDAR = {
    "name": "get_current_calendar",
    "description": "Provides current Jewish calendar information including Hebrew date, parasha, holidays, etc.",
    "input_schema": {"type": "object", "properties": {}},
}

TOOL_ENGLISH_SEMANTIC_SEARCH = {
    "name": "english_semantic_search",
    "description": "Performs semantic similarity search on English embeddings of texts from Sefaria. Uses semantic similarity to find conceptually related text chunks. Works well only with English queries.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "filters": {"type": "object"}},
        "required": ["query"],
    },
}

TOOL_GET_LINKS_BETWEEN_TEXTS = {
    "name": "get_links_between_texts",
    "description": "Finds all cross-references and connections to a specific text passage.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string"},
            "with_text": {"type": "string", "enum": ["0", "1"], "default": "0"},
        },
        "required": ["reference"],
    },
}

TOOL_SEARCH_IN_BOOK = {
    "name": "search_in_book",
    "description": "Searches for content within one specific book or text work.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "book_name": {"type": "string"},
            "size": {"type": "number", "default": 10},
        },
        "required": ["query", "book_name"],
    },
}

TOOL_SEARCH_IN_DICTIONARIES = {
    "name": "search_in_dictionaries",
    "description": "Searches specifically within Jewish reference dictionaries.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

TOOL_GET_ENGLISH_TRANSLATIONS = {
    "name": "get_english_translations",
    "description": "Retrieves all available English translations for a specific text reference.",
    "input_schema": {
        "type": "object",
        "properties": {"reference": {"type": "string"}},
        "required": ["reference"],
    },
}

TOOL_GET_TOPIC_DETAILS = {
    "name": "get_topic_details",
    "description": "Retrieves detailed information about specific topics in Jewish thought and texts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topic_slug": {"type": "string"},
            "with_links": {"type": "boolean", "default": False},
            "with_refs": {"type": "boolean", "default": False},
        },
        "required": ["topic_slug"],
    },
}

TOOL_CLARIFY_NAME_ARGUMENT = {
    "name": "clarify_name_argument",
    "description": "Validates and autocompletes text names, book titles, references, topic slugs, author names, and categories.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "limit": {"type": "number"},
            "type_filter": {"type": "string"},
        },
        "required": ["name"],
    },
}

TOOL_CLARIFY_SEARCH_PATH_FILTER = {
    "name": "clarify_search_path_filter",
    "description": "Converts a book name into a proper search filter path.",
    "input_schema": {
        "type": "object",
        "properties": {"book_name": {"type": "string"}},
        "required": ["book_name"],
    },
}

TOOL_GET_TEXT_OR_CATEGORY_SHAPE = {
    "name": "get_text_or_category_shape",
    "description": "Retrieves the hierarchical structure and organization of texts or categories.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
}

TOOL_GET_TEXT_CATALOGUE_INFO = {
    "name": "get_text_catalogue_info",
    "description": "Retrieves the bibliographic and structural information (index) for a text or work.",
    "input_schema": {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    },
}

TOOL_GET_AVAILABLE_MANUSCRIPTS = {
    "name": "get_available_manuscripts",
    "description": "Retrieves historical manuscript metadata and image URLs for text passages.",
    "input_schema": {
        "type": "object",
        "properties": {"reference": {"type": "string"}},
        "required": ["reference"],
    },
}

TOOL_GET_MANUSCRIPT_IMAGE = {
    "name": "get_manuscript_image",
    "description": "Downloads and returns a specific manuscript image from a given image URL.",
    "input_schema": {
        "type": "object",
        "properties": {"image_url": {"type": "string"}, "manuscript_title": {"type": "string"}},
        "required": ["image_url"],
    },
}

# ============================================================================
# Tool Mappings
# ============================================================================

# All tools indexed by name
ALL_TOOLS: dict[str, dict[str, Any]] = {
    "get_text": TOOL_GET_TEXT,
    "text_search": TOOL_TEXT_SEARCH,
    "get_current_calendar": TOOL_GET_CURRENT_CALENDAR,
    "english_semantic_search": TOOL_ENGLISH_SEMANTIC_SEARCH,
    "get_links_between_texts": TOOL_GET_LINKS_BETWEEN_TEXTS,
    "search_in_book": TOOL_SEARCH_IN_BOOK,
    "search_in_dictionaries": TOOL_SEARCH_IN_DICTIONARIES,
    "get_english_translations": TOOL_GET_ENGLISH_TRANSLATIONS,
    "get_topic_details": TOOL_GET_TOPIC_DETAILS,
    "clarify_name_argument": TOOL_CLARIFY_NAME_ARGUMENT,
    "clarify_search_path_filter": TOOL_CLARIFY_SEARCH_PATH_FILTER,
    "get_text_or_category_shape": TOOL_GET_TEXT_OR_CATEGORY_SHAPE,
    "get_text_catalogue_info": TOOL_GET_TEXT_CATALOGUE_INFO,
    "get_available_manuscripts": TOOL_GET_AVAILABLE_MANUSCRIPTS,
    "get_manuscript_image": TOOL_GET_MANUSCRIPT_IMAGE,
}

# ============================================================================
# Helper Functions
# ============================================================================


def get_tools_by_names(names: list[str]) -> list[dict[str, Any]]:
    """
    Get tool schemas by their names.

    Args:
        names: List of tool names

    Returns:
        List of tool schemas
    """
    return [ALL_TOOLS[name] for name in names if name in ALL_TOOLS]


def get_all_tools() -> list[dict[str, Any]]:
    """Get all available tool schemas."""
    return list(ALL_TOOLS.values())


# Legacy compatibility
SEFARIA_TOOL_SCHEMAS = get_all_tools()
