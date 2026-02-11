"""
Conversation summarization service for agent context.

Provides rolling summaries that capture:
- Current topic and user intent
- Entities mentioned (texts, topics, people)
- Constraints and preferences
"""

from ...models import ConversationSummary
from .summary_service import SummaryService, get_summary_service

__all__ = [
    "SummaryService",
    "ConversationSummary",
    "get_summary_service",
]
