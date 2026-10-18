/**
 * Session management utilities
 */

import { getStorage, setStorage, removeStorage, removeStorageByPrefix, STORAGE_KEYS } from './storage.js';

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
 * @param {boolean} loggedIn - Whether the widget has a user (a user-id)
 * @returns {{ sessionId: string, isNew: boolean }}
 */
export function getOrCreateSession(forceNew = false, loggedIn = true) {
  const stored = getStorage(STORAGE_KEYS.SESSION, null);
  
  // A logged-out visitor's conversation never times out: they come back to it,
  // and to the login prompt, however long ago it was.
  const expired = stored && stored.loggedIn !== false && isSessionExpired(stored.lastActivity);
  if (forceNew || !stored || expired) {
    const sessionId = generateSessionId();
    const session = {
      sessionId,
      lastActivity: new Date().toISOString(),
      loggedIn
    };
    setStorage(STORAGE_KEYS.SESSION, session);
    return { sessionId, isNew: true };
  }

  // A logged-out visitor's conversation that continues after login now
  // belongs to the user.
  if (loggedIn && stored.loggedIn === false) {
    setStorage(STORAGE_KEYS.SESSION, { ...stored, loggedIn: true });
  }
  
  return { sessionId: stored.sessionId, isNew: false };
}

/**
 * Update session last activity timestamp
 * @param {string} sessionId - Current session ID
 */
export function updateSessionActivity(sessionId) {
  const stored = getStorage(STORAGE_KEYS.SESSION, null);
  setStorage(STORAGE_KEYS.SESSION, {
    sessionId,
    lastActivity: new Date().toISOString(),
    loggedIn: stored?.loggedIn ?? true
  });
}

/**
 * When the widget loads logged out, delete a conversation a logged-in user
 * left in this browser (the session, every saved message list and draft), so
 * the next visitor doesn't see it. The user still has it in their chat
 * history on the server. A session saved before this marker existed counts as
 * logged in: until then only logged-in users could chat.
 * @param {boolean} loggedIn - Whether the widget has a user (a user-id)
 */
export function clearLoggedInChatWhenLoggedOut(loggedIn) {
  if (loggedIn) return;
  const stored = getStorage(STORAGE_KEYS.SESSION, null);
  if (!stored || stored.loggedIn === false) return;

  removeStorage(STORAGE_KEYS.SESSION);
  removeStorageByPrefix(STORAGE_KEYS.MESSAGES);
  removeStorageByPrefix(STORAGE_KEYS.DRAFT);
}

/**
 * Get the logged-out visitor's ID, creating it on first use.
 * The widget sends it instead of a user token when there is no user-id.
 * @returns {string} Anonymous visitor ID
 */
export function getOrCreateAnonId() {
  const stored = getStorage(STORAGE_KEYS.ANON_ID, null);
  if (stored) return stored;

  const anonId = crypto.randomUUID();
  setStorage(STORAGE_KEYS.ANON_ID, anonId);
  return anonId;
}
