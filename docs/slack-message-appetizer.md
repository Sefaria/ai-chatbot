Added a "Topic Appetizer" to the chatbot — a blue box that shows relevant Sefaria topic link(s) while the main agent is still working, so users get something to read within ~2.5s instead of waiting on the full response.

How it works: right after auth, a parallel thread runs the appetizer pipeline (independent of the main agent), and pushes the result to the SSE stream as soon as it's ready:

1. **Calendar context** (parsha, daf yomi, etc.) is fetched once per day and cached — free after the first request of the day.
2. **One structured LLM call** extracts up to 3 candidate topics from the message alone (no conversation history), using the calendar context to resolve temporal references like "this week's parsha" or "today's daf yomi."
3. **A grounding gate** (no second LLM call) searches the Sefaria topic API for each candidate and only keeps strong/plausible matches — this is what keeps false positives down, since low-confidence candidates need an exact match to survive.
4. No topic clears the bar → nothing is shown. The precision gate matters: in real traffic, "no topic" is the majority outcome, so over-extraction would spam a chip on nearly every turn.

Replaces the old approach (regex-first, single hardcoded topic, special-cased parsha/daf-yomi suppression rules) with one general pipeline that handles any phrasing/language without enumerated rules — tuned on ~600 real production prompts across 10+ languages.

Hard cap: 5 seconds, fails closed (returns nothing) on timeout. Topics-only — never returns text refs directly.

PR #136 on `waiting-source`.
