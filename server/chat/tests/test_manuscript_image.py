"""Tests for SefariaClient.get_manuscript_image host allowlisting and response limits."""

import base64
from unittest.mock import patch

import httpx
import pytest

from chat.V2.agent import sefaria_client as sefaria_client_module
from chat.V2.agent.sefaria_client import ManuscriptImageRejected, SefariaClient
from chat.V2.agent.tool_executor import SefariaToolExecutor

IMAGE_URL = "https://manuscripts.sefaria.org/bomberg/masekhet_22_0008.jpg"
JPEG_BYTES = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def requests_seen():
    return []


def _client_with_handler(handler, requests_seen):
    def recording_handler(request):
        requests_seen.append(request)
        return handler(request)

    client = SefariaClient(base_url="https://www.sefaria.org")
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))
    return client, patch.object(client, "_get_client", return_value=http_client)


def _image_response(body=JPEG_BYTES, content_type="image/jpeg"):
    return httpx.Response(200, content=body, headers={"content-type": content_type})


class TestManuscriptImageAllowed:
    @pytest.mark.asyncio
    async def test_fetches_image_from_manuscript_host(self, requests_seen):
        client, patched = _client_with_handler(lambda r: _image_response(), requests_seen)
        with patched:
            result = await client.get_manuscript_image(IMAGE_URL, "Bomberg")

        assert result["success"] is True
        assert base64.b64decode(result["image_data"]) == JPEG_BYTES
        assert result["mime_type"] == "image/jpeg"
        assert result["size"] == len(JPEG_BYTES)
        assert result["filename"] == "masekhet_22_0008.jpg"
        assert [str(r.url) for r in requests_seen] == [IMAGE_URL]

    @pytest.mark.asyncio
    async def test_follows_redirect_within_allowlist(self, requests_seen):
        moved = "https://manuscripts.sefaria.org/bomberg/moved.jpg"

        def handler(request):
            if str(request.url) == IMAGE_URL:
                return httpx.Response(302, headers={"location": "/bomberg/moved.jpg"})
            return _image_response()

        client, patched = _client_with_handler(handler, requests_seen)
        with patched:
            result = await client.get_manuscript_image(IMAGE_URL)

        assert result["success"] is True
        assert [str(r.url) for r in requests_seen] == [IMAGE_URL, moved]


class TestManuscriptImageRejected:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://manuscripts.sefaria.org/bomberg/a.jpg",
            "https://example.com/a.jpg",
            "https://manuscripts.sefaria.org.example.com/a.jpg",
            "https://127.0.0.1/a.jpg",
            "https://169.254.169.254/latest/meta-data/",
            "https://user:pass@manuscripts.sefaria.org/a.jpg",
            "https://manuscripts.sefaria.org:8443/a.jpg",
            "file:///etc/passwd",
            "not a url",
        ],
    )
    async def test_disallowed_urls_are_never_requested(self, url, requests_seen):
        client, patched = _client_with_handler(lambda r: _image_response(), requests_seen)
        with patched, pytest.raises(ManuscriptImageRejected):
            await client.get_manuscript_image(url)
        assert requests_seen == []

    @pytest.mark.asyncio
    async def test_redirect_to_other_host_is_not_followed(self, requests_seen):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})

        client, patched = _client_with_handler(handler, requests_seen)
        with patched, pytest.raises(ManuscriptImageRejected, match="can only be fetched from"):
            await client.get_manuscript_image(IMAGE_URL)
        assert [r.url.host for r in requests_seen] == ["manuscripts.sefaria.org"]

    @pytest.mark.asyncio
    async def test_redirect_loop_is_capped(self, requests_seen):
        def handler(request):
            return httpx.Response(302, headers={"location": IMAGE_URL})

        client, patched = _client_with_handler(handler, requests_seen)
        with patched, pytest.raises(ManuscriptImageRejected, match="too many redirects"):
            await client.get_manuscript_image(IMAGE_URL)
        assert len(requests_seen) == sefaria_client_module.MAX_MANUSCRIPT_IMAGE_REDIRECTS + 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content_type", ["text/html", "application/json", ""])
    async def test_non_image_content_type_is_rejected(self, content_type, requests_seen):
        client, patched = _client_with_handler(
            lambda r: _image_response(b"<html></html>", content_type), requests_seen
        )
        with patched, pytest.raises(ManuscriptImageRejected, match="expected an image"):
            await client.get_manuscript_image(IMAGE_URL)

    @pytest.mark.asyncio
    async def test_oversized_body_is_rejected(self, requests_seen):
        client, patched = _client_with_handler(lambda r: _image_response(b"x" * 11), requests_seen)
        with (
            patched,
            patch.object(sefaria_client_module, "MAX_MANUSCRIPT_IMAGE_BYTES", 10),
            pytest.raises(ManuscriptImageRejected, match="limit"),
        ):
            await client.get_manuscript_image(IMAGE_URL)

    @pytest.mark.asyncio
    async def test_oversized_declared_length_is_rejected(self, requests_seen):
        def handler(request):
            return httpx.Response(
                200,
                content=b"x",
                headers={"content-type": "image/jpeg", "content-length": "999999999"},
            )

        client, patched = _client_with_handler(handler, requests_seen)
        with patched, pytest.raises(ManuscriptImageRejected, match="limit"):
            await client.get_manuscript_image(IMAGE_URL)

    @pytest.mark.asyncio
    async def test_rejection_surfaces_as_tool_error(self, requests_seen):
        client, patched = _client_with_handler(lambda r: _image_response(), requests_seen)
        executor = SefariaToolExecutor(client)
        with patched:
            result = await executor.execute(
                "get_manuscript_image", {"image_url": "https://example.com/a.jpg"}
            )

        assert result.is_error is True
        assert "manuscripts.sefaria.org" in result.content[0]["text"]
        assert requests_seen == []
