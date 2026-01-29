"""ASGI config for chatbot_server."""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.settings")

django_asgi_app = get_asgi_application()

# Import after Django setup to avoid AppRegistryNotReady during module import.
import chat.routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(URLRouter(chat.routing.websocket_urlpatterns)),
    }
)
