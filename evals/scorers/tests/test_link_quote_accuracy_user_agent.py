"""Network-free check that the link scorer self-identifies to the Sefaria API.

Run from the repo root:
    pytest evals/scorers/tests/test_link_quote_accuracy_user_agent.py
    python evals/scorers/tests/test_link_quote_accuracy_user_agent.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code_scorers"))
import link_quote_accuracy  # noqa: E402

OUTPUT = '<a class="response-link" href="https://www.sefaria.org/Genesis.1.1">Genesis 1:1</a>'


class _FakeResponse:
    status_code = 200

    def json(self):
        return {}


class _RecordingSession:
    """Stands in for requests.Session: records the headers sent with each GET."""

    requests: list[tuple[str, dict[str, str]]] = []

    def __init__(self):
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url, **kwargs):
        _RecordingSession.requests.append((url, dict(self.headers)))
        return _FakeResponse()


def test_requests_carry_sefaria_user_agent():
    _RecordingSession.requests = []
    with patch.object(link_quote_accuracy.requests, "Session", _RecordingSession):
        result = link_quote_accuracy.handler(
            input=None, output=OUTPUT, expected=None, metadata={}
        )

    assert result["score"] == 1.0
    assert _RecordingSession.requests, "scorer made no requests"
    for url, headers in _RecordingSession.requests:
        assert headers["User-Agent"] == "Sefaria/library-assistant-evals", url


if __name__ == "__main__":
    test_requests_carry_sefaria_user_agent()
    print("ok")
