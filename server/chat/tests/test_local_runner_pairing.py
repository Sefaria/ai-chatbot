"""Pairing and request authentication for the local runner.

The daemon listens on loopback, where any page in the user's browser can reach
it, and it spends the user's Claude subscription. So these cover the two things
that keep that safe: presenting the terminal code is real proof, and every route
requires the runner token it buys.
"""

from __future__ import annotations

import json
import stat

import pytest
from django.test import RequestFactory, override_settings

from chat.auth.auth_service import AuthenticationRequired
from local_runner import auth, pairing, session
from local_runner.middleware import OriginAllowlistMiddleware, RunnerAuthMiddleware

SEFARIA = "https://www.sefaria.org"


@pytest.fixture(autouse=True)
def runner_home(tmp_path, monkeypatch):
    """Point the runner's data directory at a temp dir for every test."""
    monkeypatch.setenv("SEFARIA_AGENT_HOME", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    session.set_code(None)
    yield tmp_path
    session.set_code(None)


def _ok(request):
    from django.http import HttpResponse

    return HttpResponse("ok")


# ---------------------------------------------------------------------------
# The pairing code
# ---------------------------------------------------------------------------


class TestPairingCode:
    def test_code_is_six_digits(self):
        code = pairing.new_code()
        assert len(code) == 6 and code.isdigit()

    def test_correct_code_is_accepted(self):
        session.set_code("123456")
        assert session.consume("123456") is True

    def test_code_is_single_use(self):
        session.set_code("123456")
        session.consume("123456")
        # A replay must not mint a second runner token.
        assert session.consume("123456") is False

    def test_pairing_closes_after_use(self):
        session.set_code("123456")
        session.consume("123456")
        assert session.pairing_open() is False

    def test_wrong_code_is_rejected(self):
        session.set_code("123456")
        assert session.consume("999999") is False

    def test_repeated_guessing_closes_pairing(self):
        session.set_code("123456")
        for _ in range(session.MAX_ATTEMPTS):
            session.consume("999999")
        # Even the right code no longer works: guessing cannot be continued.
        assert session.consume("123456") is False
        assert session.pairing_open() is False

    def test_expired_code_is_rejected(self, monkeypatch):
        session.set_code("123456")
        clock = [1_000_000.0]
        monkeypatch.setattr(session.time, "monotonic", lambda: clock[0])
        clock[0] += session.TTL_SECONDS + 1
        assert session.consume("123456") is False

    def test_no_code_means_closed(self):
        assert session.pairing_open() is False
        assert session.consume(None) is False


# ---------------------------------------------------------------------------
# The pairing record
# ---------------------------------------------------------------------------


class TestPairingRecord:
    def test_unpaired_by_default(self):
        assert pairing.load() is None

    def test_pair_then_load_round_trips(self):
        created = pairing.pair(user_id="u1", sefaria_user_id="s1")
        loaded = pairing.load()
        assert loaded is not None
        assert loaded.runner_token == created.runner_token
        assert loaded.user_id == "u1"
        assert loaded.sefaria_user_id == "s1"

    def test_record_is_owner_only(self):
        pairing.pair(user_id="u1", sefaria_user_id=None)
        mode = pairing.record_path().stat().st_mode
        # The record holds the runner token; other accounts must not read it.
        assert stat.S_IMODE(mode) == 0o600

    def test_corrupt_record_reads_as_unpaired(self):
        pairing.pair(user_id="u1", sefaria_user_id=None)
        pairing.record_path().write_text("{not json")
        # A corrupt record should let the user re-pair, not crash the daemon.
        assert pairing.load() is None

    def test_repairing_replaces_the_token(self):
        first = pairing.pair(user_id="u1", sefaria_user_id=None)
        second = pairing.pair(user_id="u1", sefaria_user_id=None)
        assert first.runner_token != second.runner_token
        assert pairing.token_matches(first.runner_token) is False
        assert pairing.token_matches(second.runner_token) is True

    def test_token_matches_rejects_empty_and_wrong(self):
        pairing.pair(user_id="u1", sefaria_user_id=None)
        assert pairing.token_matches(None) is False
        assert pairing.token_matches("") is False
        assert pairing.token_matches("wrong") is False

    def test_token_never_matches_when_unpaired(self):
        assert pairing.token_matches("anything") is False


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------


class TestRunnerAuthMiddleware:
    def setup_method(self):
        self.factory = RequestFactory()
        self.middleware = RunnerAuthMiddleware(_ok)

    def test_health_needs_no_token(self):
        assert self.middleware(self.factory.get("/health")).status_code == 200

    def test_pair_needs_no_token(self):
        assert self.middleware(self.factory.post("/pair")).status_code == 200

    def test_stream_without_a_token_is_rejected(self):
        assert self.middleware(self.factory.post("/api/v2/chat/stream")).status_code == 401

    def test_history_without_a_token_is_rejected(self):
        # History reads its user from query params and never calls the auth
        # service, so only the middleware protects it.
        assert self.middleware(self.factory.get("/api/history")).status_code == 401

    def test_wrong_token_is_rejected(self):
        pairing.pair(user_id="u1", sefaria_user_id=None)
        request = self.factory.post("/api/v2/chat/stream", HTTP_AUTHORIZATION="Bearer nope")
        assert self.middleware(request).status_code == 401

    def test_correct_token_is_accepted(self):
        record = pairing.pair(user_id="u1", sefaria_user_id=None)
        request = self.factory.post(
            "/api/v2/chat/stream",
            HTTP_AUTHORIZATION=f"Bearer {record.runner_token}",
        )
        assert self.middleware(request).status_code == 200

    def test_token_without_the_bearer_scheme_is_rejected(self):
        record = pairing.pair(user_id="u1", sefaria_user_id=None)
        request = self.factory.post("/api/v2/chat/stream", HTTP_AUTHORIZATION=record.runner_token)
        assert self.middleware(request).status_code == 401

    def test_rejection_body_names_the_failure(self):
        response = self.middleware(self.factory.post("/api/v2/chat/stream"))
        assert json.loads(response.content)["error"] == "runner_unauthorized"


class TestOriginAllowlistMiddleware:
    def setup_method(self):
        self.factory = RequestFactory()
        self.middleware = OriginAllowlistMiddleware(_ok)

    @override_settings(CORS_ALLOWED_ORIGINS=[SEFARIA])
    def test_sefaria_origin_passes(self):
        request = self.factory.post("/api/v2/chat/stream", HTTP_ORIGIN=SEFARIA)
        assert self.middleware(request).status_code == 200

    @override_settings(CORS_ALLOWED_ORIGINS=[SEFARIA])
    def test_foreign_origin_is_rejected(self):
        request = self.factory.post("/api/v2/chat/stream", HTTP_ORIGIN="https://evil.com")
        assert self.middleware(request).status_code == 401

    @override_settings(CORS_ALLOWED_ORIGINS=[SEFARIA])
    def test_no_origin_passes(self):
        # curl and the pairing flow are not browser-driven.
        assert self.middleware(self.factory.post("/api/v2/chat/stream")).status_code == 200


# ---------------------------------------------------------------------------
# The Actor the runner builds
# ---------------------------------------------------------------------------


class TestPairedAuthBackend:
    def test_unpaired_daemon_refuses(self):
        request = RequestFactory().post("/api/v2/chat/stream")
        with pytest.raises(AuthenticationRequired):
            auth.authenticate_paired_request(request, {})

    def test_actor_comes_from_the_pairing_record(self):
        pairing.pair(user_id="u-stored", sefaria_user_id="s-stored")
        request = RequestFactory().post("/api/v2/chat/stream")

        actor = auth.authenticate_paired_request(request, {"userId": "encrypted"})

        # Identity is whatever pairing resolved, never what the request claims.
        assert actor.user_id == "u-stored"
        assert actor.sefaria_user_id == "s-stored"
        assert actor.encrypted_token == "encrypted"

    def test_request_cannot_override_the_paired_identity(self):
        pairing.pair(user_id="u-stored", sefaria_user_id="s-stored")
        request = RequestFactory().post("/api/v2/chat/stream")

        actor = auth.authenticate_paired_request(request, {"userId": "x", "sefariaUserId": "evil"})

        assert actor.sefaria_user_id == "s-stored"
