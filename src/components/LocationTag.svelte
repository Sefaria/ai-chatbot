<script>
  import Tooltip from './Tooltip.svelte';
  let { label = '', href = '', onActivate } = $props();
  function activate(e) { e.preventDefault(); onActivate?.(href); }
</script>

<!-- svelte-ignore a11y_missing_content -->
<Tooltip text={label}>
  <a class="lc-location-tag" {href} onclick={activate} dir="auto">
    <svg class="lc-location-pin" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>
    </svg>
    <span class="lc-location-ref">{label}</span>
  </a>
</Tooltip>

<style>
  .lc-location-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    max-width: 100%;
    box-sizing: border-box;
    padding: 4px 8px;
    border: 1px solid var(--lc-border-strong, #ccc);
    border-radius: 16px;
    color: var(--lc-text-secondary);
    text-decoration: none;
    font-family: var(--lc-font);
    font-size: var(--lc-font-size-sm);
    line-height: 20px;
    cursor: pointer;
  }
  .lc-location-tag:hover {
    background: var(--lc-bg-hover, #eee);
  }
  .lc-location-pin {
    flex: none;
  }
  .lc-location-ref {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
:global(.interface-hebrew) .lc-location-tag {
    flex-direction: row-reverse;
  }
</style>
