from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = "chat"

    def ready(self):
        from django.conf import settings

        from .V2.personalization.gate import warn_if_mode_set_outside_production

        warn_if_mode_set_outside_production(settings)
