"""
Prompt management module with Braintrust integration.

Provides:
- Core prompt retrieval from Braintrust
- Prompt versioning and caching
"""

from .prompt_service import CorePrompt, PromptService, get_prompt_service

__all__ = [
    "PromptService",
    "CorePrompt",
    "get_prompt_service",
]
