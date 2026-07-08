<script>
  import { _, locale } from '../i18n/index.js';

  /**
   * entries: array of { id, type: 'tool'|'status', toolName?, description?, text?,
   *                     status: 'running'|'complete'|'error', startTime, duration?,
   *                     refData?, toolInput? }
   */
  let { entries = [] } = $props();

  const SEFARIA_BASE_URL = 'https://www.sefaria.org';

  let isHebrew = $derived($locale === 'he');

  /** Locale-appropriate label for a resolved ref. */
  function refDisplayLabel(refData) {
    if (isHebrew && refData.he) {
      return refData.he;
    }
    return refData.en;
  }

  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /** Escape a value for safe interpolation into an HTML attribute (adds quotes). */
  function escapeAttr(value) {
    return escapeHtml(String(value))
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** Build the bare bidi-isolated ref link anchor HTML. The full-text tooltip is
   *  handled per-row by the truncationTooltip action, so no native title here. */
  function refLinkHtml(url, label) {
    const href = escapeAttr(url);
    return `<a class="trail-ref-link" href="${href}" target="_blank" rel="noopener noreferrer" data-feature-name="thinking_steps_text_link"><bdi>${escapeHtml(label)}</bdi></a>`;
  }

  /**
   * Svelte action: show a tooltip on hover when the row is truncated. For rows
   * that contain a ref link the tooltip shows only the ref label (not the
   * surrounding "Fetching text …" prose); otherwise it shows the full row text.
   * The bubble is appended to document.body
   * with inline styles and a max z-index, so it can never be clipped or
   * out-stacked by the widget's shadow-root overflow/transform/stacking context
   * (the reason earlier shadow-root + CSS-class attempts were invisible).
   */
  function truncationTooltip(node) {
    let bubble = null;
    function hide() {
      bubble?.remove();
      bubble = null;
    }
    function show() {
      if (node.scrollWidth <= node.clientWidth + 1) return; // not truncated — no tooltip
      hide();
      bubble = document.createElement('div');
      bubble.className = 'lc-trail-tooltip';
      bubble.dataset.testid = 'la-trail-tooltip';
      const refEl = node.querySelector('.trail-ref-link');
      let tooltipText;
      if (refEl) {
        tooltipText = refEl.textContent;
      } else {
        const raw = node.textContent || '';
        const qm = raw.match(/["']([^"']+)["']/);
        tooltipText = qm ? qm[1] : raw;
      }
      bubble.textContent = tooltipText.trim();
      Object.assign(bubble.style, {
        position: 'fixed',
        maxWidth: '260px',
        background: '#3a3a3a',
        color: '#fff',
        font: '12px/1.4 Roboto, sans-serif',
        padding: '8px 12px',
        borderRadius: '12px',
        whiteSpace: 'normal',
        wordBreak: 'break-word',
        pointerEvents: 'none',
        zIndex: '2147483647',
        boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
      });
      document.body.appendChild(bubble);
      const r = node.getBoundingClientRect();
      const left = Math.min(r.left, window.innerWidth - bubble.offsetWidth - 8);
      bubble.style.top = `${Math.round(r.bottom + 6)}px`;
      bubble.style.left = `${Math.round(Math.max(8, left))}px`;
    }
    node.addEventListener('mouseenter', show);
    node.addEventListener('mouseleave', hide);
    return {
      destroy() {
        hide();
        node.removeEventListener('mouseenter', show);
        node.removeEventListener('mouseleave', hide);
      },
    };
  }

  // ── Client-side ref fallback (feature: trail ref links) ───────────────────
  // Used when the backend did not attach refData — e.g. the /api/ref endpoint is
  // unavailable, the backend is unreachable, or the tool isn't ref-bearing. This
  // keeps the trail-linkification feature working independently of the ref API,
  // mirroring the backend tref fallback.

  /** Convert a bare ref ("Genesis 1:1", "Mishnah_Shabbat.7.2") to a sefaria.org URL, or null. */
  function refToUrl(ref) {
    const m = ref.match(/^(.+?)[\s.](\d[\w:.\-–]*)$/);
    if (!m) {
      return null;
    }
    const book = m[1].trim().replace(/\s+/g, '_');
    const section = m[2].replace(/:/g, '.');
    return `${SEFARIA_BASE_URL}/${book}.${section}`;
  }

  /** Prettify a dotted/API-form ref for display: "Book.1.2" → "Book 1:2". */
  function refLabel(ref) {
    if (!/\s/.test(ref)) {
      const m = ref.match(/^(.+?)\.(\d[\w.\-–]*)$/);
      if (m) {
        return `${m[1].replace(/_/g, ' ')} ${m[2].replace(/\./g, ':')}`;
      }
    }
    return ref;
  }

  /** Escape text, then replace each quoted ref ("..."/'...') with a bare link (quotes consumed). */
  function linkifyRefsFallback(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, _quote, ref) => {
      const url = refToUrl(ref);
      if (!url) {
        return match;
      }
      return refLinkHtml(url, refLabel(ref));
    });
  }

  /**
   * Render a tool entry's description. Prefer the backend-resolved refData
   * (validated bilingual label via /api/ref); otherwise fall back to client-side
   * linkification of any quoted ref in the description.
   */
  function renderToolText(entry) {
    const text = entry.description ?? entry.toolName ?? '';
    const refData = entry.refData;
    if (!refData || !refData.is_ref) {
      return linkifyRefsFallback(text);
    }
    const rawRef = entry.toolInput?.reference ?? '';
    const escaped = escapeHtml(text);
    const escapedRef = escapeHtml(rawRef);
    if (!escapedRef || !escaped.includes(escapedRef)) {
      return linkifyRefsFallback(text);
    }
    const link = refLinkHtml(`${SEFARIA_BASE_URL}/${refData.url_ref}`, refDisplayLabel(refData));
    // Tool descriptions wrap the ref in quotes (e.g. Fetching text "Genesis 1:1").
    // Consume the surrounding quotes so the link renders bare, per the design.
    const quoted = `"${escapedRef}"`;
    if (escaped.includes(quoted)) {
      return escaped.replace(quoted, link);
    }
    return escaped.replace(escapedRef, link);
  }


</script>

{#if entries.length > 0}
  <ol class="progress-trail-list" data-element-shown-name="thinking_steps_list_shown">
    {#each entries as entry (entry.id)}
      {@const isFailed = entry.status === 'error'}
      <li
        class="progress-trail-entry progress-trail-entry--{entry.status}{isFailed ? ' failed' : ''}"
        style="width: 100%;"
      >
          <span class="progress-trail-text">
            {#if isFailed}
              <span class="trail-failed-prefix">{$_('assistant.progress.failed')}</span>
              <bdi class="trail-text-body" use:truncationTooltip>{entry.description ?? entry.toolName ?? entry.text ?? ''}</bdi>
            {:else if entry.type === 'tool'}
              <!-- F2: wrap the tool text (the dominant case: "Searching texts for…",
                   "Fetching text …") in a block so text-overflow:ellipsis fires.
                   Raw {@html} content sits directly in the flex parent otherwise,
                   where ellipsis never applies and the row is hard-clipped. -->
              <span class="trail-text-body" use:truncationTooltip>{@html renderToolText(entry)}</span>
            {:else}
              <!-- F2: wrap in span so text-overflow:ellipsis fires (plain text nodes in a flex
                   container do not respond to text-overflow on the parent) -->
              <span class="trail-text-body" use:truncationTooltip>{entry.text ?? ''}</span>
            {/if}
          </span>
      </li>
    {/each}
  </ol>
{/if}

<style>
  /* ── Container: always LTR, left-aligned — even in Hebrew/RTL interface ── */
  .progress-trail-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: flex-start;
    align-self: stretch;
    width: 100%;
    /* Force LTR for thinking steps regardless of interface language */
    direction: ltr;
    text-align: start;
  }

  .progress-trail-entry {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    line-height: 20px;
    color: var(--lc-text-secondary);
    min-height: 20px;
    /* Each row fills the container; required for truncation to work */
    width: 100%;
    box-sizing: border-box;
    /* Explicit LTR per-row so nested RTL elements can't flip it */
    direction: ltr;
    text-align: start;
  }

  .progress-trail-text {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
    /* Truncate the text row as a whole when it overflows.
       F2: overflow+text-overflow only work on block/inline-block when the content
       is a single text node or inline-level child. The flex display handles the
       ref-link child (which truncates itself). For plain-text (no-ref) steps the
       text node is a direct child of the flex container — wrap it so ellipsis fires.
       We apply text-overflow here too; it fires on the flex container when all
       children are inline. */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    /* Prevent this element itself from stretching the row past the container */
    max-width: 100%;
  }

  /* ── Ref links in normal (non-failed) steps ──
     Plain inline so the ref flows as text within the trail-text-body wrapper,
     which owns truncation (overflow/ellipsis). An inline-block here produced an
     odd dangling underline and a second truncation context. */
  :global(.trail-ref-link) {
    color: var(--semantic-text-link);
    font-family: var(--lc-font);
    font-size: 12px;
    font-weight: 600;
    line-height: var(--global-dimension-250);
    text-decoration: underline;
    text-decoration-style: solid;
    text-underline-offset: auto;
  }

  /* F1: Lift the overflow:hidden clip on the wrapping containers when a trail
     link is focused so the outline isn't swallowed. overflow:visible on
     :focus-within temporarily allows the outline to paint outside the box;
     the text may un-truncate slightly while focused, which is acceptable. */
  .trail-text-body:focus-within,
  .progress-trail-text:focus-within {
    overflow: visible;
  }

  /* No custom outline — use the browser default :focus-visible style (solid blue
     ring), which matches how other links in the widget (e.g. appetizer chips)
     look when focused. The :focus-within overflow lift above ensures it isn't
     clipped by the ancestor overflow:hidden. */

  :global(.trail-ref-icon) {
    flex-shrink: 0;
    color: var(--lc-primary);
  }

  /* F2: text body for plain-text (non-ref, non-failed) steps.
     Must be block or inline-block for text-overflow to fire; min-width:0 lets it
     shrink inside the flex parent so the container never widens past its bounds. */
  .trail-text-body {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  /* ── Failed prefix label ── */
  .trail-failed-prefix {
    flex-shrink: 0;
  }

  /* Ensure no link-like styling leaks onto any element inside a failed entry */
  .progress-trail-entry.failed :global(a),
  .progress-trail-entry.failed :global(.trail-ref-link),
  .progress-trail-entry.failed :global(.trail-ref-icon) {
    color: var(--lc-text-secondary);
    text-decoration: none;
    cursor: default;
    pointer-events: none;
  }
</style>
