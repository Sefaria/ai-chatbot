<script>
  import { _ } from '../i18n/index.js';
  import Tooltip from './Tooltip.svelte';

  /**
   * entries: array of { id, type: 'tool'|'status', toolName?, description?, text?,
   *                     status: 'running'|'complete'|'error', startTime, duration? }
   */
  let { entries = [] } = $props();

  const SEFARIA_BASE_URL = 'https://www.sefaria.org';

  /**
   * Convert a bare Sefaria ref like "Pesachim 119b" or "Mishnah Pesachim 10:8"
   * into a sefaria.org URL.  Returns null if the string doesn't look like a ref.
   */
  function refToUrl(ref) {
    // Handles both space form ("Genesis 2:1-3", "Mishnah Shabbat 7:2") and
    // dotted / API form ("Genesis.2.2-3", "Mishnah_Shabbat.7.2", "Berakhot.2a").
    const m = ref.match(/^(.+?)[\s.](\d[\w:.\-–]*)$/);
    if (!m) return null;
    const book = m[1].trim().replace(/\s+/g, '_');
    const section = m[2].replace(/:/g, '.');
    return `${SEFARIA_BASE_URL}/${book}.${section}`;
  }

  /** Prettify a ref for display: dotted API form → "Book chapter:verse". */
  function refLabel(ref) {
    if (!/\s/.test(ref)) {
      const m = ref.match(/^(.+?)\.(\d[\w.\-–]*)$/);
      if (m) return `${m[1].replace(/_/g, ' ')} ${m[2].replace(/\./g, ':')}`;
    }
    return ref;
  }

  /**
   * Shared scanning engine for ref substitution.
   * Escapes the input as HTML, then replaces every quoted Sefaria ref using
   * the provided renderer callback.  Handles both single and double quotes.
   * @param {string} text - plain-text input
   * @param {(url: string, label: string) => string} renderer - returns HTML for one ref
   */
  function substituteRefs(text, renderer) {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, _quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      return renderer(url, refLabel(ref));
    });
  }

  /**
   * Return an HTML string with any quoted Sefaria refs turned into links.
   * Handles both single quotes ('Pesachim 119b') and double quotes ("Mishnah Shabbat 7:2").
   * Input is plain text, so we escape it first to prevent XSS.
   */
  function linkifyRefs(text) {
    // Bare link — no surrounding quotes, no external-link icon (matches Figma)
    return substituteRefs(text, (url, label) =>
      `<a class="trail-ref-link" href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`
    );
  }

  /**
   * Return an HTML string with quoted refs rendered as plain (non-clickable) spans.
   * Used for failed entries where refs must not look clickable.
   * Wraps each ref in a <span class="trail-failed-ref"> so it can be styled
   * with muted/secondary color and no underline.
   */
  function plainRefs(text) {
    // Bare muted ref — no surrounding quotes (matches Figma)
    return substituteRefs(text, (_url, label) =>
      `<span class="trail-failed-ref">${label}</span>`
    );
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
              <span class="trail-failed-prefix">{$_('progress.failed')}</span>
              {@html plainRefs(entry.description ?? entry.toolName ?? entry.text ?? '')}
            {:else if entry.type === 'tool'}
              {@html linkifyRefs(entry.description ?? entry.toolName ?? '')}
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
    gap: var(--global-dimension-100, 8px);
    align-items: flex-start;
    align-self: stretch;
    width: 100%;
    /* Force LTR for thinking steps regardless of interface language */
    direction: ltr;
    text-align: left;
  }

  .progress-trail-entry {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    line-height: 20px;
    color: var(--lc-text-secondary, #575757);
    min-height: 20px;
    /* Each row fills the container; required for truncation to work */
    width: 100%;
    box-sizing: border-box;
    /* Explicit LTR per-row so nested RTL elements can't flip it */
    direction: ltr;
    text-align: left;
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
    color: var(--semantic-text-link, #18345D);
    font-family: var(--lc-font);
    font-size: 12px;
    font-weight: 600;
    line-height: var(--global-dimension-250, 20px);
    text-decoration: underline;
    text-decoration-style: solid;
    text-underline-position: from-font;
  }

  :global(.trail-ref-icon) {
    flex-shrink: 0;
    color: var(--lc-primary, #18345d);
  }

  /* ── Failed prefix label ── */
  .trail-failed-prefix {
    flex-shrink: 0;
  }

  /* ── Failed ref span: muted color, no underline, not clickable ── */
  :global(.trail-failed-ref) {
    color: var(--lc-text-secondary, #575757);
    text-decoration: none;
    cursor: default;
    /* Truncation in failed refs */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  /* Ensure no link-like styling leaks onto any element inside a failed entry */
  .progress-trail-entry.failed :global(a),
  .progress-trail-entry.failed :global(.trail-ref-link),
  .progress-trail-entry.failed :global(.trail-ref-icon) {
    color: var(--lc-text-secondary, #575757);
    text-decoration: none;
    cursor: default;
    pointer-events: none;
  }
</style>
