"""Tests for BraintrustTraceLogger origin logging."""

from unittest.mock import MagicMock

from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.trace_logger import BraintrustTraceLogger


class TestTraceLoggerOrigin:
    """Test that BraintrustTraceLogger logs origin metadata and dev tag."""

    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def test_non_prod_origin_logs_dev_tag(self):
        ctx = MessageContext(origin="local")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "local"
        assert call_kwargs["tags"] == ["dev"]

    def test_prod_origin_logs_no_tag(self):
        ctx = MessageContext(origin="sefaria-production")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "sefaria-production"
        assert "tags" not in call_kwargs

    def test_origin_always_in_metadata(self):
        ctx = MessageContext(origin="eval-runner")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "eval-runner"

    def test_no_origin_logs_dev_tag(self):
        ctx = MessageContext(origin=None)
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["tags"] == ["dev"]

    def test_user_id_logged_when_present(self):
        ctx = MessageContext(origin="sefaria-production", user_id="user_abc123")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["user_id"] == "user_abc123"

    def test_user_id_omitted_when_absent(self):
        ctx = MessageContext(origin="sefaria-production", user_id=None)
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert "user_id" not in call_kwargs["metadata"]


class TestTraceLoggerPromptMetadata:
    """Test that log_prompt_metadata includes route."""

    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def _call(self, route="discovery"):
        self.logger.log_prompt_metadata(
            bt_span=self.span,
            core_prompt_id="prompt-slug",
            core_prompt_version="abc123",
            system_prompt_in_options=True,
            summary_included=False,
            route=route,
        )
        return self.span.log.call_args[1]

    def test_route_logged_in_metadata(self):
        call_kwargs = self._call(route="translation")
        assert call_kwargs["metadata"]["route"] == "translation"

    def test_route_discovery_logged(self):
        call_kwargs = self._call(route="discovery")
        assert call_kwargs["metadata"]["route"] == "discovery"
