<script>
  let { className = '', onClick, title, children, ...rest } = $props();
</script>

<button class={className} onclick={onClick} title={title} aria-label={title} {...rest}>
  {@render children()}
</button>

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
  .panel-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
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
  .menu-btn:focus-visible,
  .panel-btn:focus-visible,
  .menu-btn:active,
  .panel-btn:active {
    background: var(--lc-bg-tertiary);
    color: var(--lc-text);
    border-color: var(--lc-border);
  }

  .close-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: transparent;
    border: none;
    border-radius: var(--lc-radius-sm);
    cursor: pointer;
    color: var(--lc-text-secondary);
    transition: all 0.15s ease;
  }

  .close-btn:hover {
    background: var(--lc-bg-tertiary);
    color: var(--lc-text);
  }
</style>
