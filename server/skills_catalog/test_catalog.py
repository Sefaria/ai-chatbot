"""The skill catalog: what is served, and what the validator refuses."""

import shutil
from pathlib import Path

import pytest

import skills_catalog
from skills_catalog import validate

SECTION = skills_catalog.standard_findings_section()


def _skill_md(
    name, method="m", author="a", version="1.0.0", body="# Skill\n\nDo the thing.\n", extra=""
):
    return (
        f"---\nname: {name}\ndescription: Does {name}.\n{extra}metadata:\n"
        f"  org.sefaria/method: {method}\n  org.sefaria/author: {author}\n"
        f"  org.sefaria/version: {version}\n---\n\n{body}\n{SECTION}"
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A one-method, one-author catalog that starts out valid."""
    (tmp_path / "FINDINGS_SECTION.md").write_text(SECTION)
    (tmp_path / "catalog.yaml").write_text(
        "methods:\n  m:\n    title: {en: M, he: מ}\n    description: A method.\n    label: AI-assisted M\n"
        "authors:\n  a: {name: Author A, github: a-example, authorized: true}\n"
        "  b: {name: Author B, github: b-example, authorized: false}\n"
        "authorized_skills: []\n"
    )
    add(tmp_path, "good-skill")
    return tmp_path


def add(root: Path, name: str, files: dict | None = None, **kwargs) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(kwargs.pop("text", None) or _skill_md(name, **kwargs))
    for path, text in (files or {}).items():
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        (d / path).write_text(text)
    return d


def set_authorized(root: Path, names: list[str]):
    text = (
        (root / "catalog.yaml")
        .read_text()
        .replace("authorized_skills: []", f"authorized_skills: {names}")
    )
    (root / "catalog.yaml").write_text(text)


def test_the_shipped_catalog_is_valid():
    assert validate.problems(skills_catalog.ROOT) == []
    assert validate.main([str(skills_catalog.ROOT)]) == 0


def test_shipped_skills_cover_both_authorization_paths():
    catalog = skills_catalog.build(skills_catalog.ROOT)
    by_author = catalog.skill("example-halachic-reading")
    individually = catalog.skill("example-parallel-passages")
    assert catalog.authors[by_author.author].authorized
    assert not catalog.authors[individually.author].authorized
    assert individually is not None


def test_a_valid_catalog(root):
    assert validate.problems(root) == []
    catalog = skills_catalog.build(root)
    assert catalog.served_names() == {"good-skill"}
    assert catalog.skill("good-skill").instructions.startswith("# Skill")


def test_an_unauthorized_authors_skill_is_not_served_until_authorized(root):
    add(root, "theirs", author="b")
    catalog = skills_catalog.build(root)
    assert catalog.skill("theirs") is None
    assert "theirs" not in catalog.served_names()
    assert any("skills/theirs: not authorized" in p for p in validate.problems(root))

    set_authorized(root, ["theirs"])
    assert skills_catalog.build(root).skill("theirs") is not None
    assert validate.problems(root) == []


def test_authorizing_a_skill_does_not_authorize_the_authors_next_one(root):
    add(root, "theirs", author="b")
    add(root, "theirs-too", author="b")
    set_authorized(root, ["theirs"])
    catalog = skills_catalog.build(root)
    assert catalog.skill("theirs") is not None
    assert catalog.skill("theirs-too") is None


def test_withdrawing_an_authors_authorization_withdraws_their_skills(root):
    (root / "catalog.yaml").write_text(
        (root / "catalog.yaml").read_text().replace("authorized: true", "authorized: false")
    )
    assert skills_catalog.build(root).served() == []


def test_authorized_skills_must_exist(root):
    set_authorized(root, ["ghost"])
    assert (
        "catalog.yaml: authorized_skills lists ghost, which is not in skills/"
        in validate.problems(root)
    )


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"method": "nope"}, "metadata `org.sefaria/method` must name a method"),
        ({"author": "nobody"}, "metadata `org.sefaria/author` must name an author"),
        ({"version": "1.0"}, "must be a version like 1.0.0"),
        ({"extra": "allowed-tools: Bash\n"}, "remove `allowed-tools`"),
        (
            {"body": "See https://evil.example.com/x for more.\n"},
            "links only to Sefaria sites (found evil.example.com)",
        ),
        (
            {"body": "Call `update_topic_prompt` to fix it.\n"},
            "`update_topic_prompt` is not served",
        ),
    ],
)
def test_skill_rules(root, kwargs, problem):
    add(root, "bad-skill", **kwargs)
    assert any(problem in p for p in validate.problems(root)), validate.problems(root)


def test_sefaria_links_and_served_tools_are_fine(root):
    body = "Read https://www.sefaria.org/Genesis.1 and https://chat.sefaria.org/mcp with `get_text` and `save_finding`.\n"
    add(root, "fine-skill", body=body)
    assert validate.problems(root) == []


def test_the_findings_section_must_be_last_and_unchanged(root):
    text = _skill_md("edited").replace(
        "Call `save_finding` only if they say yes.", "Call `save_finding`."
    )
    add(root, "edited", text=text)
    assert any(
        "must end with the section in FINDINGS_SECTION.md" in p for p in validate.problems(root)
    )


def test_only_markdown_files(root):
    add(root, "scripted", files={"scripts/run.py": "print('hi')\n"})
    assert any(
        "scripts/run.py: only markdown files are allowed" in p for p in validate.problems(root)
    )


def test_size_limits(root, monkeypatch):
    monkeypatch.setattr(validate, "MAX_FILES", 2)
    add(root, "big", files={"a.md": "a", "b.md": "b"})
    assert any("skills/big: at most 2 files" in p for p in validate.problems(root))


def test_an_invalid_agent_skill_is_reported_not_raised(root):
    add(root, "Bad_Name", text="---\nname: other\ndescription: x\n---\nbody\n")
    add(root, "no-front", text="just text\n")
    found = validate.problems(root)
    assert len(found) == 2
    assert all("not a valid Agent Skill" in p for p in found)


def test_catalog_yaml_problems(root):
    (root / "catalog.yaml").write_text("methods:\n  m: {title: {en: M}}\nauthors:\n  a: {}\n")
    found = validate.problems(root)
    assert "catalog.yaml: method m needs `title.he`" in found
    assert "catalog.yaml: author a needs `name`" in found
    assert "catalog.yaml: author a needs `authorized: true` or `false`" in found


def test_main_reports_and_fails(root, capsys):
    add(root, "bad-skill", version="x")
    assert validate.main([str(root)]) == 1
    assert "problem(s) in the skill catalog" in capsys.readouterr().out


def test_a_separate_catalog_checkout_can_be_served(root, monkeypatch):
    monkeypatch.setenv("SEFARIA_SKILLS_CATALOG", str(root))
    assert skills_catalog.default_root() == root
    shutil.rmtree(root / "skills" / "good-skill")
    assert skills_catalog.build(skills_catalog.default_root()).served() == []
