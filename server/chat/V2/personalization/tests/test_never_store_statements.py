"""The code screen for statements ABOUT THE USER in the seven never-store categories, Hebrew and
English. Bare topic words in a study question must not match (this is a study assistant)."""

import pytest

from chat.V2.personalization.checks import never_store_text
from chat.V2.personalization.signals import is_sensitive, is_user_statement

POSITIVE_HE = [
    ("location", "אני גר בחיפה ורוצה ללמוד דף יומי"),
    ("location", "שאנחנו גרים בתל אביב, מה הזמן המתאים?"),
    ("family", "אשתי ביקשה שנלמד יחד"),
    ("family", "יש לי שלושה ילדים והם שואלים על החג"),
    ("health", "יש לי סכרת, איך מתפללים בצום?"),
    ("health", "אני סובלת מדיכאון ורוצה ללמוד"),
    ("mourning", "אני באבל על אבא"),
    ("mourning", "אבא שלי נפטר השבוע"),
    ("status", "אני כהן, מה אסור לי?"),
    ("status", "בעלי מסרב לתת לי גט"),
    ("conversion", "התגיירתי לפני שנה"),
    ("conversion", "אני מתגיירת ורוצה ללמוד"),
    ("denomination", "אני חרדי ורוצה סוגיה קצרה"),
    ("denomination", "ואני לא שומר שבת, אבל מתעניין"),
]
POSITIVE_EN = [
    ("location", "I live in Brooklyn and want a morning study plan"),
    ("location", "I'm based in Toronto, which zmanim apply?"),
    ("family", "my wife wants to study with me"),
    ("family", "I have two kids who ask about Pesach"),
    ("health", "my diagnosis is recent, any texts on healing?"),
    ("health", "I was diagnosed last spring"),
    ("mourning", "I'm sitting shiva this week"),
    ("mourning", "my father passed away on Tuesday"),
    ("status", "I am a kohen, can I attend?"),
    ("status", "I'm an agunah and need sources"),
    ("conversion", "I converted three years ago"),
    ("conversion", "I'm a convert and want to study"),
    ("denomination", "I am Reform and curious about Talmud"),
    ("denomination", "I'm not observant but love the stories"),
    ("denomination", "I don't keep kosher, but explain the laws"),
]
NEGATIVE_HE = [
    "מה דין גר שנתגייר",
    "הלכות אבלות",
    "מה דין חולה בשבת לפי הרמב״ם",
    "מי הייתה אשתו של רבי עקיבא",
    "האם כהן רשאי להיכנס לבית קברות",
    "מה ההבדל בין אבל לשמחה בפסוק",
    "מה דין ממזר לפי המשנה",
    "מה ההבדל בין גר צדק לגר תושב",
    "אברהם גר בארץ כנען, מה פירוש הפסוק",
    "בעלי חיים בפרשת נח",
    "בתי כנסת בתקופת התלמוד",
    "אבל אני לא מבין את הפסוק הזה",
    "איך מתנהג האבל בשבעה לפי השולחן ערוך",
    "מה אומר רש״י על בני ישראל",
    "מהי דעת הרפורמים על כשרות",
    "מה ההבדל בין חרדי לחסידי בהשקפה",
    "אבי הבן וחובתו בפדיון",
]
NEGATIVE_EN = [
    "what does the Reform movement say about kashrut",
    "who was Rabbi Akiva's wife",
    "what are the laws of mourning and shiva in the Shulchan Aruch",
    "how does the Talmud define the status of a convert",
    "is a kohen allowed to enter a cemetery",
    "explain the mamzer laws in Deuteronomy 23",
    "what is the Orthodox view on organ donation",
    "Jacob's sons in Genesis 49",
    "what does Maimonides say about a sick person on Shabbat",
    "Joseph lived in Egypt for how many years",
    "does a husband owe his wife a ketubah",
    "the diagnosis of tzaraat in Leviticus 13",
    "the ger toshav in Leviticus 25",
    "who lives in the land according to Numbers 13",
    "what is a get and who must give it",
    "how did Rachel die and where is she buried",
    "Conservative and Reform approaches to the Documentary Hypothesis",
]


@pytest.mark.parametrize("category,text", POSITIVE_HE + POSITIVE_EN)
def test_statements_about_the_user_match(category, text):
    assert is_user_statement(text), (category, text)
    assert is_sensitive(text)


@pytest.mark.parametrize("text", NEGATIVE_HE + NEGATIVE_EN)
def test_study_sentences_with_the_same_topic_words_do_not_match(text):
    assert not is_user_statement(text), text


def test_all_seven_categories_are_covered_in_both_languages():
    cats = {"location", "family", "health", "mourning", "status", "conversion", "denomination"}
    assert {c for c, _ in POSITIVE_HE} == cats and {c for c, _ in POSITIVE_EN} == cats
    assert len(POSITIVE_HE) >= 8 and len(POSITIVE_EN) >= 8
    assert len(NEGATIVE_HE) >= 10 and len(NEGATIVE_EN) >= 10


def test_prefixes_and_boundaries_in_hebrew():
    assert is_user_statement("כי אשתי חולה")
    assert is_user_statement("לאשתי יש שאלה")
    assert not is_user_statement("הנשיא אשתיים")  # not the word, just letters around it
    assert not is_user_statement("מתגיירתי")  # a different form, not a first-person claim here


def test_the_profile_check_uses_the_same_screen():
    assert never_store_text("אני גר בחיפה")
    assert never_store_text("I live in Brooklyn")
