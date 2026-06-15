<script>
  import { _ } from '../i18n/index.js';
  import Tooltip from './Tooltip.svelte';

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
    // Space form: "Genesis 2:1-3", "Mishnah Shabbat 7:2"
    let m = ref.match(/^(.+?)\s+(\d[\w:.\-–]*)$/);
    if (m) {
      const book = m[1].trim().replace(/\s+/g, '_');
      return `https://www.sefaria.org/${book}.${m[2].replace(/:/g, '.')}`;
    }
    // Dotted / API form: "Genesis.2.2-3", "Mishnah_Shabbat.7.2", "Berakhot.2a"
    m = ref.match(/^(.+?)\.(\d[\w:.\-–]*)$/);
    if (m) {
      const book = m[1].replace(/\s+/g, '_');
      return `https://www.sefaria.org/${book}.${m[2]}`;
    }
    return null;
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
   * Return an HTML string with any quoted Sefaria refs turned into links.
   * Handles both single quotes ('Pesachim 119b') and double quotes ("Mishnah Shabbat 7:2").
   * Input is plain text, so we escape it first to prevent XSS.
   */
  function linkifyRefs(text) {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      const label = refLabel(ref);
      // Bare link — no surrounding quotes, no external-link icon (matches Figma)
      return `<a class="trail-ref-link" href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
  }

  /**
   * Return an HTML string with quoted refs rendered as plain (non-clickable) spans.
   * Used for failed entries where refs must not look clickable.
   * Wraps each ref in a <span class="trail-failed-ref"> so it can be styled
   * with muted/secondary color and no underline.
   */
  function plainRefs(text) {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      // Bare muted ref — no surrounding quotes (matches Figma)
      return `<span class="trail-failed-ref">${refLabel(ref)}</span>`;
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
      {#each entries as entry, i (entry.id)}
        {@const isFailed = entry.status === 'error'}
        {@const isRunning = entry.status === 'running'}
        {@const hasIcon = !collapsed && isRunning && i === entries.length - 1}
        <li
          class="progress-trail-entry progress-trail-entry--{entry.status}{isFailed ? ' failed' : ''}"
          style="width: 100%;"
        >
          <Tooltip text={entry.type === 'tool' ? (entry.description ?? entry.toolName ?? undefined) : undefined}>
            {#if hasIcon}
              <span class="progress-trail-icon">
                <svg class="trail-loader" width="16" height="16" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                  aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
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
          </Tooltip>
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
    gap: var(--space-1, 4px);
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
    font-family: Roboto, sans-serif;
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

  /* ── Lucide loader-circle, animated, on the active step ── */
  .trail-loader {
    display: block;
    width: 16px;
    height: 16px;
    color: var(--functional-icon-icon-primary, #666666);
    animation: trail-spin 0.8s linear infinite;
    transform-origin: center;
  }

  @keyframes trail-spin {
    to { transform: rotate(360deg); }
  }
</style>
