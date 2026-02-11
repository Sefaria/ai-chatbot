(() => {
  const SCRIPT_SRC = 'https://chat.cauldron.sefaria.org/static/js/chat/lc-chatbot.umd.cjs?v=d56d11c6';
  const CHATBOT_ID = 'lc-chatbot-injected';

  function ensureBody() {
    if (document.body) {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
    });
  }

  function injectScript() {
    const existing = document.querySelector(`script[src="${SCRIPT_SRC}"]`);
    if (existing) {
      return existing;
    }

    const script = document.createElement('script');
    script.type = 'module';
    script.src = SCRIPT_SRC;
    script.dataset.lcChatbot = 'true';
    const parent = document.head || document.documentElement;
    parent.appendChild(script);
    return script;
  }

  function insertChatbot() {
    if (document.getElementById(CHATBOT_ID)) {
      return;
    }

    const el = document.createElement('lc-chatbot');
    el.id = CHATBOT_ID;
    el.setAttribute('user-id', '12346');
    el.setAttribute('api-base-url', 'https://chat-dev.sefaria.org/api');
    el.setAttribute('default-open', 'false');
    el.setAttribute('placement', 'right');
    document.body.appendChild(el);
  }

  function onScriptReady() {
    ensureBody().then(insertChatbot);
  }

  const scriptEl = injectScript();

  if (customElements.get('lc-chatbot')) {
    onScriptReady();
  } else {
    scriptEl.addEventListener('load', onScriptReady, { once: true });
    scriptEl.addEventListener(
      'error',
      () => {
        console.warn('lc-chatbot injector: failed to load script', SCRIPT_SRC);
      },
      { once: true }
    );
  }
})();
