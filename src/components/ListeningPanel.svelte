<script>
  import { onMount, onDestroy } from 'svelte';
  import { _ } from '../i18n/index.js';
  import { createListener, listeningRequest, speechRecognitionSupported } from '../lib/listening.js';

  let { apiBaseUrl, userId, language, disabled = false, onQuestion } = $props();
  let available = $state(false);
  let state = $state('idle');
  let suggestions = $state([]);
  let listener;
  let disposed = false;
  let busy = $state(false);
  const active = $derived(['starting', 'listening', 'reconnecting'].includes(state));

  onMount(async () => {
    if (!speechRecognitionSupported()) return;
    try {
      const result = await listeningRequest(apiBaseUrl, userId, '', null, AbortSignal.timeout(5000), 'GET');
      if (!disposed) available = result.enabled;
    } catch { /* Unsupported/disabled deployments keep their existing UI. */ }
  });
  onDestroy(() => { disposed = true; void listener?.stop(); });

  async function toggle() {
    if (active) { await listener?.stop(); return; }
    listener = createListener({ baseUrl: apiBaseUrl, userId, language: language === 'he' ? 'he-IL' : 'en-US',
      onState: value => { if (!disposed) state = value; },
      onSuggestions: value => { if (!disposed) suggestions = value; }
    });
    await listener.start();
  }

  async function ask(suggestion) {
    if (disabled || busy) return;
    busy = true;
    // Consume the card synchronously; the chat's existing recovery uses this stable message ID.
    listener.dismiss(suggestion, 'accepted');
    try { await onQuestion(suggestion); }
    finally { busy = false; }
  }
</script>

{#if available}
  <aside class="listening-panel" aria-label={$_('assistant.listening.label')}>
    <button type="button" class:active onclick={toggle} aria-pressed={active}
      disabled={!active && disabled} data-feature-name="listening_toggle">
      {active ? $_('assistant.listening.stop') : $_('assistant.listening.start')}
    </button>
    <span role="status">{$_(`assistant.listening.state.${state}`)}</span>
    {#if !active}<p class="hint">{$_('assistant.listening.hint')}</p>{/if}
    {#if suggestions.length}
      <ul aria-label={$_('assistant.listening.suggestions')}>
        {#each suggestions.filter(s => s.status === 'pending').slice(-3) as suggestion (suggestion.id)}
          <li>
            <button class="question" type="button" disabled={disabled || busy}
              onclick={() => ask(suggestion)} data-feature-name="listening_question">{suggestion.question}</button>
            <button type="button" onclick={() => listener.dismiss(suggestion)}
              aria-label={$_('assistant.listening.dismiss')}>×</button>
          </li>
        {/each}
      </ul>
    {/if}
  </aside>
{/if}

<style>
  .listening-panel { padding: 8px 16px; border-top: 1px solid #ddd; font-size: 13px; }
  button { font: inherit; cursor: pointer; border: 1px solid #ccc; border-radius: 8px; padding: 8px; background: transparent; color: inherit; min-height: 44px; }
  button:disabled { opacity: .5; cursor: default; }
  button.active { color: #a32323; border-color: currentColor; }
  span { margin-inline-start: 8px; }
  .hint { margin: 4px 0; }
  ul { list-style: none; padding: 0; margin: 8px 0 0; max-height: 180px; overflow-y: auto; }
  li { display: flex; gap: 6px; margin-top: 6px; }
  .question { flex: 1; text-align: start; }
</style>
