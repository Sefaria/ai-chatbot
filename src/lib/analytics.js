/** Parameter names the chatbot sends on top of @sefaria/analytics' BASE_ALLOWED_PARAMS. Anything else is dropped. */
export const CHATBOT_EXTRA_ALLOWED_PARAMS = ['link_text', 'link_url', 'source', 'search_text', 'length'];

/**
 * Decides what an `assistant_click` event reports for a click inside the chatbot.
 * `path` is every element the click passed through, innermost first (from `analytics.listen`).
 * Returns the event's params, or null when the click shouldn't be tracked here.
 */
export function assistantClickParams(path) {
  // Skip the toggle button — toggleMode() fires its own specific event
  const isToggle = path.some(el => el.getAttribute('aria-label') === 'Toggle docked/floating');
  if (isToggle) return null;

  // Explicit analytics label wins over the generic link/aria-label logic
  // below (e.g. appetizer topic links, thinking-step links are <a> elements).
  const labelled = path.find(el => el.getAttribute('data-feature-name'));
  if (labelled) {
    return { feature_name: labelled.getAttribute('data-feature-name'), link_text: labelled.textContent.trim() };
  }

  // If a response link was clicked — capture the link text
  const link = path.find(el => el.tagName === 'A' && el.getAttribute('href'));
  if (link) {
    const raw = link.getAttribute('href');
    // new URL() throws on a malformed href — a bad link must not kill the handler
    let link_url = raw;
    if (raw.startsWith('http')) {
      try {
        const url = new URL(raw);
        link_url = url.pathname + (url.search || '');
      } catch {
        // keep the raw href
      }
    }
    const link_text = link.textContent.trim();
    return { feature_name: 'Response link', text: link_text, link_url, link_text };
  }

  // Otherwise walk up the path for the nearest aria-label. No link_text here:
  // this fallback matches broad containers (e.g. the "Chat messages" scroll
  // region) whose textContent is the whole conversation, not a link label.
  const target = path.find(el => el.getAttribute('aria-label'));
  if (!target) return null;
  return { feature_name: target.getAttribute('aria-label') };
}
