"""
Conversation summary service for router context.

Creates and maintains rolling summaries of conversations to:
- Provide efficient context to the router
- Enable flow stickiness decisions
- Track conversation state without full history
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic
import braintrust

from ..metrics import TokenUsage

logger = logging.getLogger("chat.summarization")


@dataclass
class SummaryResult:
    """Result from summary update including token usage."""

    summary: "ConversationSummary"
    token_usage: TokenUsage | None = None  # Token usage from LLM call (if any)


@dataclass
class ConversationSummary:
    """
    Structured summary of a conversation.

    Designed for router consumption with key context signals.
    """

    # Core content
    text: str = ""  # Free-form summary text

    # Structured fields for routing
    current_topic: str = ""
    user_intent: str = ""  # learning, searching, asking_halacha, etc.
    flow: str = ""  # Current/suggested flow

    # Entities mentioned
    texts_referenced: list[str] = field(default_factory=list)
    topics_discussed: list[str] = field(default_factory=list)
    people_mentioned: list[str] = field(default_factory=list)

    # Context
    halachic_domain: str = ""  # shabbat, kashrut, etc.
    constraints: list[str] = field(default_factory=list)  # user-expressed constraints

    # Safety
    safety_flags: list[str] = field(default_factory=list)

    # Metadata
    turn_count: int = 0
    last_updated: datetime | None = None

    def to_text(self) -> str:
        """Convert to text format for router input."""
        parts = []

        if self.text:
            parts.append(f"Summary: {self.text}")

        if self.current_topic:
            parts.append(f"Current Topic: {self.current_topic}")

        if self.user_intent:
            parts.append(f"User Intent: {self.user_intent}")

        if self.flow:
            parts.append(f"Flow: {self.flow}")

        if self.texts_referenced:
            parts.append(f"Texts: {', '.join(self.texts_referenced[:5])}")

        if self.topics_discussed:
            parts.append(f"Topics: {', '.join(self.topics_discussed[:5])}")

        if self.halachic_domain:
            parts.append(f"Halachic Domain: {self.halachic_domain}")

        if self.constraints:
            parts.append(f"Constraints: {', '.join(self.constraints)}")

        if self.safety_flags:
            parts.append(f"Safety Flags: {', '.join(self.safety_flags)}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "text": self.text,
            "current_topic": self.current_topic,
            "user_intent": self.user_intent,
            "flow": self.flow,
            "texts_referenced": self.texts_referenced,
            "topics_discussed": self.topics_discussed,
            "people_mentioned": self.people_mentioned,
            "halachic_domain": self.halachic_domain,
            "constraints": self.constraints,
            "safety_flags": self.safety_flags,
            "turn_count": self.turn_count,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationSummary":
        """Create from dictionary."""
        last_updated = None
        if data.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(data["last_updated"])
            except (ValueError, TypeError):
                pass

        return cls(
            text=data.get("text", ""),
            current_topic=data.get("current_topic", ""),
            user_intent=data.get("user_intent", ""),
            flow=data.get("flow", ""),
            texts_referenced=data.get("texts_referenced", []),
            topics_discussed=data.get("topics_discussed", []),
            people_mentioned=data.get("people_mentioned", []),
            halachic_domain=data.get("halachic_domain", ""),
            constraints=data.get("constraints", []),
            safety_flags=data.get("safety_flags", []),
            turn_count=data.get("turn_count", 0),
            last_updated=last_updated,
        )


# Prompt for generating summaries
SUMMARY_PROMPT = """You are a summarization assistant for a Jewish learning chatbot. Given the conversation history, create a concise summary that captures:

1. Current Topic: What is the main subject being discussed?
2. User Intent: What is the user trying to accomplish? (learning, searching for sources, asking halachic questions, etc.)
3. Flow: Is this primarily HALACHIC (practical law questions), SEARCH (finding sources), or GENERAL (learning/discussion)?
4. Key References: What texts, people, or topics have been mentioned?
5. Constraints: Any user-expressed preferences or limitations?
6. Safety Concerns: Any content that might require guardrails?

Output a JSON object with this structure:
{
  "text": "Brief 1-2 sentence summary of the conversation",
  "current_topic": "Main topic being discussed",
  "user_intent": "learning|searching|halacha|discussion|other",
  "flow": "HALACHIC|SEARCH|GENERAL",
  "texts_referenced": ["Genesis 1:1", "Berakhot 2a"],
  "topics_discussed": ["shabbat", "creation"],
  "people_mentioned": ["Rashi", "Maimonides"],
  "halachic_domain": "shabbat|kashrut|prayer|other or empty",
  "constraints": ["user prefers Hebrew", "looking for Sephardic opinions"],
  "safety_flags": ["prompt_injection|high_risk_psak or empty"]
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
        model: str = "claude-haiku-4-5-20251001",  # Use fast/cheap model
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

    async def update_summary(
        self,
        current_summary: ConversationSummary | None,
        new_user_message: str,
        new_assistant_response: str,
        flow: str = "",
    ) -> SummaryResult:
        """
        Update the conversation summary with new messages.

        Args:
            current_summary: Existing summary (or None for new conversation)
            new_user_message: Latest user message
            new_assistant_response: Latest assistant response
            flow: Current flow from router

        Returns:
            SummaryResult with updated summary and token usage
        """
        if self.use_llm:
            return await self._llm_summarize(
                current_summary,
                new_user_message,
                new_assistant_response,
                flow,
            )
        else:
            summary = self._simple_summarize(
                current_summary,
                new_user_message,
                new_assistant_response,
                flow,
            )
            return SummaryResult(summary=summary, token_usage=None)

    async def _llm_summarize(
        self,
        current_summary: ConversationSummary | None,
        new_user_message: str,
        new_assistant_response: str,
        flow: str,
    ) -> SummaryResult:
        """Use Claude to generate a structured summary."""
        usage: TokenUsage | None = None
        try:
            # Build context
            context_parts = []
            if current_summary and current_summary.text:
                context_parts.append(f"Previous Summary: {current_summary.text}")
            context_parts.append(f"User: {new_user_message[:1000]}")
            context_parts.append(f"Assistant: {new_assistant_response[:1000]}")

            context = "\n\n".join(context_parts)

            # Call Claude API with tracing
            with braintrust.start_span(name="summary-llm", type="llm") as span:
                span.log(
                    input={"user_message": new_user_message[:200], "flow": flow},
                    metadata={"model": self.model},
                )

                llm_start = time.time()
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=500,
                    temperature=0.0,
                    system=SUMMARY_PROMPT,
                    messages=[{"role": "user", "content": context}],
                )
                llm_latency = int((time.time() - llm_start) * 1000)

                # Log token usage
                usage = TokenUsage.from_anthropic(response.usage)
                span.log(
                    output={"summary": response.content[0].text[:200]},
                    metrics={"latency_ms": llm_latency, **usage.to_braintrust()},
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

                summary = ConversationSummary.from_dict(data)
                summary.turn_count = (current_summary.turn_count if current_summary else 0) + 1
                summary.last_updated = datetime.now()
                summary.flow = flow or summary.flow

                return SummaryResult(summary=summary, token_usage=usage)

            except json.JSONDecodeError:
                logger.warning(f"Failed to parse summary JSON: {response_text[:200]}")
                summary = self._simple_summarize(
                    current_summary,
                    new_user_message,
                    new_assistant_response,
                    flow,
                )
                # Still return token usage since we made the LLM call
                return SummaryResult(summary=summary, token_usage=usage)

        except Exception as e:
            logger.error(f"LLM summarization error: {e}")
            summary = self._simple_summarize(
                current_summary,
                new_user_message,
                new_assistant_response,
                flow,
            )
            return SummaryResult(summary=summary, token_usage=usage)

    def _simple_summarize(
        self,
        current_summary: ConversationSummary | None,
        new_user_message: str,
        new_assistant_response: str,
        flow: str,
    ) -> ConversationSummary:
        """Simple rule-based summarization (fast fallback)."""
        summary = ConversationSummary(
            text=f"User asked: {new_user_message[:100]}...",
            current_topic=self._extract_topic(new_user_message),
            user_intent=self._infer_intent(new_user_message),
            flow=flow,
            turn_count=(current_summary.turn_count if current_summary else 0) + 1,
            last_updated=datetime.now(),
        )

        # Carry over entities from previous summary
        if current_summary:
            summary.texts_referenced = current_summary.texts_referenced[-5:]
            summary.topics_discussed = current_summary.topics_discussed[-5:]

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

        if any(word in message_lower for word in ["find", "search", "where", "source"]):
            return "searching"
        elif any(
            word in message_lower for word in ["permitted", "allowed", "halacha", "mutar", "assur"]
        ):
            return "halacha"
        elif any(word in message_lower for word in ["explain", "what is", "teach", "understand"]):
            return "learning"
        elif any(word in message_lower for word in ["compare", "difference", "opinions"]):
            return "discussion"
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
