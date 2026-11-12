"""Test helper: a run row holds no free text derived from the user."""

import json

from django.forms.models import model_to_dict

from chat.models import ProfileWriterRun


def run_row_text(row: ProfileWriterRun) -> str:
    """The whole row, every field, as one string."""
    return json.dumps(model_to_dict(row), ensure_ascii=False, default=str)


def assert_run_rows_hold_no_user_text(user_id: str, forbidden: list[str]) -> int:
    """Load every ProfileWriterRun row of the user and assert none of the forbidden strings
    appears in any field. Returns the number of rows checked (at least one is expected)."""
    rows = list(ProfileWriterRun.objects.filter(user_id=user_id))
    assert rows, "no run row to check"
    for row in rows:
        text = run_row_text(row)
        for marker in forbidden:
            assert marker not in text, f"{marker!r} found on run row {row.id} ({row.status})"
    return len(rows)
