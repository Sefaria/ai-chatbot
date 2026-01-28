"""
Conversation summarization service for router context.

Provides rolling summaries that capture:
- Current topic and user intent
- Halachic/learning context
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
