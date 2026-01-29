"""
Prompt management module with Braintrust integration.

Provides:
- Core prompt retrieval from Braintrust
- Fallback to local default prompt
- Prompt versioning and caching
"""

from .default_prompts import CORE_PROMPT
from .prompt_service import CorePrompt, PromptService, get_prompt_service

__all__ = [
    "PromptService",
    "CorePrompt",
    "get_prompt_service",
    "CORE_PROMPT",
]
