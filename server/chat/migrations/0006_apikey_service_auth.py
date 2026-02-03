# Generated manually for dual authentication support

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0005_conversation_summary"),
    ]

    operations = [
        # Create APIKey model
        migrations.CreateModel(
            name="APIKey",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("key_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("service_id", models.CharField(db_index=True, max_length=100, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "API Key",
                "verbose_name_plural": "API Keys",
            },
        ),
        # Make ChatSession.user_id nullable
        migrations.AlterField(
            model_name="chatsession",
            name="user_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        # Add ChatSession.service_id
        migrations.AddField(
            model_name="chatsession",
            name="service_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        # Add ChatSession constraint
        migrations.AddConstraint(
            model_name="chatsession",
            constraint=models.CheckConstraint(
                check=models.Q(user_id__isnull=False) | models.Q(service_id__isnull=False),
                name="chatsession_has_owner",
            ),
        ),
        # Make ChatMessage.user_id nullable
        migrations.AlterField(
            model_name="chatmessage",
            name="user_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        # Add ChatMessage.service_id
        migrations.AddField(
            model_name="chatmessage",
            name="service_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        # Add ChatMessage index for service_id
        migrations.AddIndex(
            model_name="chatmessage",
            index=models.Index(
                fields=["service_id", "server_timestamp"],
                name="chat_chatme_service_7c4a1d_idx",
            ),
        ),
        # Add ChatMessage constraint
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.CheckConstraint(
                check=models.Q(user_id__isnull=False) | models.Q(service_id__isnull=False),
                name="chatmessage_has_owner",
            ),
        ),
    ]
