"""
Hard-coded test questions and responses for QA testing.

When a user sends "Q1", "Q2", or "Q3" (case insensitive), the system
returns these pre-defined responses instead of calling the AI agent.
"""

# Test questions mapping (case-insensitive)
TEST_QUESTIONS = {
    "q1": {
        "question": "What commentaries explain the first verse of Genesis?",
        "flow": "SEARCH",
        "markdown": """The opening verse of the Torah, [Genesis 1:1](https://www.sefaria.org/Genesis.1.1), "In the beginning God created the heaven and the earth," has inspired extensive commentary throughout Jewish tradition.

## Key Commentaries

### Rashi
[Rashi on Genesis 1:1](https://www.sefaria.org/Rashi_on_Genesis.1.1) famously asks why the Torah begins with creation rather than the first commandment. He explains that it establishes God's sovereignty over the earth, giving Him the authority to grant the Land of Israel to the Jewish people.

### Ramban (Nachmanides)
[Ramban on Genesis 1:1](https://www.sefaria.org/Ramban_on_Genesis.1.1) offers a mystical interpretation, explaining that the word "בראשית" (In the beginning) hints at creation *ex nihilo* (something from nothing), a concept he explores in depth.

### Ibn Ezra
[Ibn Ezra on Genesis 1:1](https://www.sefaria.org/Ibn_Ezra_on_Genesis.1.1) takes a more grammatical approach, analyzing the Hebrew syntax and discussing whether "בראשית" is in the construct state.

### Sforno
[Sforno on Genesis 1:1](https://www.sefaria.org/Sforno_on_Genesis.1.1) emphasizes that "the heaven and the earth" refers to the raw materials from which everything else was formed.""",
    },
    "q2": {
        "question": "What are the halachic requirements for lighting Shabbat candles?",
        "flow": "HALACHIC",
        "markdown": """The laws of Shabbat candle lighting are detailed in several halachic sources.

## Primary Sources

### Shulchan Aruch
The core laws appear in [Shulchan Arukh, Orach Chaim 263](https://www.sefaria.org/Shulchan_Arukh%2C_Orach_Chayyim.263):

- **Timing**: Candles must be lit before sunset, typically 18 minutes before in most communities
- **Minimum**: At least two candles, representing "zachor" (remember) and "shamor" (observe)
- **Blessing**: The blessing "להדליק נר של שבת" is recited

### Mishnah Berurah
[Mishnah Berurah 263:1](https://www.sefaria.org/Mishnah_Berurah.263.1) elaborates on the details:
- Women have primary obligation as "guardians of the home"
- The candles should be lit where the Shabbat meal will be eaten
- Once the blessing is made, one has accepted Shabbat

### Talmudic Source
The obligation originates in [Shabbat 25b](https://www.sefaria.org/Shabbat.25b), which discusses the requirement for light on Shabbat as part of "oneg Shabbat" (Shabbat enjoyment).

## Practical Halacha
According to [Shulchan Arukh, Orach Chaim 263:10](https://www.sefaria.org/Shulchan_Arukh%2C_Orach_Chayyim.263.10), if one forgot to light candles, they should add an extra candle the following week as a reminder.""",
    },
    "q3": {
        "question": "Explain the opening of Tractate Berakhot about when to recite Shema.",
        "flow": "GENERAL",
        "markdown": """The Talmud opens with one of its most famous discussions in [Berakhot 2a](https://www.sefaria.org/Berakhot.2a).

## The Opening Mishnah

The tractate begins by asking: **"From what time may one recite the Shema in the evening?"**

[Mishnah Berakhot 1:1](https://www.sefaria.org/Mishnah_Berakhot.1.1) provides the answer: "From the time when the Kohanim enter to eat their terumah until the end of the first watch."

## Key Talmudic Discussion

### Why Start Here?
[Berakhot 2a](https://www.sefaria.org/Berakhot.2a) asks why the Mishnah begins with the evening Shema rather than the morning. The Gemara explains this follows the order of creation in [Genesis 1:5](https://www.sefaria.org/Genesis.1.5): "And there was evening and there was morning, one day."

### The Three Watches
[Berakhot 3a](https://www.sefaria.org/Berakhot.3a) discusses the division of the night into watches:
- Rabbi Eliezer says three watches
- The Sages say four watches

### Practical Timing
[Berakhot 8b](https://www.sefaria.org/Berakhot.8b) records that Rabbi Yehoshua ben Levi would recite Shema right before sleeping to fulfill the verse "when you lie down" ([Deuteronomy 6:7](https://www.sefaria.org/Deuteronomy.6.7)).""",
    },
}


def get_test_response(message: str) -> dict | None:
    """
    Check if the message is a test question (Q1, Q2, Q3).

    Args:
        message: The user's message

    Returns:
        Test response dict if message matches, None otherwise
    """
    normalized = message.strip().lower()
    return TEST_QUESTIONS.get(normalized)
