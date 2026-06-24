<script>
  import { _, locale } from '../i18n/index.js';
  import Tooltip from './Tooltip.svelte';

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

  /** Build the bare bidi-isolated ref link anchor HTML. */
  function refLinkHtml(url, label) {
    const href = escapeAttr(url);
    return `<a class="trail-ref-link" href="${href}" target="_blank" rel="noopener noreferrer" data-feature-name="thinking_steps_text_link"><bdi>${escapeHtml(label)}</bdi></a>`;
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
  <ol class="progress-trail-list">
    {#each entries as entry (entry.id)}
      {@const isFailed = entry.status === 'error'}
      <li
        class="progress-trail-entry progress-trail-entry--{entry.status}{isFailed ? ' failed' : ''}"
        style="width: 100%;"
      >
        <Tooltip text={entry.type === 'tool' ? (entry.description ?? entry.toolName ?? '') : ''}>
          <span class="progress-trail-text">
            {#if isFailed}
              <span class="trail-failed-prefix">{$_('assistant.progress.failed')}</span>
              <bdi>{entry.description ?? entry.toolName ?? entry.text ?? ''}</bdi>
            {:else if entry.type === 'tool'}
              {@html renderToolText(entry)}
            {:else}
              {entry.text ?? ''}
            {/if}
          </span>
        </Tooltip>
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
    gap: var(--global-dimension-100);
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
    /* Truncate the text row as a whole when it overflows */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

/* ── Ref links in normal (non-failed) steps ── */
  :global(.trail-ref-link) {
    flex: 1 0 0;
    min-width: 0;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--semantic-text-link);
    font-family: var(--lc-font);
    font-size: 12px;
    font-weight: 600;
    line-height: var(--global-dimension-250);
    text-decoration: underline;
    text-decoration-style: solid;
    text-underline-position: from-font;
  }

  :global(.trail-ref-icon) {
    flex-shrink: 0;
    color: var(--lc-primary);
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
