// The pill's label for each host source that relabels it (chatbot:launcher)
export const LAUNCHER_LABELS = { search_no_results: 'assistant.floating_button.search_label' };

/**
 * Listens for the host page's requests on `document` (see "Host Events" in src/CLAUDE.md).
 * Returns a function that stops listening, so it can be returned from an $effect.
 *
 * - `chatbot:open`: onOpen(detail). Hosts open the assistant from their own UI, e.g. Sefaria's
 *   mobile menu, and can pass a question to ask, e.g. from their search box.
 * - `chatbot:launcher`: a host page turns the closed launcher into a page-specific action, e.g.
 *   Sefaria's search no-results page: "✦ Search with Library Assistant", asking about the search.
 *   detail = { source, question }, or null to go back to the "✦ Ask" pill. The widget owns the
 *   label (so it's translated with the rest of the assistant): known sources map to one above.
 *   getLauncher() returns the current launcher, and setLauncher(launcher) replaces it with
 *   { source, question, asked }, or null for the "✦ Ask" pill.
 */
export function listenToHost({ onOpen, getLauncher, setLauncher }) {
  function onOpenRequest(e) {
    onOpen(e.detail);
  }

  // Going back to "✦ Ask" waits a moment: moving between two no-results tabs, the old page
  // resets the pill just before the new one relabels it, and the pill shouldn't flicker
  let resetTimer;
  function onLauncher(e) {
    const d = e.detail;
    clearTimeout(resetTimer);
    if (d?.source && LAUNCHER_LABELS[d.source]) {
      const question = d.question || '';
      const current = getLauncher();
      // Same page again (e.g. another empty tab of the same search): keep whether it was asked
      const asked = current?.source === d.source && current.question === question && current.asked;
      setLauncher({ source: d.source, question, asked });
    } else {
      resetTimer = setTimeout(() => { setLauncher(null); }, 50);
    }
  }

  document.addEventListener('chatbot:open', onOpenRequest);
  document.addEventListener('chatbot:launcher', onLauncher);
  return () => {
    clearTimeout(resetTimer);
    document.removeEventListener('chatbot:open', onOpenRequest);
    document.removeEventListener('chatbot:launcher', onLauncher);
  };
}
