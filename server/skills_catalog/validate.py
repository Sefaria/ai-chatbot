"""Check the skill catalog: ``python -m skills_catalog.validate [root]``.

Run in CI on every pull request. Exits 1 and lists every problem, so a submitter can fix
them all at once. Beyond the Agent Skills spec (checked by FastMCP when a skill loads), a
catalog skill must:

- name an existing method and author in its metadata, with a semantic version;
- be authorized: by an authorized author, or listed under ``authorized_skills``;
- hold only markdown, with no ``allowed-tools`` and nothing to execute;
- stay within our limits (well under the spec's 512 files and 16 MiB);
- link only to Sefaria sites;
- name only tools a Sefaria MCP endpoint serves;
- end with the standard findings section (FINDINGS_SECTION.md), unchanged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import (
    AUTHOR_KEY,
    METHOD_KEY,
    VERSION_KEY,
    build,
    default_root,
    read_config,
    skill_dirs,
    standard_findings_section,
)

MAX_FILES = 32
MAX_TOTAL_BYTES = 512 * 1024
ALLOWED_SUFFIXES = {".md"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
URL = re.compile(r"https?://([^/\s)>\]\"'`]+)")
ALLOWED_HOSTS = re.compile(r"^([a-z0-9-]+\.)*sefaria\.org(\.il)?$")
TOOL_NAME = re.compile(r"`([a-z]+(?:_[a-z]+)+)`")
SERVED_SURFACES = ("mcp", "mcp_user")


def _served_tool_names() -> set[str]:
    from chat.V2.agent.tool_schemas import get_tools_for_surface

    names = {t["name"] for surface in SERVED_SURFACES for t in get_tools_for_surface(surface)}
    # Added by FastMCP's ResourcesAsTools transform for clients without the Skills extension.
    return names | {"list_resources", "read_resource"}


def _all_tool_names() -> set[str]:
    from chat.V2.agent.tool_schemas import ALL_TOOLS

    return set(ALL_TOOLS)


def problems(root: Path) -> list[str]:
    """Every problem in the catalog at ``root``, as readable lines. Empty means valid."""
    found: list[str] = []
    try:
        config = read_config(root)
    except Exception as exc:  # missing file, YAML syntax, wrong top-level type
        return [f"catalog.yaml: {exc}"]

    for key in ("methods", "authors"):
        if not isinstance(config.get(key) or {}, dict):
            found.append(f"catalog.yaml: `{key}` must be a mapping")
    for slug, method in (config.get("methods") or {}).items():
        method = method or {}
        title = method.get("title") or {}
        for field, value in (
            ("title.en", title.get("en")),
            ("title.he", title.get("he")),
            ("description", method.get("description")),
            ("label", method.get("label")),
        ):
            if not isinstance(value, str) or not value.strip():
                found.append(f"catalog.yaml: method {slug} needs `{field}`")
    for slug, author in (config.get("authors") or {}).items():
        author = author or {}
        if not isinstance(author.get("name"), str) or not author["name"].strip():
            found.append(f"catalog.yaml: author {slug} needs `name`")
        if not isinstance(author.get("authorized"), bool):
            found.append(f"catalog.yaml: author {slug} needs `authorized: true` or `false`")

    names = {p.name for p in skill_dirs(root)}
    for name in config.get("authorized_skills") or []:
        if name not in names:
            found.append(f"catalog.yaml: authorized_skills lists {name}, which is not in skills/")

    from fastmcp.skills import Skill

    invalid = []
    for path in skill_dirs(root):
        try:
            Skill.from_directory(path)
        except (ValueError, OSError) as exc:
            invalid.append(f"skills/{path.name}: not a valid Agent Skill: {exc}")
    if invalid:
        return found + invalid
    catalog = build(root)

    section = standard_findings_section(root).strip()
    served_tools = _served_tool_names()
    known_tools = _all_tool_names() | served_tools

    for skill in sorted(catalog.skills.values(), key=lambda s: s.name):
        where = f"skills/{skill.name}"
        frontmatter = _frontmatter(skill.files["SKILL.md"].decode("utf-8"))
        if "allowed-tools" in frontmatter:
            found.append(
                f"{where}: remove `allowed-tools`; MCP hosts ignore it and it reads as a request for access"
            )
        if skill.method not in catalog.methods:
            found.append(f"{where}: metadata `{METHOD_KEY}` must name a method in catalog.yaml")
        if skill.author not in catalog.authors:
            found.append(f"{where}: metadata `{AUTHOR_KEY}` must name an author in catalog.yaml")
        if not SEMVER.match(skill.version):
            found.append(f"{where}: metadata `{VERSION_KEY}` must be a version like 1.0.0")
        if (
            skill.method in catalog.methods
            and skill.author in catalog.authors
            and not skill.authorized
        ):
            found.append(
                f"{where}: not authorized; its author is not authorized and it is not listed "
                "under authorized_skills in catalog.yaml"
            )

        total = 0
        for path, data in sorted(skill.files.items()):
            total += len(data)
            if Path(path).suffix not in ALLOWED_SUFFIXES:
                found.append(f"{where}/{path}: only markdown files are allowed")
                continue
            text = data.decode("utf-8", errors="replace")
            for host in URL.findall(text):
                if not ALLOWED_HOSTS.match(host.lower()):
                    found.append(f"{where}/{path}: links only to Sefaria sites (found {host})")
            for tool in TOOL_NAME.findall(text):
                if tool in known_tools and tool not in served_tools:
                    found.append(
                        f"{where}/{path}: `{tool}` is not served by a Sefaria MCP endpoint"
                    )
        if len(skill.files) > MAX_FILES:
            found.append(f"{where}: at most {MAX_FILES} files (has {len(skill.files)})")
        if total > MAX_TOTAL_BYTES:
            found.append(
                f"{where}: at most {MAX_TOTAL_BYTES // 1024} KB in total (has {total // 1024} KB)"
            )
        if not skill.instructions.rstrip().endswith(section):
            found.append(
                f"{where}: SKILL.md must end with the section in FINDINGS_SECTION.md, unchanged"
            )
    return found


def _frontmatter(text: str) -> dict:
    import yaml

    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    data = yaml.safe_load(text[3:end]) if end != -1 else None
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else default_root()
    found = problems(root)
    for line in found:
        print(line)
    if found:
        print(f"\n{len(found)} problem(s) in the skill catalog at {root}")
        return 1
    print(f"Skill catalog at {root} is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
