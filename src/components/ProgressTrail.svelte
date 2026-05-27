<script>
  import { _ } from '../i18n/index.js';

  /**
   * entries: array of { id, type: 'tool'|'status', toolName?, description?, text?,
   *                     status: 'running'|'complete'|'error', startTime, duration? }
   * collapsed: boolean — true after streaming ends; false while streaming
   */
  let { entries = [], collapsed = false } = $props();

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
    return escaped.replace(/(['"])([^'"]+)\1/g, (match, quote, ref) => {
      const url = refToUrl(ref);
      if (!url) return match;
      return `${quote}<a class="trail-ref-link" href="${url}" target="_blank" rel="noopener noreferrer">${ref}</a>${quote}`;
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
  {#if collapsed}
    <button class="progress-trail-toggle" onclick={toggle} aria-expanded={expanded}>
      {#if expanded}
        {$_('progress.hideThinking', { values: { count: entries.length } })}
      {:else}
        {$_('progress.showThinking', { values: { count: entries.length } })}
      {/if}
    </button>
  {/if}

  {#if showList}
    <ol class="progress-trail-list">
      {#each entries as entry (entry.id)}
        <li class="progress-trail-entry progress-trail-entry--{entry.status}">
          <span class="progress-trail-icon">
            {#if entry.status === 'running'}
              <span class="progress-trail-spinner" aria-hidden="true"></span>
            {:else if entry.status === 'complete'}
              <!-- Checkmark — matches LCChatbot.svelte lines ~1038-1040 -->
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
              </svg>
            {:else}
              <!-- X / error — matches LCChatbot.svelte lines ~1033-1037 -->
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="15" y1="9" x2="9" y2="15"></line>
                <line x1="9" y1="9" x2="15" y2="15"></line>
              </svg>
            {/if}
          </span>
          <span class="progress-trail-text">
            {#if entry.type === 'tool'}
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
