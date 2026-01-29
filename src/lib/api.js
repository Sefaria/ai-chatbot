/**
 * API client for chatbot communication
 */

import { generateMessageId } from './session.js';

/**
 * @typedef {Object} MessageContext
 * @property {string} pageUrl - Current page URL
 * @property {string} locale - User locale
 * @property {string} clientVersion - Widget version
 */

/**
 * @typedef {Object} SendMessagePayload
 * @property {string} userId
 * @property {string} sessionId
 * @property {string} messageId
 * @property {string} timestamp
 * @property {string} text
 * @property {MessageContext} context
 * @property {PromptSlugs} [promptSlugs]
 */

/**
 * @typedef {Object} SessionInfo
 * @property {number} turnCount
 */

/**
 * @typedef {Object} ChatResponse
 * @property {string} messageId
 * @property {string} sessionId
 * @property {string} timestamp
 * @property {string} markdown
 * @property {string} [traceId]
 * @property {SessionInfo} [session]
 */

/**
 * @typedef {Object} PromptSlugs
 * @property {string} [corePromptSlug]
 */

/**
 * @typedef {Object} HistoryMessage
 * @property {string} messageId
 * @property {string} sessionId
 * @property {string} userId
 * @property {'user' | 'assistant'} role
 * @property {string} content
 * @property {string} timestamp
 */

const CLIENT_VERSION = '1.0.0';

/**
 * Send a chat message to the server
 * @param {string} apiBaseUrl - Base URL for API
 * @param {string} userId - User ID
 * @param {string} sessionId - Session ID
 * @param {string} text - Message text
 * @returns {Promise<ChatResponse>}
 */
export async function sendMessage(apiBaseUrl, userId, sessionId, text) {
  const messageId = generateMessageId();
  const timestamp = new Date().toISOString();
  
  /** @type {SendMessagePayload} */
  const payload = {
    userId,
    sessionId,
    messageId,
    timestamp,
    text,
    context: {
      pageUrl: window.location.href,
      locale: navigator.language || 'en',
      clientVersion: CLIENT_VERSION
    }
  };
  
  const response = await fetch(`${apiBaseUrl}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    // Try to parse error response
    let errorData = null;
    try {
      errorData = await response.json();
    } catch {
      // Ignore JSON parse errors
    }

    const error = new Error(errorData?.message || `Chat request failed: ${response.status}`);
    error.status = response.status;
    error.code = errorData?.error;
    throw error;
  }

  return response.json();
}

/**
 * @typedef {Object} ProgressEvent
 * @property {string} type - 'status' | 'tool_start' | 'tool_end'
 * @property {string} [text] - Status text
 * @property {string} [toolName] - Tool being called
 * @property {Object} [toolInput] - Tool input parameters
 * @property {string} [description] - Human-readable description
 * @property {boolean} [isError] - Whether tool errored
 * @property {string} [outputPreview] - Preview of tool output
 */

/**
 * @typedef {Object} StreamCallbacks
 * @property {function(ProgressEvent): void} [onProgress] - Progress update callback
 * @property {function(ChatResponse): void} [onMessage] - Final message callback
 * @property {function(string): void} [onError] - Error callback
 */

/**
 * Send a chat message with websocket progress updates
 * @param {string} apiBaseUrl - Base URL for API
 * @param {string} userId - User ID
 * @param {string} sessionId - Session ID
 * @param {string} text - Message text
 * @param {StreamCallbacks} callbacks - Streaming callbacks
 * @param {PromptSlugs} [promptSlugs] - Prompt slug overrides
 * @returns {Promise<ChatResponse>}
 */
export async function sendMessageStream(
  apiBaseUrl,
  userId,
  sessionId,
  text,
  callbacks = {},
  promptSlugs = null
) {
  const messageId = generateMessageId();
  const timestamp = new Date().toISOString();
  
  /** @type {SendMessagePayload} */
  const payload = {
    userId,
    sessionId,
    messageId,
    timestamp,
    text,
    context: {
      pageUrl: window.location.href,
      locale: navigator.language || 'en',
      clientVersion: CLIENT_VERSION
    }
  };

  if (promptSlugs) {
    payload.promptSlugs = promptSlugs;
  }

  const wsUrl = buildWebSocketUrl(apiBaseUrl);
  let finalMessage = null;

  return new Promise((resolve, reject) => {
    let settled = false;
    const socket = new WebSocket(wsUrl);

    const fail = (error) => {
      if (settled) return;
      settled = true;
      try {
        socket.close();
      } catch {
        // ignore close errors
      }
      reject(error);
    };

    socket.onopen = () => {
      socket.send(JSON.stringify(payload));
    };

    socket.onmessage = (event) => {
      let data = null;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        console.warn('[lc-chatbot] Failed to parse websocket data:', e);
        return;
      }

      const eventType = data.event || data.type;
      if (eventType === 'progress' && callbacks.onProgress) {
        callbacks.onProgress(data);
      } else if (eventType === 'message') {
        finalMessage = {
          messageId: data.messageId,
          sessionId: data.sessionId,
          timestamp: data.timestamp,
          markdown: data.markdown,
          traceId: data.traceId,
          toolCalls: data.toolCalls,
          stats: data.stats,
          session: data.session
        };
        if (callbacks.onMessage) {
          callbacks.onMessage(finalMessage);
        }
        settled = true;
        socket.close();
        resolve(finalMessage);
      } else if (eventType === 'error') {
        if (callbacks.onError) {
          callbacks.onError(data.error);
        }
        fail(new Error(data.error || 'Chat request failed'));
      }
    };

    socket.onerror = () => {
      if (!settled) {
        fail(new Error('WebSocket error'));
      }
    };

    socket.onclose = () => {
      if (!settled) {
        fail(new Error('Stream ended without message'));
      }
    };
  });
}

function buildWebSocketUrl(apiBaseUrl) {
  const base = apiBaseUrl.startsWith('http')
    ? new URL(apiBaseUrl)
    : new URL(apiBaseUrl, window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = base.pathname.replace(/\/$/, '') + '/ws/v2/chat';
  base.search = '';
  base.hash = '';
  return base.toString();
}

/**
 * Fetch default prompt slugs from the server.
 * @param {string} apiBaseUrl - Base URL for API
 * @returns {Promise<PromptSlugs>}
 */
export async function fetchPromptDefaults(apiBaseUrl) {
  const response = await fetch(`${apiBaseUrl}/v2/prompts/defaults`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json'
    }
  });

  if (!response.ok) {
    throw new Error(`Failed to load prompt defaults: ${response.status}`);
  }

  return response.json();
}

/**
 * Send user feedback for a trace.
 * @param {string} apiBaseUrl - Base URL for API
 * @param {Object} payload - Feedback payload
 * @returns {Promise<{ success: boolean }>}
 */
export async function sendFeedback(apiBaseUrl, payload) {
  const response = await fetch(`${apiBaseUrl}/v2/chat/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    let errorData = null;
    try {
      errorData = await response.json();
    } catch {
      // Ignore JSON parse errors
    }
    const error = new Error(errorData?.error || `Feedback request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }

  return response.json();
}

/**
 * Load conversation history
 * @param {string} apiBaseUrl - Base URL for API
 * @param {string} userId - User ID
 * @param {string} sessionId - Session ID
 * @param {string} [before] - Load messages before this timestamp
 * @param {number} [limit=20] - Number of messages to load
 * @returns {Promise<{ messages: HistoryMessage[], hasMore: boolean, session: SessionInfo | null }>}
 */
export async function loadHistory(apiBaseUrl, userId, sessionId, before = null, limit = 20) {
  const params = new URLSearchParams({
    userId,
    sessionId,
    limit: String(limit)
  });
  
  if (before) {
    params.set('before', before);
  }
  
  const response = await fetch(`${apiBaseUrl}/history?${params}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json'
    }
  });
  
  if (!response.ok) {
    const error = new Error(`History request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  
  const data = await response.json();
  
  return {
    messages: data.messages || [],
    hasMore: data.hasMore ?? false,
    session: data.session || null
  };
}
