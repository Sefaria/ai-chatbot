"""The report-issue prompt must carry the translation methodology.

The whole design rests on the assistant judging a report against the same
document the reader can open from the "unverified machine translation" label.
If the methodology silently stops being spliced in, the assistant still answers
— just against nothing — so this is worth a test rather than a code review.
"""

import importlib.util
import pathlib
import sys

import pytest

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[3] / "prompts"


@pytest.fixture(scope="module")
def report_issue_module():
    """Import prompts/report_issue.py, which isn't on the Django path."""
    if str(PROMPTS_DIR) not in sys.path:
        sys.path.insert(0, str(PROMPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "prompts_report_issue", PROMPTS_DIR / "report_issue.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(name: str) -> str:
    return (PROMPTS_DIR / "prompt_text" / f"{name}.md").read_text()


class TestPromptComposition:
    def test_template_carries_the_methodology_marker(self, report_issue_module):
        assert report_issue_module.METHODOLOGY_MARKER in read("report_issue")

    def test_built_prompt_inlines_the_methodology_document(self, report_issue_module):
        built = report_issue_module.build_prompt()
        methodology = read("translation_methodology")

        assert report_issue_module.METHODOLOGY_MARKER not in built
        # The principle itself has to survive into the prompt, not just the file.
        assert "simple understanding" in methodology
        assert "simple understanding" in built

    def test_build_fails_loudly_if_the_marker_goes_missing(
        self, report_issue_module, monkeypatch
    ):
        monkeypatch.setattr(
            report_issue_module, "read_prompt_text", lambda name: "prompt with no marker"
        )
        with pytest.raises(ValueError, match="marker"):
            report_issue_module.build_prompt()

    def test_prompt_keeps_the_runtime_response_format_placeholder(self, report_issue_module):
        """Substituted at runtime by the orchestrator, so it must survive the push."""
        assert "{{response_format}}" in report_issue_module.build_prompt()

    def test_slug_matches_the_setting_the_flow_resolves(self, report_issue_module):
        from django.conf import settings

        assert report_issue_module.SLUG == settings.REPORT_ISSUE_PROMPT_SLUG


class TestPromptContent:
    """Behaviours the flow depends on, stated in the prompt."""

    @pytest.fixture(scope="class")
    def prompt(self, report_issue_module):
        return report_issue_module.build_prompt()

    def test_gives_the_escalation_address(self, prompt):
        assert "corrections@sefaria.org" in prompt

    def test_tells_the_assistant_not_to_refetch_the_seeded_segment(self, prompt):
        assert "get_text" in prompt

    def test_documents_the_seed_shape_the_widget_builds(self, prompt):
        for marker in ("says:", "English:", "Hebrew:", "Comment:"):
            assert marker in prompt, f"seed marker {marker!r} missing from the prompt"
