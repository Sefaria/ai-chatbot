"""
Prompt service with Braintrust integration.

Fetches prompts from Braintrust with fallback to local defaults.
Supports versioning and caching.
"""

import os
import time
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from threading import Lock

logger = logging.getLogger('chat.prompts')


@dataclass
class PromptBundle:
    """Bundle of prompts for a turn."""
    core_prompt: str
    flow_prompt: str
    core_prompt_id: str = ""
    core_prompt_version: str = ""
    flow_prompt_id: str = ""
    flow_prompt_version: str = ""
    
    @property
    def system_prompt(self) -> str:
        """Combined system prompt."""
        return f"{self.core_prompt}\n\n{self.flow_prompt}"


class PromptService:
    """
    Service for fetching and caching prompts from Braintrust.
    
    Features:
    - Braintrust prompt registry integration
    - In-memory caching with TTL
    - Fallback to local defaults
    - Version tracking for reproducibility
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        project_name: Optional[str] = None,
        cache_ttl_seconds: int = 300,  # 5 minutes
    ):
        """
        Initialize the prompt service.
        
        Args:
            api_key: Braintrust API key (default: from env)
            project_name: Braintrust project name (default: from env)
            cache_ttl_seconds: How long to cache prompts
        """
        self.api_key = api_key or os.environ.get('BRAINTRUST_API_KEY')
        self.project_name = project_name or os.environ.get('BRAINTRUST_PROJECT', 'sefaria-chatbot')
        self.cache_ttl = cache_ttl_seconds
        
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = Lock()
        self._braintrust_client = None
        
        # Load local defaults
        from .default_prompts import (
            CORE_PROMPT,
            HALACHIC_PROMPT,
            GENERAL_PROMPT,
            SEARCH_PROMPT,
        )
        self._defaults = {
            'core': CORE_PROMPT,
            'halachic': HALACHIC_PROMPT,
            'general': GENERAL_PROMPT,
            'search': SEARCH_PROMPT,
        }
        
        self._init_braintrust()
    
    def _init_braintrust(self):
        """Initialize Braintrust client if configured."""
        if not self.api_key:
            logger.info("Braintrust API key not configured, using local prompts only")
            return

        try:
            import braintrust
            self._braintrust_client = braintrust

            # Also initialize the router's Braintrust client for core prompt
            from ..router import get_braintrust_client
            self._router_braintrust_client = get_braintrust_client()

            logger.info(f"Braintrust client initialized for project: {self.project_name}")
        except ImportError:
            logger.warning("Braintrust package not installed, using local prompts only")
            self._router_braintrust_client = None
        except Exception as e:
            logger.warning(f"Failed to initialize Braintrust: {e}")
            self._router_braintrust_client = None
    
    def get_prompt_bundle(
        self,
        flow: str,
        core_prompt_id: str = "core-8fbc",
        flow_prompt_id: Optional[str] = None,
        version: str = "stable",
    ) -> PromptBundle:
        """
        Get a complete prompt bundle for a flow.

        Args:
            flow: Flow type (HALACHIC, GENERAL, SEARCH)
            core_prompt_id: Braintrust slug for core prompt (default: "core-8fbc")
            flow_prompt_id: Braintrust ID for flow prompt (default: derived from flow)
            version: Prompt version to fetch

        Returns:
            PromptBundle with core and flow prompts
        """
        flow_lower = flow.lower()
        flow_prompt_id = flow_prompt_id or f"bt_prompt_{flow_lower}"

        # Fetch core prompt using the router's Braintrust client (supports core-8fbc slug)
        if core_prompt_id == "core-8fbc" and hasattr(self, '_router_braintrust_client') and self._router_braintrust_client:
            try:
                core_prompt = self._router_braintrust_client.get_core_prompt(version)
                core_version = version
                logger.debug(f"Fetched core prompt via router client: {len(core_prompt)} chars")
            except Exception as e:
                logger.warning(f"Failed to fetch core prompt via router client: {e}, falling back to legacy method")
                core_prompt, core_version = self._get_prompt(core_prompt_id, version, 'core')
        else:
            # Fetch using legacy method
            logger.debug(f"Using legacy method for core prompt: {core_prompt_id}")
            core_prompt, core_version = self._get_prompt(core_prompt_id, version, 'core')

        # Fetch flow prompt
        flow_prompt, flow_version = self._get_prompt(flow_prompt_id, version, flow_lower)

        return PromptBundle(
            core_prompt=core_prompt,
            flow_prompt=flow_prompt,
            core_prompt_id=core_prompt_id,
            core_prompt_version=core_version,
            flow_prompt_id=flow_prompt_id,
            flow_prompt_version=flow_version,
        )
    
    def _get_prompt(
        self,
        prompt_id: str,
        version: str,
        fallback_key: str,
    ) -> Tuple[str, str]:
        """
        Get a single prompt by ID with caching.
        
        Returns (prompt_text, version_used)
        """
        cache_key = f"{prompt_id}:{version}"
        
        # Check cache
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached['timestamp'] < self.cache_ttl:
                return cached['prompt'], cached['version']
        
        # Try Braintrust
        if self._braintrust_client:
            try:
                prompt_text, actual_version = self._fetch_from_braintrust(prompt_id, version)
                if prompt_text:
                    with self._cache_lock:
                        self._cache[cache_key] = {
                            'prompt': prompt_text,
                            'version': actual_version,
                            'timestamp': time.time(),
                        }
                    return prompt_text, actual_version
            except Exception as e:
                logger.warning(f"Failed to fetch prompt {prompt_id} from Braintrust: {e}")
        
        # Fallback to local defaults
        fallback_prompt = self._defaults.get(fallback_key, self._defaults['core'])
        return fallback_prompt, 'local'
    
    def _fetch_from_braintrust(
        self,
        prompt_id: str,
        version: str,
    ) -> Tuple[Optional[str], str]:
        """
        Fetch a prompt from Braintrust.
        
        Returns (prompt_text, version) or (None, "") if not found.
        """
        try:
            # Use Braintrust's prompt API
            # Note: Actual API may differ - this is based on typical patterns
            prompt = self._braintrust_client.load_prompt(
                project=self.project_name,
                slug=prompt_id,
                version=version if version != "stable" else None,
            )
            
            if prompt and hasattr(prompt, 'prompt'):
                # Get the actual version
                actual_version = getattr(prompt, 'version', version)
                return prompt.prompt, str(actual_version)
            
            return None, ""
            
        except Exception as e:
            logger.debug(f"Braintrust prompt fetch error for {prompt_id}: {e}")
            return None, ""
    
    def invalidate_cache(self, prompt_id: Optional[str] = None):
        """
        Invalidate cached prompts.
        
        Args:
            prompt_id: Specific prompt to invalidate, or None for all
        """
        with self._cache_lock:
            if prompt_id:
                # Invalidate all versions of this prompt
                keys_to_remove = [k for k in self._cache if k.startswith(f"{prompt_id}:")]
                for key in keys_to_remove:
                    del self._cache[key]
            else:
                # Clear all
                self._cache.clear()
        
        logger.info(f"Prompt cache invalidated: {prompt_id or 'all'}")
    
    def get_default_prompt(self, key: str) -> str:
        """Get a local default prompt by key."""
        return self._defaults.get(key, self._defaults['core'])


# Default prompt service instance
_default_service = None


def get_prompt_service() -> PromptService:
    """Get or create the default prompt service."""
    global _default_service
    if _default_service is None:
        _default_service = PromptService()
    return _default_service


