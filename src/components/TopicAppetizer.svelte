<script>
  import { _ } from '../i18n/index.js';

  let { data, streaming = false, onClickTopic } = $props();

  function attachClickHandler(node, topic) {
    function handler(e) {
      e.preventDefault();
      e.stopPropagation();
      if (onClickTopic) onClickTopic(topic.topicSlug, topic.topicUrl);
    }
    node.addEventListener('click', handler);
    return {
      destroy() { node.removeEventListener('click', handler); }
    };
  }
</script>

<div class="topic-appetizer">
  <div class="appetizer-header">
    <svg class="appetizer-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="16" x2="12" y2="12"></line>
      <line x1="12" y1="8" x2="12.01" y2="8"></line>
    </svg>
    <span class="appetizer-header-text">
      {$_('appetizer.whileWaiting')}
    </span>
  </div>
  <div class="appetizer-body">
    {#each data.topics as topic, i}
      {#if i > 0}<span class="appetizer-separator">, </span>{/if}
      <a
        class="appetizer-link"
        href={topic.topicUrl}
        use:attachClickHandler={topic}
      >
        {topic.topicTitle}
      </a>
    {/each}
  </div>
</div>
