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
 * Send a chat message with streaming progress
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
  
  const response = await fetch(`${apiBaseUrl}/chat/stream`, {
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

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalMessage = null;
  
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
                session: data.session
              };
              if (callbacks.onMessage) {
                callbacks.onMessage(finalMessage);
              }
            } else if (currentEvent === 'guardrail' && data.blocked) {
              // Guardrail rejection: construct a synthetic ChatResponse so the
              // UI renders the block message as a normal assistant reply.
              // No server-side messageId exists, so we generate a client-side one.
              finalMessage = {
                messageId: `guardrail_${Date.now()}`,
                sessionId: data.sessionId || '',
                timestamp: new Date().toISOString(),
                markdown: data.message,
                guardrail: true,
                guardrailType: data.type || 'guardrail'
              };
              if (callbacks.onMessage) {
                callbacks.onMessage(finalMessage);
              }
            } else if (currentEvent === 'error' && callbacks.onError) {
              callbacks.onError(data.error);
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
  
  if (!finalMessage) {
    throw new Error('Stream ended without message');
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
