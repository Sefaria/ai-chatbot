from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0009_saved_conversation_metadata"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                ("ALTER TABLE chat_chatsession ALTER COLUMN is_deleted SET DEFAULT false"),
                ("ALTER TABLE chat_chatsession ALTER COLUMN title SET DEFAULT ''"),
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
