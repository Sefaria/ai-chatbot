"""Tests for persona validation and trace tagging."""

from unittest.mock import MagicMock

import pytest

from chat.serializers import MessageContextSerializer
from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.trace_logger import BraintrustTraceLogger
from chat.V2.persona import PERSONAS, resolve_persona


class TestResolvePersona:
    @pytest.mark.parametrize("persona", PERSONAS)
    def test_known_persona_returned(self, persona):
        assert resolve_persona(persona) == persona

    def test_normalizes_case_and_whitespace(self):
        assert resolve_persona("  Scholar ") == "scholar"

    @pytest.mark.parametrize("value", [None, "", "wizard", 3, ["learner"]])
    def test_unknown_values_ignored(self, value):
        assert resolve_persona(value) is None


class TestPersonaSerializer:
    @pytest.mark.parametrize("persona", PERSONAS)
    def test_valid_persona_accepted(self, persona):
        serializer = MessageContextSerializer(data={"persona": persona})
        assert serializer.is_valid()
        assert serializer.validated_data["persona"] == persona

    def test_blank_persona_accepted(self):
        assert MessageContextSerializer(data={"persona": ""}).is_valid()

    def test_missing_persona_accepted(self):
        serializer = MessageContextSerializer(data={})
        assert serializer.is_valid()
        assert "persona" not in serializer.validated_data

    def test_unknown_persona_rejected(self):
        serializer = MessageContextSerializer(data={"persona": "wizard"})
        assert not serializer.is_valid()
        assert "persona" in serializer.errors


class TestPersonaTracing:
    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def test_persona_logged_in_metadata(self):
        ctx = MessageContext(origin="library-next", persona="educator")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="m")
        assert self.span.log.call_args[1]["metadata"]["persona"] == "educator"

    def test_persona_omitted_when_absent(self):
        ctx = MessageContext(origin="library-next")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="m")
        assert "persona" not in self.span.log.call_args[1]["metadata"]
