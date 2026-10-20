"""Reading the logged-out visitors' free-answer limit from Sefaria's remote config."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.cache import cache

from chat import anon_access
from chat.anon_access import REMOTE_CONFIG_KEY, get_anon_answer_limit


@pytest.fixture(autouse=True)
def empty_cache():
    cache.clear()
    yield
    cache.clear()


def sefaria_returns(config=None, error=None):
    """Patch the request to Sefaria's /api/remote-config."""
    response = MagicMock()
    response.json.return_value = config
    response.raise_for_status.return_value = None
    return patch.object(anon_access.httpx, "get", side_effect=error, return_value=response)


def test_the_limit_comes_from_sefaria_remote_config():
    with sefaria_returns({REMOTE_CONFIG_KEY: 3, "other.key": "x"}) as get:
        assert get_anon_answer_limit() == 3

    assert get.call_args.args[0].endswith("/api/remote-config")


def test_the_limit_is_cached():
    with sefaria_returns({REMOTE_CONFIG_KEY: 3}) as get:
        get_anon_answer_limit()
        get_anon_answer_limit()

    assert get.call_count == 1


def test_zero_is_a_valid_limit():
    with sefaria_returns({REMOTE_CONFIG_KEY: 0}):
        assert get_anon_answer_limit() == 0


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="key-missing"),
        pytest.param({REMOTE_CONFIG_KEY: -1}, id="negative"),
        pytest.param({REMOTE_CONFIG_KEY: "2"}, id="not-an-int"),
        pytest.param({REMOTE_CONFIG_KEY: True}, id="bool"),
        pytest.param(["not", "a", "dict"], id="not-a-dict"),
    ],
)
def test_no_usable_value_means_no_limit(config):
    with sefaria_returns(config):
        assert get_anon_answer_limit() is None


def test_sefaria_unreachable_means_no_limit():
    with sefaria_returns(error=httpx.ConnectError("down")):
        assert get_anon_answer_limit() is None


def test_no_limit_is_cached_too():
    with sefaria_returns(error=httpx.ConnectError("down")) as get:
        get_anon_answer_limit()
        get_anon_answer_limit()

    assert get.call_count == 1


def test_the_limit_comes_from_the_requests_sefaria_cached_per_sefaria():
    from chat import sefaria_base_url

    def limit_for(base_url):
        token = sefaria_base_url._request_sefaria_base_url.set(base_url)
        try:
            return get_anon_answer_limit()
        finally:
            sefaria_base_url._request_sefaria_base_url.reset(token)

    with sefaria_returns({REMOTE_CONFIG_KEY: 3}) as get:
        limit_for("https://a.cauldron.sefaria.org")
        limit_for("https://b.cauldron.sefaria.org")
        limit_for("https://a.cauldron.sefaria.org")

    assert [call.args[0] for call in get.call_args_list] == [
        "https://a.cauldron.sefaria.org/api/remote-config",
        "https://b.cauldron.sefaria.org/api/remote-config",
    ]
