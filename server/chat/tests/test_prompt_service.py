"""Tests for PromptService - caching, Braintrust integration."""

import time
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from chat.V2.prompts.prompt_service import CorePrompt, PromptService, get_prompt_service


@pytest.fixture
def mock_braintrust() -> MagicMock:
    mock_bt = MagicMock()
    mock_prompt = MagicMock()
    mock_prompt.version = "bt_v1"
    mock_prompt.build.return_value = {
        "messages": [{"role": "system", "content": "Braintrust prompt content"}]
    }
    mock_bt.load_prompt.return_value = mock_prompt
    return mock_bt


@pytest.fixture
def service(mock_braintrust: MagicMock) -> PromptService:
    """Service with a mocked Braintrust client."""
    svc = PromptService(api_key="test-key")
    svc._braintrust_client = mock_braintrust
    return svc


@pytest.fixture
def service_short_ttl(mock_braintrust: MagicMock) -> PromptService:
    svc = PromptService(api_key="test-key", cache_ttl_seconds=1)
    svc._braintrust_client = mock_braintrust
    return svc


class TestCorePrompt:
    """Test CorePrompt dataclass."""

    def test_create_core_prompt(self) -> None:
        prompt = CorePrompt(text="System prompt", prompt_id="core-1", version="v1")
        assert prompt.text == "System prompt"
        assert prompt.prompt_id == "core-1"
        assert prompt.version == "v1"


class TestPromptServiceInit:
    """Test PromptService initialization."""

    def test_init_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="BRAINTRUST_API_KEY"):
                PromptService(api_key=None)

    def test_init_with_api_key(self) -> None:
        svc = PromptService(api_key="test_key")
        assert svc.api_key == "test_key"

    def test_init_custom_project_name(self) -> None:
        svc = PromptService(api_key="test-key", project_name="custom-project")
        assert svc.project_name == "custom-project"

    def test_init_default_project_name(self) -> None:
        with patch.dict("os.environ", {"BRAINTRUST_PROJECT": "env-project"}):
            svc = PromptService(api_key="test-key")
            assert svc.project_name == "env-project"

    def test_init_cache_ttl(self) -> None:
        svc = PromptService(api_key="test-key", cache_ttl_seconds=600)
        assert svc.cache_ttl == 600


class TestCaching:
    """Test prompt caching behavior."""

    def test_cache_hit(self, service: PromptService) -> None:
        prompt1 = service.get_core_prompt()
        prompt2 = service.get_core_prompt()
        assert prompt1.text == prompt2.text

    def test_cache_expiry_refetches(self, service_short_ttl: PromptService) -> None:
        """When cache expires, re-fetches from Braintrust."""
        service_short_ttl.get_core_prompt()
        with service_short_ttl._cache_lock:
            for key in service_short_ttl._cache:
                service_short_ttl._cache[key]["timestamp"] = time.time() - 10
        prompt = service_short_ttl.get_core_prompt()
        assert prompt.text == "Braintrust prompt content"

    def test_cache_expiry_raises_on_fetch_failure(self, service_short_ttl: PromptService) -> None:
        """When cache expires and re-fetch fails, raises error."""
        service_short_ttl.get_core_prompt()
        with service_short_ttl._cache_lock:
            for key in service_short_ttl._cache:
                service_short_ttl._cache[key]["timestamp"] = time.time() - 10
        service_short_ttl._braintrust_client.load_prompt.side_effect = Exception("API down")
        with pytest.raises(RuntimeError, match="Failed to fetch prompt"):
            service_short_ttl.get_core_prompt()

    def test_invalidate_cache_all(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._cache["prompt_a:v1"] = {
            "prompt": "a",
            "version": "v1",
            "timestamp": time.time(),
        }
        service_short_ttl._cache["prompt_b:v1"] = {
            "prompt": "b",
            "version": "v1",
            "timestamp": time.time(),
        }
        service_short_ttl.invalidate_cache()
        assert len(service_short_ttl._cache) == 0

    def test_invalidate_cache_specific(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._cache["prompt_a:v1"] = {
            "prompt": "a",
            "version": "v1",
            "timestamp": time.time(),
        }
        service_short_ttl._cache["prompt_b:v1"] = {
            "prompt": "b",
            "version": "v1",
            "timestamp": time.time(),
        }
        service_short_ttl.invalidate_cache("prompt_a")
        assert "prompt_a:v1" not in service_short_ttl._cache
        assert "prompt_b:v1" in service_short_ttl._cache


class TestBraintrustIntegration:
    """Test Braintrust prompt fetching (mocked)."""

    def test_fetch_from_braintrust_success(self, mock_braintrust: MagicMock) -> None:
        svc = PromptService(api_key="test-key")
        svc._braintrust_client = mock_braintrust
        svc.project_name = "test-project"
        prompt_text, version = svc._fetch_from_braintrust("test_slug", "stable")
        assert prompt_text == "Braintrust prompt content"
        assert version == "bt_v1"

    def test_fetch_from_braintrust_not_found(self, mock_braintrust: MagicMock) -> None:
        mock_braintrust.load_prompt.return_value = None
        svc = PromptService(api_key="test-key")
        svc._braintrust_client = mock_braintrust
        prompt_text, version = svc._fetch_from_braintrust("missing_slug", "stable")
        assert prompt_text is None
        assert version == ""

    def test_fetch_from_braintrust_error_propagates(self, mock_braintrust: MagicMock) -> None:
        mock_braintrust.load_prompt.side_effect = Exception("API Error")
        svc = PromptService(api_key="test-key")
        svc._braintrust_client = mock_braintrust
        with pytest.raises(Exception, match="API Error"):
            svc._fetch_from_braintrust("test_slug", "stable")

    def test_empty_fetch_raises(self, mock_braintrust: MagicMock) -> None:
        """When Braintrust returns empty prompt, raises RuntimeError."""
        mock_braintrust.load_prompt.return_value = None
        svc = PromptService(api_key="test-key")
        svc._braintrust_client = mock_braintrust
        with pytest.raises(RuntimeError, match="returned empty"):
            svc.get_core_prompt()

    def test_fetch_error_raises(self, mock_braintrust: MagicMock) -> None:
        """When Braintrust fetch fails, raises RuntimeError."""
        mock_braintrust.load_prompt.side_effect = Exception("Network error")
        svc = PromptService(api_key="test-key")
        svc._braintrust_client = mock_braintrust
        with pytest.raises(RuntimeError, match="Failed to fetch prompt"):
            svc.get_core_prompt()


class TestPromptExtraction:
    """Test _extract_prompt_text method."""

    def test_extract_from_build_messages(self, service: PromptService) -> None:
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {
            "messages": [{"role": "system", "content": "System prompt here"}]
        }
        text = service._extract_prompt_text(mock_prompt)
        assert text == "System prompt here"


class TestGetPromptService:
    """Test get_prompt_service singleton."""

    def test_returns_prompt_service(self) -> None:
        from chat.V2.prompts.prompt_service import reset_prompt_service

        reset_prompt_service()
        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
            svc = get_prompt_service()
        assert isinstance(svc, PromptService)

    def test_returns_same_instance(self) -> None:
        from chat.V2.prompts.prompt_service import reset_prompt_service

        reset_prompt_service()
        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
            service1 = get_prompt_service()
            service2 = get_prompt_service()
        assert service1 is service2


class TestCorePromptVersionTracking:
    """Test version tracking in core prompts."""

    def test_prompt_includes_version_info(self, service: PromptService) -> None:
        prompt = service.get_core_prompt()
        assert prompt.prompt_id == settings.CORE_PROMPT_SLUG
        assert prompt.version
