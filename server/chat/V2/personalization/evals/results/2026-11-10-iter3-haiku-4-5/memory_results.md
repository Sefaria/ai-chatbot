# Memory evaluation
Date 2026-11-10, llm `claude-code`, model `claude-haiku-4-5-20251001`, slot set v1, 3 run(s) (cells show mean (min-max)).

## Summary
| strategy | slot accuracy | checks passed | in-session | model calls/turn | Jev requests/turn | Jev input tokens/turn | Jev avg latency ms | model tokens/turn | $/1k turns (haiku-4-5) | $/1k turns (haiku-5-5) |
|---|---|---|---|---|---|---|---|---|---|---|
| realtime | 80% (78%-82%) | 13.3 (13-14)/15 | 3/3 | 0.97 | 0.00 | 0 | 0 | 834 | $1.154 | $0.115 |
| nightly | 86% (83%-89%) | 12/15 | 0/3 | 0.41 | 0.00 | 0 | 0 | 548 | $0.953 | $0.095 |
| hybrid | 86% (84%-87%) | 13/15 | 1/3 | 0.46 | 0.00 | 0 | 0 | 587 | $0.992 | $0.099 |
| hybrid_jev | 96% (96%-96%) | 15/15 | 3/3 | 0.41 | 1.01 | 1234 | 245 | 406 | $0.683 | $0.115 |
| explicit | 44% | 8/15 | 0/3 | 0.00 | 0.00 | 0 | 0 | 0 | $0.000 | $0.000 |

## Assumptions
- Hybrid: every pending proposal is auto-accepted right after the turn that created it, simulating the learner tapping yes. The count is reported as auto_accepted.
- Batches run at 03:00 UTC on the day after each day boundary and once after the last turn.
- Explicit: the persona's interview answers are written as stated slots before the first turn.
- Stated events (a learner setting a slot) apply to every strategy after the named turn.
- Personas are synthetic; accuracy is against a golden profile written by the dataset author.
- hybrid_jev: Jev requests are priced at the Jev rate in both cost scenarios; Jev never writes free text, so answer_style and learning_habits come only from the LLM batch or an accepted proposal. Auto-accept applies here too.
- Oracle runs answer with golden values on every prompt: scores show the pipeline ceiling.

## Slot accuracy by slot
| slot | realtime | nightly | hybrid | hybrid_jev | explicit |
|---|---|---|---|---|---|
| preferred_language | 89% | 100% | 100% | 100% | 0% |
| hebrew_fluency | 78% (67%-83%) | 50% | 50% | 100% | 78% |
| terminology_comfort | 50% (38%-62%) | 71% (62%-75%) | 75% | 89% | 78% |
| text_categories | 89% | 93% (89%-100%) | 89% | 89% | 0% |
| answer_style | 71% (67%-80%) | 100% | 90% (83%-100%) | 100% | 100% |
| learning_habits | 100% | 95% (86%-100%) | 100% | 100% | 57% |

## Failed checks
| strategy | persona | check | runs failed | detail |
|---|---|---|---|---|
| realtime | david-daf | not_flipped (preferred_language) | 3/3 | flipped to 'en' in snapshot 9 |
| realtime | david-daf | not_flipped (terminology_comfort) | 1/3 | flipped to 'some' in snapshot 9 |
| nightly | rachel-anon | available_by_turn (preferred_language) | 3/3 | preferred_language after rachel-1: missing |
| nightly | rachel-anon | available_by_turn (hebrew_fluency) | 3/3 | hebrew_fluency after rachel-3: missing |
| nightly | rachel-anon | available_by_turn (terminology_comfort) | 3/3 | terminology_comfort after rachel-4: missing |
| hybrid | rachel-anon | available_by_turn (hebrew_fluency) | 3/3 | hebrew_fluency after rachel-3: missing |
| hybrid | rachel-anon | available_by_turn (terminology_comfort) | 3/3 | terminology_comfort after rachel-4: missing |
| explicit | rachel-anon | available_by_turn (preferred_language) | 3/3 | preferred_language after rachel-1: missing |
| explicit | rachel-anon | available_by_turn (hebrew_fluency) | 3/3 | hebrew_fluency after rachel-3: missing |
| explicit | rachel-anon | available_by_turn (terminology_comfort) | 3/3 | terminology_comfort after rachel-4: missing |
| explicit | david-daf | not_flipped (preferred_language) | 3/3 | unset in snapshot 9 (turn) |
| explicit | miriam-evolving | changed_fact (hebrew_fluency) | 3/3 | final 'none'; earlier snapshot in old ['none']: True |
| explicit | miriam-evolving | changed_fact (terminology_comfort) | 3/3 | final 'new'; earlier snapshot in old ['new']: True |
| explicit | sarah-paste | not_flipped (preferred_language) | 3/3 | unset in snapshot 3 (turn) |
| realtime | david-daf | not_flipped (hebrew_fluency) | 1/3 | flipped to 'intermediate' in snapshot 9 |

## Why updates were dropped (counts, summed over 3 run(s))
| reason | realtime | nightly | hybrid | hybrid_jev | explicit |
|---|---|---|---|---|---|
| reply_unparsable | 0 | 0 | 0 | 0 | 0 |
| reply_repaired | 1 | 4 | 5 | 0 | 0 |
| value_invalid | 0 | 0 | 0 | 0 | 0 |
| quote_not_verbatim | 0 | 0 | 1 | 0 | 0 |
| no_valid_evidence | 0 | 0 | 0 | 0 | 0 |
| below_min_sessions | 0 | 65 | 63 | 19 | 0 |
| stated_locked | 0 | 0 | 4 | 7 | 0 |
| unchanged | 176 | 66 | 277 | 380 | 0 |
| jev_uncertain | 0 | 0 | 0 | 143 | 0 |
| jev_below_change_threshold | 0 | 0 | 0 | 3 | 0 |
| jev_error | 0 | 0 | 0 | 0 | 0 |
| applied | 174 | 126 | 117 | 210 | 0 |

## Per persona (slot verdicts, correct/total scored; first run)
| persona | trap | realtime | nightly | hybrid | hybrid_jev | explicit |
|---|---|---|---|---|---|---|
| rachel-anon | in_session | 4/5 | 3/5 | 3/5 | 6/6 | 1/5 |
| rebecca-veteran | stated_lock | 5/6 | 5/6 | 5/6 | 6/6 | 3/6 |
| david-daf | one_off | 4/5 | 5/5 | 6/6 | 6/6 | 2/5 |
| miriam-evolving | changed_fact | 5/5 | 5/5 | 5/5 | 5/5 | 0/4 |
| yossi-sensitive | sensitive | 4/4 | 4/4 | 4/4 | 5/6 | 2/4 |
| tamar-instruction | instruction | 4/4 | 4/4 | 3/4 | 5/5 | 4/6 |
| avi-mixed | mixed_language | 2/5 | 5/6 | 5/6 | 6/6 | 3/5 |
| sarah-paste | one_off | 5/6 | 4/5 | 4/5 | 5/6 | 3/5 |
| noam-stale | stale | 3/4 | 5/5 | 3/4 | 4/4 | 2/5 |
