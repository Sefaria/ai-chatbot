# Generated manually - removes unused logging models

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_braintrustlog_routedecision_toolcallevent_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="BraintrustLog",
        ),
        migrations.DeleteModel(
            name="ToolCallEvent",
        ),
    ]
