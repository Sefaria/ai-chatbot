<script>
  /**
   * Reusable tooltip wrapper — renders slotted trigger + a CSS-only bubble on hover.
   *
   * Props:
   *   text  {string}  — tooltip label; bubble is suppressed when empty/undefined.
   *
   * IMPORTANT: place this component OUTSIDE any ancestor with overflow:hidden/clip,
   * otherwise the bubble will be clipped. The wrapper uses overflow:visible but cannot
   * escape a clipping ancestor.
   */
  let { text = '', children } = $props();
</script>

<span class="lc-tooltip" data-tooltip={text || undefined}>
  {@render children?.()}
</span>

<style>
  .lc-tooltip {
    position: relative;
    display: inline-flex;
    overflow: visible;
    max-width: 100%;
  }

  /* Bubble — only present when data-tooltip attribute is set (non-empty text) */
  .lc-tooltip[data-tooltip]::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 4px);
    left: 0;
    background: var(--lc-primary, #18345D);
    color: var(--lc-on-primary, #fff);
    font-size: 11px;
    line-height: 1.4;
    padding: 4px 8px;
    border-radius: 4px;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.12s ease;
    z-index: 10;
  }

  .lc-tooltip[data-tooltip]:hover::after {
    opacity: 1;
  }
</style>
