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

/**
 * Remove a value from localStorage
 * @param {string} key - Storage key (will be prefixed)
 */
export function removeStorage(key) {
  try {
    localStorage.removeItem(PREFIX + key);
  } catch (e) {
    console.warn(`[lc-chatbot] Failed to remove ${key} from storage:`, e);
  }
}

/**
 * Remove every value whose key starts with the given (unprefixed) key prefix,
 * e.g. 'messages' removes 'messages:<sessionId>' for all sessions.
 * @param {string} keyPrefix - Start of the storage key (will be prefixed)
 */
export function removeStorageByPrefix(keyPrefix) {
  try {
    const fullPrefix = PREFIX + keyPrefix;
    const keys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && key.startsWith(fullPrefix)) keys.push(key);
    }
    keys.forEach((key) => localStorage.removeItem(key));
  } catch (e) {
    console.warn(`[lc-chatbot] Failed to remove ${keyPrefix}* from storage:`, e);
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
  ANON_ID: 'anon_id',
  ANON_LIMIT_REACHED: 'anon_limit_reached'
};
