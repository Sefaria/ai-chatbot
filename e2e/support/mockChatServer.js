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
 * - Personalization lab (option `lab`): POST /v2/personalization/status says whether the
 *   visitor may use the lab override, and POST /v2/personalization/profile answers the
 *   profile actions (get, set, release, run_job, apply_candidate, discard_candidate,
 *   seed_persona) from a small in-memory profile. A chat request with
 *   context.personalizationMode "off" gets the answer `Plain answer.` and is not kept.
 *
 * Options:
 * - lab: { allowed: boolean, slots?: slot[] } turns the lab on for the visitor.
 * - longAnswers: answers taller than the message list, for scrolling checks.
 *   The last paragraph of answer n is `End of answer n.`
 */

import { getFreeAnswerLimit } from './freeAnswerLimit.js';

const LONG_ANSWER_PARAGRAPHS = 30;

/**
 * @param {import('@playwright/test').BrowserContext} context
 * @param {{ longAnswers?: boolean }} [options]
 */
export async function mockChatServer(context, { longAnswers = false, failedClaims = 0, lab = null } = {}) {
  const labProfile = lab ? createLabProfile(lab) : null;
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
    if (body.context?.personalizationMode === 'off') {
      const message = {
        messageId: `msg_plain_${body.messageId}`,
        sessionId: body.sessionId,
        timestamp: new Date().toISOString(),
        markdown: 'Plain answer.',
        toolCalls: [],
        stats: {},
      };
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseEvent('message', message),
      });
    }
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
    if (path.endsWith('/v2/personalization/status')) {
      return route.fulfill({ json: { overrideAllowedForYou: !!lab?.allowed, mode: 'lab' } });
    }
    if (path.endsWith('/v2/personalization/profile')) {
      if (!labProfile || !lab.allowed) return route.fulfill({ status: 404, json: { error: 'not_found' } });
      return labProfile.handle(route, request.postDataJSON());
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

const DEFAULT_LAB_SLOTS = [
  { slot: 'learning_level', value: 'beginner', status: 'stated', evidence: ['e1', 'e2'], updatedAt: '2026-11-10T09:00:00Z', expiresAt: null, slotSetVersion: 1 },
  { slot: 'interests', value: ['Talmud', 'ethics'], status: 'inferred', evidence: ['e3'], updatedAt: '2026-11-10T09:05:00Z', expiresAt: null, slotSetVersion: 1 },
];

/** An in-memory profile for the lab actions. Synthetic data only. */
function createLabProfile({ slots = DEFAULT_LAB_SLOTS } = {}) {
  let revision = 1;
  let current = slots.map((slot) => ({ ...slot }));
  let candidate = null;

  const payload = () => ({
    storeUserId: 'lab-user',
    revision,
    slots: current,
    settings: {},
    proposals: [],
    observations: [],
    quarantinedObservations: [],
  });

  return {
    handle(route, body) {
      switch (body.action) {
        case 'get':
          return route.fulfill({ json: payload() });
        case 'set': {
          current = current.map((s) => (s.slot === body.slot ? { ...s, value: body.value, status: 'stated' } : s));
          revision += 1;
          return route.fulfill({ json: payload() });
        }
        case 'release': {
          current = current.filter((s) => s.slot !== body.slot);
          revision += 1;
          return route.fulfill({ json: payload() });
        }
        case 'run_job':
          candidate = {
            status: 'candidate',
            changes: [{ slot: 'learning_level', old: 'beginner', new: 'intermediate', reason: 'Asked about commentaries twice' }],
            conflicts: [],
            observationsRead: 7,
            tokens: 1234,
            costUsd: 0.0123,
            revision,
          };
          return route.fulfill({ json: candidate });
        case 'apply_candidate': {
          if (!candidate) return route.fulfill({ status: 409, json: { error: 'stale_revision' } });
          candidate.changes.forEach((c) => {
            current = current.map((s) => (s.slot === c.slot ? { ...s, value: c.new } : s));
          });
          candidate = null;
          revision += 1;
          return route.fulfill({ json: { applied: true, profile: payload() } });
        }
        case 'discard_candidate':
          candidate = null;
          return route.fulfill({ json: { discarded: true } });
        case 'seed_persona':
          return route.fulfill({ json: { seeded: body.turns.length - (body.turns.filter((t) => t.user.includes('SKIPME')).length), skipped: body.turns.filter((t) => t.user.includes('SKIPME')).length } });
        default:
          return route.fulfill({ status: 400, json: { error: 'unknown_action' } });
      }
    },
  };
}
