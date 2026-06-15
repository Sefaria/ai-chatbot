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
  .lc-tooltip { position: relative; display: inline-flex; overflow: visible; max-width: 100%; }

  /* Tooltip bubble — appears BELOW the trigger, dark charcoal, wraps long text */
  .lc-tooltip[data-tooltip]::after {
    content: attr(data-tooltip);
    position: absolute;
    top: calc(100% + 8px);
    left: 0;
    background: var(--lc-tooltip-bg, #3a3a3a);
    color: #fff;
    font-family: var(--lc-font, inherit);
    font-size: 12px;
    line-height: 1.4;
    text-align: left;
    padding: 8px 12px;
    border-radius: 12px;
    max-width: 252px;
    width: max-content;
    white-space: normal;
    word-break: break-word;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.12s ease;
    z-index: 20;
  }

  /* Caret — small triangle pointing UP toward the trigger */
  .lc-tooltip[data-tooltip]::before {
    content: '';
    position: absolute;
    top: calc(100% + 2px);
    left: 16px;
    border: 6px solid transparent;
    border-bottom-color: var(--lc-tooltip-bg, #3a3a3a);
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.12s ease;
    z-index: 20;
  }

  .lc-tooltip[data-tooltip]:hover::after,
  .lc-tooltip[data-tooltip]:hover::before { opacity: 1; }
</style>
