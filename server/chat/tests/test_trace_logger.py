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

    def test_turn_number_logged_when_set(self):
        ctx = MessageContext(origin="sefaria-production", turn_number=3)
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["turn_number"] == 3

    def test_turn_number_omitted_when_none(self):
        ctx = MessageContext(origin="sefaria-production", turn_number=None)
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert "turn_number" not in call_kwargs["metadata"]


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


class TestTraceLoggerPersonalizationFields:
    """Every turn carries the personalization fields, so they can be Braintrust columns."""

    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def _metadata(self, ctx):
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        return self.span.log.call_args[1]["metadata"]

    def test_plain_turn_carries_default_personalization_fields(self):
        metadata = self._metadata(MessageContext(origin="sefaria-production"))
        assert metadata["personalized"] is False
        assert metadata["profile_on"] is None
        assert metadata["instructions_on"] is None
        assert metadata["profile_revision"] is None
        assert metadata["notice_shown"] is False
        assert metadata["profile_injected"] is None
        assert metadata["prompt_versions"] == {}

    def test_plain_turn_omits_chapter_and_lab_only_keys(self):
        metadata = self._metadata(MessageContext(origin="sefaria-production"))
        assert set(metadata) >= {
            "personalized",
            "profile_on",
            "instructions_on",
            "profile_revision",
            "notice_shown",
            "profile_injected",
            "prompt_versions",
        }
        assert "chapter_sha" not in metadata
        assert "instructions_injected" not in metadata

    def test_plain_turn_does_not_add_profile_text(self):
        metadata = self._metadata(MessageContext(origin="sefaria-production"))
        assert "profile" not in metadata
        assert "instructions" not in metadata

    def test_plain_turn_defaults_do_not_share_prompt_versions(self):
        first = self._metadata(MessageContext(origin="sefaria-production"))
        first["prompt_versions"]["leaked"] = "x"
        second = self._metadata(MessageContext(origin="sefaria-production"))
        assert second["prompt_versions"] == {}

    def test_personalized_turn_keeps_its_meta_values(self):
        meta = {
            "personalized": True,
            "profile_on": True,
            "instructions_on": False,
            "profile_revision": 7,
            "chapter_sha": "abc123",
            "notice_shown": True,
            "prompt_versions": {"personalization_answer": "v2"},
        }
        ctx = MessageContext(origin="sefaria-production", personalization_meta=meta)
        metadata = self._metadata(ctx)
        assert metadata["personalized"] is True
        assert metadata["profile_on"] is True
        assert metadata["instructions_on"] is False
        assert metadata["profile_revision"] == 7
        assert metadata["chapter_sha"] == "abc123"
        assert metadata["notice_shown"] is True
        assert metadata["prompt_versions"] == {"personalization_answer": "v2"}
        assert "profile_injected" not in metadata

    def test_personalized_turn_keeps_lab_injected_values(self):
        meta = {
            "personalized": True,
            "profile_revision": 2,
            "profile_injected": "Reads Talmud",
            "instructions_injected": "Be brief",
        }
        ctx = MessageContext(origin="sefaria-production", personalization_meta=meta)
        metadata = self._metadata(ctx)
        assert metadata["profile_injected"] == "Reads Talmud"
        assert metadata["instructions_injected"] == "Be brief"
