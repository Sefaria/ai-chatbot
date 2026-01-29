"""Tests for PromptService - caching, fallback to defaults, Braintrust integration."""

import time
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from chat.V2.prompts.prompt_service import CorePrompt, PromptService, get_prompt_service


@pytest.fixture
def service() -> PromptService:
    return PromptService(api_key=None)


@pytest.fixture
def service_short_ttl() -> PromptService:
    return PromptService(api_key=None, cache_ttl_seconds=1)


class TestCorePrompt:
    """Test CorePrompt dataclass."""

    def test_create_core_prompt(self) -> None:
        prompt = CorePrompt(text="System prompt", prompt_id="core-1", version="v1")
        assert prompt.text == "System prompt"
        assert prompt.prompt_id == "core-1"
        assert prompt.version == "v1"


class TestPromptServiceInit:
    """Test PromptService initialization."""

    def test_init_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            svc = PromptService(api_key=None)
            assert svc.api_key is None
            assert svc._braintrust_client is None

    def test_init_with_api_key(self) -> None:
        svc = PromptService(api_key="test_key")
        assert svc.api_key == "test_key"

    def test_init_custom_project_name(self) -> None:
        svc = PromptService(api_key=None, project_name="custom-project")
        assert svc.project_name == "custom-project"

    def test_init_default_project_name(self) -> None:
        with patch.dict("os.environ", {"BRAINTRUST_PROJECT": "env-project"}):
            svc = PromptService(api_key=None)
            assert svc.project_name == "env-project"

    def test_init_cache_ttl(self) -> None:
        svc = PromptService(api_key=None, cache_ttl_seconds=600)
        assert svc.cache_ttl == 600


class TestFallbackToDefaults:
    """Test fallback to local default prompts."""

    def test_get_core_prompt(self, service: PromptService) -> None:
        prompt = service.get_core_prompt()
        assert isinstance(prompt, CorePrompt)
        assert prompt.text is not None
        assert prompt.version == "local"


class TestCaching:
    """Test prompt caching behavior."""

    def test_cache_stores_prompt(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._cache["core:v1"] = {
            "prompt": "Test prompt",
            "version": "v1",
            "timestamp": time.time(),
        }
        assert "core:v1" in service_short_ttl._cache

    def test_cache_hit(self, service_short_ttl: PromptService) -> None:
        prompt1 = service_short_ttl.get_core_prompt()
        prompt2 = service_short_ttl.get_core_prompt()
        assert prompt1.text == prompt2.text

    def test_cache_expiry(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._get_prompt("test_id", "v1")
        with service_short_ttl._cache_lock:
            for key in service_short_ttl._cache:
                service_short_ttl._cache[key]["timestamp"] = time.time() - 10
        text, version = service_short_ttl._get_prompt("test_id", "v1")
        assert version == "local"
        assert text

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

    @pytest.fixture
    def mock_braintrust(self) -> MagicMock:
        mock_bt = MagicMock()
        mock_prompt = MagicMock()
        mock_prompt.version = "bt_v1"
        mock_prompt.build.return_value = {
            "messages": [{"role": "system", "content": "Braintrust prompt content"}]
        }
        mock_bt.load_prompt.return_value = mock_prompt
        return mock_bt

    def test_fetch_from_braintrust_success(self, mock_braintrust: MagicMock) -> None:
        svc = PromptService(api_key=None)
        svc._braintrust_client = mock_braintrust
        svc.project_name = "test-project"
        prompt_text, version = svc._fetch_from_braintrust("test_slug", "stable")
        assert prompt_text == "Braintrust prompt content"
        assert version == "bt_v1"

    def test_fetch_from_braintrust_not_found(self, mock_braintrust: MagicMock) -> None:
        mock_braintrust.load_prompt.return_value = None
        svc = PromptService(api_key=None)
        svc._braintrust_client = mock_braintrust
        prompt_text, version = svc._fetch_from_braintrust("missing_slug", "stable")
        assert prompt_text is None
        assert version == ""

    def test_fetch_from_braintrust_error(self, mock_braintrust: MagicMock) -> None:
        mock_braintrust.load_prompt.side_effect = Exception("API Error")
        svc = PromptService(api_key=None)
        svc._braintrust_client = mock_braintrust
        prompt_text, _ = svc._fetch_from_braintrust("test_slug", "stable")
        assert prompt_text is None


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
        import chat.V2.prompts.prompt_service as ps

        ps._default_service = None
        svc = get_prompt_service()
        assert isinstance(svc, PromptService)

    def test_returns_same_instance(self) -> None:
        import chat.V2.prompts.prompt_service as ps

        ps._default_service = None
        service1 = get_prompt_service()
        service2 = get_prompt_service()
        assert service1 is service2


class TestCorePromptVersionTracking:
    """Test version tracking in core prompts."""

    def test_prompt_includes_version_info(self, service: PromptService) -> None:
        prompt = service.get_core_prompt()
        assert prompt.prompt_id == settings.CORE_PROMPT_SLUG
        assert prompt.version
