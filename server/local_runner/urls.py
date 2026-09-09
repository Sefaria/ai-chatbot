"""URL surface the local runner serves.

Deliberately narrower than the server's. Local mode needs the chat endpoints and
history; it does not need the Anthropic eval endpoint or the prompt-reload admin
route, and running Django on a user's machine means every route not needed is
attack surface kept open for nothing.
"""

from django.urls import path

from chat import views as shared_views
from chat.V2 import views as v2_views

from . import views as runner_views

urlpatterns = [
    # The turn itself — the production view, unmodified.
    path("api/v2/chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2"),
    path("api/chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2_alias"),
    path("api/v2/chat/recover", v2_views.chat_recover_v2, name="chat_recover_v2"),
    path("api/v2/chat/client-event", v2_views.chat_client_event_v2, name="chat_client_event_v2"),
    path("api/v2/chat/feedback", v2_views.chat_feedback_v2, name="chat_feedback_v2"),
    path("api/v2/prompts/defaults", v2_views.prompt_defaults, name="prompt_defaults_v2"),
    # Local history: the daemon's SQLite is authoritative for local-mode
    # conversations (D4), and the widget reaches it through the same path.
    path("api/history", shared_views.history, name="history"),
    # Runner-only.
    path("health", runner_views.health, name="runner_health"),
]
