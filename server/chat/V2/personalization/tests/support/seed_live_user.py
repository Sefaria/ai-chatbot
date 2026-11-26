"""Seed one invented learner for checking the nightly update on a lab deployment.

    python manage.py shell -c "from chat.V2.personalization.tests.support.seed_live_user import seed; seed()"

Writes the rows the way a real chat turn is stored (a completed user message and its successful
assistant reply), then moves `server_timestamp` (auto_now_add) into the past: 12 prompts over 3
days, the last one yesterday. Made-up text only. Safe to run again: it replaces its own rows.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

USER_ID = "live-check-user"

PROMPTS = [
    "Please answer in English, even if I quote Hebrew. What does Rashi say on Genesis 1:1?",
    "Please answer in English. What does Ramban say about the first verse of Genesis?",
    "Please reply in English. How does Rashi explain the word Bereshit?",
    "Please answer in English: what does Ramban add to Rashi on Genesis 1:1?",
    "Please answer in English. What does Rashi say on Exodus 3:14?",
    "Please answer in English. What does Ramban say on Exodus 3:14?",
    "Please reply in English. Where do Rashi and Ramban disagree on the Binding of Isaac?",
    "Please answer in English. What does Rashi say on Genesis 22:1?",
    "Please answer in English. What does Ramban say on Genesis 22:1?",
    "Please answer in English. What does Rashi say about Leviticus 19:18?",
    "Please answer in English. What does Ramban say about Leviticus 19:18?",
    "Please answer in English. Compare Rashi and Ramban on Deuteronomy 6:4.",
]


def seed(user_id: str = USER_ID) -> int:
    from chat.models import ChatMessage, ChatSession
    from chat.V2.personalization import profile_store

    ChatMessage.objects.filter(user_id=user_id).delete()
    ChatSession.objects.filter(user_id=user_id).delete()
    profile_store.set_enabled(user_id, "profile", True)

    now = timezone.now()
    for n, text in enumerate(PROMPTS):
        day = 3 - n // 4  # days ago: 3, 2, 1 (four prompts a day)
        at = now - timedelta(days=day, hours=4 - (n % 4))
        session_id = f"live-check-s{n // 4}"
        ChatSession.objects.get_or_create(session_id=session_id, defaults={"user_id": user_id})
        turn_id = f"live-check-t{n}"
        user_msg = ChatMessage.objects.create(
            message_id=f"live-check-u{n}",
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            role=ChatMessage.Role.USER,
            content=text,
            processing_state=ChatMessage.ProcessingState.COMPLETED,
        )
        reply = ChatMessage.objects.create(
            message_id=f"live-check-a{n}",
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            role=ChatMessage.Role.ASSISTANT,
            content="An invented answer.",
            status=ChatMessage.Status.SUCCESS,
        )
        ChatMessage.objects.filter(pk__in=[user_msg.pk, reply.pk]).update(server_timestamp=at)
        ChatMessage.objects.filter(pk=user_msg.pk).update(response_message=reply)
    return len(PROMPTS)


if __name__ == "__main__":
    print(f"seeded {seed()} prompts for {USER_ID}")
