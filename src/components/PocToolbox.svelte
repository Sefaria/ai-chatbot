<script>
  // POC only: a floating toolbox for switching between versions of the launcher.
  // Delete this component (and its use in LCChatbot) before anything ships.
  // While open, every change previews live (onPreview); Save keeps it, Close drops it.
  // `phone` picks the phone groups (entry point) or the desktop ones (Sefaria's header item).
  let { config, onSave, onPreview, onEditingText, phone = true, defaultTexts = {} } = $props();

  // Each group can come with a text field (empty keeps the default text)
  const ENTRY_TEXT = { pill: ['pillText', 'Pill text'], bar: ['barText', 'Bar placeholder'] };
  const GROUPS = [
    { key: 'entry', label: 'Entry point', on: 'phone', text: (d) => ENTRY_TEXT[d.entry],
      options: [['circle', 'Circle'], ['bar', 'Input bar'], ['pill', 'Wide pill'], ['ask', 'Ask pill']] },
    { key: 'mobileOpen', label: 'LA open on mobile', on: 'phone',
      options: [['full', 'Full screen'], ['split', 'Split screen']] },
    { key: 'placement', label: 'Header item placement', on: 'desktop',
      text: (d) => d.placement === 'banner' && ['bannerText', 'Banner text'],
      options: [['header', 'Header item'], ['banner', 'Banner above Browse']] },
    { key: 'headerStyle', label: 'Header item style', on: 'desktop', when: (d) => d.placement !== 'banner',
      options: [['text', 'Star + text'], ['pill', 'Pill']] },
    // The pill sits at an end of a group, never between two header links
    { key: 'headerSlot', label: 'Header item position', on: 'desktop', when: (d) => d.placement !== 'banner',
      text: () => ['headerText', 'Header item text'],
      options: [['afterDonate', 'After Donate'], ['beforeDonate', 'Before Donate', (d) => d.headerStyle !== 'pill'], ['beforeSearch', 'Before search']] },
    { key: 'color', label: 'Button color', options: [['blue', 'Sefaria blue'], ['purple', 'Purple']] },
    { key: 'icon', label: 'Icon (circle & pill)', options: [['logo', 'Samekh'], ['star', 'Star ✦']] }
  ];
  let groups = $derived(GROUPS.filter(g => !g.on || g.on === (phone ? 'phone' : 'desktop')));

  let open = $state(false);
  let draft = $state({});

  $effect(() => {
    if (draft.headerStyle === 'pill' && draft.headerSlot === 'beforeDonate') draft.headerSlot = 'afterDonate';
  });
  $effect(() => {
    if (open) onPreview({ ...draft });
  });
  $effect(() => () => onPreview(null));

  function openToolbox() {
    draft = { ...config };
    open = true;
  }

  function close() {
    open = false;
    onEditingText(false);
    onPreview(null);
  }

  function save() {
    onSave({ ...draft });
    close();
  }
</script>

{#if open}
  <div class="poc-backdrop" onclick={close} aria-hidden="true"></div>
  <div class="poc-panel" role="dialog" aria-label="Sandbox controls" dir="ltr">
    <h2>Sandbox controls</h2>
    {#each groups.filter(g => !g.when || g.when(draft)) as group}
      <fieldset>
        <legend>{group.label}</legend>
        <div class="poc-segments">
          {#each group.options.filter(([, , shown]) => !shown || shown(draft)) as [value, label]}
            <label class:selected={draft[group.key] === value}>
              <input type="radio" name={group.key} {value} bind:group={draft[group.key]} />
              {label}
            </label>
          {/each}
        </div>
      </fieldset>
      {@const textField = group.text?.(draft)}
      {#if textField}
        <label class="poc-text">
          <span>{textField[1]}</span>
          <input
            type="text"
            bind:value={draft[textField[0]]}
            placeholder={defaultTexts[textField[0]]}
            onfocus={() => onEditingText(true)}
            onblur={() => onEditingText(false)}
          />
        </label>
      {/if}
    {/each}
    <div class="poc-actions">
      <button type="button" class="poc-close" onclick={close}>Close</button>
      <button type="button" class="poc-save" onclick={save}>Save</button>
    </div>
  </div>
{:else}
  <button type="button" class="poc-fab" onclick={openToolbox} aria-label="Open sandbox controls">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line>
      <line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line>
      <line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line>
      <line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line>
      <line x1="17" y1="16" x2="23" y2="16"></line>
    </svg>
  </button>
{/if}

<style>
  .poc-fab {
    position: fixed;
    top: 40%;
    left: 0;
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    padding: 0;
    background: #333;
    color: #fff;
    border: none;
    border-radius: 0 12px 12px 0;
    box-shadow: var(--lc-shadow);
    opacity: 0.85;
    cursor: pointer;
  }

  /* Below the launcher (z-index 9999), so the previewed launcher isn't dimmed */
  .poc-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9998;
    background: rgba(0, 0, 0, 0.3);
  }

  /* At the top, so the launcher at the bottom stays in view while previewing */
  .poc-panel {
    position: fixed;
    inset-inline: 16px;
    top: calc(16px + env(safe-area-inset-top));
    z-index: 10001;
    max-width: 400px;
    margin-inline: auto;
    padding: 16px;
    background: var(--lc-bg);
    color: var(--lc-text);
    border-radius: 16px;
    box-shadow: var(--lc-shadow);
    font-family: var(--lc-font);
    font-size: 14px;
  }

  h2 {
    margin: 0 0 12px;
    font-size: 16px;
    font-weight: 600;
  }

  fieldset {
    margin: 0 0 16px;
    padding: 0;
    border: none;
  }

  legend {
    margin-bottom: 6px;
    padding: 0;
    color: var(--semantic-text-muted);
  }

  .poc-segments {
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--core-neutral-gray-100);
    border-radius: 12px;
  }

  .poc-segments label {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 0 4px;
    border-radius: 8px;
    text-align: center;
    cursor: pointer;
  }

  .poc-segments label.selected {
    background: var(--lc-bg);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
  }

  .poc-segments label:has(input:focus-visible) {
    outline: 2px solid var(--brand-sefaria-blue);
  }

  .poc-segments input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }

  .poc-text {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: -8px 0 16px;
    color: var(--semantic-text-muted);
  }

  .poc-text input {
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--core-neutral-gray-100);
    border-radius: 12px;
    color: var(--lc-text);
    font-family: inherit;
    font-size: 16px; /* below 16px, iOS zooms the page on focus */
  }

  .poc-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

  .poc-actions button {
    min-height: 44px;
    padding: 0 20px;
    border-radius: 9999px;
    font-family: inherit;
    font-size: 14px;
    cursor: pointer;
  }

  .poc-close {
    background: transparent;
    color: var(--lc-text);
    border: 1px solid var(--core-neutral-gray-100);
  }

  .poc-save {
    background: var(--brand-sefaria-blue);
    color: var(--core-base-white);
    border: none;
  }
</style>
