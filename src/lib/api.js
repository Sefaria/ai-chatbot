/**
 * API client for chatbot communication
 */

import { generateMessageId } from './session.js';

/**
 * @typedef {Object} MessageContext
 * @property {string} pageUrl - Current page URL
 * @property {string} locale - User locale
 * @property {string} clientVersion - Widget version
 * @property {string} [origin] - Origin identifier for Braintrust trace tagging
 * @property {boolean} [isStaff] - Whether the user is a Sefaria staff member
 * @property {boolean} [forceStreamBreakBeforeFinal] - Testing-only forced stream break hook
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
const STREAM_RECOVERY_TIMEOUT_MS = 20_000;

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function buildMessageContext(origin = '', isStaff = false) {
  /** @type {MessageContext} */
  const context = {
    pageUrl: window.location.href,
    locale: navigator.language || 'en',
    clientVersion: CLIENT_VERSION
  };
  if (origin !== undefined && origin !== '') {
    context.origin = origin;
  }
  if (isStaff) {
    context.isStaff = true;
  }
  return context;
}

function shouldForceStreamBreak(text) {
  return typeof text === 'string' && text.includes('#test-stream-break');
}

async function reportClientStreamEvent(
  apiBaseUrl,
  {
    userId,
    sessionId,
    messageId,
    event,
    error = '',
    context,
    timestamp = new Date().toISOString()
  }
) {
  try {
    await fetch(`${apiBaseUrl}/v2/chat/client-event`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        userId,
        sessionId,
        messageId,
        timestamp,
        event,
        error,
        context
      })
    });
  } catch {
    // Avoid cascading client-side telemetry failures into the chat flow.
  }
}

async function recoverStreamMessage(apiBaseUrl, { userId, sessionId, messageId, context }) {
  const fallbackDeadline = Date.now() + STREAM_RECOVERY_TIMEOUT_MS;
  let heartbeatDeadline = null;
  let timeoutReason = 'recovery_timeout';

  while (true) {
    try {
      const response = await fetch(`${apiBaseUrl}/v2/chat/recover`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          userId,
          sessionId,
          messageId
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (data.status === 'complete' || data.status === 'failed') {
          return data;
        }
        if (
          typeof data.heartbeatTimeoutMs === 'number' &&
          typeof data.heartbeatAgeMs === 'number'
        ) {
          heartbeatDeadline =
            Date.now() + Math.max(data.heartbeatTimeoutMs - data.heartbeatAgeMs, 0);
        }
        if (data.status === 'stale') {
          timeoutReason = 'heartbeat_stale';
          break;
        }
      }
    } catch {
      // Keep polling until the relevant deadline expires.
    }

    const activeDeadline = heartbeatDeadline ?? fallbackDeadline;
    if (Date.now() >= activeDeadline) {
      timeoutReason = heartbeatDeadline ? 'heartbeat_timeout' : 'recovery_timeout';
      break;
    }

    await sleep(1000);
  }

  await reportClientStreamEvent(apiBaseUrl, {
    userId,
    sessionId,
    messageId,
    event: 'stream_recovery_timeout',
    error: timeoutReason,
    context
  });

  return null;
}

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
    context: buildMessageContext()
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
 * Send a chat message with streaming progress
 * @param {string} apiBaseUrl - Base URL for API
 * @param {string} userId - User ID
 * @param {string} sessionId - Session ID
 * @param {string} text - Message text
 * @param {StreamCallbacks} callbacks - Streaming callbacks
 * @param {PromptSlugs} [promptSlugs] - Prompt slug overrides
 * @param {string} [origin] - Origin identifier for Braintrust trace tagging
 * @param {boolean} [isStaff] - Whether the user is a staff/moderator, for trace tagging
 * @param {{messageId?: string, timestamp?: string}} [requestMetadata] - Stable request identifiers
 * @param {{signal?: AbortSignal}} [options] - Optional request options (e.g. signal to abort the stream)
 * @returns {Promise<ChatResponse>}
 */
export async function sendMessageStream(
  apiBaseUrl,
  userId,
  sessionId,
  text,
  callbacks = {},
  promptSlugs = null,
  origin = '',
  isStaff = false,
  requestMetadata = null,
  options = {}
) {
  const { signal = null } = options;
  const messageId = requestMetadata?.messageId || generateMessageId();
  const timestamp = requestMetadata?.timestamp || new Date().toISOString();

  const context = buildMessageContext(origin, isStaff);
  if (shouldForceStreamBreak(text)) {
    context.forceStreamBreakBeforeFinal = true;
  }

  /** @type {SendMessagePayload} */
  const payload = {
    userId,
    sessionId,
    messageId,
    timestamp,
    text,
    context
  };

  if (promptSlugs) {
    payload.promptSlugs = promptSlugs;
  }
  
  let response;
  try {
    response = await fetch(`${apiBaseUrl}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload),
      signal
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    await reportClientStreamEvent(apiBaseUrl, {
      userId,
      sessionId,
      messageId,
      event: 'stream_fetch_failed',
      error: error?.message || 'fetch failed',
      context,
      timestamp
    });

    const recovered = await recoverStreamMessage(apiBaseUrl, {
      userId,
      sessionId,
      messageId,
      context
    });
    if (recovered?.status === 'complete') {
      if (callbacks.onMessage) {
        callbacks.onMessage(recovered.message);
      }
      return recovered.message;
    }
    throw error;
  }

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

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalMessage = null;
  let streamError = '';
  let streamReadError = null;
  
  try {
    while (true) {
      const { done, value } = await reader.read();
      
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      
      // Process complete SSE events
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer
      
      let currentEvent = null;
      let currentData = '';
      
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6);
          
          if (currentEvent && currentData) {
            try {
              const data = JSON.parse(currentData);
              
              if (currentEvent === 'progress' && callbacks.onProgress) {
                callbacks.onProgress(data);
              } else if (currentEvent === 'message') {
                finalMessage = {
                  messageId: data.messageId,
                  sessionId: data.sessionId,
                  timestamp: data.timestamp,
                  markdown: data.markdown,
                  traceId: data.traceId,
                  toolCalls: data.toolCalls,
                  stats: data.stats,
                  session: data.session,
                  recovered: data.recovered || false
                };
                if (callbacks.onMessage) {
                  callbacks.onMessage(finalMessage);
                }
              } else if (currentEvent === 'error') {
                streamError = data.error || 'Stream error';
                await reportClientStreamEvent(apiBaseUrl, {
                  userId,
                  sessionId,
                  messageId,
                  event: 'stream_error_event',
                  error: streamError,
                  context,
                  timestamp
                });
                if (callbacks.onError) {
                  callbacks.onError(streamError);
                }
              }
            } catch (e) {
              console.warn('[lc-chatbot] Failed to parse SSE data:', e);
            }
          }
          
          currentEvent = null;
          currentData = '';
        } else if (line.startsWith(':')) {
          // Comment (keepalive), ignore
        } else if (line === '') {
          // Empty line, reset
          currentEvent = null;
          currentData = '';
        }
      }
    }
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    streamReadError = error;
    await reportClientStreamEvent(apiBaseUrl, {
      userId,
      sessionId,
      messageId,
      event: 'stream_read_failed',
      error: error?.message || 'reader failed',
      context,
      timestamp
    });
  }
  
  if (!finalMessage) {
    await reportClientStreamEvent(apiBaseUrl, {
      userId,
      sessionId,
      messageId,
      event: 'stream_missing_final_message',
      error: streamError || streamReadError?.message || '',
      context,
      timestamp
    });

    const recovered = await recoverStreamMessage(apiBaseUrl, {
      userId,
      sessionId,
      messageId,
      context
    });
    if (recovered?.status === 'complete') {
      await reportClientStreamEvent(apiBaseUrl, {
        userId,
        sessionId,
        messageId,
        event: 'stream_recovery_succeeded',
        context,
        timestamp
      });
      if (callbacks.onMessage) {
        callbacks.onMessage(recovered.message);
      }
      return recovered.message;
    }
    if (recovered?.status === 'failed') {
      const error = new Error(recovered.error || streamError || 'Recovered failed response');
      error.code = 'stream_recovered_failed';
      throw error;
    }

    if (streamReadError) {
      throw streamReadError;
    }

    throw new Error(streamError || 'Stream ended without message');
  }
  
  return finalMessage;
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
