"""
Tool schemas for Claude agent - Sefaria API tools.
"""

SEFARIA_TOOL_SCHEMAS = [
    {
        "name": "get_text",
        "description": "Retrieves the actual text content from a specific reference in the Jewish library.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": 'Specific text reference (e.g. "Genesis 1:1", "Berakhot 2a")'
                },
                "version_language": {
                    "type": "string",
                    "enum": ["source", "english", "both"],
                    "description": "Which language version to retrieve. Omit for all versions."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "text_search",
        "description": "Searches across the entire Jewish library for passages containing specific terms. Hebrew/Aramaic searches are more reliable than English translations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filters": {"type": "array", "items": {"type": "string"}},
                "size": {"type": "number", "default": 10}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_current_calendar",
        "description": "Provides current Jewish calendar information including Hebrew date, parasha, holidays, etc.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "english_semantic_search",
        "description": "Performs semantic similarity search on English embeddings of texts from Sefaria. Uses semantic similarity to find conceptually related text chunks. Works well only with English queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filters": {"type": "object"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_links_between_texts",
        "description": "Finds all cross-references and connections to a specific text passage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string"},
                "with_text": {"type": "string", "enum": ["0", "1"], "default": "0"}
            },
            "required": ["reference"]
        }
    },
    {
        "name": "search_in_book",
        "description": "Searches for content within one specific book or text work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "book_name": {"type": "string"},
                "size": {"type": "number", "default": 10}
            },
            "required": ["query", "book_name"]
        }
    },
    {
        "name": "search_in_dictionaries",
        "description": "Searches specifically within Jewish reference dictionaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_english_translations",
        "description": "Retrieves all available English translations for a specific text reference.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"]
        }
    },
    {
        "name": "get_topic_details",
        "description": "Retrieves detailed information about specific topics in Jewish thought and texts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic_slug": {"type": "string"},
                "with_links": {"type": "boolean", "default": False},
                "with_refs": {"type": "boolean", "default": False}
            },
            "required": ["topic_slug"]
        }
    },
    {
        "name": "clarify_name_argument",
        "description": "Validates and autocompletes text names, book titles, references, topic slugs, author names, and categories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "number"},
                "type_filter": {"type": "string"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "clarify_search_path_filter",
        "description": "Converts a book name into a proper search filter path.",
        "input_schema": {
            "type": "object",
            "properties": {"book_name": {"type": "string"}},
            "required": ["book_name"]
        }
    },
    {
        "name": "get_text_or_category_shape",
        "description": "Retrieves the hierarchical structure and organization of texts or categories.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        }
    },
    {
        "name": "get_text_catalogue_info",
        "description": "Retrieves the bibliographic and structural information (index) for a text or work.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"]
        }
    },
    {
        "name": "get_available_manuscripts",
        "description": "Retrieves historical manuscript metadata and image URLs for text passages.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"]
        }
    },
    {
        "name": "get_manuscript_image",
        "description": "Downloads and returns a specific manuscript image from a given image URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_url": {"type": "string"},
                "manuscript_title": {"type": "string"}
            },
            "required": ["image_url"]
        }
    }
]


