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
 * - It keeps each visitor's conversations, with an owner (the anonId or the
 *   user token). A question in a session owned by someone else is refused
 *   with a 500, like the real server. After login the widget claims the
 *   anonymous chats (POST /v2/anon/claim), which moves them to the user, so
 *   they're in the user's chat history. (Done.)
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
export async function mockChatServer(context, { longAnswers = false, failedClaims = 0 } = {}) {
  // failedClaims: how many claims fail (500) before claims work again.
  let claimsToFail = failedClaims;
  const limit = getFreeAnswerLimit();
  let answersGiven = 0;
  /** sessionId → { owner, messages } — owner is the anonId or the user token. */
  const sessions = new Map();

  function answerText(n) {
    if (!longAnswers) {
      return `Answer ${n}.`;
    }
    const paragraphs = Array.from({ length: LONG_ANSWER_PARAGRAPHS }, (_, i) => `Paragraph ${i + 1}.`);
    return [`Answer ${n}.`, ...paragraphs, `End of answer ${n}.`].join('\n\n');
  }

  function answersOwnedBy(owner) {
    let count = 0;
    for (const session of sessions.values()) {
      if (session.owner === owner) {
        count += session.messages.filter((m) => m.role === 'assistant').length;
      }
    }
    return count;
  }

  function questionsSentBy(owner) {
    let count = 0;
    for (const session of sessions.values()) {
      if (session.owner === owner) {
        count += session.messages.filter((m) => m.role === 'user').length;
      }
    }
    return count;
  }

  function sessionsOwnedBy(owner) {
    return [...sessions.entries()].filter(([, session]) => session.owner === owner);
  }

  function answer(route, body) {
    const owner = body.userId || body.anonId;
    let remaining;
    if (!body.userId) {
      const used = answersOwnedBy(owner);
      if (used >= limit) {
        return route.fulfill({ status: 403, json: { error: 'login_required' } });
      }
      remaining = limit - (used + 1);
    }

    // Like the real server: a session that belongs to someone else is refused.
    const existing = sessions.get(body.sessionId);
    if (existing && existing.owner !== owner) {
      return route.fulfill({ status: 500, body: 'Session belongs to different user' });
    }
    const session = existing ?? { owner, messages: [] };
    sessions.set(body.sessionId, session);

    answersGiven += 1;
    const n = answersGiven;
    const timestamp = new Date().toISOString();
    const message = {
      messageId: `msg_answer_${n}`,
      sessionId: body.sessionId,
      timestamp,
      markdown: answerText(n),
      traceId: `trace_${n}`,
      toolCalls: [],
      stats: {},
    };
    session.messages.push(
      { messageId: body.messageId, sessionId: body.sessionId, role: 'user', content: body.text, timestamp, status: 'success' },
      { messageId: message.messageId, sessionId: body.sessionId, role: 'assistant', content: message.markdown, timestamp, status: 'success' },
    );
    if (remaining !== undefined) {
      message.anonResponsesRemaining = remaining;
      // Like the real server: at the limit, how many questions the visitor sent.
      if (remaining === 0) {
        message.anonMessagesSent = questionsSentBy(owner);
      }
    }

    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseEvent('progress', { type: 'status', text: 'Thinking...' }) + sseEvent('message', message),
    });
  }

  function claim(route, body) {
    if (claimsToFail > 0) {
      claimsToFail -= 1;
      return route.fulfill({ status: 500, body: 'Claim failed' });
    }
    const claimed = sessionsOwnedBy(body.anonId);
    claimed.forEach(([, session]) => {
      session.owner = body.userId;
    });
    return route.fulfill({ json: { claimedSessions: claimed.length } });
  }

  function conversationSummary(sessionId, session) {
    return {
      sessionId,
      title: session.messages.find((m) => m.role === 'user')?.content ?? '',
      createdAt: session.messages[0]?.timestamp,
      lastActivity: session.messages.at(-1)?.timestamp,
      messageCount: session.messages.length,
      turnCount: session.messages.filter((m) => m.role === 'assistant').length,
    };
  }

  function conversationList(route, query) {
    const conversations = sessionsOwnedBy(query.get('userId'))
      .reverse()
      .map(([sessionId, session]) => conversationSummary(sessionId, session));
    return route.fulfill({ json: { conversations, hasMore: false, total: conversations.length } });
  }

  function conversationDetail(route, sessionId, query) {
    const session = sessions.get(sessionId);
    if (!session || session.owner !== query.get('userId')) {
      return route.fulfill({ status: 404, json: { error: 'conversation_not_found' } });
    }
    return route.fulfill({
      json: {
        conversation: conversationSummary(sessionId, session),
        messages: session.messages,
        session: { turnCount: conversationSummary(sessionId, session).turnCount },
      },
    });
  }

  function sessionHistory(route, query) {
    const session = sessions.get(query.get('sessionId'));
    const owned = session && session.owner === query.get('userId');
    return route.fulfill({
      json: {
        messages: owned ? session.messages : [],
        hasMore: false,
        session: owned ? { turnCount: conversationSummary('', session).turnCount } : null,
      },
    });
  }

  await context.route('**/api/**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path.endsWith('/chat/stream')) {
      return answer(route, request.postDataJSON());
    }
    if (path.endsWith('/v2/anon/claim')) {
      return claim(route, request.postDataJSON());
    }
    if (path.endsWith('/v2/chat/recover')) {
      return route.fulfill({ json: { status: 'failed' } });
    }
    if (path.endsWith('/v2/prompts/defaults')) {
      return route.fulfill({ json: { corePromptSlug: '', labs: false } });
    }
    const detail = path.match(/\/history\/conversations\/([^/]+)$/);
    if (detail) {
      return conversationDetail(route, decodeURIComponent(detail[1]), url.searchParams);
    }
    if (path.endsWith('/history/conversations')) {
      return conversationList(route, url.searchParams);
    }
    if (path.endsWith('/history')) {
      return sessionHistory(route, url.searchParams);
    }
    // client-event, cancel, feedback: accept and ignore.
    return route.fulfill({ json: { success: true } });
  });
}

function sseEvent(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
