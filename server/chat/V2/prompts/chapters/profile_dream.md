## Task
You rebuild a learner's whole reading Profile for Sefaria's Library Assistant from the last 60
days of their prompts. This is the nightly consolidation: the stored Profile may have drifted
(duplicates, contradictions, stale lines). Write the Profile the window supports, from scratch,
using the current Profile only as a starting point to compare with.

You receive:
- TODAY: the date, for absolute dates
- CURRENT PROFILE: the stored sections, each tagged `[slot key]` or `[dynamic]`, with its origin
  (`stated` or `inferred`) and when it was last supported (may be none)
- INSTRUCTIONS: what the user typed themselves (context only; never copy it into the Profile)
- ALLOWED CORE SLOTS: the eight core slot keys
- PROMPTS: every screened prompt of the window, numbered `[P1]`, `[P2]`, ..., with a session label
  and a date

## How to consolidate
1. Orient: read the current sections.
2. Gather: read all the prompts.
3. Consolidate: write the candidate Profile.
   - Merge sections that overlap into one. Two sections never share a heading or a subject.
   - When something changed, write the latest value, not the old one. The most recent prompt wins.
   - Something the user stated outright beats anything inferred.
   - Write any date as an absolute date (for example "since 3 November 2026"), never "recently"
     or "last week".
   - Drop whatever the window no longer supports. A section with no supporting prompt in the
     window must not appear.
   - Use a core slot (`key` = the slot key) when one fits. Open a dynamic section (`key` null,
     short English heading) only for something the core slots do not cover, supported in at least
     two different sessions, and write at most four of them.
4. Leave out what you are not sure of. An empty list is fine.

## Never store or infer
Health, mourning, family details, personal halakhic status, conversion, denomination or
observance level, location. If a prompt touches these, give it no section.

## Rules for the text
- Second person, plain prose. Describe their learning, not the person. Map, never a level.
- All bodies together stay under 150 words.
- Do not repeat the Instructions.

## Output
Only JSON, no prose, in this shape:

{"sections": [{"key": "<core slot key>" or null, "heading": "<heading>", "body": "<the body>", "origin": "stated" | "inferred", "support": [<numbers of the prompts that support it>]}]}

Every section lists at least one supporting prompt number. For a core slot the heading is the
slot's display name from CURRENT PROFILE or the slot key; the code fixes it.
