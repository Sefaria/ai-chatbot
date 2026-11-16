from django.db import migrations, models


def drop_duplicates(apps, schema_editor):
    """Keep the first observation per (user, message) and one pending proposal per (user, slot),
    so the unique constraints below can be added to a database that already has rows."""
    observation = apps.get_model("chat", "ProfileObservation")
    proposal = apps.get_model("chat", "ProfileProposal")
    seen = set()
    for row in observation.objects.order_by("user_id", "message_id", "id").only(
        "id", "user_id", "message_id"
    ):
        key = (row.user_id, row.message_id)
        if key in seen:
            row.delete()
        seen.add(key)
    seen = set()
    for row in proposal.objects.filter(status="pending").order_by(
        "user_id", "slot_key", "-created_at"
    ):
        key = (row.user_id, row.slot_key)
        if key in seen:
            row.status = "rejected"
            row.save(update_fields=["status"])
        seen.add(key)


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0017_profile_generation"),
    ]

    operations = [
        migrations.RenameField(
            model_name="profilesettings",
            old_name="generation",
            new_name="revision",
        ),
        migrations.AlterField(
            model_name="profileslot",
            name="expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(drop_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="profileobservation",
            constraint=models.UniqueConstraint(
                fields=("user_id", "message_id"), name="uniq_profile_observation_user_message"
            ),
        ),
        migrations.AddConstraint(
            model_name="profileproposal",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("user_id", "slot_key"),
                name="uniq_pending_proposal_user_slot",
            ),
        ),
    ]
