"""
Sefaria API client — async HTTP interface to the Sefaria REST APIs.

This is the lowest layer in the tool-call stack:

    Claude Agent SDK → tool handler → SefariaToolExecutor → SefariaClient → HTTP

Two base URLs are used:
- base_url (sefaria.org): texts, search, calendar, links, manuscripts, etc.
- ai_base_url (ai.sefaria.org): semantic search (KNN embeddings service)

Response optimization methods (_optimize_*) strip large/unnecessary fields
from API responses before they're sent back to Claude, keeping token usage low.
"""

import asyncio
import base64
import os
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

# ---------------------------------------------------------------------------
# Base URL configuration — supports both public Sefaria and local k8s service
# ---------------------------------------------------------------------------

DEFAULT_SEFARIA_BASE_URL = os.environ.get("SEFARIA_API_BASE_URL", "https://www.sefaria.org")

# In k8s, the AI service may be available via service discovery env vars.
VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST = os.environ.get("VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST")
VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT = os.environ.get("VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT")

if VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST and VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT:
    DEFAULT_SEFARIA_AI_BASE_URL = (
        f"http://{VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST}:{VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT}"
    )
else:
    DEFAULT_SEFARIA_AI_BASE_URL = os.environ.get("SEFARIA_AI_BASE_URL", "https://ai.sefaria.org")

# Mapping from Sefaria search filter paths to human-friendly dictionary names.
# Used by search_in_dictionaries to scope search to lexicon categories.
LEXICON_MAP = {
    "Reference/Dictionary/Jastrow": "Jastrow Dictionary",
    "Reference/Dictionary/Klein Dictionary": "Klein Dictionary",
    "Reference/Dictionary/BDB": "BDB Dictionary",
    "Reference/Dictionary/BDB Aramaic": "BDB Aramaic Dictionary",
    "Reference/Encyclopedic Works/Kovetz Yesodot VaChakirot": "Kovetz Yesodot VaChakirot",
}

LEXICON_SEARCH_FILTERS = list(LEXICON_MAP.keys())


class SefariaClient:
    """Async HTTP client for the Sefaria REST API.

    Lazily creates an httpx.AsyncClient and reuses it for connection pooling.
    The client is bound to the current event loop — if the loop changes
    (e.g. asyncio.run in a new thread), a fresh client is created.
    """

    def __init__(
        self, base_url: str | None = None, ai_base_url: str | None = None, timeout: float = 30.0
    ):
        self.base_url = (base_url or DEFAULT_SEFARIA_BASE_URL).rstrip("/")
        self.ai_base_url = (ai_base_url or DEFAULT_SEFARIA_AI_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    async def close(self):
        """Close the HTTP client and release connections."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._client_loop = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get an AsyncClient bound to the current event loop.

        If the event loop changed (e.g. different thread), close the old
        client and create a new one to avoid "attached to a different loop" errors.
        """
        loop = asyncio.get_running_loop()
        if self._client and self._client_loop is loop and not loop.is_closed():
            return self._client

        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass

        self._client = httpx.AsyncClient(timeout=self.timeout)
        self._client_loop = loop
        return self._client

    async def get_text(self, reference: str, version_language: str | None = None) -> dict[str, Any]:
        """Retrieve text content from a specific reference."""
        encoded_ref = quote(reference)
        params = {}

        if version_language == "source":
            params["version"] = "source"
        elif version_language == "english":
            params["version"] = "english"
        elif version_language == "both":
            params["version"] = ["english", "source"]

        data = await self._get_json(f"api/v3/texts/{encoded_ref}", params)
        return self._optimize_text_response(data)

    async def text_search(
        self, query: str, filters: list[str] | None = None, size: int = 10
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Search across the Jewish library.

        Uses Sefaria's Elasticsearch wrapper. If no results are found with
        the requested filters, retries without filters as a fallback (so the
        agent still gets useful results even if the filter path was wrong).
        """
        data = await self._search(query, filters, size)
        results = self._format_search_results(data, filters)

        if results:
            return results

        # Fallback: retry without filters (the filter path may have been wrong)
        if filters:
            fallback_data = await self._search(query, None, size)
            fallback_results = self._format_search_results(fallback_data, None, filters)
            if fallback_results:
                return fallback_results

        return {
            "no_results": True,
            "query": query,
            "suggestion": "No texts found matching this query. Consider using different keywords or trying a broader search term. If searching in Hebrew, try the exact phrase from the source text.",
        }

    async def get_current_calendar(self) -> dict[str, Any]:
        """Get current Jewish calendar information."""
        calendar_data = await self._get_json("api/calendars")
        return {**calendar_data, "Gregorian Date": datetime.now().isoformat()}

    async def english_semantic_search(
        self, query: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Semantic (KNN) search on English text embeddings via ai.sefaria.org.

        This hits a separate service from the main Sefaria API. Returns a 404
        gracefully if the embeddings service is down.
        """
        payload: dict[str, Any] = {"query": query}
        if filters:
            payload["filters"] = filters

        headers = {"Content-Type": "application/json"}
        bearer = os.environ.get("SEFARIA_AI_TOKEN")
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        url = f"{self.ai_base_url}/api/knn-search"
        client = await self._get_client()

        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {
                    "error": "Semantic search is currently unavailable.",
                    "suggestion": "Use text_search instead for keyword-based search.",
                }
            raise

    async def get_links_between_texts(
        self, reference: str, with_text: str = "0"
    ) -> list[dict[str, Any]]:
        """Find cross-references to a text passage."""
        encoded_ref = quote(reference)
        data = await self._get_json(f"api/links/{encoded_ref}", {"with_text": with_text})
        return self._optimize_links_response(data)

    async def search_in_book(self, query: str, book_name: str, size: int = 10) -> Any:
        """Search within a specific book."""
        filter_path = await self.clarify_search_path_filter(book_name)
        if not filter_path:
            return {"error": f"Could not find valid filter path for book '{book_name}'"}
        return await self.text_search(query, [filter_path], size)

    async def search_in_dictionaries(self, query: str) -> list[dict[str, Any]]:
        """Search within Jewish reference dictionaries."""
        response = await self._search(query, LEXICON_SEARCH_FILTERS, 8)
        hits = response.get("hits", {}).get("hits", [])

        return [
            {
                "ref": hit.get("_source", {}).get("ref"),
                "headword": (hit.get("_source", {}).get("titleVariants") or [None])[0],
                "lexicon_name": LEXICON_MAP.get(
                    hit.get("_source", {}).get("path", ""), hit.get("_source", {}).get("path", "")
                ),
                "text": hit.get("_source", {}).get("exact"),
            }
            for hit in hits
        ]

    async def get_english_translations(self, reference: str) -> dict[str, Any]:
        """Get all available English translations for a reference."""
        encoded_ref = quote(reference)
        data = await self._get_json(f"api/v3/texts/{encoded_ref}", {"version": "english|all"})

        english_translations = [
            {"versionTitle": version.get("versionTitle", ""), "text": version.get("text", "")}
            for version in data.get("versions", [])
        ]

        return {"reference": reference, "englishTranslations": english_translations}

    async def get_topic_details(
        self, topic_slug: str, with_links: bool = False, with_refs: bool = False
    ) -> dict[str, Any]:
        """Get detailed information about a topic."""
        encoded_slug = quote(topic_slug)
        params = {}
        if with_links:
            params["with_links"] = "1"
        if with_refs:
            params["with_refs"] = "1"

        data = await self._get_json(f"api/v2/topics/{encoded_slug}", params)
        return self._optimize_topics_response(data)

    async def get_author_indexes(
        self,
        author_slug: str,
        include_aggregations: bool = False,
        include_descriptions: bool = False,
    ) -> dict[str, Any]:
        """Get authored works for a Sefaria author slug."""
        encoded_slug = quote(author_slug)
        params = {
            "include_aggregations": "1" if include_aggregations else None,
            "include_descriptions": "1" if include_descriptions else None,
        }
        return await self._get_json(f"api/authors/{encoded_slug}/indexes", params)

    async def clarify_name_argument(
        self, name: str, limit: int | None = None, type_filter: str | None = None
    ) -> dict[str, Any]:
        """Validate and autocomplete text names."""
        encoded_name = quote(name)
        params = {}
        if limit is not None:
            params["limit"] = str(limit)
        if type_filter:
            params["type"] = type_filter

        return await self._get_json(f"api/name/{encoded_name}", params)

    async def clarify_search_path_filter(self, book_name: str) -> str | None:
        """Convert a book name into a search filter path."""
        encoded_name = quote(book_name)
        url = f"{self.base_url}/api/search-path-filter/{encoded_name}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            if response.status_code != 200:
                return None
            text = response.text.strip()
            return text if text else None
        except Exception:
            return None

    async def get_text_or_category_shape(self, name: str) -> dict[str, Any]:
        """Get hierarchical structure of texts or categories."""
        encoded_name = quote(name)
        return await self._get_json(f"api/shape/{encoded_name}")

    async def get_text_catalogue_info(self, title: str) -> dict[str, Any]:
        """Get bibliographic information for a text."""
        encoded_title = quote(title)
        data = await self._get_json(f"api/v2/raw/index/{encoded_title}")
        return self._optimize_index_response(data)

    async def get_available_manuscripts(self, reference: str) -> dict[str, Any]:
        """Get manuscript metadata for a text passage."""
        encoded_ref = quote(reference)
        return await self._get_json(f"api/manuscripts/{encoded_ref}")

    async def get_manuscript_image(
        self, image_url: str, manuscript_title: str | None = None
    ) -> dict[str, Any]:
        """Download a manuscript image."""
        client = await self._get_client()
        response = await client.get(image_url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "image/jpeg")
        image_data = base64.b64encode(response.content).decode("utf-8")

        filename = image_url.split("/")[-1] or "manuscript.jpg"
        title = manuscript_title or f"Manuscript: {filename}"

        return {
            "success": True,
            "image_data": image_data,
            "mime_type": content_type,
            "size": len(response.content),
            "original_size": len(response.content),
            "was_resized": False,
            "filename": filename,
            "title": title,
            "source_url": image_url,
        }

    # -------------------------------------------------------------------
    # Low-level HTTP helpers
    # -------------------------------------------------------------------

    async def _search(
        self, query: str, filters: list[str] | None = None, size: int = 8
    ) -> dict[str, Any]:
        """POST to Sefaria's Elasticsearch wrapper (es8 endpoint).

        Uses naive_lemmatizer field with slop=10 for fuzzy phrase matching,
        sorted by pagesheetrank (Sefaria's relevance score).
        """
        url = f"{self.base_url}/api/search-wrapper/es8"
        filter_list = filters or []
        # filter_fields must be the same length as filters; None means "path" field
        filter_fields = [None] * len(filter_list)

        payload = {
            "aggs": [],
            "field": "naive_lemmatizer",
            "filter_fields": filter_fields,
            "filters": filter_list,
            "query": query,
            "size": size,
            "slop": 10,
            "sort_fields": ["pagesheetrank"],
            "sort_method": "score",
            "sort_reverse": False,
            "sort_score_missing": 0.04,
            "source_proj": True,
            "type": "text",
        }

        client = await self._get_client()
        response = await client.post(
            url, json=payload, headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()

    async def _get_json(
        self, endpoint: str, params: dict[str, str | list[str]] | None = None
    ) -> Any:
        """GET a JSON endpoint from the main Sefaria API."""
        url = f"{self.base_url}/{endpoint}"
        if params:
            filtered_params = {k: v for k, v in params.items() if v}
            if filtered_params:
                url = f"{url}?{urlencode(filtered_params, doseq=True)}"

        client = await self._get_client()
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------
    # Response optimizers — strip unnecessary fields to save tokens
    # -------------------------------------------------------------------

    def _optimize_text_response(self, data: Any) -> dict[str, Any]:
        """Keep only fields Claude needs from a /texts response."""
        if not isinstance(data, dict):
            return data

        essential_fields = {
            "ref",
            "versions",
            "available_versions",
            "requestedRef",
            "spanningRefs",
            "textType",
            "sectionRef",
            "he",
            "text",
            "primary_title",
        }

        optimized = {k: v for k, v in data.items() if k in essential_fields}

        if isinstance(optimized.get("versions"), list):
            optimized["versions"] = [
                {
                    "text": v.get("text", ""),
                    "versionTitle": v.get("versionTitle", ""),
                    "languageFamilyName": v.get("languageFamilyName", ""),
                    "versionSource": v.get("versionSource", ""),
                }
                for v in optimized["versions"]
            ]

        if isinstance(optimized.get("available_versions"), list):
            optimized["available_versions"] = [
                {
                    "versionTitle": v.get("versionTitle", ""),
                    "languageFamilyName": v.get("languageFamilyName", ""),
                }
                for v in optimized["available_versions"]
            ]

        return optimized

    def _optimize_links_response(self, data: Any) -> list[dict[str, Any]]:
        """Optimize links response."""
        if not isinstance(data, list):
            return data

        results = []
        for link in data:
            optimized = {
                "ref": link.get("ref", ""),
                "sourceRef": link.get("sourceRef", ""),
                "anchorText": link.get("anchorText", ""),
                "type": link.get("type", ""),
                "category": link.get("category", ""),
            }
            if isinstance(link.get("text"), str):
                text = link["text"]
                optimized["text"] = text[:500] + "..." if len(text) > 500 else text
            results.append(optimized)

        return results

    def _optimize_topics_response(self, data: Any) -> dict[str, Any]:
        """Optimize topics response."""
        if not isinstance(data, dict):
            return data

        essential_fields = {
            "slug",
            "titles",
            "description",
            "categoryDescription",
            "numSources",
            "primaryTitle",
            "image",
            "good_to_promote",
        }

        optimized = {k: v for k, v in data.items() if k in essential_fields}

        if isinstance(data.get("links"), list):
            optimized["links"] = data["links"][:10]

        if isinstance(data.get("refs"), list):
            optimized["refs"] = data["refs"][:10]
            optimized["refs_note"] = f"Showing first 10 of {len(data['refs'])} total refs"

        return optimized

    def _optimize_index_response(self, data: Any) -> dict[str, Any]:
        """Optimize index response."""
        if not isinstance(data, dict):
            return data

        essential_fields = {
            "title",
            "heTitle",
            "titleVariants",
            "schema",
            "categories",
            "sectionNames",
            "addressTypes",
            "length",
            "lengths",
            "textDepth",
            "primaryTitle",
            "compDate",
            "era",
            "authors",
        }

        return {k: v for k, v in data.items() if k in essential_fields}

    def _format_search_results(
        self,
        data: dict[str, Any],
        filter_used: list[str] | None = None,
        original_filters: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Format Elasticsearch hits into a compact list for Claude.

        If results came from a fallback (no filters), annotates each result
        so Claude knows the original filter was removed.
        """
        hits = data.get("hits", {}).get("hits", [])
        if not isinstance(hits, list):
            return []

        results = []
        for hit in hits:
            source = hit.get("_source", {})
            result = {"ref": source.get("ref", ""), "categories": source.get("categories", [])}

            if original_filters and not filter_used:
                result["original_filter"] = original_filters
                result["filter_correction"] = "Removed filters due to no results"

            # Extract text snippet
            text_snippet = ""
            highlight = hit.get("highlight", {})
            for highlights in highlight.values():
                if isinstance(highlights, list) and highlights:
                    text_snippet = " [...] ".join(highlights)
                    break

            if not text_snippet:
                for field_name in ["naive_lemmatizer", "exact"]:
                    content = source.get(field_name)
                    if isinstance(content, str) and content:
                        text_snippet = content[:300] + ("..." if len(content) > 300 else "")
                        break

            result["text_snippet"] = text_snippet
            results.append(result)

        return results
