"""Keeping findings in line with their sheets, staff takedowns, and the demo seeder."""

import io

import httpx
import pytest
from django.core.management import CommandError, call_command

from findings import service
from findings.models import Finding

from .conftest import OTHER_USER, USER

pytestmark = pytest.mark.django_db


def segments(finding):
    return sorted(finding.refs.values_list("segment_ref", "role"))


def test_unchanged_sheet(make, sheets):
    finding = make()
    assert service.sync(finding) == "unchanged"
    finding.refs.all()
    assert Finding.objects.get().synced is not None


def test_an_edited_sheet_has_its_refs_read_again(make, sheets):
    finding = make()
    sheets.add(
        1001,
        expanded=["Genesis 1:1", "Genesis 1:3"],
        modified="t2",
        title="Renamed",
        included=["Genesis 1:1", "Genesis 1:3"],
    )
    assert service.sync(finding) == "updated"
    finding.refresh_from_db()
    assert finding.sheet_modified == "t2" and finding.sheet_title == "Renamed"
    assert segments(finding) == [("Genesis 1:1", "anchor"), ("Genesis 1:3", "support")]


def test_a_deleted_sheet_hides_its_finding(make, sheets):
    finding = make()
    del sheets.sheets[1001]
    assert service.sync(finding) == "hidden"
    finding.refresh_from_db()
    assert finding.hidden and finding.hidden_reason == "Source sheet deleted"


def test_a_404_hides_and_other_errors_do_not(make, sheets):
    finding = make()
    request = httpx.Request("GET", "https://www.sefaria.test/api/sheets/1001")
    sheets.sheets[1001] = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(500, request=request)
    )
    assert service.sync(finding) == "error"
    assert not Finding.objects.get().hidden
    sheets.sheets[1001] = httpx.ConnectError("down")
    assert service.sync(finding) == "error"
    sheets.sheets[1001] = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(404, request=request)
    )
    assert service.sync(finding) == "hidden"


def test_a_sheet_that_changed_owner_hides_its_finding(make, sheets):
    finding = make()
    sheets.add(1001, owner=OTHER_USER, modified="t2")
    assert service.sync(finding) == "hidden"
    assert Finding.objects.get().hidden_reason == "Source sheet changed owner"


def test_sync_command(make, sheets):
    make(sheet_id=1)
    make(sheet_id=2)
    sheets.add(2, modified="t9")
    out = io.StringIO()
    call_command("sync_findings", stdout=out)
    assert out.getvalue().strip() == "Synced findings: 1 unchanged, 1 updated"
    out = io.StringIO()
    call_command("sync_findings", "--limit", "1", stdout=out)
    assert "1 unchanged" in out.getvalue()


def test_hide_and_unhide(make):
    finding = make()
    with pytest.raises(CommandError, match="--reason"):
        call_command("hide_finding", str(finding.pk))
    call_command(
        "hide_finding",
        str(finding.pk),
        "--reason",
        "Misattributes a teaching",
        stdout=io.StringIO(),
    )
    finding.refresh_from_db()
    assert finding.hidden and finding.hidden_reason == "Misattributes a teaching"
    call_command("hide_finding", str(finding.pk), "--unhide", stdout=io.StringIO())
    finding.refresh_from_db()
    assert not finding.hidden and finding.hidden_reason == ""
    with pytest.raises(CommandError, match="No finding"):
        call_command("hide_finding", "not-a-uuid", "--reason", "x")


def test_seed_demo_findings(settings, client):
    settings.DEBUG = True
    out = io.StringIO()
    call_command("seed_demo_findings", stdout=out)
    call_command("seed_demo_findings", stdout=io.StringIO())  # idempotent
    assert Finding.objects.count() == 5
    assert "Seeded 5 demo findings" in out.getvalue()
    data = client.get("/api/findings/ref/Shulchan Arukh, Orach Chayim 1").json()
    assert len(data["findings"]) == 2
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_demo_findings")


def test_fetch_sheet_reads_as_the_user(monkeypatch, settings):
    """The real reader mints a 5-minute mcp token and maps Sefaria's sheet fields."""
    import functools

    from chat.V2.agent import sefaria_client as sefaria_client_module
    from mcp_auth import sefaria_tokens

    settings.MCP_TOKEN_SECRET = "test-mcp-token-secret"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": 1001,
                "owner": USER,
                "title": "T",
                "status": "unlisted",
                "dateModified": "t1",
                "includedRefs": ["Genesis 1:1-2"],
                "expandedRefs": ["Genesis 1:1", "Genesis 1:2"],
                "sources": [],
            },
        )

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )
    sheet = service.fetch_sheet(1001, USER)
    assert sheet == service.SheetRefs(
        1001, USER, "T", "t1", ["Genesis 1:1", "Genesis 1:2"], ["Genesis 1:1-2"]
    )
    assert seen[0].url.path == "/api/sheets/1001"
    token = seen[0].headers["X-Session-ID"]
    assert sefaria_tokens.user_id_from(token, "test-mcp-token-secret", aud="mcp") == USER
