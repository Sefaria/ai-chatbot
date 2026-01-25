"""
Conversation summarization service for router context.

Provides rolling summaries that capture:
- Current topic and user intent
- Halachic/learning context
- Entities mentioned (texts, topics, people)
- Constraints and preferences
"""

from .summary_service import (
    ConversationSummary,
    SummaryResult,
    SummaryService,
    get_summary_service,
)

__all__ = [
    "SummaryService",
    "SummaryResult",
    "ConversationSummary",
    "get_summary_service",
]
