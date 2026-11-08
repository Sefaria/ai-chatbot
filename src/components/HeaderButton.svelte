<script>
  import Tooltip from './Tooltip.svelte';
  let { className = '', onClick, title, children, ...rest } = $props();
</script>

<Tooltip text={title}>
  <button class={className} onclick={onClick} aria-label={title} {...rest}>
    {@render children()}
  </button>
</Tooltip>

<style>
  .settings-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    border: 1px solid var(--lc-border);
    background: var(--lc-bg-tertiary);
    color: var(--lc-text-secondary);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .settings-btn:hover {
    background: var(--lc-bg-secondary);
    color: var(--lc-text);
  }

  .menu-btn,
  .panel-btn,
  .history-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    width: 26px;
    height: 26px;
    padding: 4px;
    border: 0px;
    background: transparent;
    color: var(--lc-text-secondary);
    cursor: pointer;
    font-size: var(--lc-font-size-sm);
    font-weight: 600;
    font-family: var(--lc-font);
    transition: all 0.15s ease;
  }

  .menu-btn:hover,
  .panel-btn:hover,
  .history-btn:hover,
  .menu-btn:focus-visible,
  .panel-btn:focus-visible,
  .history-btn:focus-visible,
  .menu-btn:active,
  .panel-btn:active,
  .history-btn:active {
    background: var(--lc-bg-tertiary);
    color: var(--lc-text);
    border-color: var(--lc-border);
  }

  /* Locked (e.g. history for a logged-out visitor): faded, no hover highlight. */
  .history-btn[aria-disabled='true'],
  .history-btn[aria-disabled='true']:hover,
  .history-btn[aria-disabled='true']:active {
    opacity: 0.5;
    cursor: not-allowed;
    background: transparent;
  }

  .close-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    padding: 4px;
    background: transparent;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    color: var(--lc-text-secondary);
    transition: all 0.15s ease;
  }

  .close-btn:hover {
    background: var(--lc-bg-tertiary);
    color: var(--lc-text);
  }
</style>
