/**
 * Local storage utilities with namespaced keys
 */

const PREFIX = 'lc_chatbot:';

/**
 * Get a value from localStorage
 * @param {string} key - Storage key (will be prefixed)
 * @param {*} defaultValue - Default value if not found
 * @returns {*} Parsed value or default
 */
export function getStorage(key, defaultValue = null) {
  try {
    const item = localStorage.getItem(PREFIX + key);
    return item ? JSON.parse(item) : defaultValue;
  } catch (e) {
    console.warn(`[lc-chatbot] Failed to read ${key} from storage:`, e);
    return defaultValue;
  }
}

/**
 * Set a value in localStorage
 * @param {string} key - Storage key (will be prefixed)
 * @param {*} value - Value to store (will be JSON stringified)
 */
export function setStorage(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (e) {
    console.warn(`[lc-chatbot] Failed to write ${key} to storage:`, e);
  }
}

// Storage keys constants
export const STORAGE_KEYS = {
  SIZE: 'size',
  SESSION: 'session',
  DRAFT: 'draft',
  UI: 'ui',
  MESSAGES: 'messages',
  PROMPT_SLUGS: 'prompt_slugs',
  BOT_VERSION: 'bot_version',
  HAS_USED: 'has_used',
  // Logged-out visitors: stable anonymous id, and whether its free responses are used up
  ANON_ID: 'anon_id',
  ANON_LOGIN_REQUIRED: 'anon_login_required',
  // Set when a logged-out visitor goes to log in from the limit banner, so the assistant
  // reopens on their conversation once they're back: { sessionId, at }
  RESUME_AFTER_LOGIN: 'resume_after_login',
  // 'user' | 'anon' — the identity the stored session belongs to
  IDENTITY: 'identity',
  // Whether the signed-in user had saved chats last time history loaded, so its search
  // button starts out enabled or disabled before the list arrives
  HAS_CONVERSATIONS: 'has_conversations'
};
