<script>
  import { _ } from '../i18n/index.js';

  /**
   * entries: array of { id, type: 'tool'|'status', toolName?, description?, text?,
   *                     status: 'running'|'complete'|'error', startTime, duration? }
   * collapsed: boolean — true after streaming ends; false while streaming
   */
  let { entries = [], collapsed = false, showToggle = true } = $props();

  /**
   * Convert a bare Sefaria ref like "Pesachim 119b" or "Mishnah Pesachim 10:8"
   * into a sefaria.org URL.  Returns null if the string doesn't look like a ref.
   */
  function refToUrl(ref) {
    // Must end with a chapter/verse token: digits optionally followed by a letter
    // and optionally a range like 115b-116a or a verse like 10:8
    const m = ref.match(/^(.+?)\s+(\d[\w:.-]*)$/);
    if (!m) return null;
    const book = m[1].replace(/ /g, '_');
    const chapter = m[2].replace(/:/g, '.');
    return `https://www.sefaria.org/${book}.${chapter}`;
  }

  /**
   * Return an HTML string with any quoted Sefaria refs turned into links.
   * Handles both single quotes ('Pesachim 119b') and double quotes ("Mishnah Shabbat 7:2").
   * Input is plain text, so we escape it first to prevent XSS.
   */
  function linkifyRefs(text) {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const icon = '<svg class="trail-ref-icon" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>';
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      return `${quote}${icon}<a class="trail-ref-link" href="${url}" target="_blank" rel="noopener noreferrer">${ref}</a>${quote}`;
    });
  }

  /**
   * Return an HTML string with quoted refs rendered as plain (non-clickable) spans.
   * Used for failed entries where refs must not look clickable.
   * Wraps each ref in a <span class="trail-failed-ref"> so it can be styled
   * with muted/secondary color and no underline, while the tooltip on the
   * parent <li data-tooltip> still works (it lives outside the overflow clip).
   */
  function plainRefs(text) {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      return `${quote}<span class="trail-failed-ref">${ref}</span>${quote}`;
    });
  }

  let expanded = $state(false);

  // Show the list when streaming (collapsed=false) or when the user expands it
  let showList = $derived(!collapsed || expanded);

  function toggle() {
    expanded = !expanded;
  }
</script>

{#if entries.length > 0}
  {#if collapsed && showToggle}
    <button class="progress-trail-toggle" onclick={toggle} aria-expanded={expanded}>
      {#if expanded}
        {$_('progress.hideThinking', { values: { count: entries.length } })}
      {:else}
        {$_('progress.showThinking', { values: { count: entries.length } })}
      {/if}
    </button>
  {/if}

  {#if showList || !showToggle}
    <ol class="progress-trail-list">
      {#each entries as entry (entry.id)}
        {@const isFailed = entry.status === 'error'}
        {@const isRunning = entry.status === 'running'}
        {@const hasIcon = entry.type === 'tool' && isRunning}
        <li
          class="progress-trail-entry progress-trail-entry--{entry.status}{isFailed ? ' failed' : ''}"
          data-tooltip={entry.type === 'tool' ? (entry.description ?? entry.toolName ?? undefined) : undefined}
          style="width: 100%;"
        >
          {#if hasIcon}
            <span class="progress-trail-icon">
              <span class="progress-trail-spinner" aria-hidden="true"></span>
            </span>
          {/if}
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
        </li>
      {/each}
    </ol>
  {/if}
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
    /* Needed so the ::before tooltip can be absolutely positioned */
    position: relative;
    /* Explicit LTR per-row so nested RTL elements can't flip it */
    direction: ltr;
    text-align: left;
  }

  .progress-trail-icon {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
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

  /* ── Fast CSS tooltip (~120ms) on the li — stays visible outside any overflow clip ── */
  .progress-trail-entry[data-tooltip] {
    /* overflow must NOT be hidden on the li itself, so the tooltip is not clipped */
    overflow: visible;
  }

  .progress-trail-entry[data-tooltip]::before {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 4px);
    left: 0;
    background: var(--lc-primary, #18345d);
    color: var(--lc-on-primary, #fff);
    font-size: 11px;
    line-height: 1.4;
    padding: 4px 8px;
    border-radius: 4px;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    /* 120ms fast fade — matches app tooltip speed */
    transition: opacity 0.12s ease;
    z-index: 10;
  }

  .progress-trail-entry[data-tooltip]:hover::before {
    opacity: 1;
  }

  /* ── Ref links in normal (non-failed) steps ── */
  :global(.trail-ref-link) {
    color: var(--lc-primary, #18345d);
    text-decoration: none;
    /* Truncation: a link inside the flex text row should also truncate */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
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

  /* ── Spinner animation ── */
  .progress-trail-spinner {
    display: block;
    width: 18px;
    height: 18px;
    border: 2px solid var(--lc-text-secondary, #575757);
    border-top-color: transparent;
    border-radius: 50%;
    animation: trail-spin 1s linear infinite;
  }

  @keyframes trail-spin {
    to { transform: rotate(360deg); }
  }
</style>
