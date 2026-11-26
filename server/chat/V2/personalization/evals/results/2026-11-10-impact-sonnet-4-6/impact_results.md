# Impact evaluation
Date 2026-11-10, llm `claude-code`, answers `claude-sonnet-4-6`, judge `claude-sonnet-5-5`, 12 cases x 3 profiles.

## Preference for the personalized answer (blind pairwise)
| group | n | personalized | tie | baseline | rate |
|---|---|---|---|---|---|
| overall | 36 | 35 | 1 | 0 | 97% |
| profile beginner_en | 12 | 11 | 1 | 0 | 92% |
| profile daf_he | 12 | 12 | 0 | 0 | 100% |
| profile dvar_torah | 12 | 12 | 0 | 0 | 100% |
| expect_change = true | 27 | 27 | 0 | 0 | 100% |
| expect_change = false | 9 | 8 | 1 | 0 | 89% |

## Rubric (personalized answers)
| metric | value |
|---|---|
| uses_profile (mean, 0-2) | 1.6 |
| language_matches_profile | 100% |
| narrows_sources | 3% |
| creepy | 0% |
| mentions_profile_explicitly | 28% |
| difference_explained_by_slots | 94% |
| narrows_sources on multivocality cases | 0% |

## Language match per profile
- beginner_en: 100%
- daf_he: 100%
- dvar_torah: 100%

## Length and cost
- Average characters: 1601.1 personalized, 2012.0 baseline
- Average added input tokens: 146.8; added output tokens: 43.4
