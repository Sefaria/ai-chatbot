"""
Conversation summary service for router context.

Creates and maintains rolling summaries of conversations to:
- Provide efficient context to the agent
- Track conversation state without full history
"""

import logging
import os
from typing import Any

import anthropic
from django.utils import timezone

from ...models import ChatSession, ConversationSummary

logger = logging.getLogger("chat.summarization")


# Prompt for generating summaries
SUMMARY_PROMPT = """You are a summarization assistant for a Jewish learning chatbot. Given the conversation history, create a concise summary that captures:

1. Current Topic: What is the main subject being discussed?
2. User Intent: What is the user trying to accomplish? (translation, discovery, deep engagement, etc.)
4. Key References: What texts, people, or topics have been mentioned?
5. Constraints: Any user-expressed preferences or limitations?
Output a JSON object with this structure:
{
  "text": "Brief 1-2 sentence summary of the conversation",
  "current_topic": "Main topic being discussed",
  "user_intent": "translation|discovery|deep_engagement|other",
  "texts_referenced": ["Genesis 1:1", "Berakhot 2a"],
  "topics_discussed": ["shabbat", "creation"],
  "people_mentioned": ["Rashi", "Maimonides"],
  "constraints": ["user prefers Hebrew", "looking for Sephardic opinions"]
}

Keep the summary focused and under 500 characters total."""


class SummaryService:
    """
    Service for generating and managing conversation summaries.

    Uses Claude for intelligent summarization with structured output.
    Falls back to simple extraction for speed when needed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-haiku-20240307",  # Use fast/cheap model
        use_llm: bool = True,
    ):
        """
        Initialize the summary service.

        Args:
            api_key: Anthropic API key (default: from env)
            model: Model to use for summarization
            use_llm: Whether to use LLM (False = simple extraction)
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.use_llm = use_llm and bool(self.api_key)

        if self.use_llm:
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = None

        logger.info(f"SummaryService initialized (use_llm={self.use_llm})")

    def update_summary(
        self,
        session: ChatSession,
        new_user_message: str,
        new_assistant_response: str,
    ) -> ConversationSummary:
        """
        Update the conversation summary with new messages.

        Args:
            session: Chat session to update/create summary for
            new_user_message: Latest user message
            new_assistant_response: Latest assistant response
        Returns:
            Updated ConversationSummary
        """
        current_summary = ConversationSummary.objects.filter(session=session).first()

        if self.use_llm:
            return self._llm_summarize(
                session,
                current_summary,
                new_user_message,
                new_assistant_response,
            )

        return self._simple_summarize(
            session,
            current_summary,
            new_user_message,
            new_assistant_response,
        )

    def _llm_summarize(
        self,
        session: ChatSession,
        current_summary: ConversationSummary | None,
        new_user_message: str,
        new_assistant_response: str,
    ) -> ConversationSummary:
        """Use Claude to generate a structured summary."""
        try:
            # Build context
            context_parts = []
            if current_summary:
                summary_text = current_summary.to_prompt_text()
                if summary_text:
                    context_parts.append(f"Previous Summary:\n{summary_text}")
            context_parts.append(f"User: {new_user_message[:1000]}")
            context_parts.append(f"Assistant: {new_assistant_response[:1000]}")

            context = "\n\n".join(context_parts)

            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0.0,
                system=SUMMARY_PROMPT,
                messages=[{"role": "user", "content": context}],
            )

            # Parse JSON response
            response_text = response.content[0].text

            # Try to extract JSON
            import json

            try:
                # Handle potential markdown code blocks
                if "```json" in response_text:
                    json_str = response_text.split("```json")[1].split("```")[0]
                elif "```" in response_text:
                    json_str = response_text.split("```")[1].split("```")[0]
                else:
                    json_str = response_text

                data = json.loads(json_str.strip())

                return self._apply_summary_data(
                    session=session,
                    current_summary=current_summary,
                    data=data,
                )

            except json.JSONDecodeError:
                logger.warning(f"Failed to parse summary JSON: {response_text[:200]}")
                return self._simple_summarize(
                    session,
                    current_summary,
                    new_user_message,
                    new_assistant_response,
                )

        except Exception as e:
            logger.error(f"LLM summarization error: {e}")
            return self._simple_summarize(
                session,
                current_summary,
                new_user_message,
                new_assistant_response,
            )

    def _simple_summarize(
        self,
        session: ChatSession,
        current_summary: ConversationSummary | None,
        new_user_message: str,
        new_assistant_response: str,
    ) -> ConversationSummary:
        """Simple rule-based summarization (fast fallback)."""
        summary = current_summary or ConversationSummary(session=session)
        summary.text = f"User asked: {new_user_message[:100]}..."
        summary.current_topic = self._extract_topic(new_user_message)
        summary.user_intent = self._infer_intent(new_user_message)
        summary.turn_count = (summary.turn_count or 0) + 1
        summary.last_updated = timezone.now()

        # Carry over entities from previous summary
        if current_summary:
            summary.texts_referenced = current_summary.texts_referenced[-5:]
            summary.topics_discussed = current_summary.topics_discussed[-5:]
            summary.people_mentioned = current_summary.people_mentioned[-5:]

        summary.save()
        return summary

    def _apply_summary_data(
        self,
        session: ChatSession,
        current_summary: ConversationSummary | None,
        data: dict[str, Any],
    ) -> ConversationSummary:
        """Apply structured summary data to the model and persist."""

        def _safe_list(value: Any) -> list:
            return value if isinstance(value, list) else []

        summary = current_summary or ConversationSummary(session=session)
        summary.text = data.get("text", summary.text)
        summary.current_topic = data.get("current_topic", "")
        summary.user_intent = data.get("user_intent", "")
        summary.flow = data.get("flow", summary.flow)
        summary.texts_referenced = _safe_list(data.get("texts_referenced", []))
        summary.topics_discussed = _safe_list(data.get("topics_discussed", []))
        summary.people_mentioned = _safe_list(data.get("people_mentioned", []))
        summary.halachic_domain = data.get("halachic_domain", "")
        summary.constraints = _safe_list(data.get("constraints", []))
        summary.safety_flags = _safe_list(data.get("safety_flags", []))
        summary.turn_count = (summary.turn_count or 0) + 1
        summary.last_updated = timezone.now()

        summary.save()
        return summary

    def _extract_topic(self, message: str) -> str:
        """Extract the main topic from a message."""
        # Simple extraction - take first 50 chars or first sentence
        message = message.strip()
        if "?" in message:
            topic = message.split("?")[0]
        elif "." in message:
            topic = message.split(".")[0]
        else:
            topic = message[:50]

        return topic.strip()[:100]

    def _infer_intent(self, message: str) -> str:
        """Infer user intent from message patterns."""
        message_lower = message.lower()

        if any(word in message_lower for word in ["translate", "translation", "render", "in english"]):
            return "translation"
        elif any(word in message_lower for word in ["find", "search", "where", "source"]):
            return "discovery"
        elif any(
            word in message_lower for word in ["permitted", "allowed", "halacha", "mutar", "assur"]
        ):
            return "deep_engagement"
        elif any(word in message_lower for word in ["explain", "what is", "teach", "understand"]):
            return "deep_engagement"
        elif any(word in message_lower for word in ["compare", "difference", "opinions"]):
            return "deep_engagement"
        else:
            return "other"


# Default service instance
_default_service = None


def get_summary_service() -> SummaryService:
    """Get or create the default summary service."""
    global _default_service
    if _default_service is None:
        _default_service = SummaryService()
    return _default_service
