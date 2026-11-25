import uuid

from django.db import models


class Finding(models.Model):
    """One saved result of a skill: the record behind a source sheet.

    Public once saved, as long as its skill is authorized in the catalog. Staff hide a finding
    (``hidden``) rather than delete it, so a takedown can be reviewed and undone.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.CharField(max_length=64, db_index=True)
    skill_version = models.CharField(max_length=32, blank=True)
    method = models.CharField(max_length=64, db_index=True)
    sefaria_user_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=160)
    conclusion = models.TextField()
    # The cited sources as given: [{"ref": normal form, "role": "anchor" | "position" | "support"}].
    roles = models.JSONField(default=list)
    # One finding per sheet: saving again for the same sheet updates it.
    sheet_id = models.IntegerField(unique=True)
    sheet_title = models.CharField(max_length=255, blank=True)
    # The sheet's dateModified when its refs were last read, to notice later edits.
    sheet_modified = models.CharField(max_length=40, blank=True)
    hidden = models.BooleanField(default=False, db_index=True)
    hidden_reason = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.title} ({self.skill})"


class FindingRef(models.Model):
    """One segment a finding touches, with the role of the source that covers it."""

    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="refs")
    source_ref = models.CharField(max_length=255)
    segment_ref = models.CharField(max_length=255, db_index=True)
    section_ref = models.CharField(max_length=255, db_index=True)
    book = models.CharField(max_length=200, db_index=True)
    role = models.CharField(max_length=16)

    class Meta:
        indexes = [models.Index(fields=["section_ref", "role"])]

    def __str__(self):
        return f"{self.segment_ref} ({self.role})"
