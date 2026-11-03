<script>
  import { tick } from 'svelte';
  import { _ } from '../i18n/index.js';

  // label: a host's label (chatbot:launcher) in place of "✦ Ask", or ''.
  // ready: a reply landed while the assistant was closed.
  let { label = '', ready = false, onclick } = $props();

  // When the pill's label changes (Ask ↔ a host label), it grows or shrinks to the new width
  // over 200ms instead of jumping. No motion with reduced motion.
  function animateWidth(node) {
    let width = node.offsetWidth;
    return {
      async update() {
        const from = width;
        await tick(); // measure once the new label is in place
        width = node.offsetWidth;
        if (!from || from === width || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        node.style.overflow = 'hidden';
        node.animate([{ width: `${from}px` }, { width: `${width}px` }], { duration: 200, easing: 'cubic-bezier(.2, .8, .2, 1)' })
          .finished.finally(() => { node.style.overflow = ''; });
      }
    };
  }
</script>

<!-- Launcher: a "✦ Ask" pill in the corner -->
<!-- The name starts with the visible text, so voice control ("tap Ask") finds it -->
<button aria-label={label
          ? (ready ? $_('assistant.floating_button.aria_label_ready', { values: { label } }) : label)
          : $_(ready ? 'assistant.floating_button.aria_ready' : 'assistant.floating_button.aria')}
        class="lc-chatbot-ask" class:response-ready={ready}
        use:animateWidth={label}
        {onclick}>
  <!-- ✦ drawn as a shape: as text, its size depends on the host page's font -->
  <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
    <path fill="currentColor" d="M12 0C12.6 6.6 17.4 11.4 24 12C17.4 12.6 12.6 17.4 12 24C11.4 17.4 6.6 12.6 0 12C6.6 11.4 11.4 6.6 12 0Z"/>
  </svg>
  <span>{label || $_('assistant.floating_button.label')}</span>
  {#if ready}<span class="trigger-badge" aria-hidden="true"></span>{/if}
</button>

<style>
  /* LCChatbot's reset, which doesn't reach into this component */
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  /* Launcher color: purple, for the closed button only. Once open, the assistant keeps
     the Sefaria blue theme (sefaria-design-foundations Core Brand/Purple scale). */
  .lc-chatbot-ask {
    --purple-700: var(--mussar-purple, #7C416F); /* Sefaria-Project's --mussar-purple */
    --purple-800: #653259;
    --lc-entry-bg: var(--purple-700);
    /* Hover and pressed share the darker step, like Sefaria's header Library Assistant pill */
    --lc-entry-bg-hover: var(--purple-800);
    --lc-entry-bg-pressed: var(--purple-800);
  }

  .lc-chatbot-ask {
    display: flex;
    align-items: center;
    gap: var(--global-dimension-100);
    height: 56px;
    white-space: nowrap;
    padding-inline: var(--global-dimension-250) var(--global-dimension-300);
    background: var(--lc-entry-bg);
    color: var(--core-base-white);
    border: none;
    border-radius: 9999px;
    box-shadow: var(--lc-shadow);
    font-family: var(--lc-font);
    font-size: 16px;
    font-weight: 500;
    cursor: pointer;
  }

  /* Hover only where it exists: on touch screens it would stick after the tap */
  @media (hover: hover) {
    .lc-chatbot-ask:hover {
      background: var(--lc-entry-bg-hover);
    }
  }

  .lc-chatbot-ask:active {
    background: var(--lc-entry-bg-pressed);
  }

  /* A reply arrived while closed: a badge sits on the launcher, which nudges twice to
     draw the eye. The badge pings with each nudge, then stays put, still. */
  .response-ready {
    position: relative;
    animation: trigger-nudge 0.6s ease 2;
  }

  .trigger-badge {
    position: absolute;
    top: -2px;
    inset-inline-start: -2px;
    width: var(--global-dimension-150);
    height: var(--global-dimension-150);
    border-radius: 50%;
    background: var(--lc-danger);
    box-shadow: 0 0 0 2px var(--lc-bg);
  }

  .trigger-badge::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--lc-danger);
    animation: badge-ping 0.6s ease-out 2;
  }

  @keyframes trigger-nudge {
    0%, 100% { transform: translateY(0); }
    40% { transform: translateY(-6px); }
    70% { transform: translateY(0); }
    85% { transform: translateY(-2px); }
  }

  @keyframes badge-ping {
    0% { transform: scale(1); opacity: 0.6; }
    100% { transform: scale(2.4); opacity: 0; }
  }

  @media (prefers-reduced-motion: reduce) {
    .response-ready,
    .trigger-badge::after {
      animation: none;
    }
  }
</style>
