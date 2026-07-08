<script>
  import Tooltip from './Tooltip.svelte';
  let {
    label = '',
    href = '',
    onActivate,
  } = $props();

  /** F4: Only show the tooltip when the label text is actually truncated. */
  let isTruncated = $state(false);
  let labelEl = $state(null);

  /** Svelte action: attaches a ResizeObserver to check truncation on the ref span. */
  function truncationObserver(node) {
    function check() {
      isTruncated = node.scrollWidth > node.clientWidth;
    }
    check();
    const ro = new ResizeObserver(check);
    ro.observe(node);
    return {
      destroy() { ro.disconnect(); },
    };
  }

  function activate(e) {
    e.preventDefault();
    onActivate?.(href);
  }
</script>

<!-- svelte-ignore a11y_missing_content -->
<Tooltip text={isTruncated ? label : ''}>
  <a class="lc-location-tag" {href} onclick={activate} dir="auto" data-feature-name="initial_location_link" data-element-shown-name="initial_location_link_shown">
    <svg class="lc-location-pin" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>
    </svg>
    <span class="lc-location-ref" use:truncationObserver><bdi>{label}</bdi></span>
  </a>
</Tooltip>

<style>
  /* Figma: Library-Assistant-Wireframes — Location Tag Component (node 6447:7199).
     Pill hugs its content and caps at the chat-bubble width (set by the parent
     .message-location-tag, max-width 560px); the ref truncates when too long. */
  .lc-location-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    width: fit-content;
    max-width: 100%;
    box-sizing: border-box;
    padding: 4px 8px;
    border: 1px solid var(--lc-border-strong);
    border-radius: 16px;
    color: var(--lc-text-secondary);
    text-decoration: none;
    font-family: var(--lc-font);
    font-size: 12px;
    line-height: normal;
    cursor: pointer;
  }
  .lc-location-tag:hover,
  .lc-location-tag:active {
    background: var(--lc-bg-hover);
  }
  .lc-location-pin {
    flex: none;
    color: var(--lc-icon-primary);
  }
  .lc-location-ref {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  :global(.interface-hebrew) .lc-location-tag {
    flex-direction: row-reverse;
  }
</style>
