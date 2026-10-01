from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0012_appetizer_data_and_history_search_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="persona",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
