"""Cached library catalog service backed by Sefaria's ``api/index`` endpoint."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Any, TypeAlias

from .sefaria_client import SefariaClient

AuthorRecord: TypeAlias = dict[str, str]


class CatalogService:
    """Provides cached traversal and search over the Sefaria catalog tree."""

    _cache_lock = Lock()
    _raw_catalog: list[dict[str, Any]] | None = None
    _raw_catalog_ts: float = 0.0
    _compiled_index: dict[str, Any] | None = None
    _compiled_index_ts: float = 0.0

    def __init__(
        self,
        client: SefariaClient | None = None,
        *,
        cache_ttl_seconds: int = 60 * 60 * 24,
    ):
        self.client = client or SefariaClient()
        self.cache_ttl_seconds = cache_ttl_seconds

    async def get_node(
        self,
        identifier: str,
        *,
        identifier_type: str = "path",
        child_limit: int = 20,
    ) -> dict[str, Any]:
        """Return one catalog node or an ambiguity payload."""
        index = await self._get_compiled_index()
        matches = self._resolve_identifier(index, identifier, identifier_type)

        if not matches:
            return {
                "found": False,
                "identifier": identifier,
                "identifier_type": identifier_type,
                "suggestion": "Try catalog_search with a broader lexical query.",
            }

        if len(matches) > 1:
            return {
                "found": False,
                "ambiguous": True,
                "identifier": identifier,
                "identifier_type": identifier_type,
                "matches": [
                    self._summarize_node(index["nodes_by_id"][node_id]) for node_id in matches[:20]
                ],
            }

        node = index["nodes_by_id"][matches[0]]
        return {
            "found": True,
            "node": self._serialize_node(node, child_limit=child_limit),
        }

    async def get_children(
        self,
        path: str,
        *,
        child_type: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List direct children for a category path."""
        index = await self._get_compiled_index()
        normalized_path = self._normalize_key(path)
        node_id = index["path_lookup"].get(normalized_path)
        if not node_id:
            return {
                "found": False,
                "path": path,
                "suggestion": "Try catalog_search to resolve the closest category path.",
            }

        node = index["nodes_by_id"][node_id]
        if node["type"] != "category":
            return {
                "found": False,
                "path": path,
                "error": "Path resolved to a book node. Use catalog_get_node for book metadata.",
            }

        child_ids = list(node["children"])
        if child_type == "category":
            child_ids = [
                child_id
                for child_id in child_ids
                if index["nodes_by_id"][child_id]["type"] == "category"
            ]
        elif child_type == "book":
            child_ids = [
                child_id
                for child_id in child_ids
                if index["nodes_by_id"][child_id]["type"] == "book"
            ]

        page = child_ids[offset : offset + limit]
        return {
            "found": True,
            "parent": self._summarize_node(node),
            "child_type": child_type,
            "total_children": len(child_ids),
            "offset": offset,
            "limit": limit,
            "children": [self._summarize_node(index["nodes_by_id"][child_id]) for child_id in page],
        }

    async def search(
        self,
        query: str,
        *,
        node_type: str = "any",
        category_path: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Run a lexical search over titles, descriptions, and creator metadata."""
        index = await self._get_compiled_index()
        query_norm = self._normalize_text(query)
        if not query_norm:
            return {"results": [], "query": query}

        path_prefix = self._normalize_key(category_path) if category_path else None
        results: list[dict[str, Any]] = []
        for node in index["nodes_by_id"].values():
            if not self._matches_node_type(node, node_type):
                continue
            if path_prefix and not node["path_key"].startswith(path_prefix):
                continue

            score, matched_fields = self._score_node(node, query_norm)
            if score <= 0:
                continue

            results.append(
                {
                    "score": score,
                    "matched_fields": matched_fields,
                    "node": self._summarize_node(node),
                }
            )

        results.sort(
            key=lambda item: (
                -item["score"],
                item["node"].get("order", 10**9),
                item["node"]["path"],
            )
        )
        return {
            "query": query,
            "node_type": node_type,
            "category_path": category_path,
            "results": results[:limit],
        }

    async def query(
        self,
        *,
        node_type: str = "any",
        filters: dict[str, Any] | None = None,
        select: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Filter normalized catalog nodes with structured predicates."""
        index = await self._get_compiled_index()
        filters = filters or {}

        matches = [
            node
            for node in index["nodes_by_id"].values()
            if self._matches_node_type(node, node_type) and self._matches_filters(node, filters)
        ]
        matches.sort(key=lambda node: (node["path"], node.get("order", 10**9)))
        page = matches[offset : offset + limit]

        return {
            "node_type": node_type,
            "filters": filters,
            "total_matches": len(matches),
            "offset": offset,
            "limit": limit,
            "results": [self._project_node(node, select) for node in page],
        }

    async def _get_compiled_index(self) -> dict[str, Any]:
        cls = type(self)
        now = time.time()
        with cls._cache_lock:
            if (
                cls._compiled_index is not None
                and now - cls._compiled_index_ts < self.cache_ttl_seconds
            ):
                return cls._compiled_index

        raw_catalog = await self._get_raw_catalog()
        compiled = self._build_index(raw_catalog)

        with cls._cache_lock:
            cls._compiled_index = compiled
            cls._compiled_index_ts = time.time()

        return compiled

    async def _get_raw_catalog(self) -> list[dict[str, Any]]:
        cls = type(self)
        now = time.time()
        with cls._cache_lock:
            if cls._raw_catalog is not None and now - cls._raw_catalog_ts < self.cache_ttl_seconds:
                return cls._raw_catalog

        raw_catalog = await self.client.get_library_index()
        if not isinstance(raw_catalog, list):
            raise ValueError("Catalog index must be a top-level list")

        with cls._cache_lock:
            cls._raw_catalog = raw_catalog
            cls._raw_catalog_ts = time.time()

        return raw_catalog

    def _build_index(self, raw_catalog: list[dict[str, Any]]) -> dict[str, Any]:
        nodes_by_id: dict[str, dict[str, Any]] = {}
        title_lookup: dict[str, list[str]] = defaultdict(list)
        he_title_lookup: dict[str, list[str]] = defaultdict(list)
        path_lookup: dict[str, str] = {}
        root_ids: list[str] = []

        def visit(node: dict[str, Any], path: list[str], parent_id: str | None) -> str | None:
            if not isinstance(node, dict):
                return None

            if self._is_category_node(node):
                label = node.get("category")
                if not label:
                    return None
                current_path = [*path, label]
                node_id = "/".join(current_path)
                compiled_node = {
                    "id": node_id,
                    "type": "category",
                    "path": current_path,
                    "path_key": self._normalize_key(node_id),
                    "title": label,
                    "heTitle": node.get("heCategory"),
                    "order": node.get("order"),
                    "enDesc": node.get("enDesc"),
                    "heDesc": node.get("heDesc"),
                    "enShortDesc": node.get("enShortDesc"),
                    "heShortDesc": node.get("heShortDesc"),
                    "searchRoot": node.get("searchRoot"),
                    "isPrimary": node.get("isPrimary"),
                    "parent_id": parent_id,
                    "children": [],
                    "raw": node,
                    "creators": [],
                }
                nodes_by_id[node_id] = compiled_node
                path_lookup[compiled_node["path_key"]] = node_id
                title_lookup[self._normalize_key(label)].append(node_id)
                if compiled_node["heTitle"]:
                    he_title_lookup[self._normalize_key(compiled_node["heTitle"])].append(node_id)
                if parent_id is None:
                    root_ids.append(node_id)
                for child in node.get("contents", []) or []:
                    child_id = visit(child, current_path, node_id)
                    if child_id:
                        compiled_node["children"].append(child_id)
                return node_id

            if "title" not in node:
                return None

            title = node["title"]
            current_path = [*path, title]
            node_id = "/".join(current_path)
            authors = self._extract_authors(node)
            creators = self._extract_creators(node, authors)
            compiled_node = {
                "id": node_id,
                "type": "book",
                "path": current_path,
                "path_key": self._normalize_key(node_id),
                "title": title,
                "heTitle": node.get("heTitle"),
                "order": node.get("order"),
                "categories": node.get("categories", []),
                "primary_category": node.get("primary_category"),
                "corpus": node.get("corpus"),
                "dependence": node.get("dependence"),
                "commentator": node.get("commentator"),
                "heCommentator": node.get("heCommentator"),
                "collectiveTitle": node.get("collectiveTitle"),
                "heCollectiveTitle": node.get("heCollectiveTitle"),
                "base_text_titles": node.get("base_text_titles", []),
                "base_text_order": node.get("base_text_order"),
                "base_text_mapping": node.get("base_text_mapping"),
                "hidden": node.get("hidden", False),
                "isCollection": node.get("isCollection", False),
                "slug": node.get("slug"),
                "name": node.get("name"),
                "nodeType": node.get("nodeType"),
                "enShortDesc": node.get("enShortDesc"),
                "heShortDesc": node.get("heShortDesc"),
                "parent_id": parent_id,
                "children": [],
                "raw": node,
                "authors": authors,
                "creators": creators,
            }
            nodes_by_id[node_id] = compiled_node
            path_lookup[compiled_node["path_key"]] = node_id
            title_lookup[self._normalize_key(title)].append(node_id)
            if compiled_node["heTitle"]:
                he_title_lookup[self._normalize_key(compiled_node["heTitle"])].append(node_id)
            return node_id

        for node in raw_catalog:
            visit(node, [], None)

        return {
            "nodes_by_id": nodes_by_id,
            "title_lookup": title_lookup,
            "he_title_lookup": he_title_lookup,
            "path_lookup": path_lookup,
            "root_ids": root_ids,
        }

    @staticmethod
    def _is_category_node(node: dict[str, Any]) -> bool:
        return "category" in node and "title" not in node

    @staticmethod
    def _extract_authors(node: dict[str, Any]) -> list[AuthorRecord]:
        authors = node.get("authors")
        if not isinstance(authors, list):
            return []

        normalized_authors: list[AuthorRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for author in authors:
            if not isinstance(author, dict):
                continue

            normalized_author = {
                "en": str(author.get("en") or "").strip(),
                "he": str(author.get("he") or "").strip(),
                "slug": str(author.get("slug") or "").strip(),
            }
            if not any(normalized_author.values()):
                continue

            dedupe_key = (
                normalized_author["en"].casefold(),
                normalized_author["he"].casefold(),
                normalized_author["slug"].casefold(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized_authors.append(normalized_author)

        return normalized_authors

    @staticmethod
    def _extract_creators(node: dict[str, Any], authors: list[AuthorRecord]) -> list[str]:
        creators: list[str] = []
        for key in [
            "author",
            "commentator",
            "heCommentator",
            "collectiveTitle",
            "heCollectiveTitle",
        ]:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                creators.append(value.strip())

        for author in authors:
            for key in ["en", "he", "slug"]:
                value = author.get(key)
                if value:
                    creators.append(value)

        seen = set()
        deduped: list[str] = []
        for creator in creators:
            norm = creator.casefold()
            if norm not in seen:
                seen.add(norm)
                deduped.append(creator)
        return deduped

    def _resolve_identifier(
        self, index: dict[str, Any], identifier: str, identifier_type: str
    ) -> list[str]:
        normalized = self._normalize_key(identifier)
        if identifier_type in {"path", "id"}:
            match = index["path_lookup"].get(normalized)
            return [match] if match else []
        if identifier_type == "title":
            return list(index["title_lookup"].get(normalized, []))
        if identifier_type == "he_title":
            return list(index["he_title_lookup"].get(normalized, []))
        raise ValueError(f"Unsupported identifier_type: {identifier_type}")

    @staticmethod
    def _normalize_key(value: str | None) -> str:
        if not value:
            return ""
        return "/".join(part.strip().casefold() for part in str(value).split("/") if part.strip())

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return " ".join(str(value or "").casefold().split())

    def _serialize_node(self, node: dict[str, Any], *, child_limit: int) -> dict[str, Any]:
        payload = self._project_node(node, None)
        if node["type"] == "category":
            payload["child_count"] = len(node["children"])
            payload["children_preview"] = [
                self._summarize_node(child)
                for child in (self._project_children(node)[:child_limit])
            ]
        return payload

    def _project_children(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        index = type(self)._compiled_index or {}
        nodes_by_id = index.get("nodes_by_id", {})
        return [nodes_by_id[child_id] for child_id in node["children"] if child_id in nodes_by_id]

    def _summarize_node(self, node: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "id": node["id"],
            "type": node["type"],
            "path": node["id"],
            "title": node["title"],
            "heTitle": node.get("heTitle"),
            "order": node.get("order"),
            "enShortDesc": node.get("enShortDesc"),
            "heShortDesc": node.get("heShortDesc"),
        }
        if node["type"] == "book":
            summary["categories"] = node.get("categories", [])
            if node.get("authors"):
                summary["authors"] = node["authors"]
            if node.get("creators"):
                summary["creators"] = node["creators"]
        return summary

    def _project_node(self, node: dict[str, Any], select: list[str] | None) -> dict[str, Any]:
        default_fields = [
            "id",
            "type",
            "title",
            "heTitle",
            "path",
            "order",
            "enShortDesc",
            "heShortDesc",
            "enDesc",
            "heDesc",
            "categories",
            "primary_category",
            "corpus",
            "dependence",
            "authors",
            "creators",
            "commentator",
            "collectiveTitle",
            "base_text_titles",
            "searchRoot",
        ]
        fields = select or default_fields
        payload: dict[str, Any] = {}
        for field in fields:
            if field == "path":
                payload[field] = node["id"]
            elif field in node:
                payload[field] = node[field]
            elif field == "child_count" and node["type"] == "category":
                payload[field] = len(node["children"])
        return payload

    def _score_node(self, node: dict[str, Any], query_norm: str) -> tuple[int, list[str]]:
        fields: list[tuple[str, str | list[str] | None, int]] = [
            ("title", node.get("title"), 12),
            ("heTitle", node.get("heTitle"), 12),
            ("path", node.get("id"), 10),
            ("enShortDesc", node.get("enShortDesc"), 5),
            ("heShortDesc", node.get("heShortDesc"), 5),
            ("enDesc", node.get("enDesc"), 4),
            ("heDesc", node.get("heDesc"), 4),
            ("primary_category", node.get("primary_category"), 4),
            ("corpus", node.get("corpus"), 3),
            ("commentator", node.get("commentator"), 6),
            ("collectiveTitle", node.get("collectiveTitle"), 6),
            ("authors", self._flatten_authors(node.get("authors", [])), 6),
            ("creators", node.get("creators"), 6),
            ("categories", node.get("categories"), 4),
            ("base_text_titles", node.get("base_text_titles"), 4),
        ]

        score = 0
        matched_fields: list[str] = []
        for field_name, value, weight in fields:
            haystacks = value if isinstance(value, list) else [value]
            for haystack in haystacks:
                normalized = self._normalize_text(haystack)
                if not normalized:
                    continue
                if query_norm == normalized:
                    score += weight * 3
                    matched_fields.append(field_name)
                    break
                if normalized.startswith(query_norm):
                    score += weight * 2
                    matched_fields.append(field_name)
                    break
                if query_norm in normalized:
                    score += weight
                    matched_fields.append(field_name)
                    break
        return score, sorted(set(matched_fields))

    @staticmethod
    def _matches_node_type(node: dict[str, Any], node_type: str) -> bool:
        return node_type == "any" or node["type"] == node_type

    @staticmethod
    def _flatten_authors(authors: list[AuthorRecord]) -> list[str]:
        values: list[str] = []
        for author in authors:
            values.extend(
                value for value in [author.get("en"), author.get("he"), author.get("slug")] if value
            )
        return values

    def _matches_filters(self, node: dict[str, Any], filters: dict[str, Any]) -> bool:
        if not filters:
            return True

        path_prefix = filters.get("path_prefix")
        if path_prefix and not node["path_key"].startswith(self._normalize_key(path_prefix)):
            return False

        exact_match_fields = {
            "title": node.get("title"),
            "he_title": node.get("heTitle"),
            "primary_category": node.get("primary_category"),
            "corpus": node.get("corpus"),
            "dependence": node.get("dependence"),
            "commentator": node.get("commentator"),
            "collective_title": node.get("collectiveTitle"),
        }
        for filter_name, value in exact_match_fields.items():
            expected = filters.get(filter_name)
            if expected and self._normalize_text(value) != self._normalize_text(expected):
                return False

        contains_fields = {
            "title_contains": node.get("title"),
            "he_title_contains": node.get("heTitle"),
            "description_contains": " ".join(
                part
                for part in [
                    str(node.get("enShortDesc") or ""),
                    str(node.get("heShortDesc") or ""),
                    str(node.get("enDesc") or ""),
                    str(node.get("heDesc") or ""),
                ]
                if part
            ),
            "author_name_contains": " ".join(self._flatten_authors(node.get("authors", []))),
            "creator_contains": " ".join(node.get("creators", [])),
        }
        for filter_name, value in contains_fields.items():
            if value_filter := filters.get(filter_name):
                if self._normalize_text(value_filter) not in self._normalize_text(value):
                    return False

        if category := filters.get("category"):
            if self._normalize_text(category) not in {
                self._normalize_text(item) for item in node.get("categories", [])
            }:
                return False

        if base_text_title := filters.get("base_text_title"):
            if self._normalize_text(base_text_title) not in {
                self._normalize_text(item) for item in node.get("base_text_titles", [])
            }:
                return False

        if creator := filters.get("creator"):
            if self._normalize_text(creator) not in {
                self._normalize_text(item) for item in node.get("creators", [])
            }:
                return False

        author_values = node.get("authors", [])
        author_match_fields = {
            "author_en": "en",
            "author_he": "he",
            "author_slug": "slug",
        }
        for filter_name, field_name in author_match_fields.items():
            if expected := filters.get(filter_name):
                if self._normalize_text(expected) not in {
                    self._normalize_text(author.get(field_name))
                    for author in author_values
                    if author.get(field_name)
                }:
                    return False

        if has_field := filters.get("has_field"):
            value = node.get(has_field)
            if value in (None, "", [], {}):
                return False

        for boolean_name in ["hidden", "is_collection"]:
            if boolean_name in filters:
                node_value = (
                    node.get("isCollection")
                    if boolean_name == "is_collection"
                    else node.get(boolean_name)
                )
                if bool(node_value) != bool(filters[boolean_name]):
                    return False

        return True
