/**
 * Mock chat server for the widget behaviour tests.
 *
 * NOT IMPLEMENTED YET. It is written together with the server side of
 * sc-47704, once the way the widget and the server talk is decided. Until
 * then every test that uses it fails with the error below.
 *
 * mockChatServer(context, options) installs a fake chat backend for every tab
 * in the browser context (via context.route on the widget's API URLs), so tabs
 * share it. Tests never read anything from it; they only check what the
 * visitor sees. It must behave like this:
 *
 * - It answers questions.
 * - A logged-out visitor gets getFreeAnswerLimit() answers (see
 *   ./freeAnswerLimit.js). After that it refuses, in whatever way the widget
 *   turns into the login prompt.
 * - It keeps each visitor's conversation. After the visitor signs up or logs
 *   in, that conversation is in the user's chat history.
 *
 * Options:
 * - longAnswers: answers taller than the message list, for scrolling checks.
 *   The last paragraph of answer n is `End of answer n.`
 *
 * @param {import('@playwright/test').BrowserContext} context
 * @param {{ longAnswers?: boolean }} [options]
 */
export async function mockChatServer(context, options = {}) {
  throw new Error('Mock chat server not implemented yet — written with the server side of sc-47704');
}
