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
    "description": "Use for exact phrases or specific Hebrew/Aramaic terms. Searches across the entire Jewish library for passages containing specific textual terms. This is a full-text search over text content, not a metadata search. Do not use author names, book titles, or other metadata as query terms unless you expect those exact words to appear in the text itself. To search within an author's works, first retrieve the author's books, then search within those books. Hebrew/Aramaic searches are more reliable than English translations.",
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
    "description": "Use for calendar questions including holiday dates, Torah portions (parasha), and zmanim. Provides current Jewish calendar information including Hebrew date, parasha, holidays, etc.",
    "input_schema": {"type": "object", "properties": {}},
}

TOOL_ENGLISH_SEMANTIC_SEARCH = {
    "name": "english_semantic_search",
    "description": "Use for conceptual/thematic English queries. Performs semantic similarity search on English embeddings of texts from Sefaria. Uses semantic similarity to find conceptually related text chunks. Works well only with English queries.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "filters": {"type": "object"}},
        "required": ["query"],
    },
}

TOOL_GET_LINKS_BETWEEN_TEXTS = {
    "name": "get_links_between_texts",
    "description": "Use to find commentaries and related passages after retrieving a primary text. Finds all cross-references and connections to a specific text passage.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string"},
            "with_text": {"type": "string", "enum": ["0", "1"], "default": "0"},
            "category": {
                "type": "string",
                "description": "Filter links to a specific category. Allowed values: Chasidut, Commentary, Essay, Guides, Halakhah, Jewish Thought, Kabbalah, Liturgy, Midrash, Mishnah, Musar, Quoting Commentary, Reference, Responsa, Second Temple, Talmud, Tanakh, Targum, Tosefta.",
            },
        },
        "required": ["reference"],
    },
}

TOOL_SEARCH_IN_BOOK = {
    "name": "search_in_book",
    "description": "Use for queries about a known specific book. Searches for content within one specific book or text work.",
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
    "description": "Use when multiple translation options would help the user or enable a semantic search. Retrieves all available English translations for a specific text reference.",
    "input_schema": {
        "type": "object",
        "properties": {"reference": {"type": "string"}},
        "required": ["reference"],
    },
}

TOOL_GET_TOPIC_DETAILS = {
    "name": "get_topic_details",
    "description": "Use when the question relates to a topic (e.g. 'What is Shabbat?') to explore a topic's connections. Retrieves detailed information about specific topics in Jewish thought and texts.",
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
    "description": "Converts a book name into the exact backend search filter path used for scoped text search.",
    "input_schema": {
        "type": "object",
        "properties": {"book_name": {"type": "string"}},
        "required": ["book_name"],
    },
}

TOOL_CATALOG_GET_NODE = {
    "name": "catalog_get_node",
    "description": "Retrieves one catalog node from the cached library index by path, title, or Hebrew title.",
    "input_schema": {
        "type": "object",
        "properties": {
            "identifier": {"type": "string"},
            "identifier_type": {
                "type": "string",
                "enum": ["path", "title", "he_title", "id"],
                "default": "path",
            },
            "child_limit": {"type": "number", "default": 20},
        },
        "required": ["identifier"],
    },
}

TOOL_CATALOG_GET_CHILDREN = {
    "name": "catalog_get_children",
    "description": "Lists direct children of a catalog category path from the cached library index.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": 'Slash-delimited category path (e.g. "Tanakh/Torah")',
            },
            "child_type": {
                "type": "string",
                "enum": ["all", "category", "book"],
                "default": "all",
            },
            "limit": {"type": "number", "default": 50},
            "offset": {"type": "number", "default": 0},
        },
        "required": ["path"],
    },
}

TOOL_CATALOG_SEARCH = {
    "name": "catalog_search",
    "description": "Performs lexical search over cached catalog metadata including titles, Hebrew titles, descriptions, categories, and author metadata from the api/index payload.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "node_type": {
                "type": "string",
                "enum": ["any", "category", "book"],
                "default": "any",
            },
            "category_path": {"type": "string"},
            "limit": {"type": "number", "default": 10},
        },
        "required": ["query"],
    },
}

TOOL_CATALOG_QUERY = {
    "name": "catalog_query",
    "description": "Runs structured filters against the cached library catalog. Use this for metadata lookup such as works by creator, titles within categories, or books matching descriptive fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "node_type": {
                "type": "string",
                "enum": ["any", "category", "book"],
                "default": "any",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "path_prefix": {"type": "string"},
                    "title": {"type": "string"},
                    "he_title": {"type": "string"},
                    "title_contains": {"type": "string"},
                    "he_title_contains": {"type": "string"},
                    "description_contains": {"type": "string"},
                    "author_en": {"type": "string"},
                    "author_he": {"type": "string"},
                    "author_slug": {"type": "string"},
                    "author_name_contains": {"type": "string"},
                    "creator": {"type": "string"},
                    "creator_contains": {"type": "string"},
                    "category": {"type": "string"},
                    "primary_category": {"type": "string"},
                    "corpus": {"type": "string"},
                    "dependence": {"type": "string"},
                    "commentator": {"type": "string"},
                    "collective_title": {"type": "string"},
                    "base_text_title": {"type": "string"},
                    "has_field": {"type": "string"},
                    "hidden": {"type": "boolean"},
                    "is_collection": {"type": "boolean"},
                },
            },
            "select": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "number", "default": 20},
            "offset": {"type": "number", "default": 0},
        },
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
    "catalog_get_node": TOOL_CATALOG_GET_NODE,
    "catalog_get_children": TOOL_CATALOG_GET_CHILDREN,
    "catalog_search": TOOL_CATALOG_SEARCH,
    "catalog_query": TOOL_CATALOG_QUERY,
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
