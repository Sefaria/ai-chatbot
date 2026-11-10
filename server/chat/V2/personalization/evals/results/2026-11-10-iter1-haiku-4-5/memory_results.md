# Memory evaluation
Date 2026-11-10, llm `claude-code`, model `claude-haiku-4-5-20251001`, slot set v1.

## Summary
| strategy | slot accuracy | checks passed | in-session | model calls/turn | Jev requests/turn | Jev input tokens/turn | Jev avg latency ms | model tokens/turn | $/1k turns (haiku-4-5) | $/1k turns (haiku-5-5) |
|---|---|---|---|---|---|---|---|---|---|---|
| realtime | 78% | 12/15 | 3/3 | 0.97 | 0.00 | 0 | 0 | 845 | $1.245 | $0.125 |
| nightly | 73% | 10/15 | 0/3 | 0.41 | 0.00 | 0 | 0 | 535 | $1.014 | $0.101 |
| hybrid | 80% | 9/15 | 1/3 | 0.46 | 0.00 | 0 | 0 | 556 | $0.968 | $0.097 |
| hybrid_jev | 92% | 12/15 | 3/3 | 0.41 | 1.03 | 1251 | 265 | 373 | $0.640 | $0.111 |
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
| preferred_language | 89% | 89% | 100% | 89% | 0% |
| hebrew_fluency | 100% | 50% | 50% | 100% | 78% |
| terminology_comfort | 50% | 50% | 50% | 89% | 78% |
| text_categories | 78% | 67% | 78% | 89% | 0% |
| answer_style | 67% | 100% | 100% | 86% | 100% |
| learning_habits | 86% | 86% | 100% | 100% | 57% |

## Failed checks
| strategy | persona | check | detail |
|---|---|---|---|
| realtime | david-daf | not_flipped (preferred_language) | flipped to 'en' in snapshot 9 |
| realtime | sarah-paste | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 3 |
| realtime | noam-stale | expired (text_categories) | final ['Midrash', 'Mishnah', 'Commentary']; stale values left: ['Midrash'] |
| nightly | rachel-anon | available_by_turn (preferred_language) | preferred_language after rachel-1: missing |
| nightly | rachel-anon | available_by_turn (hebrew_fluency) | hebrew_fluency after rachel-3: missing |
| nightly | rachel-anon | available_by_turn (terminology_comfort) | terminology_comfort after rachel-4: missing |
| nightly | sarah-paste | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 5 |
| nightly | noam-stale | expired (text_categories) | final ['Tanakh', 'Midrash', 'Mishnah']; stale values left: ['Midrash'] |
| hybrid | rachel-anon | available_by_turn (hebrew_fluency) | hebrew_fluency after rachel-3: missing |
| hybrid | rachel-anon | available_by_turn (terminology_comfort) | terminology_comfort after rachel-4: missing |
| hybrid | david-daf | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 9 |
| hybrid | miriam-evolving | changed_fact (hebrew_fluency) | final None; earlier snapshot in old ['none']: True |
| hybrid | sarah-paste | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 3 |
| hybrid | noam-stale | expired (text_categories) | final ['Mishnah', 'Midrash']; stale values left: ['Midrash'] |
| hybrid_jev | david-daf | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 9 |
| hybrid_jev | tamar-instruction | never_stored | scope profile: 'Reform' in evidence of text_categories |
| hybrid_jev | sarah-paste | not_flipped (preferred_language) | flipped to 'mixed' in snapshot 3 |
| explicit | rachel-anon | available_by_turn (preferred_language) | preferred_language after rachel-1: missing |
| explicit | rachel-anon | available_by_turn (hebrew_fluency) | hebrew_fluency after rachel-3: missing |
| explicit | rachel-anon | available_by_turn (terminology_comfort) | terminology_comfort after rachel-4: missing |
| explicit | david-daf | not_flipped (preferred_language) | unset in snapshot 9 (turn) |
| explicit | miriam-evolving | changed_fact (hebrew_fluency) | final 'none'; earlier snapshot in old ['none']: True |
| explicit | miriam-evolving | changed_fact (terminology_comfort) | final 'new'; earlier snapshot in old ['new']: True |
| explicit | sarah-paste | not_flipped (preferred_language) | unset in snapshot 3 (turn) |

## Per persona (slot verdicts, correct/total scored)
| persona | trap | realtime | nightly | hybrid | hybrid_jev | explicit |
|---|---|---|---|---|---|---|
| rachel-anon | in_session | 5/6 | 3/5 | 4/6 | 6/6 | 1/5 |
| rebecca-veteran | stated_lock | 4/6 | 6/6 | 6/6 | 5/6 | 3/6 |
| david-daf | one_off | 4/5 | 1/5 | 5/5 | 5/5 | 2/5 |
| miriam-evolving | changed_fact | 5/5 | 5/5 | 4/5 | 5/5 | 0/4 |
| yossi-sensitive | sensitive | 3/4 | 3/4 | 3/4 | 5/6 | 2/4 |
| tamar-instruction | instruction | 4/4 | 4/4 | 3/4 | 5/5 | 4/6 |
| avi-mixed | mixed_language | 3/5 | 5/6 | 6/6 | 6/6 | 3/5 |
| sarah-paste | one_off | 5/6 | 3/5 | 4/6 | 4/6 | 3/5 |
| noam-stale | stale | 2/4 | 3/5 | 2/4 | 4/4 | 2/5 |
