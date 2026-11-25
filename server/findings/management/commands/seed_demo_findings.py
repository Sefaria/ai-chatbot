"""Fill a development database with dummy findings: ``python manage.py seed_demo_findings``.

The findings are made up and belong to dummy user ids; the passages are real refs so the
pages and the reader sidebar can be tried against them. Refuses to run unless DEBUG is on.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from findings import service
from findings.models import Finding
from findings.service import SheetRefs

DEMO_USER = 900000001
DEMO_SHEET_BASE = 990000000

DEMO = [
    {
        "skill": "example-halachic-reading",
        "title": "Rising like a lion: readiness as a form of answering",
        "conclusion": "The ruling asks for strength in rising to serve before the day's demands arrive. On this reading the detail that matters is timing: the person is asked to meet the morning as an address already waiting for them, so that service begins as a response rather than an initiative.",
        "roles": [
            {"ref": "Shulchan Arukh, Orach Chayim 1:1", "role": "anchor"},
            {"ref": "Pirkei Avot 5:20", "role": "support"},
            {"ref": "Psalms 16:8", "role": "support"},
        ],
        "expanded": ["Shulchan Arukh, Orach Chayim 1:1", "Pirkei Avot 5:20", "Psalms 16:8"],
    },
    {
        "skill": "example-halachic-reading",
        "title": "Two ways of standing before God at the start of the day",
        "conclusion": "The Shulchan Arukh and the Rema place the weight of the first moments differently. One centres the act of rising; the other centres the awareness that accompanies it. Each gives a full account of the morning's relationship, and the difference is one of emphasis rather than of theology.",
        "roles": [
            {"ref": "Shulchan Arukh, Orach Chayim 1:1", "role": "anchor"},
            {"ref": "Shulchan Arukh, Orach Chayim 1:2", "role": "position"},
            {"ref": "Shulchan Arukh, Orach Chayim 1:3", "role": "position"},
        ],
        "expanded": [
            "Shulchan Arukh, Orach Chayim 1:1",
            "Shulchan Arukh, Orach Chayim 1:2",
            "Shulchan Arukh, Orach Chayim 1:3",
        ],
    },
    {
        "skill": "example-halachic-reading",
        "title": "Leaving the corner of the field: a claim the owner does not control",
        "conclusion": "The obligation is defined by what the owner may not choose: which poor person takes the produce. The law trains the owner to treat part of their own harvest as already belonging to someone else's need, so that generosity is enacted as acknowledgement rather than as a gift.",
        "roles": [
            {"ref": "Leviticus 19:9-10", "role": "anchor"},
            {"ref": "Mishnah Peah 1:1", "role": "support"},
        ],
        "expanded": ["Leviticus 19:9", "Leviticus 19:10", "Mishnah Peah 1:1"],
    },
    {
        "skill": "example-parallel-passages",
        "title": "Creation in two accounts: order and intimacy",
        "conclusion": "Set side by side, the two creation accounts differ in sequence, in the divine name, and in where the human stands. The first frames the human as the summit of an ordered world; the second as the first creature formed by hand. The differences teach two complementary positions a person holds before their Creator.",
        "roles": [
            {"ref": "Genesis 1:26-28", "role": "anchor"},
            {"ref": "Genesis 2:7", "role": "anchor"},
            {"ref": "Genesis Rabbah 8:1", "role": "support"},
        ],
        "expanded": [
            "Genesis 1:26",
            "Genesis 1:27",
            "Genesis 1:28",
            "Genesis 2:7",
            "Genesis Rabbah 8:1",
        ],
    },
    {
        "skill": "example-parallel-passages",
        "title": "The opening of Berakhot and its parallel: when evening begins",
        "conclusion": "The Mishnah's opening question and its parallel in the Tosefta answer the same case with different markers of time. Aligning them shows that one ties the obligation to the priests' schedule while the other ties it to the household's, and each choice changes who the time belongs to.",
        "roles": [
            {"ref": "Mishnah Berakhot 1:1", "role": "anchor"},
            {"ref": "Berakhot 2a:1", "role": "position"},
            {"ref": "Tosefta Berakhot 1:1", "role": "position"},
        ],
        "expanded": ["Mishnah Berakhot 1:1", "Berakhot 2a:1", "Tosefta Berakhot 1:1"],
    },
]


class Command(BaseCommand):
    help = "Create dummy findings for development (DEBUG only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed demo findings with DEBUG off")
        made = 0
        for i, demo in enumerate(DEMO):
            sheet_id = DEMO_SHEET_BASE + i
            Finding.objects.filter(sheet_id=sheet_id).delete()
            sheet = SheetRefs(sheet_id, DEMO_USER, demo["title"], f"demo-{i}", demo["expanded"])
            payload = {k: demo[k] for k in ("skill", "title", "conclusion", "roles")}
            service.record(DEMO_USER, {**payload, "sheet_id": sheet_id}, lambda *_a, s=sheet: s)
            made += 1
        self.stdout.write(f"Seeded {made} demo findings for dummy user {DEMO_USER}.")
