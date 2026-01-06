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

// Storage keys constants
export const STORAGE_KEYS = {
  SIZE: 'size',
  SESSION: 'session',
  DRAFT: 'draft',
  UI: 'ui',
  MESSAGES: 'messages'
};

