from __future__ import annotations

import asyncio
import logging

from ..router import get_router_service
from .contracts import ConversationMessage


class Router:
    def __init__(self, *, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("chat.agent")

    async def run_router(
        self, bt_span, user_message: str, messages: list[ConversationMessage]
    ) -> tuple[str | None, list[ConversationMessage]]:
        """Run the router to classify the message and select the appropriate prompt.

        Returns (core_prompt_id_override, possibly_updated_messages).
        Fails open: errors default to (None, original_messages) — i.e. Discovery with no rewrite.
        """
        router_span = bt_span.start_span(name="router", type="task")
        try:
            router_result = await asyncio.to_thread(get_router_service().classify, user_message)
            router_span.log(
                input={"message": user_message},
                output={
                    "route": router_result.route.value,
                    "core_prompt_id": router_result.core_prompt_id,
                    "rewritten_message": router_result.rewritten_message,
                },
            )

            # If the router rewrote the message, replace the last user message
            if router_result.rewritten_message:
                updated = list(messages)
                for i in range(len(updated) - 1, -1, -1):
                    if updated[i].role == "user":
                        updated[i] = ConversationMessage(
                            role="user", content=router_result.rewritten_message
                        )
                        break
                messages = updated

            return router_result.core_prompt_id, messages
        except Exception as exc:
            self.logger.error(f"Router failed, defaulting to Discovery: {exc}")
            router_span.log(
                input={"message": user_message},
                output={"route": "discovery", "error": str(exc)},
            )
            return None, messages
        finally:
            router_span.end()
