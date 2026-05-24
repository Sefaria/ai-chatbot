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
import html
import json
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .source_sheet_serializer import prepare_source_sheet_sources, serialize_source_sheet_payload

# ---------------------------------------------------------------------------
# Base URL configuration — supports both public Sefaria and local k8s service
# ---------------------------------------------------------------------------

DEFAULT_SEFARIA_BASE_URL = os.environ.get("SEFARIA_API_BASE_URL", "https://www.sefaria.org")


def _get_default_sefaria_base_url() -> str:
    return os.environ.get("SEFARIA_API_BASE_URL") or "https://www.sefaria.org"


def _get_default_sefaria_ai_base_url() -> str:
    # In k8s, the AI service may be available via service discovery env vars.
    service_host = os.environ.get("VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST")
    service_port = os.environ.get("VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT")

    if service_host and service_port:
        return f"http://{service_host}:{service_port}"

    return os.environ.get("SEFARIA_AI_BASE_URL") or "https://ai.sefaria.org"


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
        self.base_url = (base_url or _get_default_sefaria_base_url()).rstrip("/")
        self.ai_base_url = (ai_base_url or _get_default_sefaria_ai_base_url()).rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._user_id: str | None = None
        self._session_token: str | None = None

    def set_user_session(self, user_id: str | None, session_token: str | None) -> None:
        """Store per-request auth context for endpoints that require user session access."""
        self._user_id = user_id
        self._session_token = session_token

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

        Uses Sefaria's Elasticsearch wrapper. Searches remain scoped to the
        requested filters; if no results are found, a no-results payload is returned.
        """
        data = await self._search(query, filters, size)
        results = self._format_search_results(data, filters)

        if results:
            return results

        if filters:
            filter_summary = ", ".join(filters)
            suggestion = (
                "No texts found matching this query within the requested book or filter scope "
                f"({filter_summary}). Consider using different keywords, a nearby title, or a broader "
                "search term. If searching in Hebrew, try the exact phrase from the source text."
            )
        else:
            suggestion = (
                "No texts found matching this query. Consider using different keywords or trying a "
                "broader search term. If searching in Hebrew, try the exact phrase from the source text."
            )

        return {
            "no_results": True,
            "query": query,
            "suggestion": suggestion,
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
        self, reference: str, with_text: str = "0", category: str | None = None
    ) -> list[dict[str, Any]]:
        """Find cross-references to a text passage."""
        encoded_ref = quote(reference)
        params = {"with_text": with_text}
        if category:
            params["category"] = category
        data = await self._get_json(f"api/links/{encoded_ref}", params)
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

    async def get_library_index(self) -> list[dict[str, Any]]:
        """Get the full library index tree from Sefaria."""
        data = await self._get_json("api/index", {"include_authors": "1"})
        if not isinstance(data, list):
            raise ValueError("Expected api/index to return a top-level list")
        return data

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
        try:
            data = await self._get_json(f"api/authors/{encoded_slug}/indexes", params)
            return self._optimize_author_indexes_response(data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {
                    "error": f"Author slug '{author_slug}' was not found.",
                    "suggestion": "Use clarify_name_argument to find the correct author slug, then try get_author_indexes again.",
                }
            raise

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
            data = response.json()
            if isinstance(data, str):
                return data.strip() or None
            return None
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

    async def search_user_source_sheets(
        self, query: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """Search the authenticated user's own source sheets."""
        user_id, session_token = self._require_user_session()
        encoded_user_id = quote(user_id, safe="")
        data = await self._get_json(
            f"api/sheets/user/{encoded_user_id}/date/0/0",
            headers={
                "Accept": "application/json",
                "X-Session-ID": session_token,
            },
        )

        sheets = data.get("sheets", []) if isinstance(data, dict) else []
        matching_sheets = self._filter_user_source_sheets(sheets, query)
        normalized_limit = self._normalize_sheet_limit(limit)

        if query and not matching_sheets:
            return {
                "query": query,
                "total_matches": 0,
                "total_sheets": len(sheets),
                "sheets": [],
                "suggestion": "No matching source sheets were found. Try a broader keyword or ask for recent source sheets instead.",
            }

        return {
            "query": query or "",
            "total_matches": len(matching_sheets),
            "total_sheets": len(sheets),
            "sheets": matching_sheets[:normalized_limit],
        }

    async def get_source_sheet(self, sheet_id: int | str) -> dict[str, Any]:
        """Load and optimize a source sheet by ID."""
        encoded_sheet_id = quote(str(sheet_id), safe="")
        headers = {"Accept": "application/json"}
        if self._session_token:
            headers["X-Session-ID"] = self._session_token

        data = await self._get_json(f"api/sheets/{encoded_sheet_id}", headers=headers)
        return self._optimize_source_sheet_response(data)

    async def create_source_sheet(
        self, title: str, summary: str, sources: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Create a new authenticated source sheet."""
        _, session_token = self._require_user_session("create_source_sheet")
        payload = serialize_source_sheet_payload(
            title=title,
            summary=summary,
            sources=await self._hydrate_source_sheet_sources(sources),
        )
        data = await self._post_form_json(
            "api/sheets/",
            data={"json": json.dumps(payload, ensure_ascii=False)},
            headers={
                "Accept": "application/json",
                "X-Session-ID": session_token,
            },
        )
        return self._optimize_source_sheet_response(data)

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
        self,
        endpoint: str,
        params: dict[str, str | list[str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET a JSON endpoint from the main Sefaria API."""
        url = f"{self.base_url}/{endpoint}"
        if params:
            filtered_params = {k: v for k, v in params.items() if v}
            if filtered_params:
                url = f"{url}?{urlencode(filtered_params, doseq=True)}"

        client = await self._get_client()
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    async def _post_form_json(
        self,
        endpoint: str,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST form-encoded data to the main Sefaria API and parse JSON response."""
        url = f"{self.base_url}/{endpoint}"
        client = await self._get_client()
        response = await client.post(url, data=data, headers=headers)
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

    def _optimize_source_sheet_response(self, data: Any) -> dict[str, Any]:
        """Keep the parts of a source sheet that are useful for the model."""
        if not isinstance(data, dict):
            return data

        optimized_sources = []
        for source in data.get("sources", []):
            if not isinstance(source, dict):
                continue

            optimized_source = {}

            if source.get("node") is not None:
                optimized_source["node"] = source.get("node")
            if source.get("ref"):
                optimized_source["ref"] = source.get("ref")
            if source.get("heRef"):
                optimized_source["heRef"] = source.get("heRef")
            if source.get("outsideText"):
                optimized_source["outsideText"] = self._strip_sheet_html(source.get("outsideText"))
            if source.get("outsideBiText"):
                optimized_source["outsideBiText"] = {
                    key: self._strip_sheet_html(value)
                    for key, value in source.get("outsideBiText", {}).items()
                    if value
                }
            if source.get("comment"):
                optimized_source["comment"] = self._strip_sheet_html(source.get("comment"))
            if isinstance(source.get("text"), dict):
                optimized_source["text"] = {
                    key: self._strip_sheet_html(value)
                    for key, value in source["text"].items()
                    if value
                }

            if optimized_source:
                optimized_sources.append(optimized_source)

        return {
            "id": data.get("id"),
            "_id": data.get("_id"),
            "title": data.get("title", ""),
            "status": data.get("status", ""),
            "summary": data.get("summary", ""),
            "sheetUrl": self._build_sheet_url(data.get("id")),
            "owner": data.get("owner"),
            "ownerName": data.get("ownerName", ""),
            "source_count": len(optimized_sources),
            "sources": optimized_sources,
        }

    def _build_sheet_url(self, sheet_id: Any) -> str:
        """Build an absolute source sheet URL when a numeric sheet id is available."""
        if sheet_id is None:
            return ""
        return f"{DEFAULT_SEFARIA_BASE_URL}/sheets/{sheet_id}"

    async def _hydrate_source_sheet_sources(
        self, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Fill in source text HTML for ref sources before sheet creation."""
        normalized_sources = prepare_source_sheet_sources(sources)
        ref_indices = [
            index for index, source in enumerate(normalized_sources) if source.get("ref")
        ]
        if not ref_indices:
            return normalized_sources

        ref_payloads = await asyncio.gather(
            *[self.get_text(normalized_sources[index]["ref"], "both") for index in ref_indices]
        )

        for index, ref_payload in zip(ref_indices, ref_payloads, strict=True):
            normalized_sources[index]["text"] = self._build_sheet_text_block(ref_payload)

        return normalized_sources

    def _build_sheet_text_block(self, payload: dict[str, Any]) -> dict[str, str]:
        """Convert a get_text response into the bilingual HTML saved on sheets."""
        english_text = self._extract_sheet_language_text(payload, "en")
        hebrew_text = self._extract_sheet_language_text(payload, "he")
        return {
            "en": self._render_sheet_html(english_text),
            "he": self._render_sheet_html(hebrew_text),
        }

    def _extract_sheet_language_text(self, payload: dict[str, Any], language: str) -> Any:
        """Pick the best available language payload from get_text output."""
        language_matches = {
            "en": ("english",),
            "he": ("hebrew", "source"),
        }

        versions = payload.get("versions")
        if isinstance(versions, list):
            for version in versions:
                language_name = str(version.get("languageFamilyName", "")).casefold()
                if any(match in language_name for match in language_matches[language]):
                    return version.get("text")

        return payload.get("text") if language == "en" else payload.get("he")

    def _render_sheet_html(self, text_value: Any) -> str:
        """Render text data from /api/v3/texts into simple sheet-safe HTML."""
        segments = self._flatten_text_segments(text_value)
        if not segments:
            return "..."

        rendered_segments = [self._normalize_sheet_segment_html(segment) for segment in segments]
        joined_segments = " ".join(segment for segment in rendered_segments if segment).strip()
        return f"<p>{joined_segments}</p>" if joined_segments else "..."

    def _flatten_text_segments(self, value: Any) -> list[str]:
        """Flatten nested arrays returned by /texts into a linear list of strings."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            flattened: list[str] = []
            for item in value:
                flattened.extend(self._flatten_text_segments(item))
            return flattened
        if isinstance(value, dict):
            flattened: list[str] = []
            for key in ("text", "he", "en"):
                if key in value:
                    flattened.extend(self._flatten_text_segments(value[key]))
            return flattened
        return [str(value)]

    @staticmethod
    def _normalize_sheet_segment_html(segment: str) -> str:
        """Preserve trusted HTML segments when present; otherwise escape text."""
        stripped_segment = segment.strip()
        if not stripped_segment:
            return ""
        if re.search(r"<[^>]+>", stripped_segment):
            return stripped_segment
        return html.escape(stripped_segment)

    def _require_user_session(self, operation: str = "this action") -> tuple[str, str]:
        """Return authenticated user session context required by private user endpoints."""
        if not self._user_id or not self._session_token:
            raise ValueError(
                f"{operation} requires authenticated user context with a retained session token"
            )
        return self._user_id, self._session_token

    def _filter_user_source_sheets(
        self, sheets: list[dict[str, Any]], query: str | None
    ) -> list[dict[str, Any]]:
        """Return compact source sheet records, optionally ranked by query match."""
        optimized = [
            self._optimize_user_source_sheet(sheet) for sheet in sheets if isinstance(sheet, dict)
        ]
        if not query:
            return optimized

        normalized_query = query.casefold().strip()
        if not normalized_query:
            return optimized

        terms = [term for term in re.split(r"\s+", normalized_query) if term]
        scored_results: list[tuple[int, int, dict[str, Any]]] = []

        for index, sheet in enumerate(optimized):
            searchable_fields = {
                "title": sheet.get("title", ""),
                "summary": sheet.get("summary", ""),
                "tags": " ".join(sheet.get("tags", [])),
                "topics": " ".join(
                    filter(
                        None,
                        [
                            value
                            for topic in sheet.get("topics", [])
                            for value in [
                                topic.get("asTyped", ""),
                                topic.get("slug", ""),
                                topic.get("he", ""),
                                topic.get("en", ""),
                            ]
                        ],
                    )
                ),
            }

            matched_fields = [
                field_name
                for field_name, value in searchable_fields.items()
                if self._matches_sheet_query(value, normalized_query, terms)
            ]
            if not matched_fields:
                continue

            title_text = searchable_fields["title"].casefold()
            score = len(matched_fields) + sum(
                1
                for term in terms
                if any(term in value.casefold() for value in searchable_fields.values())
            )
            if normalized_query in title_text:
                score += 3
            elif any(term in title_text for term in terms):
                score += 1

            enriched_sheet = {
                **sheet,
                "matched_fields": matched_fields,
            }
            scored_results.append((score, index, enriched_sheet))

        scored_results.sort(key=lambda item: (-item[0], item[1]))
        return [sheet for _, _, sheet in scored_results]

    def _optimize_user_source_sheet(self, sheet: dict[str, Any]) -> dict[str, Any]:
        """Keep the sheet fields that help the model identify a user's relevant source sheet."""
        topics = []
        for topic in sheet.get("topics", []):
            if isinstance(topic, dict):
                topics.append(
                    {
                        "asTyped": topic.get("asTyped", ""),
                        "slug": topic.get("slug", ""),
                        "he": topic.get("he", ""),
                        "en": topic.get("en", ""),
                    }
                )

        return {
            "id": sheet.get("id"),
            "title": sheet.get("title", ""),
            "summary": sheet.get("summary", ""),
            "status": sheet.get("status", ""),
            "sheetUrl": sheet.get("sheetUrl", ""),
            "modified": sheet.get("modified", ""),
            "created": sheet.get("created", ""),
            "published": sheet.get("published", ""),
            "views": sheet.get("views", 0),
            "tags": [tag for tag in sheet.get("tags", []) if isinstance(tag, str)],
            "topics": topics,
        }

    @staticmethod
    def _matches_sheet_query(value: str, normalized_query: str, terms: list[str]) -> bool:
        if not value:
            return False
        normalized_value = value.casefold()
        return normalized_query in normalized_value or any(
            term in normalized_value for term in terms
        )

    @staticmethod
    def _normalize_sheet_limit(limit: Any) -> int:
        try:
            parsed_limit = int(limit)
        except (TypeError, ValueError):
            parsed_limit = 10
        return max(1, min(parsed_limit, 25))

    @staticmethod
    def _strip_sheet_html(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    def _optimize_author_indexes_response(self, data: Any) -> dict[str, Any]:
        """Preserve the main payload shape for now; placeholder for future trimming."""
        if not isinstance(data, dict):
            return data

        essential_fields = {
            "author",
            "indexes",
            "total",
            "aggregations",
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
