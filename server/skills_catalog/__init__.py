"""The catalog of Sefaria skills: methods of study served over MCP, and who may publish them.

A *skill* is an Agent Skills directory under ``skills/`` (``SKILL.md`` plus supporting files),
served by the MCP process through the MCP Skills extension. A *method* is a way of learning
(one teacher's systematic approach, say) that one or more skills belong to. ``catalog.yaml``,
owned by staff, defines the methods, the authors, and which skills are authorized: every skill
of an authorized author, plus skills authorized one by one. Only authorized skills are served,
and only their findings are shown.

No Django imports: both the MCP process and the Django app load the catalog.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
META_PREFIX = "org.sefaria/"
METHOD_KEY = f"{META_PREFIX}method"
AUTHOR_KEY = f"{META_PREFIX}author"
VERSION_KEY = f"{META_PREFIX}version"
ROLES = ("anchor", "position", "support")


@dataclass(frozen=True)
class Method:
    slug: str
    title_en: str
    title_he: str
    description: str
    label: str

    def to_json(self) -> dict:
        return {
            "slug": self.slug,
            "title": {"en": self.title_en, "he": self.title_he},
            "description": self.description,
            "label": self.label,
        }


@dataclass(frozen=True)
class Author:
    slug: str
    name: str
    github: str
    authorized: bool


@dataclass(frozen=True)
class CatalogSkill:
    name: str
    description: str
    method: str
    author: str
    version: str
    authorized: bool
    path: Path
    files: dict[str, bytes] = field(repr=False)

    @property
    def title(self) -> str:
        return self.name.replace("-", " ").capitalize()

    @property
    def instructions(self) -> str:
        """The body of SKILL.md, without its frontmatter."""
        text = self.files["SKILL.md"].decode("utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                return text[end + 4 :].lstrip("\n")
        return text


@dataclass(frozen=True)
class Catalog:
    methods: dict[str, Method]
    authors: dict[str, Author]
    skills: dict[str, CatalogSkill]
    root: Path

    def served(self) -> list[CatalogSkill]:
        """The skills that are served and whose findings are shown, by name."""
        return [s for s in sorted(self.skills.values(), key=lambda s: s.name) if s.authorized]

    def served_names(self) -> set[str]:
        return {s.name for s in self.served()}

    def skill(self, name: str) -> CatalogSkill | None:
        """A served skill by name, or None."""
        skill = self.skills.get(name)
        return skill if skill and skill.authorized else None

    def skills_of(self, method: str) -> list[CatalogSkill]:
        return [s for s in self.served() if s.method == method]


def default_root() -> Path:
    """``SEFARIA_SKILLS_CATALOG`` if set (a checkout of a separate catalog), else this package."""
    return Path(os.environ.get("SEFARIA_SKILLS_CATALOG") or ROOT)


def _text(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def read_config(root: Path) -> dict:
    with open(root / "catalog.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise ValueError("catalog.yaml must be a mapping")
    return config


def skill_dirs(root: Path) -> list[Path]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(p for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("."))


def build(root: Path) -> Catalog:
    """Read a catalog. Raises ValueError for a folder that is not a valid Agent Skill.

    Authorization and metadata problems do not raise: such skills are kept but not served,
    and ``skills_catalog.validate`` reports them.
    """
    from fastmcp.skills import Skill  # imported here: heavy, and only needed to load

    config = read_config(root)
    methods = {}
    for slug, m in (config.get("methods") or {}).items():
        m = m or {}
        title = m.get("title") or {}
        methods[slug] = Method(
            slug=slug,
            title_en=_text(title.get("en"), slug),
            title_he=_text(title.get("he")),
            description=_text(m.get("description")),
            label=_text(m.get("label")),
        )
    authors = {
        slug: Author(
            slug=slug,
            name=_text((a or {}).get("name"), slug),
            github=_text((a or {}).get("github")),
            authorized=(a or {}).get("authorized") is True,
        )
        for slug, a in (config.get("authors") or {}).items()
    }
    individually = set(config.get("authorized_skills") or [])

    skills = {}
    for path in skill_dirs(root):
        skill = Skill.from_directory(path)
        metadata = skill.frontmatter.metadata or {}
        method = str(metadata.get(METHOD_KEY, ""))
        author = str(metadata.get(AUTHOR_KEY, ""))
        known = method in methods and author in authors
        authorized = known and (authors[author].authorized or skill.name in individually)
        skills[skill.name] = CatalogSkill(
            name=skill.name,
            description=skill.description,
            method=method,
            author=author,
            version=str(metadata.get(VERSION_KEY, "")),
            authorized=authorized,
            path=path,
            files=dict(skill.files),
        )
    return Catalog(methods=methods, authors=authors, skills=skills, root=root)


@functools.cache
def load() -> Catalog:
    """The process's catalog, read once. Tests call ``load.cache_clear()`` after changing it."""
    return build(default_root())


def standard_findings_section(root: Path | None = None) -> str:
    """The section every skill ends with, verbatim."""
    return (Path(root or ROOT) / "FINDINGS_SECTION.md").read_text(encoding="utf-8")
