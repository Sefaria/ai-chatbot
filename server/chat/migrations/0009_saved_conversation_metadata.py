from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0008_chatmessage_processing_heartbeat"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="is_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="title",
            field=models.CharField(
                blank=True,
                default="",
                help_text="User-visible saved conversation title",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="title_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
