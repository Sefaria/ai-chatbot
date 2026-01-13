"""
Prompt management module with Braintrust integration.

Provides:
- Core and flow-specific prompt retrieval from Braintrust
- Fallback to local default prompts
- Prompt versioning and A/B testing support
"""

from .prompt_service import PromptService, PromptBundle, get_prompt_service
from .default_prompts import (
    CORE_PROMPT,
    HALACHIC_PROMPT,
    GENERAL_PROMPT,
    SEARCH_PROMPT,
)

__all__ = [
    'PromptService',
    'PromptBundle',
    'get_prompt_service',
    'CORE_PROMPT',
    'HALACHIC_PROMPT',
    'GENERAL_PROMPT',
    'SEARCH_PROMPT',
]


