import pytest

import skills_catalog
from findings import service, write_tokens
from findings.service import SheetRefs

ISSUER = "https://chat.test"
SIGNING_KEY = "test-signing-key-" + "x" * 32
USER = 5550101
OTHER_USER = 5550202


@pytest.fixture(autouse=True)
def findings_settings(settings):
    settings.MCP_OAUTH_SIGNING_KEY = SIGNING_KEY
    settings.MCP_OAUTH_ISSUER = ISSUER
    settings.CHAT_PUBLIC_URL = ISSUER
    settings.SEFARIA_WEB_URL = "https://www.sefaria.test"
    settings.FINDINGS_DAILY_LIMIT = 20
    # Pages render {% static %}; the manifest storage needs collectstatic, which tests skip.
    settings.STORAGES = {
        **getattr(settings, "STORAGES", {}),
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    skills_catalog.load.cache_clear()
    yield settings
    skills_catalog.load.cache_clear()


def write_token(user_id: int = USER, **kwargs) -> str:
    return write_tokens.mint(
        user_id, kwargs.pop("key", SIGNING_KEY), kwargs.pop("issuer", ISSUER), **kwargs
    )


class Sheets:
    """A stand-in for Sefaria's sheet API: sheet id -> SheetRefs, or an exception to raise."""

    def __init__(self):
        self.sheets: dict[int, object] = {}
        self.reads: list[tuple[int, int | None]] = []

    def add(
        self,
        sheet_id: int,
        owner: int = USER,
        expanded=("Genesis 1:1",),
        modified="t1",
        title="Sheet",
        included=None,
    ):
        self.sheets[sheet_id] = SheetRefs(
            sheet_id, owner, title, modified, list(expanded), included
        )
        return self.sheets[sheet_id]

    def __call__(self, sheet_id, as_user):
        self.reads.append((sheet_id, as_user))
        sheet = self.sheets.get(sheet_id)
        if sheet is None:
            raise ValueError("Sheet not found.")
        if isinstance(sheet, Exception):
            raise sheet
        return sheet


@pytest.fixture
def sheets(monkeypatch):
    fake = Sheets()
    monkeypatch.setattr(service, "fetch_sheet", fake)
    monkeypatch.setattr(service.record, "__defaults__", (fake,))
    monkeypatch.setattr(service.sync, "__defaults__", (fake,))
    return fake


def payload(**overrides) -> dict:
    data = {
        "skill": "example-halachic-reading",
        "title": "Rising like a lion",
        "conclusion": "What was found.",
        "sheet_id": 1001,
        "roles": [
            {"ref": "Genesis 1:1-2", "role": "anchor"},
            {"ref": "Psalms 16:8", "role": "support"},
        ],
    }
    data.update(overrides)
    return data


@pytest.fixture
def make(sheets):
    """Record a finding through the service, as the API would."""

    def make(
        user=USER,
        sheet_id=1001,
        expanded=("Genesis 1:1", "Genesis 1:2", "Psalms 16:8"),
        **overrides,
    ):
        sheets.add(sheet_id, owner=user, expanded=expanded)
        finding, _ = service.record(user, payload(sheet_id=sheet_id, **overrides))
        return finding

    return make
