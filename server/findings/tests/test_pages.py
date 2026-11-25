"""The findings pages: methods, skills, passages and single findings."""

import pytest

pytestmark = pytest.mark.django_db


def html(response) -> str:
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def test_home_lists_methods_skills_and_recent_findings(client, make):
    make(title="About light")
    page = html(client.get("/findings/"))
    assert "Rabbi Ploni&#x27;s Method" in page and "שיטת רבי פלוני" in page
    assert "Parallel Passages" in page
    assert "/findings/skills/example-halachic-reading/" in page
    assert "by Ploni Almoni" in page
    assert "About light" in page
    assert "<strong>1</strong> finding on <strong>1</strong> passage" in page
    assert 'class="chip chip-anchor"' in page


def test_home_without_findings(client):
    page = html(client.get("/findings/"))
    assert "No findings yet." in page


def test_text_search(client, make):
    make(sheet_id=1, title="About light")
    make(sheet_id=2, title="About water")
    page = html(client.get("/findings/?q=water"))
    assert "About water" in page and "About light" not in page
    assert "Findings matching “water”" in page


def test_a_ref_search_goes_to_the_passage(client):
    response = client.get("/findings/?q=Genesis 1:1")
    assert response.status_code == 302
    assert response["Location"] == "/findings/ref/Genesis.1.1/"


def test_method_page_with_books_filter(client, make):
    make(sheet_id=1, title="In Genesis")
    make(
        sheet_id=2,
        title="In Exodus",
        expanded=["Exodus 1:1"],
        roles=[{"ref": "Exodus 1:1", "role": "anchor"}],
    )
    page = html(client.get("/findings/methods/example-method/"))
    assert "Where it has been applied" in page
    assert "?book=Genesis" in page and "?book=Exodus" in page
    assert "In Genesis" in page and "In Exodus" in page
    assert "AI-assisted reading in the approach of Rabbi Ploni" in page

    page = html(client.get("/findings/methods/example-method/?book=Exodus"))
    assert "Findings in Exodus" in page
    assert "In Exodus" in page and "In Genesis" not in page


def test_unknown_method_is_404(client):
    assert client.get("/findings/methods/nope/").status_code == 404


def test_skill_page_renders_instructions_without_raw_html(client, make):
    make()
    page = html(client.get("/findings/skills/example-halachic-reading/"))
    assert "<h2>Begin inside the law</h2>" in page
    assert "claude mcp add --transport http sefaria https://chat.test/mcp/user" in page
    assert "skill://example-halachic-reading/SKILL.md" in page
    assert "/findings/skills/example-halachic-reading/references/attribution.md" in page
    assert "Findings made with this skill" in page


def test_skill_reference_file(client):
    page = html(client.get("/findings/skills/example-halachic-reading/references/attribution.md"))
    assert "<h1>Attribution</h1>" in page
    assert client.get("/findings/skills/example-halachic-reading/SKILL.md").status_code == 404
    assert (
        client.get("/findings/skills/example-halachic-reading/../catalog.yaml").status_code == 404
    )
    assert client.get("/findings/skills/nope/").status_code == 404


def test_markdown_filter_escapes_html():
    from findings.templatetags.findings_tags import markdown

    rendered = markdown("# Hi\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))")
    assert "<script>" not in rendered
    assert 'href="javascript' not in rendered


def test_passage_page(client, make):
    make(sheet_id=1, title="Studies it")
    make(
        sheet_id=2,
        title="Only cites it",
        expanded=["Exodus 1:1", "Genesis 1:1"],
        roles=[{"ref": "Exodus 1:1", "role": "anchor"}, {"ref": "Genesis 1:1", "role": "support"}],
    )
    page = html(client.get("/findings/ref/Genesis.1.1/"))
    assert "<h1>Genesis 1:1</h1>" in page
    assert "https://www.sefaria.test/Genesis.1.1" in page
    studies, cited = page.split("Findings that also cite it")
    assert "Studies it" in studies and "Only cites it" in cited


def test_finding_page(client, make):
    finding = make(
        roles=[
            {"ref": "Genesis 1:1-2", "role": "anchor"},
            {"ref": "Exodus 1:1", "role": "position"},
            {"ref": "Psalms 16:8", "role": "support"},
        ],
        expanded=["Genesis 1:1", "Genesis 1:2", "Exodus 1:1", "Psalms 16:8"],
    )
    page = html(client.get(f"/findings/{finding.pk}/"))
    assert "AI-assisted reading in the approach of Rabbi Ploni" in page
    assert "Passage studied" in page and "Position" in page and "Also cited" in page
    assert 'href="https://www.sefaria.test/Genesis.1.1-2"' in page
    assert 'href="https://www.sefaria.test/sheets/1001"' in page
    assert "Exodus 1:1, Genesis 1:1, Genesis 1:2" in page


def test_related_findings(client, make):
    first = make(sheet_id=1, title="First")
    make(sheet_id=2, title="Second")
    page = html(client.get(f"/findings/{first.pk}/"))
    assert "Other findings on these passages" in page and "Second" in page


def test_hidden_finding_page_is_404(client, make):
    finding = make()
    finding.hidden = True
    finding.save()
    assert client.get(f"/findings/{finding.pk}/").status_code == 404


def test_learner_text_is_escaped(client, make):
    finding = make(title="<img src=x onerror=alert(1)>", conclusion="<script>bad()</script>")
    for url in ("/findings/", f"/findings/{finding.pk}/", "/findings/methods/example-method/"):
        page = html(client.get(url))
        assert "<img src=x" not in page and "<script>bad()" not in page


def test_pagination(client, make, monkeypatch):
    from findings import views

    monkeypatch.setattr(views, "PAGE_SIZE", 2)
    for i in range(5):
        make(sheet_id=i + 1, title=f"Finding {i}")
    page = html(client.get("/findings/"))
    assert "Page 1 of 3" in page and "Finding 4" in page and "Finding 0" not in page
    page = html(client.get("/findings/?page=3"))
    assert "Finding 0" in page
