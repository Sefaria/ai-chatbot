"""Tests for PromptService - caching, fallback to defaults, Braintrust integration."""

import time
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from chat.prompts.prompt_service import (
    PromptBundle,
    PromptService,
    get_prompt_service,
)


@pytest.fixture
def service() -> PromptService:
    return PromptService(api_key=None)


@pytest.fixture
def service_short_ttl() -> PromptService:
    return PromptService(api_key=None, cache_ttl_seconds=1)


class TestPromptBundle:
    """Test PromptBundle dataclass."""

    def test_create_bundle(self) -> None:
        bundle = PromptBundle(
            core_prompt="You are a helpful assistant",
            flow_prompt="Focus on halachic questions",
            core_prompt_id="core-123",
            core_prompt_version="v1",
            flow_prompt_id="halachic-456",
            flow_prompt_version="v2",
        )
        assert bundle.core_prompt == "You are a helpful assistant"
        assert bundle.flow_prompt == "Focus on halachic questions"

    def test_system_prompt_property(self) -> None:
        bundle = PromptBundle(core_prompt="Core instructions here", flow_prompt="Flow-specific")
        assert bundle.system_prompt == "Core instructions here\n\nFlow-specific"

    def test_system_prompt_empty_flow(self) -> None:
        bundle = PromptBundle(core_prompt="Core only", flow_prompt="")
        assert "Core only" in bundle.system_prompt


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

    def test_init_loads_defaults(self) -> None:
        svc = PromptService(api_key=None)
        assert all(key in svc._defaults for key in ["core", "halachic", "general", "search"])

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

    @pytest.mark.parametrize("flow", ["HALACHIC", "SEARCH", "GENERAL"])
    def test_get_prompt_bundle(self, service: PromptService, flow: str) -> None:
        bundle = service.get_prompt_bundle(flow)
        assert bundle.core_prompt is not None
        assert bundle.flow_prompt is not None
        assert bundle.flow_prompt_version == "local"

    def test_get_default_prompt(self, service: PromptService) -> None:
        core = service.get_default_prompt("core")
        assert core is not None
        assert len(core) > 0

    def test_get_default_prompt_unknown_key(self, service: PromptService) -> None:
        prompt = service.get_default_prompt("unknown")
        core = service.get_default_prompt("core")
        assert prompt == core


class TestCaching:
    """Test prompt caching behavior."""

    def test_cache_stores_prompt(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._cache["test_prompt:v1"] = {
            "prompt": "Test prompt",
            "version": "v1",
            "timestamp": time.time(),
        }
        assert "test_prompt:v1" in service_short_ttl._cache

    def test_cache_hit(self, service_short_ttl: PromptService) -> None:
        bundle1 = service_short_ttl.get_prompt_bundle("HALACHIC")
        bundle2 = service_short_ttl.get_prompt_bundle("HALACHIC")
        assert bundle1.core_prompt == bundle2.core_prompt

    def test_cache_expiry(self, service_short_ttl: PromptService) -> None:
        service_short_ttl._get_prompt("test_id", "v1", "core")
        with service_short_ttl._cache_lock:
            for key in service_short_ttl._cache:
                service_short_ttl._cache[key]["timestamp"] = time.time() - 10
        result, version = service_short_ttl._get_prompt("test_id", "v1", "core")
        assert version == "local"

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
        assert service._extract_prompt_text(mock_prompt) == "System prompt here"

    def test_extract_from_build_content_list(self, service: PromptService) -> None:
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {
            "messages": [{"role": "system", "content": [{"text": "Part 1"}, {"text": " Part 2"}]}]
        }
        assert service._extract_prompt_text(mock_prompt) == "Part 1 Part 2"

    def test_extract_from_build_completion_format(self, service: PromptService) -> None:
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {"prompt": "Completion format prompt"}
        assert service._extract_prompt_text(mock_prompt) == "Completion format prompt"

    def test_extract_from_direct_attribute(self, service: PromptService) -> None:
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {}
        mock_prompt.prompt = "Direct prompt"
        del mock_prompt.prompt_data
        assert service._extract_prompt_text(mock_prompt) == "Direct prompt"

    def test_extract_returns_none_on_failure(self, service: PromptService) -> None:
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {}
        del mock_prompt.prompt_data
        del mock_prompt.prompt
        del mock_prompt.content
        del mock_prompt.text
        del mock_prompt.system
        assert service._extract_prompt_text(mock_prompt) is None


class TestGetPromptService:
    """Test get_prompt_service singleton function."""

    def test_returns_service_instance(self) -> None:
        import chat.prompts.prompt_service as ps

        ps._default_service = None
        svc = get_prompt_service()
        assert isinstance(svc, PromptService)

    def test_returns_same_instance(self) -> None:
        import chat.prompts.prompt_service as ps

        ps._default_service = None
        service1 = get_prompt_service()
        service2 = get_prompt_service()
        assert service1 is service2


class TestPromptBundleVersionTracking:
    """Test version tracking in prompt bundles."""

    def test_bundle_includes_version_info(self, service: PromptService) -> None:
        bundle = service.get_prompt_bundle("HALACHIC")
        assert bundle.core_prompt_id == settings.CORE_PROMPT_SLUG
        assert bundle.core_prompt_version is not None
        assert bundle.flow_prompt_id is not None
        assert bundle.flow_prompt_version is not None

    def test_bundle_custom_prompt_ids(self, service: PromptService) -> None:
        bundle = service.get_prompt_bundle(
            "HALACHIC", core_prompt_id="custom_core", flow_prompt_id="custom_flow"
        )
        assert bundle.core_prompt_id == "custom_core"
        assert bundle.flow_prompt_id == "custom_flow"

    def test_bundle_local_version(self, service: PromptService) -> None:
        bundle = service.get_prompt_bundle("GENERAL")
        assert bundle.flow_prompt_version == "local"
