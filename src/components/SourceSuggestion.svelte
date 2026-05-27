<script>
  import { _ } from '../i18n/index.js';

  let { preview, streaming = false } = $props();

  let expanded = $state(false);

  function toggle() {
    if (!streaming) expanded = !expanded;
  }

  let showBody = $derived(streaming || expanded);
  let ref = $derived(preview?.toolInput?.reference ?? null);
  let sefariaHref = $derived(
    ref ? `https://www.sefaria.org/${encodeURIComponent(ref)}` : null
  );
</script>

<div class="source-suggestion">
  <button
    class="source-header"
    onclick={toggle}
    disabled={streaming}
    aria-expanded={showBody}
  >
    <svg class="source-book-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
    </svg>
    <span class="source-header-text">
      {streaming ? $_('source.readWhileWaiting') : (ref ?? preview.description)}
    </span>
    {#if !streaming}
      <svg class="source-chevron" class:rotated={expanded} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    {/if}
  </button>

  {#if showBody && sefariaHref}
    <div class="source-body">
      <a class="source-link" href={sefariaHref} target="_blank" rel="noopener noreferrer">
        {$_('source.readOnSefaria')} →
      </a>
    </div>
  {/if}
</div>
