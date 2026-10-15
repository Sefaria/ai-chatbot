/**
 * How many free answers a logged-out visitor gets.
 *
 * An admin will set this number; where it lives is not decided yet. Tests and
 * the mock chat server read it from here, so choosing the real source changes
 * only this function.
 *
 * TODO(sc-47704): read the limit from wherever the admin sets it.
 */
export function getFreeAnswerLimit() {
  return 2;
}
