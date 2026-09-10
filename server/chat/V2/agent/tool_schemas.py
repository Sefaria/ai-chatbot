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
    "surfaces": ("agent", "mcp"),
    "description": "Retrieves the actual text content from a specific reference in the Jewish library.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": 'Specific text reference (e.g. "Genesis 1:1", "Berakhot 2a")',
            },
        },
        "required": ["reference"],
    },
}

TOOL_TEXT_SEARCH = {
    "name": "specific_keyword_search",
    "surfaces": ("agent", "mcp"),
    "description": "Use ONLY when the query is a specific Hebrew/Aramaic term or exact phrase, e.g. 'Find all places where the word X appears' or 'What does the term X mean in context Y?'. For all other source-finding queries, use semantic_search instead. This is a full-text keyword search — it matches exact words, not concepts. Do not use author names, book titles, or metadata as query terms. To scope to a book or category, pass filters as Sefaria path strings. Use clarify_search_path_filter to resolve a book name to its filter path before filtering.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "filters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sefaria path-based scope filters (e.g. 'Tanakh/Torah/Genesis'). Use clarify_search_path_filter to resolve a book name to its correct path before passing it here.",
            },
            "size": {"type": "number", "default": 10},
        },
        "required": ["query"],
    },
}

TOOL_GET_CURRENT_CALENDAR = {
    "name": "get_current_calendar",
    "surfaces": ("agent", "mcp"),
    "description": "Use for calendar questions including holiday dates, Torah portions (parasha), and zmanim. Provides current Jewish calendar information including Hebrew date, parasha, holidays, etc.",
    "input_schema": {"type": "object", "properties": {}},
}

TOOL_SEMANTIC_SEARCH = {
    "name": "semantic_search",
    "surfaces": ("agent", "mcp"),
    "description": "Use for conceptual/thematic queries, great as a first tool for queries whose intent is to discover sources. Performs semantic similarity search over Sefaria texts using embeddings. Finds conceptually related text chunks even without exact keyword matches. Prefer English queries; Hebrew queries are also supported.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
        },
        "required": ["query"],
    },
}

TOOL_GET_LINKS_BETWEEN_TEXTS = {
    "name": "get_links_between_texts",
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
    "description": "Searches specifically within Jewish reference dictionaries.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

TOOL_GET_ENGLISH_TRANSLATIONS = {
    "name": "get_english_translations",
    "surfaces": ("agent", "mcp"),
    "description": "Use when multiple translation options would help the user or enable a semantic search. Retrieves all available English translations for a specific text reference.",
    "input_schema": {
        "type": "object",
        "properties": {"reference": {"type": "string"}},
        "required": ["reference"],
    },
}

TOOL_VALIDATE_RESPONSE_LINKS = {
    "name": "validate_response_links",
    # MCP only. Our own agent already validates links after drafting and asks
    # itself for a repair (see turn_orchestrator); handing it a second, manual
    # route to the same check would change hosted behaviour for no gain. The
    # tool exists for agents we do not orchestrate.
    "surfaces": ("mcp",),
    "description": (
        "Checks every link in a draft response before you send it. Pass the full draft, "
        "HTML included. Returns whether it is safe to send, plus one issue per bad link: "
        "non-Sefaria hosts, and Sefaria text links whose ref does not resolve. "
        "Always run this on a draft that contains links, and fix or remove anything it "
        "flags rather than sending the draft as written."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "response": {
                "type": "string",
                "description": "The complete draft response, exactly as you intend to send it.",
            },
        },
        "required": ["response"],
    },
}

TOOL_VALIDATE_REFS = {
    "name": "validate_refs",
    "surfaces": ("agent", "mcp"),
    "description": "Strictly validates one or more Sefaria text references before using them in a final response. Use this when you plan to cite or link to a ref that was not directly returned by a Sefaria tool. Returns canonical Sefaria URL refs for valid refs and marks invalid refs.",
    "input_schema": {
        "type": "object",
        "properties": {
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Text references to validate, e.g. ["Genesis 1:1", "Nahum 2", "The War of the Jews 1:4:1"]',
            },
        },
        "required": ["refs"],
    },
}

TOOL_GET_TOPIC_DETAILS = {
    "name": "get_topic_details",
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
    "description": "Converts a book name into the exact backend search filter path used for scoped text search.",
    "input_schema": {
        "type": "object",
        "properties": {"book_name": {"type": "string"}},
        "required": ["book_name"],
    },
}

TOOL_CATALOG_GET_NODE = {
    "name": "catalog_get_node",
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
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
    "surfaces": ("agent", "mcp"),
    "description": "Retrieves historical manuscript metadata and image URLs for text passages.",
    "input_schema": {
        "type": "object",
        "properties": {"reference": {"type": "string"}},
        "required": ["reference"],
    },
}

TOOL_GET_MANUSCRIPT_IMAGE = {
    "name": "get_manuscript_image",
    "surfaces": ("agent", "mcp"),
    "description": "Downloads and returns a specific manuscript image from a given image URL.",
    "input_schema": {
        "type": "object",
        "properties": {"image_url": {"type": "string"}, "manuscript_title": {"type": "string"}},
        "required": ["image_url"],
    },
}

TOOL_SEARCH_USER_SOURCE_SHEETS = {
    "name": "search_user_source_sheets",
    "surfaces": ("agent",),
    "description": "Searches the authenticated user's own Sefaria source sheets by title, summary, tags, and topics. Use this when the user refers to 'my source sheet', 'my sheets', or wants to reuse an idea or workflow from one of their sheets. This tool automatically uses the authenticated user's stored session token; never ask the user for a user ID or token.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words or phrase to match against the user's source sheet titles, summaries, tags, and topics. Omit to list recent sheets.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of matching sheets to return.",
            },
        },
    },
}

TOOL_GET_SOURCE_SHEET = {
    "name": "get_source_sheet",
    "surfaces": ("agent",),
    "description": "Retrieves the contents of a Sefaria source sheet by sheet ID. Use this after identifying a relevant sheet, especially when the user refers to one of their own sheets. This tool automatically includes the retained session token when available so unlisted user sheets can be read.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sheet_id": {
                "type": "integer",
                "description": "The numeric Sefaria sheet ID to load.",
            }
        },
        "required": ["sheet_id"],
    },
}

TOOL_CREATE_SOURCE_SHEET = {
    "name": "create_source_sheet",
    "surfaces": ("agent",),
    "description": "Creates a new authenticated Sefaria source sheet. Provide the sheet title, summary, and an ordered list of sources. Ref sources should include both ref and heRef; the tool will fetch the English and Hebrew text and serialize the final sheet payload automatically.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The source sheet title.",
            },
            "summary": {
                "type": "string",
                "description": "A short summary for the sheet.",
                "default": "",
            },
            "sources": {
                "type": "array",
                "description": "Ordered list of sheet sources. Each item should contain either outsideText, or ref plus heRef.",
                "items": {
                    "type": "object",
                    "properties": {
                        "outsideText": {"type": "string"},
                        "ref": {"type": "string"},
                        "heRef": {"type": "string"},
                        "node": {"type": "integer"},
                    },
                },
            },
        },
        "required": ["title", "sources"],
    },
}

# ----------------------------------------------------------------------------
# MCP-only tools
#
# These two predate the cached ``catalog_*`` tools, which superseded them for the
# agent in 1a087d6a. They remain part of the public MCP server's contract, so they
# are marked for the "mcp" surface only and never reach the agent.
# ----------------------------------------------------------------------------

TOOL_GET_TEXT_OR_CATEGORY_SHAPE = {
    "name": "get_text_or_category_shape",
    "surfaces": ("mcp",),
    "description": "Retrieves the hierarchical structure and organization of texts or categories.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Text title or category name (e.g. 'Genesis', 'Talmud').",
            },
        },
        "required": ["name"],
    },
}

TOOL_GET_TEXT_CATALOGUE_INFO = {
    "name": "get_text_catalogue_info",
    "surfaces": ("mcp",),
    "description": "Retrieves the bibliographic and structural information (index) for a text or work.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the text or work (e.g. 'Genesis', 'Mishnah Berakhot').",
            },
        },
        "required": ["title"],
    },
}


# ============================================================================
# Tool Mappings
# ============================================================================

# All tools indexed by name
ALL_TOOLS: dict[str, dict[str, Any]] = {
    "get_text": TOOL_GET_TEXT,
    "semantic_search": TOOL_SEMANTIC_SEARCH,
    "get_current_calendar": TOOL_GET_CURRENT_CALENDAR,
    "specific_keyword_search": TOOL_TEXT_SEARCH,
    "get_links_between_texts": TOOL_GET_LINKS_BETWEEN_TEXTS,
    "search_in_book": TOOL_SEARCH_IN_BOOK,
    "search_in_dictionaries": TOOL_SEARCH_IN_DICTIONARIES,
    "get_english_translations": TOOL_GET_ENGLISH_TRANSLATIONS,
    "validate_refs": TOOL_VALIDATE_REFS,
    "validate_response_links": TOOL_VALIDATE_RESPONSE_LINKS,
    "get_topic_details": TOOL_GET_TOPIC_DETAILS,
    "clarify_name_argument": TOOL_CLARIFY_NAME_ARGUMENT,
    "clarify_search_path_filter": TOOL_CLARIFY_SEARCH_PATH_FILTER,
    "catalog_get_node": TOOL_CATALOG_GET_NODE,
    "catalog_get_children": TOOL_CATALOG_GET_CHILDREN,
    "catalog_search": TOOL_CATALOG_SEARCH,
    "catalog_query": TOOL_CATALOG_QUERY,
    "get_available_manuscripts": TOOL_GET_AVAILABLE_MANUSCRIPTS,
    "get_manuscript_image": TOOL_GET_MANUSCRIPT_IMAGE,
    "search_user_source_sheets": TOOL_SEARCH_USER_SOURCE_SHEETS,
    "get_source_sheet": TOOL_GET_SOURCE_SHEET,
    "create_source_sheet": TOOL_CREATE_SOURCE_SHEET,
    "get_text_or_category_shape": TOOL_GET_TEXT_OR_CATEGORY_SHAPE,
    "get_text_catalogue_info": TOOL_GET_TEXT_CATALOGUE_INFO,
}

LABS_TOOL_NAMES = {
    "search_user_source_sheets",
    "get_source_sheet",
    "create_source_sheet",
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


DEFAULT_SURFACES = ("agent",)


def has_surface(schema: dict[str, Any], surface: str) -> bool:
    """Whether a tool schema is exposed on the given surface.

    Tools without an explicit ``surfaces`` marker are agent-only, so forgetting the
    marker keeps a tool off the public MCP server rather than leaking it onto it.
    """
    return surface in schema.get("surfaces", DEFAULT_SURFACES)


def get_tools_for_surface(surface: str) -> list[dict[str, Any]]:
    """Get every tool schema marked for a surface (e.g. "agent", "mcp")."""
    return [tool for tool in ALL_TOOLS.values() if has_surface(tool, surface)]


def get_all_tools() -> list[dict[str, Any]]:
    """Get all generally available agent tool schemas."""
    return [
        tool
        for name, tool in ALL_TOOLS.items()
        if name not in LABS_TOOL_NAMES and has_surface(tool, "agent")
    ]


def get_tools_for_labs(labs: bool = False) -> list[dict[str, Any]]:
    """Get agent tool schemas available for the request's Labs setting."""
    if labs:
        return get_tools_for_surface("agent")
    return get_all_tools()


# Legacy compatibility
SEFARIA_TOOL_SCHEMAS = get_tools_for_labs(labs=True)
