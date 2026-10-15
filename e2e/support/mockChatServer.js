/**
 * Mock chat server for the widget behaviour tests.
 *
 * mockChatServer(context, options) installs a fake chat backend for every tab
 * in the browser context: context.route hands each request the widget makes to
 * /api/** to this file instead of the network, and it is answered here with
 * route.fulfill. Because it is per context, tabs share it. Tests never read
 * anything from it; they only check what the visitor sees.
 *
 * It must behave like this:
 *
 * - It answers questions. (Done.)
 * - A logged-out visitor (a request with `anonId`) gets getFreeAnswerLimit()
 *   answers (see ./freeAnswerLimit.js), like the real server: each answer
 *   carries `anonResponsesRemaining`, and the send after the last one gets
 *   403 {"error": "login_required"}. (Done.)
 * - It keeps each visitor's conversation. After the visitor signs up or logs
 *   in, that conversation is in the user's chat history. (Later step.)
 *
 * Options:
 * - longAnswers: answers taller than the message list, for scrolling checks.
 *   The last paragraph of answer n is `End of answer n.`
 */

import { getFreeAnswerLimit } from './freeAnswerLimit.js';

const LONG_ANSWER_PARAGRAPHS = 30;

/**
 * @param {import('@playwright/test').BrowserContext} context
 * @param {{ longAnswers?: boolean }} [options]
 */
export async function mockChatServer(context, { longAnswers = false } = {}) {
  const limit = getFreeAnswerLimit();
  let answersGiven = 0;
  const answersByAnonId = new Map();

  function answerText(n) {
    if (!longAnswers) {
      return `Answer ${n}.`;
    }
    const paragraphs = Array.from({ length: LONG_ANSWER_PARAGRAPHS }, (_, i) => `Paragraph ${i + 1}.`);
    return [`Answer ${n}.`, ...paragraphs, `End of answer ${n}.`].join('\n\n');
  }

  function answer(route, body) {
    let remaining;
    if (body.anonId) {
      const used = answersByAnonId.get(body.anonId) ?? 0;
      if (used >= limit) {
        return route.fulfill({ status: 403, json: { error: 'login_required' } });
      }
      answersByAnonId.set(body.anonId, used + 1);
      remaining = limit - (used + 1);
    }

    answersGiven += 1;
    const n = answersGiven;
    const message = {
      messageId: `msg_answer_${n}`,
      sessionId: body.sessionId,
      timestamp: new Date().toISOString(),
      markdown: answerText(n),
      traceId: `trace_${n}`,
      toolCalls: [],
      stats: {},
    };
    if (remaining !== undefined) {
      message.anonResponsesRemaining = remaining;
    }
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseEvent('progress', { type: 'status', text: 'Thinking...' }) + sseEvent('message', message),
    });
  }

  await context.route('**/api/**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path.endsWith('/chat/stream')) {
      return answer(route, request.postDataJSON());
    }
    if (path.endsWith('/v2/chat/recover')) {
      return route.fulfill({ json: { status: 'failed' } });
    }
    if (path.endsWith('/v2/prompts/defaults')) {
      return route.fulfill({ json: { corePromptSlug: '', labs: false } });
    }
    if (path.includes('/history/conversations')) {
      return route.fulfill({ json: { conversations: [], hasMore: false, total: 0 } });
    }
    if (path.endsWith('/history')) {
      return route.fulfill({ json: { messages: [], hasMore: false, session: null } });
    }
    // client-event, cancel, feedback: accept and ignore.
    return route.fulfill({ json: { success: true } });
  });
}

function sseEvent(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
