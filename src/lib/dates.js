/**
 * Date formatting utilities
 */

/**
 * Format a date for display as a date marker
 * @param {Date} date - Date to format
 * @returns {string} Formatted date string (e.g., "Jan 5, 2026")
 */
export function formatDateMarker(date) {
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  });
}

/**
 * Format a timestamp for display in message
 * @param {string} isoString - ISO timestamp
 * @returns {string} Formatted time (e.g., "2:30 PM")
 */
export function formatTime(isoString) {
  const date = new Date(isoString);
  return date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true
  });
}

/**
 * Check if two dates are on the same day
 * @param {Date} date1 
 * @param {Date} date2 
 * @returns {boolean}
 */
export function isSameDay(date1, date2) {
  return (
    date1.getFullYear() === date2.getFullYear() &&
    date1.getMonth() === date2.getMonth() &&
    date1.getDate() === date2.getDate()
  );
}

/**
 * Get date key for grouping messages
 * @param {string} isoString - ISO timestamp
 * @returns {string} Date key (YYYY-MM-DD)
 */
export function getDateKey(isoString) {
  const date = new Date(isoString);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

