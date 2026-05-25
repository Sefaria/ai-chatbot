<script>
  import { _ } from '../i18n/index.js';

  let { data, streaming = false, onClickTopic } = $props();

  let expanded = $state(true);

  function toggle() {
    expanded = !expanded;
  }

  function handleClick() {
    if (onClickTopic) onClickTopic(data.topicSlug);
  }
</script>

<div class="topic-appetizer">
  <button
    class="appetizer-header"
    onclick={toggle}
    aria-expanded={expanded}
  >
    <svg class="appetizer-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="16" x2="12" y2="12"></line>
      <line x1="12" y1="8" x2="12.01" y2="8"></line>
    </svg>
    <span class="appetizer-header-text">
      {$_('appetizer.whileWaiting')}
    </span>
    <svg class="appetizer-chevron" class:rotated={!expanded} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
  </button>

  {#if expanded}
    <div class="appetizer-body">
      <a
        class="appetizer-link"
        href={data.topicUrl}
        target="_blank"
        rel="noopener noreferrer"
        onclick={handleClick}
      >
        {data.topicTitle} →
      </a>
      <span class="appetizer-hint">{$_('appetizer.exploreHint')}</span>
    </div>
  {/if}
</div>
