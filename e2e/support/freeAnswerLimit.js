/**
 * How many free answers a logged-out visitor gets in the widget tests.
 *
 * This is the limit the mock chat server is configured with, not the real one:
 * in production an admin sets it in Sefaria's remote config
 * (`feature.chatbot.anonymous_free_answers`), and the chat server reads it from
 * there. The widget tests never talk to Sefaria, so the mock and the tests
 * both take the number from here.
 */
export function getFreeAnswerLimit() {
  return 2;
}
