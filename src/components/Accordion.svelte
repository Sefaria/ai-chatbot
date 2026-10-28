<script>
  import { _ } from 'svelte-i18n';

  let { kind, expanded = false, onToggle, preview = '', children } = $props();

  const titleKey = $derived(
    kind === 'topics'
      ? (expanded ? 'assistant.accordion.hideTopics' : 'assistant.accordion.showTopics')
      : (expanded ? 'assistant.accordion.hideThought' : 'assistant.accordion.showThought')
  );

  function toggle() {
    onToggle();
  }
</script>

<div class="lc-accordion">
  <button class="lc-accordion-header" aria-expanded={expanded} onclick={toggle}
    data-feature-name={kind === 'thought' ? 'thinking_steps_toggle' : undefined}
    data-element-shown-name={kind === 'thought' ? 'thinking_steps_toggle_shown' : undefined}>
    {#if preview && !expanded}
      <span class="lc-accordion-preview">{preview}</span>
    {:else}
      <span class="lc-accordion-title">{$_(titleKey)}</span>
    {/if}
    <svg class="lc-accordion-chevron" class:expanded width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  </button>
  {#if expanded}
    <div class="lc-accordion-slot">{@render children?.()}</div>
  {/if}
</div>

<style>
  .lc-accordion { width: 100%; }
  .lc-accordion-header {
    max-width: 100%;
    display: flex; align-items: center; gap: 4px;
    background: none; border: 0; padding: 0; cursor: pointer;
    font-family: var(--lc-font); font-size: var(--lc-font-size-sm); line-height: 20px;
    color: var(--lc-text-secondary);
  }
  .lc-accordion-title { flex: none; }
  .lc-accordion-preview { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .lc-accordion-chevron { transition: transform 0.15s ease; flex: none; }
  .lc-accordion-chevron.expanded { transform: rotate(180deg); }
  .lc-accordion-slot { display: flex; flex-direction: column; gap: 0; margin-top: 8px; }
  :global(.interface-hebrew) .lc-accordion-header { flex-direction: row-reverse; }
</style>
