"""
Tests for PromptService - caching, fallback to defaults, Braintrust integration.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from chat.prompts.prompt_service import (
    PromptService,
    PromptBundle,
    get_prompt_service,
)


class TestPromptBundle:
    """Test PromptBundle dataclass."""

    def test_create_bundle(self):
        """Test basic bundle creation."""
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

    def test_system_prompt_property(self):
        """Test combined system_prompt property."""
        bundle = PromptBundle(
            core_prompt="Core instructions here",
            flow_prompt="Flow-specific instructions",
        )
        expected = "Core instructions here\n\nFlow-specific instructions"
        assert bundle.system_prompt == expected

    def test_system_prompt_empty_flow(self):
        """Test system_prompt with empty flow prompt."""
        bundle = PromptBundle(
            core_prompt="Core only",
            flow_prompt="",
        )
        assert "Core only" in bundle.system_prompt


class TestPromptServiceInit:
    """Test PromptService initialization."""

    def test_init_without_api_key(self):
        """Test initialization without Braintrust API key."""
        with patch.dict("os.environ", {}, clear=True):
            service = PromptService(api_key=None)
            assert service.api_key is None
            assert service._braintrust_client is None

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        # The service stores the api_key even if braintrust import fails
        service = PromptService(api_key="test_key")
        assert service.api_key == "test_key"

    def test_init_loads_defaults(self):
        """Test that default prompts are loaded."""
        service = PromptService(api_key=None)
        assert "core" in service._defaults
        assert "halachic" in service._defaults
        assert "general" in service._defaults
        assert "search" in service._defaults

    def test_init_custom_project_name(self):
        """Test initialization with custom project name."""
        service = PromptService(api_key=None, project_name="custom-project")
        assert service.project_name == "custom-project"

    def test_init_default_project_name(self):
        """Test default project name from env."""
        with patch.dict("os.environ", {"BRAINTRUST_PROJECT": "env-project"}):
            service = PromptService(api_key=None)
            assert service.project_name == "env-project"

    def test_init_cache_ttl(self):
        """Test custom cache TTL."""
        service = PromptService(api_key=None, cache_ttl_seconds=600)
        assert service.cache_ttl == 600


class TestFallbackToDefaults:
    """Test fallback to local default prompts."""

    @pytest.fixture
    def service(self):
        """Create service without Braintrust."""
        return PromptService(api_key=None)

    def test_get_prompt_bundle_halachic(self, service):
        """Test getting halachic prompt bundle falls back to default."""
        bundle = service.get_prompt_bundle("HALACHIC")
        assert bundle.core_prompt is not None
        assert bundle.flow_prompt is not None
        assert len(bundle.core_prompt) > 0
        assert bundle.flow_prompt_version == "local"

    def test_get_prompt_bundle_search(self, service):
        """Test getting search prompt bundle."""
        bundle = service.get_prompt_bundle("SEARCH")
        assert bundle.flow_prompt is not None

    def test_get_prompt_bundle_general(self, service):
        """Test getting general prompt bundle."""
        bundle = service.get_prompt_bundle("GENERAL")
        assert bundle.flow_prompt is not None

    def test_get_default_prompt(self, service):
        """Test getting default prompt directly."""
        core = service.get_default_prompt("core")
        assert core is not None
        assert len(core) > 0

    def test_get_default_prompt_unknown_key(self, service):
        """Test getting unknown key falls back to core."""
        prompt = service.get_default_prompt("unknown")
        core = service.get_default_prompt("core")
        assert prompt == core


class TestCaching:
    """Test prompt caching behavior."""

    @pytest.fixture
    def service(self):
        """Create service with short TTL for testing."""
        return PromptService(api_key=None, cache_ttl_seconds=1)

    def test_cache_stores_prompt(self, service):
        """Test that cache can store prompts (manual test)."""
        # Manually populate cache to verify structure
        service._cache["test_prompt:v1"] = {
            "prompt": "Test prompt",
            "version": "v1",
            "timestamp": time.time()
        }
        # Check cache has the entry
        assert len(service._cache) > 0
        assert "test_prompt:v1" in service._cache

    def test_cache_hit(self, service):
        """Test cache hit returns same result."""
        bundle1 = service.get_prompt_bundle("HALACHIC")
        bundle2 = service.get_prompt_bundle("HALACHIC")
        # Should get same cached result
        assert bundle1.core_prompt == bundle2.core_prompt

    def test_cache_expiry(self, service):
        """Test cache expires after TTL."""
        # Get prompt (caches it)
        service._get_prompt("test_id", "v1", "core")

        # Add to cache with old timestamp
        with service._cache_lock:
            for key in service._cache:
                service._cache[key]["timestamp"] = time.time() - 10  # Expired

        # Should fetch again (cache expired)
        # Since no Braintrust, falls back to default
        result, version = service._get_prompt("test_id", "v1", "core")
        assert version == "local"

    def test_invalidate_cache_all(self, service):
        """Test invalidating all cache."""
        # Manually populate cache
        service._cache["prompt_a:v1"] = {"prompt": "a", "version": "v1", "timestamp": time.time()}
        service._cache["prompt_b:v1"] = {"prompt": "b", "version": "v1", "timestamp": time.time()}
        assert len(service._cache) > 0

        # Invalidate all
        service.invalidate_cache()
        assert len(service._cache) == 0

    def test_invalidate_cache_specific(self, service):
        """Test invalidating specific prompt."""
        # Populate cache with known keys
        service._cache["prompt_a:v1"] = {"prompt": "a", "version": "v1", "timestamp": time.time()}
        service._cache["prompt_b:v1"] = {"prompt": "b", "version": "v1", "timestamp": time.time()}

        # Invalidate only prompt_a
        service.invalidate_cache("prompt_a")

        assert "prompt_a:v1" not in service._cache
        assert "prompt_b:v1" in service._cache


class TestBraintrustIntegration:
    """Test Braintrust prompt fetching (mocked)."""

    @pytest.fixture
    def mock_braintrust(self):
        """Create mock Braintrust module."""
        mock_bt = MagicMock()
        mock_prompt = MagicMock()
        mock_prompt.version = "bt_v1"
        mock_prompt.build.return_value = {
            "messages": [
                {"role": "system", "content": "Braintrust prompt content"}
            ]
        }
        mock_bt.load_prompt.return_value = mock_prompt
        return mock_bt

    def test_fetch_from_braintrust_success(self, mock_braintrust):
        """Test successful Braintrust fetch."""
        service = PromptService(api_key=None)  # Create without Braintrust
        service._braintrust_client = mock_braintrust
        service.project_name = "test-project"

        prompt_text, version = service._fetch_from_braintrust("test_slug", "stable")

        assert prompt_text == "Braintrust prompt content"
        assert version == "bt_v1"

    def test_fetch_from_braintrust_not_found(self, mock_braintrust):
        """Test Braintrust returns None for missing prompt."""
        mock_braintrust.load_prompt.return_value = None

        service = PromptService(api_key=None)
        service._braintrust_client = mock_braintrust

        prompt_text, version = service._fetch_from_braintrust("missing_slug", "stable")

        assert prompt_text is None
        assert version == ""

    def test_fetch_from_braintrust_error(self, mock_braintrust):
        """Test Braintrust fetch error falls back gracefully."""
        mock_braintrust.load_prompt.side_effect = Exception("API Error")

        service = PromptService(api_key=None)
        service._braintrust_client = mock_braintrust

        prompt_text, version = service._fetch_from_braintrust("test_slug", "stable")

        assert prompt_text is None


class TestPromptExtraction:
    """Test _extract_prompt_text method."""

    @pytest.fixture
    def service(self):
        return PromptService(api_key=None)

    def test_extract_from_build_messages(self, service):
        """Test extracting from build() with messages."""
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {
            "messages": [
                {"role": "system", "content": "System prompt here"}
            ]
        }

        result = service._extract_prompt_text(mock_prompt)
        assert result == "System prompt here"

    def test_extract_from_build_content_list(self, service):
        """Test extracting from build() with content as list."""
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"text": "Part 1"},
                        {"text": " Part 2"},
                    ],
                }
            ]
        }

        result = service._extract_prompt_text(mock_prompt)
        assert result == "Part 1 Part 2"

    def test_extract_from_build_completion_format(self, service):
        """Test extracting from completion format."""
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {
            "prompt": "Completion format prompt"
        }

        result = service._extract_prompt_text(mock_prompt)
        assert result == "Completion format prompt"

    def test_extract_from_direct_attribute(self, service):
        """Test extracting from direct prompt attribute."""
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {}
        mock_prompt.prompt = "Direct prompt"
        # Remove other attributes
        del mock_prompt.prompt_data

        result = service._extract_prompt_text(mock_prompt)
        assert result == "Direct prompt"

    def test_extract_returns_none_on_failure(self, service):
        """Test returns None when extraction fails."""
        mock_prompt = MagicMock()
        mock_prompt.build.return_value = {}
        # Remove all possible attributes
        del mock_prompt.prompt_data
        del mock_prompt.prompt
        del mock_prompt.content
        del mock_prompt.text
        del mock_prompt.system

        result = service._extract_prompt_text(mock_prompt)
        assert result is None


class TestGetPromptService:
    """Test get_prompt_service singleton function."""

    def test_returns_service_instance(self):
        """Test that it returns a PromptService."""
        # Reset global state
        import chat.prompts.prompt_service as ps
        ps._default_service = None

        service = get_prompt_service()
        assert isinstance(service, PromptService)

    def test_returns_same_instance(self):
        """Test singleton behavior."""
        import chat.prompts.prompt_service as ps
        ps._default_service = None

        service1 = get_prompt_service()
        service2 = get_prompt_service()
        assert service1 is service2


class TestPromptBundleVersionTracking:
    """Test version tracking in prompt bundles."""

    @pytest.fixture
    def service(self):
        return PromptService(api_key=None)

    def test_bundle_includes_version_info(self, service):
        """Test that bundle includes version information."""
        bundle = service.get_prompt_bundle("HALACHIC")
        assert bundle.core_prompt_id == "core-8fbc"
        assert bundle.core_prompt_version is not None
        assert bundle.flow_prompt_id is not None
        assert bundle.flow_prompt_version is not None

    def test_bundle_custom_prompt_ids(self, service):
        """Test bundle with custom prompt IDs."""
        bundle = service.get_prompt_bundle(
            "HALACHIC",
            core_prompt_id="custom_core",
            flow_prompt_id="custom_flow",
        )
        assert bundle.core_prompt_id == "custom_core"
        assert bundle.flow_prompt_id == "custom_flow"

    def test_bundle_local_version(self, service):
        """Test that fallback shows 'local' version."""
        bundle = service.get_prompt_bundle("GENERAL")
        # Without Braintrust, should show local
        assert bundle.flow_prompt_version == "local"
