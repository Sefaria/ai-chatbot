"""The findings JSON API: recording a finding, and the public reads the reader sidebar uses."""

import json

import pytest

import skills_catalog
from findings.models import Finding, FindingRef

from .conftest import ISSUER, OTHER_USER, SIGNING_KEY, USER, payload, write_token

pytestmark = pytest.mark.django_db


def post(client, body, token=None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(
        "/api/findings", data=json.dumps(body), content_type="application/json", **headers
    )


# ---------------------------------------------------------------------------
# POST /api/findings
# ---------------------------------------------------------------------------


def test_records_a_finding_from_the_users_sheet(client, sheets):
    sheets.add(1001, expanded=["Psalms 16:8", "Genesis 1:2", "Genesis 1:1"], title="My sheet")
    response = post(client, payload(), write_token())
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["created"] is True
    finding = body["finding"]
    assert finding["title"] == "Rising like a lion"
    assert finding["method"] == {
        "slug": "example-method",
        "title": {"en": "Rabbi Ploni's Method", "he": "שיטת רבי פלוני"},
    }
    assert finding["skill"]["name"] == "example-halachic-reading"
    assert finding["label"] == "AI-assisted reading in the approach of Rabbi Ploni"
    assert finding["anchorRefs"] == ["Genesis 1:1", "Genesis 1:2"]
    assert finding["refs"][0] == {
        "ref": "Genesis 1:1-2",
        "role": "anchor",
        "url": "https://www.sefaria.test/Genesis.1.1-2",
    }
    assert finding["sheetUrl"] == "https://www.sefaria.test/sheets/1001"
    assert finding["url"] == f"{ISSUER}/findings/{finding['id']}/"
    assert sheets.reads == [(1001, USER)]

    saved = Finding.objects.get()
    assert (saved.sefaria_user_id, saved.method, saved.skill_version, saved.sheet_title) == (
        USER,
        "example-method",
        "1.0.0",
        "My sheet",
    )
    rows = sorted(FindingRef.objects.values_list("segment_ref", "section_ref", "book", "role"))
    assert rows == [
        ("Genesis 1:1", "Genesis 1", "Genesis", "anchor"),
        ("Genesis 1:2", "Genesis 1", "Genesis", "anchor"),
        ("Psalms 16:8", "Psalms 16", "Psalms", "support"),
    ]


def test_saving_again_for_the_same_sheet_updates_it(client, sheets):
    sheets.add(1001)
    assert post(client, payload(), write_token()).status_code == 201
    response = post(client, payload(title="Sharper title"), write_token())
    assert response.status_code == 200
    assert response.json()["created"] is False
    assert Finding.objects.get().title == "Sharper title"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "not-a-jwt",
        write_token(key="another-key-" + "y" * 32),
        write_token(issuer="https://elsewhere.test"),
        write_token(ttl_seconds=-10),
    ],
)
def test_requires_a_valid_write_token(client, sheets, token):
    sheets.add(1001)
    assert post(client, payload(), token).status_code == 401
    assert not Finding.objects.exists()


def test_an_mcp_access_token_is_not_a_write_token(client, sheets):
    from mcp_auth import access_tokens

    sheets.add(1001)
    token = access_tokens.issue(
        signing_key=SIGNING_KEY,
        issuer=ISSUER,
        audience=f"{ISSUER}/mcp/user",
        grant_id="g",
        sefaria_user_id=USER,
        client_id="c",
    )
    assert post(client, payload(), token).status_code == 401


@pytest.mark.parametrize(
    "overrides, status, message",
    [
        ({"skill": "nope"}, 403, "not authorized"),
        ({"title": ""}, 400, "`title` is required"),
        ({"title": "x" * 161}, 400, "at most 160"),
        ({"conclusion": None}, 400, "`conclusion` is required"),
        ({"roles": []}, 400, "`roles`"),
        ({"roles": [{"ref": "Genesis 1:1", "role": "support"}]}, 400, "at least one `anchor`"),
        ({"roles": [{"ref": "Genesis 1:1", "role": "boss"}]}, 400, "anchor, position or support"),
        (
            {"roles": [{"ref": f"Genesis 1:{i}", "role": "anchor"} for i in range(31)]},
            400,
            "at most 30",
        ),
        ({"sheet_id": "abc"}, 400, "`sheet_id`"),
    ],
)
def test_refuses_bad_requests(client, sheets, overrides, status, message):
    sheets.add(1001)
    response = post(client, payload(**overrides), write_token())
    assert response.status_code == status
    assert message in response.json()["error"]
    assert not Finding.objects.exists()


def test_refuses_invalid_json(client):
    response = client.post(
        "/api/findings",
        data="{",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {write_token()}",
    )
    assert response.status_code == 400


def test_refuses_a_sheet_the_user_does_not_own(client, sheets):
    sheets.add(1001, owner=OTHER_USER)
    response = post(client, payload(), write_token())
    assert response.status_code == 403
    assert "your own sheets" in response.json()["error"]


def test_refuses_a_sheet_holding_another_users_finding(client, make, sheets):
    make(user=OTHER_USER, sheet_id=1001)
    response = post(client, payload(sheet_id=1001), write_token())
    assert response.status_code == 409


def test_reports_an_unreadable_sheet(client, sheets):
    response = post(client, payload(sheet_id=404), write_token())
    assert response.status_code == 502
    assert "Could not read source sheet 404" in response.json()["error"]


def test_daily_limit(client, sheets, settings):
    settings.FINDINGS_DAILY_LIMIT = 2
    for sheet_id in (1, 2, 3):
        sheets.add(sheet_id)
    assert post(client, payload(sheet_id=1), write_token()).status_code == 201
    assert post(client, payload(sheet_id=2), write_token()).status_code == 201
    response = post(client, payload(sheet_id=3), write_token())
    assert response.status_code == 429
    # Updating an existing finding is not a new one.
    assert post(client, payload(sheet_id=1, title="Again"), write_token()).status_code == 200


def test_other_methods_are_refused(client):
    assert client.put("/api/findings").status_code == 405


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------


def test_findings_for_a_section(client, make):
    finding = make()
    response = client.get("/api/findings/ref/Genesis 1")
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=60"
    data = response.json()
    assert data["ref"] == "Genesis 1"
    assert [f["id"] for f in data["findings"]] == [str(finding.pk)]
    assert data["findings"][0]["anchorRefs"] == ["Genesis 1:1", "Genesis 1:2"]


@pytest.mark.parametrize("path", ["Genesis 1:2", "Genesis.1.2", "Genesis%201%3A2"])
def test_findings_for_a_segment_in_any_url_form(client, make, path):
    make()
    data = client.get(f"/api/findings/ref/{path}").json()
    assert data["ref"] == "Genesis 1:2"
    assert data["findings"][0]["anchorRefs"] == ["Genesis 1:2"]


def test_support_passages_are_left_out_unless_asked(client, make):
    make()
    assert client.get("/api/findings/ref/Psalms 16").json()["findings"] == []
    data = client.get("/api/findings/ref/Psalms 16?roles=all").json()
    assert len(data["findings"]) == 1


def test_position_passages_are_shown(client, make):
    make(
        expanded=["Genesis 1:1", "Exodus 1:1"],
        roles=[{"ref": "Genesis 1:1", "role": "anchor"}, {"ref": "Exodus 1:1", "role": "position"}],
    )
    data = client.get("/api/findings/ref/Exodus 1").json()
    assert data["findings"][0]["anchorRefs"] == ["Exodus 1:1"]


def test_newest_first(client, make):
    first = make(sheet_id=1)
    second = make(sheet_id=2)
    ids = [f["id"] for f in client.get("/api/findings/ref/Genesis 1").json()["findings"]]
    assert ids == [str(second.pk), str(first.pk)]


def test_hidden_findings_are_not_shown(client, make):
    finding = make()
    finding.hidden = True
    finding.save()
    assert client.get("/api/findings/ref/Genesis 1").json()["findings"] == []
    assert client.get(f"/api/findings/{finding.pk}").status_code == 404
    assert client.get("/api/findings").json()["count"] == 0


def test_findings_of_a_withdrawn_skill_are_not_shown(client, make, monkeypatch):
    from findings import service

    finding = make()
    real = skills_catalog.load()
    withdrawn = type(real)(
        methods=real.methods,
        authors=real.authors,
        skills={k: v for k, v in real.skills.items() if k != finding.skill},
        root=real.root,
    )
    monkeypatch.setattr(service, "catalog", lambda: withdrawn)
    assert client.get("/api/findings/ref/Genesis 1").json()["findings"] == []
    assert client.get(f"/api/findings/{finding.pk}").status_code == 404


def test_detail(client, make):
    finding = make()
    data = client.get(f"/api/findings/{finding.pk}").json()["finding"]
    assert data["id"] == str(finding.pk)
    assert data["anchorRefs"] == ["Genesis 1:1", "Genesis 1:2"]
    assert client.get("/api/findings/00000000-0000-0000-0000-000000000000").status_code == 404


def test_search(client, make):
    make(sheet_id=1, title="About light")
    make(sheet_id=2, title="About water", skill="example-parallel-passages")
    assert client.get("/api/findings").json()["count"] == 2
    assert [f["title"] for f in client.get("/api/findings?q=light").json()["findings"]] == [
        "About light"
    ]
    by_method = client.get("/api/findings?method=example-parallels").json()
    assert [f["title"] for f in by_method["findings"]] == ["About water"]
    by_skill = client.get("/api/findings?skill=example-halachic-reading").json()
    assert by_skill["count"] == 1
    by_ref = client.get("/api/findings?ref=Genesis.1.1").json()
    assert by_ref["count"] == 2 and by_ref["findings"][0]["anchorRefs"] == ["Genesis 1:1"]
    assert len(client.get("/api/findings?limit=1").json()["findings"]) == 1
    assert len(client.get("/api/findings?limit=nonsense").json()["findings"]) == 2


def test_methods(client, make):
    make(sheet_id=1)
    make(sheet_id=2)
    data = client.get("/api/methods").json()["methods"]
    by_slug = {m["slug"]: m for m in data}
    assert set(by_slug) == {"example-method", "example-parallels"}
    method = by_slug["example-method"]
    assert method["findingCount"] == 2
    assert method["url"] == f"{ISSUER}/findings/methods/example-method/"
    assert method["skills"][0] == {
        "name": "example-halachic-reading",
        "uri": "skill://example-halachic-reading/SKILL.md",
        "description": method["skills"][0]["description"],
        "version": "1.0.0",
        "author": "Ploni Almoni",
        "findingCount": 2,
    }
    assert by_slug["example-parallels"]["findingCount"] == 0


def test_cors_is_open_for_the_reader(client, make):
    make()
    response = client.get("/api/findings/ref/Genesis 1", HTTP_ORIGIN="https://www.sefaria.org")
    assert response["Access-Control-Allow-Origin"] in ("*", "https://www.sefaria.org")


def test_user_ids_are_never_published(client, make):
    make()
    text = client.get("/api/findings/ref/Genesis 1").content.decode()
    assert str(USER) not in text
    assert "sefaria_user_id" not in text and "owner" not in text


def test_cited_sources_the_sheet_does_not_hold_are_not_published(client, sheets):
    sheets.add(1001, expanded=["Genesis 1:1"], included=["Genesis 1:1"])
    body = payload(
        roles=[{"ref": "Genesis 1:1", "role": "anchor"}, {"ref": "Exodus 2:2", "role": "support"}]
    )
    finding = post(client, body, write_token()).json()["finding"]
    assert [r["ref"] for r in finding["refs"]] == ["Genesis 1:1"]


def test_an_anchor_the_sheet_does_not_hold_is_refused(client, sheets):
    sheets.add(1001, expanded=["Psalms 16:8"], included=["Psalms 16:8"])
    response = post(client, payload(), write_token())
    assert response.status_code == 400
    assert "None of the anchor sources is on the sheet" in response.json()["error"]


def test_truncation_keeps_anchors(client, sheets, monkeypatch):
    from findings import service

    monkeypatch.setattr(service, "SEGMENTS_MAX", 2)
    sheets.add(1001, expanded=["Amos 1:1", "Amos 1:2", "Amos 1:3", "Zechariah 1:1"])
    body = payload(roles=[{"ref": "Zechariah 1:1", "role": "anchor"}])
    assert post(client, body, write_token()).status_code == 201
    rows = list(FindingRef.objects.values_list("segment_ref", "role"))
    assert len(rows) == 2 and ("Zechariah 1:1", "anchor") in rows


def test_nul_characters_are_dropped(client, sheets):
    sheets.add(1001)
    finding = post(client, payload(title="Ri\x00sing"), write_token()).json()["finding"]
    assert finding["title"] == "Rising"


def test_dry_run_checks_without_reading_the_sheet(client, make, sheets, settings):
    response = post(client, {**payload(), "sheet_id": None, "dry_run": True}, write_token())
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert sheets.reads == [] and not Finding.objects.exists()

    assert (
        post(client, {**payload(skill="nope"), "dry_run": True}, write_token()).status_code == 403
    )
    make(user=OTHER_USER, sheet_id=2002)
    taken = post(client, {**payload(sheet_id=2002), "dry_run": True}, write_token())
    assert taken.status_code == 409
    settings.FINDINGS_DAILY_LIMIT = 0
    assert post(client, {**payload(), "dry_run": True}, write_token()).status_code == 429


def test_a_concurrent_first_save_is_a_clean_409(client, sheets, monkeypatch):
    from django.db import IntegrityError

    sheets.add(1001)

    def racing_save(self, *args, **kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(Finding, "save", racing_save)
    response = post(client, payload(), write_token())
    assert response.status_code == 409
    assert "being saved" in response.json()["error"]


@pytest.mark.parametrize(
    "path, anchors",
    [("Genesis 1:1-2", ["Genesis 1:1", "Genesis 1:2"]), ("Genesis.1.2-3", ["Genesis 1:2"])],
)
def test_a_range_within_a_section_finds_its_segments(client, make, path, anchors):
    make()
    data = client.get(f"/api/findings/ref/{path}").json()
    assert [f["anchorRefs"] for f in data["findings"]] == [anchors]
    assert client.get("/api/findings/ref/Genesis 1:3-4").json()["findings"] == []
