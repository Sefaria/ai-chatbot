/**
 * Session management utilities
 */

import { getStorage, setStorage, STORAGE_KEYS } from './storage.js';

// Session timeout in minutes
const SESSION_TIMEOUT_MINUTES = 30;

/**
 * Generate a unique session ID
 * @returns {string} Session ID
 */
export function generateSessionId() {
  return 'sess_' + crypto.randomUUID();
}

/**
 * Generate a unique message ID
 * @returns {string} Message ID
 */
export function generateMessageId() {
  return 'msg_' + crypto.randomUUID();
}

/**
 * Check if session has expired
 * @param {string} lastActivity - ISO timestamp of last activity
 * @returns {boolean} True if expired
 */
function isSessionExpired(lastActivity) {
  if (!lastActivity) return true;
  
  const lastTime = new Date(lastActivity).getTime();
  const now = Date.now();
  const diffMinutes = (now - lastTime) / (1000 * 60);
  
  return diffMinutes > SESSION_TIMEOUT_MINUTES;
}

/**
 * Get or create a session ID
 * @param {boolean} forceNew - Force creation of new session
 * @returns {{ sessionId: string, isNew: boolean }}
 */
export function getOrCreateSession(forceNew = false) {
  const stored = getStorage(STORAGE_KEYS.SESSION, null);
  
  if (forceNew || !stored || isSessionExpired(stored.lastActivity)) {
    const sessionId = generateSessionId();
    const session = {
      sessionId,
      lastActivity: new Date().toISOString()
    };
    setStorage(STORAGE_KEYS.SESSION, session);
    return { sessionId, isNew: true };
  }
  
  return { sessionId: stored.sessionId, isNew: false };
}

/**
 * Update session last activity timestamp
 * @param {string} sessionId - Current session ID
 */
export function updateSessionActivity(sessionId) {
  setStorage(STORAGE_KEYS.SESSION, {
    sessionId,
    lastActivity: new Date().toISOString()
  });
}

/**
 * Get current session ID without creating new one
 * @returns {string|null} Session ID or null
 */
export function getCurrentSessionId() {
  const stored = getStorage(STORAGE_KEYS.SESSION, null);
  return stored?.sessionId || null;
}

