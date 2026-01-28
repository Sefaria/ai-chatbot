"""
Prompt management module with Braintrust integration.

Provides:
- Core and flow-specific prompt retrieval from Braintrust
- Fallback to local default prompts
- Prompt versioning and A/B testing support
"""

from .default_prompts import (
    CORE_PROMPT,
    DEEP_ENGAGEMENT_PROMPT,
    DISCOVERY_PROMPT,
    TRANSLATION_PROMPT,
)
from .prompt_service import PromptBundle, PromptService, get_prompt_service

__all__ = [
    "PromptService",
    "PromptBundle",
    "get_prompt_service",
    "CORE_PROMPT",
    "TRANSLATION_PROMPT",
    "DISCOVERY_PROMPT",
    "DEEP_ENGAGEMENT_PROMPT",
]
