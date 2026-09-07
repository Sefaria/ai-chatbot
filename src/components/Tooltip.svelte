<script>
  /**
   * Reusable tooltip wrapper — renders slotted trigger + a bubble on hover.
   *
   * Props:
   *   text  {string}  — tooltip label; bubble is suppressed when empty/undefined.
   *
   * The bubble is portalled to document.body (see lib/tooltip.js) so it is never
   * clipped by the panel's `overflow: hidden`, and it clamps to the viewport.
   */
  import { tooltip } from '../lib/tooltip.js';

  let { text = '', children } = $props();
</script>

<span class="lc-tooltip" use:tooltip={text}>
  {@render children?.()}
</span>

<style>
  .lc-tooltip { position: relative; display: inline-flex; max-width: 100%; }
</style>
