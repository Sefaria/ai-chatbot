import hashlib

from django.db import migrations


def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()


def _hash_numeric_user_ids(apps, schema_editor):
    ChatSession = apps.get_model("chat", "ChatSession")
    ChatMessage = apps.get_model("chat", "ChatMessage")

    for model in (ChatSession, ChatMessage):
        queryset = model.objects.filter(user_id__regex=r"^[0-9]+$")
        batch = []
        for obj in queryset.iterator(chunk_size=1000):
            obj.user_id = _hash_user_id(obj.user_id)
            batch.append(obj)
            if len(batch) >= 1000:
                model.objects.bulk_update(batch, ["user_id"])
                batch = []
        if batch:
            model.objects.bulk_update(batch, ["user_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0008_chatmessage_processing_heartbeat"),
    ]

    operations = [
        migrations.RunPython(_hash_numeric_user_ids, reverse_code=migrations.RunPython.noop),
    ]
