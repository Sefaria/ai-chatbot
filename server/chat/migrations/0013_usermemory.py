from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0012_appetizer_data_and_history_search_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserMemory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("user_id", models.CharField(max_length=100, unique=True)),
                ("experience", models.CharField(blank=True, default="", max_length=100)),
                ("orientation", models.CharField(blank=True, default="", max_length=100)),
                ("hebrew", models.CharField(blank=True, default="", max_length=100)),
                ("notes", models.CharField(blank=True, default="", max_length=250)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
