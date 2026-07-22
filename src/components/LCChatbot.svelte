<svelte:options customElement="lc-chatbot" />

<script>
  import { getStorage, setStorage, STORAGE_KEYS } from '../lib/storage.js';
  import { getOrCreateSession, updateSessionActivity, generateMessageId } from '../lib/session.js';
  import { sendMessageStream, loadHistory, fetchPromptDefaults, sendFeedback } from '../lib/api.js';
  import { tick } from 'svelte';
  import { renderMarkdown } from '../lib/markdown.js';
  import HeaderButton from './HeaderButton.svelte';
  import ProgressTrail from './ProgressTrail.svelte';
  import TopicAppetizer from './TopicAppetizer.svelte';
  import LocationTag from './LocationTag.svelte';
  import Accordion from './Accordion.svelte';
  import { setLocale, _ } from '../i18n/index.js';
  import { get } from 'svelte/store';

  const DEFAULT_MAX_PROMPTS = 100;
  const DEFAULT_MAX_INPUT_CHARS = 10000;
  // The release version of the deployed chatbot, used to tag analytics events with the build that produced them.
  // CI passes the version into the Docker build, and Vite bakes it in at build time.
  // In local dev there is no version, so fall back to null and gtag omits the field instead of sending an empty value.
  const APP_VERSION = import.meta.env.VITE_APP_VERSION || null;

  // Props (attributes)
  let {
    'user-id': userId = '',
    'api-base-url': apiBaseUrl = '',
    'default-open': defaultOpen = true,
    mode: modeProp = 'floating',
    'max-input-chars': maxInputChars = DEFAULT_MAX_INPUT_CHARS,
    'max-prompts': maxPrompts = DEFAULT_MAX_PROMPTS,
    origin: originProp = '',
    'is-moderator': isModerator = false,
    'interface-lang': interfaceLang = 'en'
  } = $props();

  // State
  let mode = $state('floating');
  let isOpen = $state(false);
  let messages = $state([]);
  let inputText = $state('');
  let isSending = $state(false);
  let isLoadingHistory = $state(false);
  let hasMoreHistory = $state(true);
  let sessionId = $state('');
  let panelWidth = $state(300);
  let panelHeight = $state(456);
  let isResizing = $state(false);
  let resizeEdge = $state(null);
  
  // Agent progress state
  let toolHistory = $state([]);
  let trailEntryId = $state(0);
  // The trail is the record of tools only. Status events ("Thinking...",
  // "Synthesizing response...") are excluded — the live status is rendered as a
  // persistent loader line below the tool record (see the loading block).
  let displayTrail = $derived(toolHistory.filter(e => e.type === 'tool'));
  // Final phase: once the backend emits the synthesizing status, the persistent
  // "Thinking" loader line is replaced by the text-only "Synthesizing Response".
  // statusKey is only ever set on type: 'status' entries, so checking it alone is sufficient.
  let isSynthesizing = $derived(toolHistory.some(e => e.statusKey === 'synthesizing'));
  let appetizerData = $state(null);

  // Auto-scroll controller
  let autoScrollEnabled = $state(true);
  // Per latency UX spec: on final response the top edge of the response package
  // is scrolled to sit 80px below the container top, clearing the package's top margin/padding.
  const RESPONSE_PACKAGE_TOP_OFFSET = 80;
  const SEFARIA_BASE_URL = 'https://www.sefaria.org';
  let loadingWrapperRef = $state(null);

  // Settings state
  let showSettings = $state(false);
  let promptSlugs = $state({
    corePromptSlug: '',
    labs: false
  });
  let defaultPromptSlugs = $state({
    corePromptSlug: '',
    labs: false
  });
  let settingsLoaded = $state(false);
  let isLoadingSettings = $state(false);
  let settingsError = $state('');

  let expandedSections = $state({});
  function toggleSection(key) {
    expandedSections[key] = !expandedSections[key];
  }

  let isClearing = $state(false);
  let isFirstTimeUser = $state(true);
  let isRestarted = $state(false);
  let isNewSession = $state(false);

  let turnCount = $state(0);
  let chatJustRestarted = $state(false);

  // maxPrompts and maxInputChars are set by admins in RemoteConfig but for security's sake, there are default absolute maximums
  // We want to use the minimum of the two values, thus allowing RemoteConfig to override the hardcoded defaults
  let effectiveMaxPrompts = $derived(Math.min(Number(maxPrompts), DEFAULT_MAX_PROMPTS));
  let effectiveMaxInputChars = $derived(Math.min(Number(maxInputChars), DEFAULT_MAX_INPUT_CHARS));

  let limitReached = $derived(turnCount >= effectiveMaxPrompts);

  // Menu state
  let showMenu = $state(false);
  let menuContainer = $state(null);
  // Feedback modal state
  let showFeedbackModal = $state(false);
  let feedbackModalMessageId = $state(null);
  let feedbackComment = $state('');
  let feedbackType = $state(null); // FEEDBACK_UP | FEEDBACK_DOWN
  let feedbackReason = $state(''); // For dislikes: selected reason category

  const STATUS_FAILED = 'failed';

  // Feedback score constants (must match backend SCORE_CHOICES)
  const FEEDBACK_UP = 'up';
  const FEEDBACK_DOWN = 'down';

  const FEEDBACK_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
  const THUMBUP = '<svg width="17" height="17" viewBox="0 0 17 17" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8.3457 6.439e-05C8.82494 0.00605952 9.29664 0.120247 9.72559 0.334049C10.1546 0.547943 10.53 0.856213 10.8232 1.23542C11.1165 1.61466 11.3208 2.05545 11.4199 2.52448C11.5187 2.9925 11.5096 3.47698 11.3955 3.94147L10.8975 6.00006H14.207C14.5695 6.00006 14.9277 6.08404 15.252 6.24616C15.576 6.4082 15.8577 6.64384 16.0752 6.93366C16.2926 7.22359 16.44 7.5605 16.5049 7.91706C16.5697 8.27354 16.5506 8.64049 16.4492 8.98835L14.7012 14.9883C14.5597 15.4733 14.2654 15.9001 13.8613 16.2032C13.4571 16.5063 12.9652 16.67 12.46 16.67H2.33496C1.71568 16.67 1.12149 16.4243 0.683594 15.9864C0.245697 15.5485 0 14.9543 0 14.335V8.33503C0 7.71574 0.245696 7.12156 0.683594 6.68366C1.12149 6.24576 1.71568 6.00006 2.33496 6.00006H4.4043C4.52801 6 4.64974 5.96566 4.75488 5.90045C4.86 5.83526 4.94496 5.74169 5 5.63092L7.58789 0.461002L7.64844 0.359439C7.80498 0.133378 8.0657 -0.00340299 8.3457 6.439e-05ZM6.49414 6.37604C6.30081 6.76418 6.0033 7.09086 5.63477 7.3194C5.56531 7.36247 5.49306 7.40024 5.41992 7.43561V15.0001H12.46C12.6038 15.0001 12.7443 14.9536 12.8594 14.8673C12.9743 14.781 13.0583 14.6595 13.0986 14.5215L14.8457 8.52155C14.8746 8.42244 14.8798 8.31746 14.8613 8.21589C14.8428 8.1144 14.8012 8.01813 14.7393 7.93561C14.6774 7.8532 14.5971 7.7864 14.5049 7.7403C14.4125 7.69413 14.3103 7.66999 14.207 7.66999H9.83496C9.57899 7.66999 9.33703 7.55274 9.17871 7.35163C9.0204 7.15029 8.96303 6.88665 9.02344 6.63776L9.77344 3.54792L9.77441 3.54499C9.82901 3.32384 9.83314 3.09306 9.78613 2.87018C9.73906 2.64723 9.6423 2.43718 9.50293 2.2569C9.36353 2.07661 9.18442 1.93085 8.98047 1.82917C8.92425 1.80114 8.86657 1.77666 8.80762 1.75592L6.49414 6.37604ZM1.66992 14.335C1.66992 14.5114 1.73955 14.681 1.86426 14.8057C1.98897 14.9304 2.15859 15.0001 2.33496 15.0001H3.75V7.66999H2.33496C2.15859 7.66999 1.98897 7.73961 1.86426 7.86432C1.73955 7.98903 1.66992 8.15866 1.66992 8.33503V14.335Z" fill="currentColor"/></svg>'
  const THUMBDOWN = '<svg width="17" height="17" viewBox="0 0 17 17" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.8716 2.33496C14.8716 2.15859 14.802 1.98897 14.6773 1.86426C14.5526 1.73968 14.3829 1.66992 14.2066 1.66992H12.7916V9H14.2066C14.3829 9 14.5526 8.93024 14.6773 8.80566C14.802 8.68095 14.8716 8.51133 14.8716 8.33496V2.33496ZM4.0816 1.66992C3.93795 1.67001 3.79812 1.71658 3.68316 1.80273C3.56816 1.88899 3.48424 2.01046 3.44391 2.14844L1.69586 8.14844C1.66695 8.24755 1.66177 8.35253 1.68023 8.4541C1.69872 8.55561 1.7404 8.65183 1.8023 8.73438C1.86414 8.81678 1.94456 8.88355 2.03668 8.92969C2.12902 8.97586 2.23129 9 2.33453 9H6.7066C6.96268 9 7.20551 9.11708 7.36383 9.31836C7.52214 9.51969 7.57853 9.78333 7.51812 10.0322L6.76812 13.1221V13.125C6.71352 13.3462 6.70938 13.5769 6.7564 13.7998C6.80348 14.0228 6.90021 14.2328 7.03961 14.4131C7.17892 14.5932 7.35734 14.7392 7.56109 14.8408C7.61711 14.8687 7.67521 14.8924 7.73394 14.9131L10.0474 10.2939C10.2407 9.90584 10.5384 9.57915 10.9068 9.35059C10.9763 9.30751 11.0485 9.26877 11.1216 9.2334V1.66992H4.0816ZM16.5416 8.33496C16.5416 8.95424 16.2959 9.54843 15.858 9.98633C15.4201 10.4241 14.8258 10.6699 14.2066 10.6699H12.1373C12.0137 10.67 11.8927 10.7045 11.7877 10.7695C11.6825 10.8347 11.5976 10.9283 11.5425 11.0391L8.95367 16.209C8.81047 16.4948 8.51653 16.6738 8.19683 16.6699C7.71735 16.664 7.24512 16.5499 6.81598 16.3359C6.3869 16.122 6.01161 15.8139 5.71832 15.4346C5.42511 15.0554 5.22171 14.6145 5.12262 14.1455C5.02356 13.6763 5.03111 13.1902 5.14605 12.7246L5.64508 10.6699H2.33453C1.97218 10.6699 1.61471 10.5858 1.29058 10.4238C0.966416 10.2617 0.683849 10.0263 0.466366 9.73633C0.248938 9.44642 0.102533 9.10945 0.0376551 8.75293C-0.0271694 8.39639 -0.00809404 8.02954 0.0933192 7.68164L1.84039 1.68164L1.90094 1.50195C2.05771 1.09146 2.32764 0.731974 2.68121 0.466797C3.08524 0.163841 3.57661 9.28572e-05 4.0816 0H14.2066C14.8258 0 15.4201 0.245831 15.858 0.683594C16.2959 1.12149 16.5416 1.71568 16.5416 2.33496V8.33496Z" fill="currentColor"/></svg>'

  $effect(() => {
    setLocale(interfaceLang);
  });

  let welcomeMessage = $derived($_('assistant.welcome.message'));
  let restartMessage = $derived($_('assistant.welcome.restart'));
  let newSessionMessage = $derived($_('assistant.welcome.newSession'));

  // Feedback issue options for dislikes — labels resolved reactively from the i18n store
  const DISLIKE_REASON_KEYS = [
    { value: 'inaccurate', key: 'assistant.feedback.reason.inaccurate' },
    { value: 'disrespectful', key: 'assistant.feedback.reason.disrespectful' },
    { value: 'unhelpful', key: 'assistant.feedback.reason.unhelpful' },
    { value: 'overly_definitive', key: 'assistant.feedback.reason.overlyDefinitive' },
    { value: 'tech_issue', key: 'assistant.feedback.reason.techIssue' },
    { value: 'other', key: 'assistant.feedback.reason.other' }
  ];
  let DISLIKE_REASONS = $derived(DISLIKE_REASON_KEYS.map(r => ({ value: r.value, label: $_(r.key) })));

  // Refs
  let messageListRef = $state(null);
  let inputRef = $state(null);

  // Derive static base URL by removing '/api' suffix from apiBaseUrl
  let staticBaseUrl = $derived(apiBaseUrl.replace(/\/api\/?$/, ''));
  let staticIconsBaseUrl = `${staticBaseUrl}/static/icons`;

  function getTestingVersionFromApiBaseUrl(url) {
    if (!url) return '';

    try {
      const normalizedUrl =
        url.startsWith('http://') || url.startsWith('https://') ? url : `https://${url}`;
      const hostname = new URL(normalizedUrl).hostname;
      const match = hostname.match(/^(\d+)\.ai-server\.coolifydev\.sefaria\.org$/i);
      return match ? match[1] : '';
    } catch {
      return '';
    }
  }

  let testingVersion = $derived(getTestingVersionFromApiBaseUrl(apiBaseUrl));

  // Size constraints
  const MIN_WIDTH = 300;
  const MIN_HEIGHT = 456;
  const MAX_WIDTH = 640;
  const MAX_HEIGHT_RATIO = 0.8;

  // Initialize on mount
  $effect(() => {
    // Initialize session
    const { sessionId: sid, isNew } = getOrCreateSession();
    sessionId = sid;
    isNewSession = isNew;
    isFirstTimeUser = !getStorage(STORAGE_KEYS.HAS_USED, false);

    // Restore UI state
    const savedUI = getStorage(STORAGE_KEYS.UI, null);
    isOpen = savedUI?.isOpen ?? defaultOpen;
    if (savedUI?.mode) {
      mode = savedUI.mode;
    } else {
      mode = modeProp;
    }

    // Restore size
    const savedSize = getStorage(STORAGE_KEYS.SIZE, null);
    if (savedSize) {
      const maxHeight = window.innerHeight * MAX_HEIGHT_RATIO;
      panelWidth = Math.max(MIN_WIDTH, Math.min(savedSize.width, MAX_WIDTH));
      panelHeight = Math.max(MIN_HEIGHT, Math.min(savedSize.height, maxHeight));
    }

    // Restore draft
    const savedDraft = getStorage(STORAGE_KEYS.DRAFT, null);
    if (savedDraft?.text) {
      inputText = savedDraft.text;
    }

    // Restore prompt slugs
    const savedPromptSlugs = getStorage(STORAGE_KEYS.PROMPT_SLUGS, null);
    if (savedPromptSlugs) {
      promptSlugs = {
        corePromptSlug: savedPromptSlugs.corePromptSlug || '',
        labs: savedPromptSlugs.labs === true
      };
      settingsLoaded = true;
    }

    // Load messages from local storage
    const savedMessages = getStorage(STORAGE_KEYS.MESSAGES + ':' + sid, []);
    messages = savedMessages;
  });

  // Sync turn limits from server when panel opens (skip when chat was just restarted)
  $effect(() => {
    if (sessionId && apiBaseUrl && isOpen) {
      if (chatJustRestarted) {
        chatJustRestarted = false;
        return;
      }
      syncSessionState();
    }
  });

  // Save draft on input change
  $effect(() => {
    if (inputText) {
      setStorage(STORAGE_KEYS.DRAFT, { text: inputText });
    }
  });

  // GA4 tracking: attach listener on the host element (light DOM) so it
  // receives clicks bubbling out of the shadow root
  $effect(() => {
    const host = $host();
    if (!host) return;

    function trackClick(e) {
      const path = e.composedPath();
      // Skip the toggle button — toggleMode() fires its own specific event
      const isToggle = path.some(
        el => el instanceof Element && el.getAttribute('aria-label') === 'Toggle docked/floating'
      );
      if (isToggle) return;
      // Explicit analytics label wins over the generic link/aria-label logic
      // below (e.g. appetizer topic links, thinking-step links are <a> elements).
      const labelled = path.find(
        el => el instanceof Element && el.getAttribute('data-feature-name')
      );
      if (labelled) {
        if (typeof window.gtag === 'function') {
          window.gtag('event', 'assistant_click', { feature_name: labelled.getAttribute('data-feature-name'), link_text: labelled.textContent.trim() });
        }
        return;
      }
      // If a response link was clicked — capture the link text
      const link = path.find(
        el => el instanceof Element && el.tagName === 'A' && el.getAttribute('href')
      );
      if (link) {
        if (typeof window.gtag === 'function') {
          const raw = link.getAttribute('href');
          const link_url = raw.startsWith('http') ? new URL(raw).pathname + (new URL(raw).search || '') : raw;
          const link_text = link.textContent.trim();
          window.gtag('event', 'assistant_click', { feature_name: 'Response link', text: link_text, link_url, link_text, la_version: APP_VERSION });
        }
        return;
      }

      // Otherwise walk up the path for the nearest aria-label. No link_text here:
      // this fallback matches broad containers (e.g. the "Chat messages" scroll
      // region) whose textContent is the whole conversation, not a link label.
      const target = path.find(
        el => el instanceof Element && el.getAttribute('aria-label')
      );
      if (!target) return;
      if (typeof window.gtag === 'function') {
        window.gtag('event', 'assistant_click', { feature_name: target.getAttribute('aria-label'), la_version: APP_VERSION });
      }
    }

    host.addEventListener('click', trackClick);
    return () => host.removeEventListener('click', trackClick);
  });

  // GA4 tracking: fire 'assistant_element_shown' the first time an element with
  // data-element-shown-name scrolls into view within the messages panel. Content
  // (thinking steps, appetizer topics, location tag) streams in after mount, so
  // this watches for new matching elements via MutationObserver rather than
  // scanning once.
  $effect(() => {
    if (!messageListRef) return;
    const root = messageListRef;

    const seen = new WeakSet();
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const el = entry.target;
        io.unobserve(el);
        if (seen.has(el)) continue;
        seen.add(el);
        if (typeof window.gtag === 'function') {
          window.gtag('event', 'assistant_element_shown', { feature_name: el.getAttribute('data-element-shown-name') });
        }
      }
    }, { root, threshold: 0.5 });

    function observe(node) {
      if (!(node instanceof Element)) return;
      if (node.hasAttribute('data-element-shown-name') && !seen.has(node)) {
        io.observe(node);
      }
      node.querySelectorAll?.('[data-element-shown-name]').forEach(el => {
        if (!seen.has(el)) io.observe(el);
      });
    }

    observe(root);

    const mo = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach(observe);
      }
    });
    mo.observe(root, { childList: true, subtree: true });

    return () => {
      io.disconnect();
      mo.disconnect();
    };
  });

  // Dispatch custom events
  function dispatchEvent(name, detail = {}) {
    const event = new CustomEvent(`chatbot:${name}`, {
      bubbles: true,
      composed: true,
      detail
    });
    document.dispatchEvent(event);
  }

  function openPanel() {
    isOpen = true;
    showSettings = false;
    setStorage(STORAGE_KEYS.UI, { isOpen: true, mode });
    dispatchEvent('opened');

    // Focus input after panel opens
    setTimeout(() => {
      inputRef?.focus();
    }, 100);

  }

  function closePanel() {
    isOpen = false;
    showSettings = false;
    setStorage(STORAGE_KEYS.UI, { isOpen: false, mode });
    dispatchEvent('closed');
  }

  function toggleMode() {
    const newMode = mode === 'floating' ? 'docked' : 'floating';
    mode = newMode;
    const savedUI = getStorage(STORAGE_KEYS.UI, null) || {};
    setStorage(STORAGE_KEYS.UI, { ...savedUI, mode });
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'assistant_click', { feature_name: `Toggle to ${newMode}`, la_version: APP_VERSION });
    }
  }

  function handleNewChat() {
    if (isSending) return;

    const { sessionId: newSessionId } = getOrCreateSession(true);
    chatJustRestarted = true; // Skip sync — session doesn't exist on server yet
    sessionId = newSessionId;
    messages = [];
    inputText = '';
    isLoadingHistory = false;
    hasMoreHistory = false;

    toolHistory = [];
    turnCount = 0;

    setStorage(STORAGE_KEYS.DRAFT, { text: '' });
    setStorage(STORAGE_KEYS.MESSAGES + ':' + newSessionId, []);
  }

  async function openSettings() {
    showSettings = true;
    settingsError = '';

    if (!settingsLoaded && apiBaseUrl) {
      isLoadingSettings = true;
      try {
        const defaults = await fetchPromptDefaults(apiBaseUrl);
        defaultPromptSlugs = {
          corePromptSlug: defaults.corePromptSlug || '',
          labs: defaults.labs === true
        };
        promptSlugs = {
          corePromptSlug: promptSlugs.corePromptSlug || defaultPromptSlugs.corePromptSlug,
          labs: promptSlugs.labs === true
        };
        settingsLoaded = true;
      } catch (e) {
        settingsError = e.message || 'Failed to load settings.';
      } finally {
        isLoadingSettings = false;
      }
    }
  }

  function closeSettings() {
    showSettings = false;
    settingsError = '';
  }

  function saveSettings() {
    setStorage(STORAGE_KEYS.PROMPT_SLUGS, {
      corePromptSlug: promptSlugs.corePromptSlug || '',
      labs: promptSlugs.labs === true
    });
    settingsError = '';
  }

  async function resetSettings() {
    settingsError = '';
    if (!apiBaseUrl) {
      settingsError = 'API base URL is missing.';
      return;
    }

    isLoadingSettings = true;
    try {
      const defaults = await fetchPromptDefaults(apiBaseUrl);
      defaultPromptSlugs = {
        corePromptSlug: defaults.corePromptSlug || '',
        labs: defaults.labs === true
      };
      promptSlugs = { ...defaultPromptSlugs };
      setStorage(STORAGE_KEYS.PROMPT_SLUGS, { ...defaultPromptSlugs });
      settingsLoaded = true;
    } catch (e) {
      settingsError = e.message || 'Failed to reset settings.';
    } finally {
      isLoadingSettings = false;
    }
  }

  async function syncSessionState() {
    if (!userId || !sessionId || !apiBaseUrl) return;

    try {
      const result = await loadHistory(apiBaseUrl, userId, sessionId, null, 20);

      if (result.session) {
        turnCount = result.session.turnCount ?? 0;
      }

      // Only load messages if we don't have any locally
      if (messages.length === 0 && result.messages.length > 0) {
        messages = result.messages;
        hasMoreHistory = result.hasMore;
        saveMessagesToStorage();
        scrollToBottom();
      }
    } catch (e) {
      console.warn('[lc-chatbot] Failed to sync session state:', e);
    }
  }

  async function loadMoreHistory() {
    if (isLoadingHistory || !hasMoreHistory || messages.length === 0) return;
    
    const oldestMessage = messages[0];
    if (!oldestMessage) return;

    isLoadingHistory = true;
    try {
      const result = await loadHistory(apiBaseUrl, userId, sessionId, oldestMessage.timestamp, 20);
      messages = [...result.messages, ...messages];
      hasMoreHistory = result.hasMore;
      saveMessagesToStorage();
    } catch (e) {
      console.warn('[lc-chatbot] Failed to load more history:', e);
    } finally {
      isLoadingHistory = false;
    }
  }

  function saveMessagesToStorage() {
    setStorage(STORAGE_KEYS.MESSAGES + ':' + sessionId, messages);
  }

  async function scrollToBottom() {
    await tick();
    if (messageListRef) {
      messageListRef.scrollTop = messageListRef.scrollHeight - messageListRef.clientHeight;
    }
  }

  /** Returns the scrollTop value that places el's top edge at the container's top edge. */
  function getScrollTopForElement(el) {
    const containerRect = messageListRef.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    return messageListRef.scrollTop + elRect.top - containerRect.top;
  }

  async function scrollToResponseStart() {
    await tick();
    if (!messageListRef || !autoScrollEnabled) return;
    // Prefer to scroll so the .lc-response-package top sits RESPONSE_PACKAGE_TOP_OFFSET px below container top.
    // querySelectorAll returns a NodeList (no Array.at), so spread before indexing.
    const pkgEl = [...messageListRef.querySelectorAll('.lc-response-package')].at(-1);
    if (pkgEl) {
      applyAutoScroll(getScrollTopForElement(pkgEl) - RESPONSE_PACKAGE_TOP_OFFSET);
      return;
    }

    // Fallback: scroll to the last assistant message top.
    const contents = [...messageListRef.querySelectorAll('.message.assistant .message-content')];
    const lastResponse = contents.at(-1)?.closest('.message.assistant');
    if (!lastResponse) return;
    applyAutoScroll(getScrollTopForElement(lastResponse));
  }

  function applyAutoScroll(top) {
    messageListRef.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  function resetScroll() {
    autoScrollEnabled = true;
  }

  async function scrollToLoadingElement() {
    if (!autoScrollEnabled || !messageListRef) return;
    await tick();
    // Always scroll so the bottom of the loading wrapper (newest step) is visible.
    // We unconditionally target the wrapper bottom rather than checking rects, because
    // in-progress smooth scrolls leave getBoundingClientRect() in an intermediate state
    // that causes the condition checks to incorrectly skip the scroll.
    const el = loadingWrapperRef || messageListRef.querySelector('.lc-loading-wrapper');
    if (!el) return;

    const containerHeight = messageListRef.clientHeight;
    const elTop = getScrollTopForElement(el);
    const elBottom = elTop + el.offsetHeight;

    if (el.offsetHeight <= containerHeight) {
      // Wrapper fits: keep its top in view (don't scroll past the top edge).
      applyAutoScroll(elTop);
    } else {
      // Wrapper taller than viewport: show its bottom (newest content).
      applyAutoScroll(elBottom - containerHeight);
    }
  }

  async function handleSend() {
    const text = inputText.trim();
    const isConfigured = userId && apiBaseUrl;
    const isReadyToSend = text && !isSending && !limitReached;
    if (!isConfigured || !isReadyToSend) return;
    // Reset auto-scroll on each new send
    resetScroll();
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'assistant_message_sent', { length: text.length, la_version: APP_VERSION });
    }
    // Clear input and draft
    inputText = '';
    setStorage(STORAGE_KEYS.DRAFT, { text: '' });

    // Create user message
    const locationRef = await parseSefariaRef(window.location.href);
    const userMessage = {
      messageId: generateMessageId(),
      sessionId,
      userId,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
      status: 'sending',
      locationRef
    };

    messages = [...messages, userMessage];
    saveMessagesToStorage();
    scrollToBottom();

    isSending = true;

    toolHistory = [];
    trailEntryId = 0;
    appetizerData = null;
    updateSessionActivity(sessionId);

    try {
      const response = await sendMessageStream(apiBaseUrl, userId, sessionId, text, {
        onProgress: (progress) => {
          if (progress?.type === 'appetizer' && progress.appetizerData) {
            appetizerData = progress.appetizerData;
            // Dump the full served sentence (frame + topic titles) into `text` so
            // analytics can measure what was actually shown — e.g. "While we prepare
            // a response, explore sources about Prayer." The server sends the
            // comma-joined, locale-aware titles as progress.text; we splice them into
            // the localized sentence frame (same string the user sees).
            if (typeof window.gtag === 'function' && progress.text) {
              const shownText = get(_)('assistant.appetizer.sentence').replace('{topics}', progress.text);
              window.gtag('event', 'assistant_element_shown', { feature_name: 'related_topics', text: shownText });
            }
            scrollToLoadingElement();
            return;
          }
          let displayText;
          if (progress?.type === 'status') {
            displayText = progress.text;
            toolHistory = [...toolHistory, {
              id: trailEntryId++,
              type: 'status',
              statusKey: /synthesi/i.test(progress.text || '') ? 'synthesizing' : 'thinking',
              text: displayText?.replace(/…|\.\.\./, '') || '',
              status: 'running',
              startTime: Date.now()
            }];
            scrollToLoadingElement();
          } else if (progress?.type === 'tool_start') {
            displayText = progress.description || `Running ${progress.toolName}`;
            toolHistory = [...toolHistory, {
              id: trailEntryId++,
              type: 'tool',
              toolName: progress.toolName,
              description: displayText?.replace(/…|\.\.\./, '') || '',
              status: 'running',
              startTime: Date.now(),
              refData: progress.refData ?? null,
              toolInput: progress.toolInput ?? null
            }];
            scrollToLoadingElement();
          } else if (progress.type === 'tool_end') {
            const idx = toolHistory.findLastIndex(t =>
              t.type === 'tool' && t.status === 'running' && t.toolName === progress.toolName
            );
            if (idx !== -1) {
              toolHistory = toolHistory.map((t, i) =>
                i === idx
                  ? { ...t, status: progress.isError ? 'error' : 'complete', duration: Date.now() - t.startTime }
                  : t
              );
            }
          }
        },
        onError: (error) => {
          console.error('[lc-chatbot] Stream error:', error);
        }
      }, promptSlugs, originProp, isModerator, promptSlugs.labs === true, {
        messageId: userMessage.messageId,
        timestamp: userMessage.timestamp
      }, interfaceLang);

      // Update user message status
      messages = messages.map(m => 
        m.messageId === userMessage.messageId 
          ? { ...m, status: 'sent' }
          : m
      );

      // Persist only the tool record (no status entries), marking any
      // still-running tool entries as complete.
      const finalTrail = toolHistory
        .filter(t => t.type === 'tool')
        .map(t => (t.status === 'running' ? { ...t, status: 'complete' } : t));

      // Add assistant response
      const assistantMessage = {
        messageId: response.messageId,
        sessionId: response.sessionId,
        userId,
        role: 'assistant',
        content: response.markdown,
        timestamp: response.timestamp,
        status: 'sent',
        traceId: response.traceId || null,
        feedback: null,
        toolCalls: response.toolCalls,
        stats: response.stats,
        toolHistory: finalTrail,
        appetizerData: appetizerData ? {...appetizerData} : null
      };

      messages = [...messages, assistantMessage];
      saveMessagesToStorage();
      scrollToResponseStart();

      // Update turn count from server response
      if (response.session) {
        turnCount = response.session.turnCount ?? 0;
      }
      if (isFirstTimeUser) {
        isFirstTimeUser = false;
        setStorage(STORAGE_KEYS.HAS_USED, true);
      }
      if (isRestarted) {
        isRestarted = false;
      }

      dispatchEvent('message_sent', {
        messageId: userMessage.messageId,
        sessionId,
        toolCalls: response.toolCalls,
        stats: response.stats
      });

    } catch (e) {
      console.error('[lc-chatbot] Send failed:', e);

      // Mark message as failed for other errors
      messages = messages.map(m =>
        m.messageId === userMessage.messageId
          ? { ...m, status: STATUS_FAILED }
          : m
      );
      saveMessagesToStorage();

      dispatchEvent('error', {
        type: 'send_failed',
        messageId: userMessage.messageId,
        error: e.message
      });
    } finally {
      isSending = false;
  
      toolHistory = [];
    }
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  async function handleFeedback(messageId, score) {
    const target = messages.find(m => m.messageId === messageId);
    if (!target?.traceId || !apiBaseUrl) return;

    // Show the feedback modal for both likes and dislikes
    feedbackModalMessageId = messageId;
    feedbackComment = '';
    feedbackReason = '';
    feedbackType = score === 1 ? FEEDBACK_UP : FEEDBACK_DOWN;
    showFeedbackModal = true;

    // Update UI immediately to show selection
    messages = messages.map(m =>
      m.messageId === messageId ? { ...m, feedback: feedbackType } : m
    );
  }

  function closeFeedbackModal() {
    showFeedbackModal = false;
    feedbackModalMessageId = null;
    feedbackComment = '';
    feedbackType = null;
    feedbackReason = '';
  }

  async function submitFeedback(includeDetails = true) {
    const target = messages.find(m => m.messageId === feedbackModalMessageId);
    try {
      if (target?.traceId) {
        await sendFeedback(apiBaseUrl, {
          traceId: target.traceId,
          score: feedbackType,
          userId,
          sessionId,
          messageId: feedbackModalMessageId,
          comment: includeDetails ? feedbackComment : '',
          feedbackReason: includeDetails ? feedbackReason : ''
        });
      }
    } catch (e) {
      console.warn('[lc-chatbot] Feedback failed:', e);
    } finally {
      closeFeedbackModal();
    }
  }

  function handleScroll(e) {
    const el = e.target;
    // Load more when near top (within 50px)
    if (el.scrollTop < 50 && hasMoreHistory && !isLoadingHistory) {
      loadMoreHistory();
    }
  }

  // Genuine user-scroll intent is detected via explicit input events (wheel/touch)
  // rather than scroll-position drift, which programmatic smooth scrolls trip falsely.
  function handleWheel() {
    if (isSending) autoScrollEnabled = false;
  }

  function handleTouchMove() {
    if (isSending) autoScrollEnabled = false;
  }

  async function retryMessage(messageId) {
    const failedMessage = messages.find(m => m.messageId === messageId && m.status === STATUS_FAILED);
    if (!failedMessage) return;

    // Remove the failed message and resend
    messages = messages.filter(m => m.messageId !== messageId);
    inputText = failedMessage.content;
    await handleSend();
  }

  // Resize handling
  function startResize(edge, e) {
    e.preventDefault();

    // In docked mode, only allow horizontal resize (e/w)
    const allowHorizontal = edge.includes('w') || edge.includes('e');
    const allowVertical = (edge.includes('n') || edge.includes('s')) && mode !== 'docked'; 

    if (!allowHorizontal && !allowVertical) return;

    isResizing = true;
    resizeEdge = edge;

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = panelWidth;
    const startHeight = panelHeight;

    function onMouseMove(e) {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const maxHeight = window.innerHeight * MAX_HEIGHT_RATIO;

      if (allowHorizontal) {
        const widthDelta = resizeEdge.includes('w') ? -dx : dx;
        panelWidth = Math.max(MIN_WIDTH, Math.min(startWidth + widthDelta, MAX_WIDTH));
      }

      if (allowVertical) {
        const heightDelta = resizeEdge.includes('n') ? -dy : dy;
        panelHeight = Math.max(MIN_HEIGHT, Math.min(startHeight + heightDelta, maxHeight));
      }
    }

    function onMouseUp() {
      isResizing = false;
      resizeEdge = null;
      setStorage(STORAGE_KEYS.SIZE, { width: panelWidth, height: panelHeight });
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    }

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  function handleMessageLinkClick(e) {
    const anchor = e.target?.closest?.('a');
    if (!anchor) return;

    const href = anchor.getAttribute('href');
    if (!href) return;

    e.preventDefault();

    let resolvedUrl;
    try {
      resolvedUrl = new URL(href, window.location.href);
    } catch {
      return;
    }

    const sheetMatch = resolvedUrl.pathname.match(/^\/sheets\/([^/?#]+)\/?$/);
    if (sheetMatch) {
      const rebasedSheetUrl = `${window.location.origin}/sheets/${sheetMatch[1]}`;
      window.open(rebasedSheetUrl, '_blank', 'noopener,noreferrer');
      return;
    }

    // Only dispatch in-page navigation for Sefaria URLs on a Sefaria host.
    // External links and off-Sefaria embeds fall back to a new tab.
    const isSefariaDomain = (h) => h === '' || h.includes('sefaria.org');
    if (!isSefariaDomain(resolvedUrl.hostname)) {
      window.open(resolvedUrl.href, '_blank', 'noopener,noreferrer');
      return;
    }
    if (!isSefariaDomain(window.location.hostname)) {
      window.open(`${SEFARIA_BASE_URL}${resolvedUrl.pathname}${resolvedUrl.search}${resolvedUrl.hash}`, '_blank', 'noopener,noreferrer');
      return;
    }

    const path = resolvedUrl.pathname + resolvedUrl.search + resolvedUrl.hash;

    document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', {
      detail: {
        url: path,
        replaceHistory: true
      }
    }));
  }

  function toggleMenu() {
    showMenu = !showMenu;
  }

  function closeMenu() {
    showMenu = false;
  }

  $effect(() => {
    if (!showMenu) return;

    function handleClickOutside(e) {
      if (!e.composedPath().includes(menuContainer)) {
        closeMenu();
      }
    }

    // Defer so the click that opened the menu doesn't immediately trigger close
    const timeoutId = setTimeout(() => {
      document.addEventListener('click', handleClickOutside);
    }, 0);

    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener('click', handleClickOutside);
    };
  });

  function handleRestartConvo() {
    closeMenu();
    isRestarted = true;
    handleNewChat();
  }

  function normalizeAppetizerData(raw) {
    if (!raw) return null;
    if (raw.topics) return raw;
    return { topics: [{ topicSlug: raw.topicSlug, topicTitle: raw.topicTitle, topicUrl: raw.topicUrl }] };
  }

  /** Returns true for sefaria.org, *.sefaria.org (incl. voices.sefaria.org), sefaria.org.il, *.sefaria.org.il */
  function isSefariaHostname(hostname) {
    return /(^|\.)sefaria\.org(\.il)?$/.test(hostname);
  }

  function refToUrlPath(ref) {
    const m = ref.match(/^(.+?)[\s.](\d[\w:.\-–]*)$/);
    if (!m) {
      return null;
    }
    const book = m[1].trim().replace(/\s+/g, '_');
    const section = m[2].replace(/:/g, '.');
    return `${book}.${section}`;
  }

  function refLabelFromTref(ref) {
    if (!/\s/.test(ref)) {
      const m = ref.match(/^(.+?)\.(\d[\w.\-–]*)$/);
      if (m) {
        return `${m[1].replace(/_/g, ' ')} ${m[2].replace(/\./g, ':')}`;
      }
    }
    return ref;
  }

  /**
   * Base host for ref resolution + links: the current Sefaria host when embedded
   * on one (prod, .org.il, or a cauldron/staging), else canonical prod. This lets
   * the ref API be exercised on whatever environment the widget runs in.
   */
  function sefariaBase() {
    if (isSefariaHostname(window.location.hostname)) {
      return window.location.origin;
    }
    return SEFARIA_BASE_URL;
  }

  async function parseSefariaRef(href) {
    const tref = extractCandidateTref(href);
    if (!tref) {
      return null;
    }
    const refData = await fetchRefData(tref);
    if (refData && refData.is_ref) {
      const label = (interfaceLang === 'he' && refData.hebrew) ? refData.hebrew : refData.normalized;
      return { label, url: `${sefariaBase()}/${refData.url_ref}` };
    }
    // Fallback (feature: location pin) — /api/ref unavailable: derive URL+label from the tref.
    const fallbackPath = refToUrlPath(tref);
    if (!fallbackPath) {
      return null;
    }
    return { label: refLabelFromTref(tref), url: `${sefariaBase()}/${fallbackPath}` };
  }

  function extractCandidateTref(href) {
    let url;
    try {
      url = new URL(href);
    } catch {
      return null;
    }
    if (!isSefariaHostname(url.hostname)) {
      return null;
    }
    const path = decodeURIComponent(url.pathname).replace(/^\//, '');
    const skip = /^(topics|sheets|search|profile|collections|groups|community|static|api|questions|calendars|donate|account|login|register)\//i;
    if (!path || skip.test(path)) {
      return null;
    }
    return path;
  }

  async function fetchRefData(tref) {
    try {
      const res = await fetch(`${sefariaBase()}/api/ref/${encodeURIComponent(tref)}`);
      if (!res.ok) {
        return null;
      }
      return await res.json();
    } catch {
      return null;
    }
  }

  function handleLocationClick(url) {
    try {
      const urlObj = new URL(url);
      const hostname = urlObj.hostname;
      if (isSefariaHostname(hostname)) {
        document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', { detail: { url } }));
      } else {
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  function handleAppetizerClick(topicSlug, topicUrl) {
    const onSefaria = window.location.hostname.includes('sefaria.org');

    if (onSefaria) {
      // In-page navigation via ReaderApp's existing event listener
      document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', {
        detail: { url: `/topics/${topicSlug}` }
      }));
    } else {
      // Off-site: open topic page in new tab
      window.open(topicUrl || `${SEFARIA_BASE_URL}/topics/${topicSlug}`, '_blank', 'noopener,noreferrer');
    }

    const el = $host();
    if (el) {
      el.dispatchEvent(new CustomEvent('appetizer_click', {
        detail: { topicSlug, sessionId },
        bubbles: true,
        composed: true
      }));
    }
  }

  function getEmptyStateMessage() {
    if (isFirstTimeUser) return welcomeMessage;
    if (isRestarted) return restartMessage;
    if (isNewSession) return newSessionMessage;
    return welcomeMessage;
  }

</script>

<div
  class="lc-chatbot-container"
  class:mode-floating={mode === 'floating'}
  class:mode-docked={mode === 'docked'}
  class:is-open={isOpen}
  class:interface-hebrew={interfaceLang === 'he'}
>
  {#if !isOpen}
    <!-- Floating Button -->
    <button aria-label={$_('assistant.header.openAssistant')} class="lc-chatbot-trigger" onclick={openPanel}>
      <img src="{staticIconsBaseUrl}/logo.svg"/>
      <span class="trigger-label">{$_('assistant.header.triggerLabel')}</span>
    </button>
  {:else}
    <!-- Chat Panel -->
    <div 
      class="lc-chatbot-panel"
      class:resizing={isResizing}
      style="width: {panelWidth}px;{mode === 'docked' && isOpen ? '' : ` height: ${panelHeight}px;`}"
      role="dialog"
      aria-label={$_('assistant.header.chatWindow')}
    >
      <!-- Resize Handles - visual-only affordances for mouse resizing -->
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-n" onmousedown={(e) => startResize('n', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-s" onmousedown={(e) => startResize('s', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-e" onmousedown={(e) => startResize('e', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-w" onmousedown={(e) => startResize('w', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-ne" onmousedown={(e) => startResize('ne', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-nw" onmousedown={(e) => startResize('nw', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-se" onmousedown={(e) => startResize('se', e)}></div>
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
      <div class="resize-handle resize-sw" onmousedown={(e) => startResize('sw', e)}></div>

      <!-- Header -->
      <header class="lc-chatbot-header" role="banner">
        <div class="header-left">
          <h2>{$_('assistant.title')} {#if testingVersion}(V{testingVersion}){/if}
          <img src="{staticIconsBaseUrl}/AI.svg" alt={$_('assistant.badge.ai')} />
          </h2>
        </div>
        <div class="header-actions">
          <HeaderButton
            className="panel-btn"
            title={(mode === 'floating') ? $_('assistant.header.dock.tooltip') : $_('assistant.header.undock.tooltip')}
            onClick={(e) => { e.stopPropagation(); toggleMode(); }}
          >
            <img
              class:panel-close-icon={mode === 'floating'}
              src="{staticIconsBaseUrl}/{(mode === 'floating') ? 'panel-right-close' : 'minimize'}.svg"
              alt=""
              width="16"
              height="16"
            />
          </HeaderButton>
          <div class="menu-container" bind:this={menuContainer}>
            <HeaderButton className="menu-btn" onClick={toggleMenu} title={$_('assistant.header.moreOptions')} aria-expanded={showMenu}>
              <img src="{staticIconsBaseUrl}/ellipsis-vertical.svg" alt="" width="18" height="18" />
            </HeaderButton>
            {#if showMenu}
              <div class="menu-dropdown" role="menu">
                {#if isModerator}
                  <button class="menu-item" aria-label={$_('assistant.menu.settings.aria')} onclick={() => { openSettings(); closeMenu(); }} role="menuitem">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <circle cx="12" cy="12" r="3"></circle>
                      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .64.38 1.22.97 1.49.22.1.46.15.7.15H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                    </svg>
                    {$_('assistant.menu.settings')}
                  </button>
                {/if}
                <button class="menu-item" aria-label={$_('assistant.menu.restart.aria')} onclick={handleRestartConvo} disabled={isSending} role="menuitem">
                  <img src="{staticIconsBaseUrl}/rotate-ccw.svg" alt="" width="16" height="16" />
                  {$_('assistant.menu.restart')}
                </button>
                <a class="menu-item" aria-label={$_('assistant.menu.feedback')} href={$_('assistant.menu.feedbackURL')} target="_blank" rel="noopener noreferrer" role="menuitem" onclick={closeMenu}>
                  {@html FEEDBACK_ICON}
                  {$_('assistant.menu.feedback')}
                </a>
<a class="menu-item" aria-label={$_('assistant.menu.help.aria')} href={$_('assistant.menu.helpURL')} target="_blank" rel="noopener noreferrer" role="menuitem" onclick={closeMenu}>
                  <img src="{staticIconsBaseUrl}/info.svg" alt="" width="16" height="16" />
                  {$_('assistant.menu.help')}
                </a>
                <a class="menu-item" aria-label={$_('assistant.menu.optOut.aria')} href="/settings/account" role="menuitem" onclick={closeMenu}>
                  <img src="{staticIconsBaseUrl}/toggle-right.svg" alt="" width="16" height="16" />
                  {$_('assistant.menu.optout')}
                </a>
              </div>
            {/if}
          </div>
          <HeaderButton className="close-btn" onClick={closePanel} title={$_('assistant.header.close.tooltip')}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </HeaderButton>
        </div>
      </header>

      {#if showSettings}
        <div class="settings-panel">
          <div class="settings-header">
            <button class="settings-back" onclick={closeSettings} aria-label={$_('assistant.settings.back.aria')}>
              {$_('assistant.settings.back')}
            </button>
            <div class="settings-title">{$_('assistant.settings.title')}</div>
          </div>

          {#if isLoadingSettings}
            <div class="settings-loading">{$_('assistant.settings.loading')}</div>
          {/if}

          {#if settingsError}
            <div class="settings-error">{settingsError}</div>
          {/if}

          <div class="settings-fields">
            <label class="settings-field">
              <span>{$_('assistant.settings.corePromptSlug')}</span>
              <input
                type="text"
                bind:value={promptSlugs.corePromptSlug}
                placeholder="core-8fbc"
                disabled={isLoadingSettings}
              />
            </label>
            <label class="settings-toggle">
              <input
                type="checkbox"
                bind:checked={promptSlugs.labs}
                disabled={isLoadingSettings}
              />
              <span>{$_('assistant.settings.labs')}</span>
            </label>
          </div>

          <div class="settings-actions">
            <button class="settings-save" onclick={saveSettings} disabled={isLoadingSettings}>
              {$_('assistant.settings.save')}
            </button>
            <button class="settings-reset" onclick={resetSettings} disabled={isLoadingSettings}>
              {$_('assistant.settings.reset')}
            </button>
          </div>

          <p class="settings-note">{$_('assistant.settings.note')}</p>
        </div>
      {:else}
      <!-- Message List -->
      <div
        class="lc-chatbot-messages"
        class:clearing={isClearing}
        bind:this={messageListRef}
        onscroll={handleScroll}
        onwheel={handleWheel}
        ontouchmove={handleTouchMove}
        onclick={handleMessageLinkClick}
        role="log"
        aria-label={$_('assistant.messages.aria')}
        aria-live="polite"
      >
        {#snippet assistantBubble(content, showFeedback, feedbackProps)}
          <div class="message assistant" class:failed={feedbackProps?.status === STATUS_FAILED}>
            <div class="message-content">
              {@html renderMarkdown(content)}
            </div>
            <div class="message-meta">
              {#if feedbackProps?.status === STATUS_FAILED}
                <button class="retry-btn" aria-label={$_('assistant.messages.retry')} onclick={() => retryMessage(feedbackProps.messageId)}>
                  {$_('assistant.messages.retry')}
                </button>
              {/if}
              {#if showFeedback && feedbackProps}
                <div class="feedback">
                  <div class="feedback-buttons">
                    <button
                      class="feedback-btn"
                      class:active={feedbackProps.feedback === FEEDBACK_UP}
                      onclick={() => handleFeedback(feedbackProps.messageId, 1)}
                      aria-label={$_('assistant.feedback.positive')}
                    >
                      {@html THUMBUP}
                    </button>
                    <button
                      class="feedback-btn"
                      class:active={feedbackProps.feedback === FEEDBACK_DOWN}
                      onclick={() => handleFeedback(feedbackProps.messageId, 0)}
                      aria-label={$_('assistant.feedback.negative')}
                    >
                      {@html THUMBDOWN}
                    </button>
                  </div>
                  {#if feedbackProps.feedback}
                    <p class="feedback-thanks">{$_('assistant.messages.feedbackThanks')}</p>
                  {/if}
                </div>
              {/if}
            </div>
          </div>
        {/snippet}

        {#if isLoadingHistory}
          <div class="loading-indicator">
            <div class="loading-spinner"></div>
            <span>{$_('assistant.messages.loadingHistory')}</span>
          </div>
        {/if}

        {#if messages.length === 0 && !isLoadingHistory}
          <div class="empty-state">
            {@render assistantBubble(getEmptyStateMessage(), false, null)}
          </div>
        {/if}

        {#each messages as item (item.messageId)}
          {#if item.role === 'assistant'}
            <div class="lc-response-package">
              {#if item.appetizerData}
                <Accordion kind="topics"
                  expanded={!!expandedSections[`${item.messageId}_topics`]}
                  onToggle={() => toggleSection(`${item.messageId}_topics`)}>
                  <TopicAppetizer collapsed data={normalizeAppetizerData(item.appetizerData)} onClickTopic={handleAppetizerClick} />
                </Accordion>
              {/if}
              {#if item.toolHistory?.length > 0}
                <Accordion kind="thought"
                  expanded={!!expandedSections[`${item.messageId}_thought`]}
                  onToggle={() => toggleSection(`${item.messageId}_thought`)}>
                  <ProgressTrail entries={item.toolHistory} />
                </Accordion>
              {/if}
              {@render assistantBubble(item.content, item.status === 'sent' && !!item.traceId, item)}
            </div>
          {:else}
            <div class="message user">
              <div class="message-content">
                <p>{item.content}</p>
              </div>
              <div class="message-meta">
                {#if item.status === STATUS_FAILED}
                  <button class="retry-btn" aria-label={$_('assistant.messages.retry')} onclick={() => retryMessage(item.messageId)}>
                    {$_('assistant.messages.retry')}
                  </button>
                {/if}
              </div>
              {#if item.locationRef}
                <div class="message-location-tag">
                  <LocationTag label={item.locationRef.label} href={item.locationRef.url} onActivate={handleLocationClick} />
                </div>
              {/if}
            </div>
          {/if}
        {/each}

        {#if isSending}
          <div class="message assistant">
            <div class="lc-loading-wrapper" bind:this={loadingWrapperRef}>
              {#if appetizerData}
                <TopicAppetizer data={normalizeAppetizerData(appetizerData)} streaming={true} onClickTopic={handleAppetizerClick} />
              {/if}
              <div class="lc-thinking-block">
                {#if displayTrail.length > 0}
                  <ProgressTrail entries={displayTrail} />
                {/if}
                <div class="lc-thinking-step">
                  <span class="lc-loading-spinner" aria-hidden="true">
                    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path fill="currentColor" d="M1.5 8.99983C1.50001 7.416 2.00167 5.87296 2.93262 4.59162C3.86356 3.31028 5.17632 2.35646 6.68262 1.86701C8.18883 1.37766 9.81117 1.37766 11.3174 1.86701C11.7113 1.99501 11.9268 2.41838 11.7988 2.81233C11.6707 3.20599 11.2473 3.42172 10.8535 3.29377C9.64856 2.90236 8.35043 2.90226 7.14551 3.29377C5.94063 3.68536 4.89019 4.4485 4.14551 5.47346C3.40094 6.49845 3.00001 7.73294 3 8.99983C3 10.2667 3.40093 11.5012 4.14551 12.5262C4.89019 13.5512 5.9406 14.3143 7.14551 14.7059C8.35045 15.0974 9.64853 15.0973 10.8535 14.7059C12.0584 14.3144 13.1087 13.552 13.8535 12.5272C14.5983 11.5021 14.9999 10.2669 15 8.99983C15.0002 8.58576 15.3359 8.24983 15.75 8.24983C16.1641 8.24985 16.4998 8.58578 16.5 8.99983C16.4999 10.5835 15.9983 12.1268 15.0674 13.408C14.1364 14.6893 12.8237 15.6433 11.3174 16.1326C9.81118 16.622 8.18881 16.622 6.68262 16.1326C5.17636 15.6432 3.86354 14.6893 2.93262 13.408C2.0017 12.1267 1.5 10.5836 1.5 8.99983Z"/></svg>
                  </span>
                  <span class="lc-thinking-label">{isSynthesizing ? $_('assistant.loading.synthesizing') : $_('assistant.status.thinking')}</span>
                </div>
              </div>
            </div>
          </div>
        {/if}

        {#if limitReached}
          <div class="message assistant limit-message">
            <div class="message-content">
              <p>
                {$_('assistant.limit.reached')}
              </p>
              <p>
                 <button aria-label={$_('assistant.limit.maxTurnsRestart.aria')} type="button" class="link-like" onclick={handleRestartConvo}>{$_('assistant.limit.startNew')}</button>
              </p>
            </div>
          </div>
        {/if}
      </div>

      <!-- Input Footer -->
      <footer class="lc-chatbot-input">
        <textarea
          bind:this={inputRef}
          bind:value={inputText}
          onkeydown={handleKeydown}
          maxlength={effectiveMaxInputChars}
          placeholder={limitReached ? "" : $_('assistant.input.placeholder')}
          aria-label={$_('assistant.input.aria')}
          rows="1"
          disabled={isSending || limitReached}
        ></textarea>
        <button
          class="send-btn"
          onclick={handleSend}
          disabled={!inputText.trim() || isSending || limitReached}
          aria-label={$_('assistant.input.send.tooltip')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </footer>
      {/if}

      <!-- Feedback Modal -->
      {#if showFeedbackModal}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="feedback-modal-overlay" onclick={closeFeedbackModal} onkeydown={(e) => e.key === 'Escape' && closeFeedbackModal()}>
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div class="feedback-modal" onclick={(e) => e.stopPropagation()}>
            <h3 class="feedback-modal-title">{$_('assistant.feedback.modal.title')}</h3>
            <p class="feedback-modal-subtitle">{$_('assistant.feedback.modal.subtitle')}</p>
            {#if feedbackType === FEEDBACK_DOWN}
              <div class="feedback-modal-field">
                <label for="select" class="feedback-modal-select-label">{$_('assistant.feedback.modal.issueLabel')}</label>
                <select
                  id="select"
                  class="feedback-modal-select"
                  class:is-placeholder={!feedbackReason}
                  bind:value={feedbackReason}
                >
                  <option value="" disabled>{$_('assistant.feedback.modal.selectIssue')}</option>
                  {#each DISLIKE_REASONS as issue}
                    <option value={issue.value}>{issue.label}</option>
                  {/each}
                </select>
              </div>
            {/if}
            <textarea
              class="feedback-modal-input"
              bind:value={feedbackComment}
              placeholder={feedbackType === FEEDBACK_DOWN ? $_('assistant.feedback.modal.placeholder.detailed') : $_('assistant.feedback.modal.placeholder.optional')}
            />
            <div class="feedback-modal-actions">
              <button
                class="feedback-modal-btn submit"
                onclick={() => submitFeedback(true)}
                disabled={feedbackType === FEEDBACK_DOWN && !feedbackReason}
              >
                {$_('assistant.feedback.modal.submit')}
              </button>
              <button class="feedback-modal-btn skip" onclick={() => submitFeedback(false)}>
                {$_('assistant.feedback.modal.skip')}
              </button>
            </div>
          </div>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* CSS Custom Properties for theming */
  :host {
    /* Figma design tokens (canonical) */
    --global-dimension-0: 0px;
    --global-dimension-100: 8px;
    --global-dimension-150: 12px;
    --global-dimension-200: 16px;
    --global-dimension-250: 20px;
    --global-dimension-300: 24px;
    --space-1: 4px;
    --spacing-spacing-large: 16px;
    --spacing-spacing-medium: 12px;
    --semantic-action-primary: #18345D;
    --semantic-text-link: #18345D;
    --semantic-text-secondary: #575757;
    --semantic-text-muted: #707070;
    --core-blue-tbr-100: #F0F7FF;
    --core-base-white: #FFFFFF;
    --core-neutral-gray-100: #EEEEEE;
    --core-neutral-gray-300: #CCCCCC;
    --functional-icon-icon-primary: #666666;

    /* Component tokens — aliased to Figma tokens where applicable */
    --lc-primary: var(--semantic-action-primary);
    --brand-sefaria-blue: #18345D;
    --lc-primary-hover: #465D7D;
    --lc-bg: #ffffff;
    --lc-body-bg: #F9FAFB;
    --lc-bg-secondary: #FAFAFA;
    --lc-bg-tertiary: #f1f5f9;
    --lc-text: #1e293b;
    --lc-text-secondary: var(--semantic-text-secondary);
    --lc-text-muted: #999999;
    --lc-border: #e2e8f0;
    --lc-user-bg: #0056B3;
    --lc-user-text: #ffffff;
    --lc-assistant-bg: #f1f5f9;
    --lc-assistant-text: #1e293b;
    --lc-error: #ef4444;
    --lc-sefaria-blue: var(--sefaria-blue);
    --lc-disabled-button: #e6e6e6;
    --lc-disabled-text: #999;
    --lc-submit-white: #FBFDFE;

    --lc-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
    --lc-radius: 16px;
    --lc-radius-sm: 8px;
    --lc-font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    --lc-font-size-sm: 12px;
    --lc-font-size: 14px;
    --lc-font-size-lg: 16px;
    /* Matches Sefaria reader chrome: #panelWrapBox uses top: 60px; docked column must inset too or it sits under the fixed header */
    --lc-docked-top-offset: 60px;
    --lc-border-strong: var(--core-neutral-gray-300);
    --lc-bg-hover: var(--core-neutral-gray-100);
    --lc-on-primary: var(--core-base-white);
    --lc-icon-primary: var(--functional-icon-icon-primary);
    --lc-topics-bg: var(--core-blue-tbr-100);

    display: block;
    font-family: var(--lc-font);
  }

  /* Fill #main row height when docked so the panel stays in view (banners shrink #main, not 100vh) */
  :host(:has(.lc-chatbot-container.mode-docked.is-open)) {
    height: 100%;
    min-height: 0;
    max-height: 100%;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  .lc-chatbot-container {
    position: fixed;
    bottom: 24px;
    inset-inline-end: 24px;
    z-index: 9999;
    direction: ltr;
  }

  .lc-chatbot-container.interface-hebrew {
    direction: rtl;
    font-family: Heebo;
  }

  .lc-chatbot-container.mode-docked.is-open {
    position: static;
    flex-shrink: 0;
    align-self: stretch;
    width: fit-content;
    /* Inset below global header (same band as .multiPanel #panelWrapBox top: 60px); height shrinks so column still fits #main */
    margin-top: var(--lc-docked-top-offset);
    height: calc(100% - var(--lc-docked-top-offset));
    max-height: calc(100% - var(--lc-docked-top-offset));
    min-height: 0;
    padding-bottom: 24px;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }

  .lc-chatbot-container.mode-docked .lc-chatbot-panel {
    flex: 1 1 0;
    min-height: 0;
    height: auto;
    max-height: 100%;
    border-radius: 12px;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.08), 0 16px 32px 0 rgba(13, 3, 32, 0.16);
    margin-inline-start: 10px;
    margin-inline-end: 42px;
    margin-bottom: 0;
  }

  .lc-chatbot-container.mode-docked .resize-n,
  .lc-chatbot-container.mode-docked .resize-s,
  .lc-chatbot-container.mode-docked .resize-ne,
  .lc-chatbot-container.mode-docked .resize-nw,
  .lc-chatbot-container.mode-docked .resize-se,
  .lc-chatbot-container.mode-docked .resize-sw {
    display: none;
  }

  /* Trigger Button */
  .lc-chatbot-trigger {
    display: flex;
    align-items: center;
    gap: 0;
    padding: 12px 20px;
    background: var(--brand-sefaria-blue);
    color: white;
    border: none;
    border-radius: 9999px;
    cursor: pointer;
    font-family: var(--lc-font);
    font-size: var(--lc-font-size);
    font-weight: 500;
    box-shadow: var(--lc-shadow);
    transition: all 0.2s ease;
  }

  .lc-chatbot-trigger:hover,
  .lc-chatbot-trigger:focus,
  .lc-chatbot-trigger:active {
    gap: 8px;
  }


  .lc-chatbot-trigger:active {
    background: #0B1A2D;
  }

  .trigger-label {
    font-weight: 400;
    color: var(--lc-user-text);
    font-family: var(--lc-font);
    font-size: var(--lc-font-size-sm);
    line-height: 18px; 
    letter-spacing: 0.24px;
    max-width: 0;
    overflow: hidden;
    opacity: 0;
    white-space: nowrap;
    transition: max-width 0.2s ease, opacity 0.2s ease;
  }

  .lc-chatbot-trigger:hover .trigger-label,
  .lc-chatbot-trigger:focus .trigger-label,
  .lc-chatbot-trigger:active .trigger-label {
    max-width: 12em;
    opacity: 1;
  }

  /* Chat Panel */
  .lc-chatbot-panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--lc-body-bg);
    border-radius: var(--lc-radius);
    box-shadow: var(--lc-shadow);
    overflow: hidden;
    position: relative;
  }

  .lc-chatbot-panel.resizing {
    user-select: none;
  }

  /* Resize Handles */
  .resize-handle {
    position: absolute;
    background: transparent;
    z-index: 10;
  }

  .resize-n, .resize-s { height: 8px; left: 8px; right: 8px; cursor: ns-resize; }
  .resize-e, .resize-w { width: 8px; top: 8px; bottom: 8px; cursor: ew-resize; }
  .resize-n { top: 0; }
  .resize-s { bottom: 0; }
  .resize-e { right: 0; }
  .resize-w { left: 0; }

  .resize-ne, .resize-nw, .resize-se, .resize-sw { width: 16px; height: 16px; }
  .resize-ne { top: 0; right: 0; cursor: nesw-resize; }
  .resize-nw { top: 0; left: 0; cursor: nwse-resize; }
  .resize-se { bottom: 0; right: 0; cursor: nwse-resize; }
  .resize-sw { bottom: 0; left: 0; cursor: nesw-resize; }

  /* Header */
  .lc-chatbot-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    background: var(--lc-bg-secondary);
    border-bottom: 1px solid var(--lc-border);
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .lc-chatbot-header h2 {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: var(--lc-font-size-lg);
    white-space: nowrap;
    margin: 0;
    line-height: 1.1;
    color: var(--brand-sefaria-blue);
    font-family: Roboto;
    font-style: normal;
    font-weight: 600;
  }

  .interface-hebrew .lc-chatbot-header h2 {
    line-height: normal;
  }


  .lc-chatbot-header h2 img {
    display: block;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-inline-start: 15px;
  }

  .menu-container {
    position: relative;
  }

  .menu-dropdown {
    position: absolute;
    top: 100%;
    margin-top: 4px;
    min-width: 200px;
    background: var(--lc-bg);
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    box-shadow: var(--lc-shadow);
    z-index: 100;
    overflow: hidden;
  }

  .menu-dropdown {
      right: auto;
      left: auto;
      inset-inline-start: auto;
      inset-inline-end: 0;
    }
  

  .menu-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 14px;
    background: transparent;
    border: none;
    color: var(--lc-text);
    font-size: 13px;
    font-family: var(--lc-font);
    text-decoration: none;
    cursor: pointer;
    text-align: start;
    transition: background 0.15s ease;
  }

  .menu-item:hover:not(:disabled) {
    background: var(--lc-bg-tertiary);
  }

  .menu-item:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .menu-item svg {
    flex-shrink: 0;
    color: var(--lc-text-secondary);
  }

  /* Message List */
  .lc-chatbot-messages {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
    /* Vertical scroll only. Without an explicit overflow-x, `overflow-y: auto`
       makes overflow-x compute to `auto` too (CSS spec), so any 1px-too-wide
       child shows a horizontal scrollbar. Clip horizontally so it can never. */
    overflow-x: hidden;
    padding: var(--space-1, 4px) var(--global-dimension-300, 24px) var(--spacing-spacing-medium, 12px) var(--global-dimension-300, 24px);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-spacing-large, 16px);
    scroll-behavior: smooth;
  }

  /* Messages */
  .message {
    display: flex;
    flex-direction: column;
    animation: fadeInUp 0.2s ease;
  }

  @keyframes fadeInUp {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .message.user {
    max-width: 85%;
    align-self: flex-end;
  }

  .message.assistant {
    align-self: flex-start;
  }

  /* While streaming, the topics box fills the full content width (Figma spec):
     stretch the loading-state assistant message so the box isn't shrink-wrapped.
     Chat bubbles are unaffected — they cap via .message-content's max-width. */
  .message.assistant:has(.lc-loading-wrapper) {
    align-self: stretch;
  }

  /* Response package: stacks topics accordion → thought accordion → answer bubble.
     8px gap between accordions; answer bubble pulled flush (0px) via negative margin. */
  .lc-response-package {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* Pull the answer bubble flush against the last accordion (8px gap - 8px = 0px).
     When there are no accordions the bubble is the only child and the gap spec
     does not apply, so we scope this to :not(:first-child). */
  .lc-response-package > .message.assistant:not(:first-child) {
    margin-top: -8px;
  }

  .empty-state .message.assistant .message-content,
  .empty-state .message.assistant .message-content :global(a) {
    color: #575757;
  }

  .message.assistant .message-content :global(ul),
  .message.assistant .message-content :global(ol) {
    padding-inline-start: 20px;
  }

  .message.assistant .message-content :global(li) {
    margin-bottom: 5px;
  }
  .interface-hebrew .message.assistant .message-content :global(li) {
    margin-bottom: 10px;
  }

  .message-content {
    max-width: 560px;
  }

  .message.user .message-content {
    padding: 12px 16px;
    font-size: var(--lc-font-size);
    word-wrap: break-word;
    color: var(--lc-user-text);
    /* Blue bubble lives on the bubble only — not the whole column — so the
       location tag below renders as a gray pill on the white chat background. */
    background-color: var(--lc-user-bg);
    border-radius: 0 16px 16px 16px;
    border-bottom-right-radius: 4px;
  }

  .message.failed .message-content {
    border: 1px solid var(--lc-error);
    background: #fef2f2;
  }

  .message.limit-message .message-content {
    color: var(--lc-text-secondary);
  }
  .message.limit-message .message-content .link-like {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    font-weight: bold;
    color: var(--lc-sefaria-blue);
    text-decoration: underline;
    cursor: pointer;
  }

  .message-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 4px;
    padding: 0 4px;
  }

  .message-location-tag {
    display: flex;
    justify-content: flex-end;
    margin-top: 4px;
    /* Figma: max width = chat bubble width (560px), but never exceed the
       available message column so long refs truncate instead of overflowing. */
    max-width: min(560px, 100%);
    align-self: flex-end;
  }

  .message-status {
    font-size: 11px;
    color: var(--lc-text-muted);
  }

  .message-status.sending {
    color: var(--brand-sefaria-blue);
  }

  .retry-btn {
    font-size: 11px;
    color: var(--lc-error);
    background: none;
    border: none;
    cursor: pointer;
    text-decoration: underline;
    font-family: var(--lc-font);
  }

  .retry-btn:hover {
    color: #dc2626;
  }

  .feedback-buttons {
    display: inline-flex;
    gap: 4px;
    margin-left: 4px;
  }

  .feedback-btn {
    border: none;
    background: transparent;
    padding: 2px 6px;
    cursor: pointer;
    color: var(--lc-disabled-text);
  }

  .feedback-btn:hover,
  .feedback-btn.active {
    color: #666;
  }

  /* Thinking/Progress Indicator */
  .thinking-content {
    min-width: 200px;
    padding: 12px 16px !important;
    margin-bottom: 8px;
    direction: ltr;
  }

  .message.assistant:has(.thinking-content) {
     align-self: revert;
  }

  .status-text {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--lc-text-secondary);
  }

  .status-text.tool-running {
    color: var(--brand-sefaria-blue);
  }

  .status-text.tool-error {
    color: var(--lc-error);
  }

.thinking-fallback {
    padding: 4px 12px;
    font-size: 12px;
    color: #777;
  }

.dots::after {
  content: '';
  animation: dots 1.5s steps(4, end) infinite;
}

@keyframes dots {
  0%   { content: ''; }
  25%  { content: '.'; }
  50%  { content: '..'; }
  75%  { content: '...'; }
  100% { content: ''; }
}

  /* Steps trail + the live status line share one 4px-gapped column so the
     "Thinking"/"Synthesizing" line always sits 4px below the newest step.
     F3: Force LTR on both so the Hebrew/RTL interface never flips them. */
  .lc-thinking-block {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    align-self: stretch;
    gap: var(--space-1, 4px);
    /* F3: Hebrew RTL must not flip this block — thinking steps are always LTR */
    direction: ltr;
    text-align: start;
    /* F6: Prevent the block from ever pushing past the container width */
    min-width: 0;
    width: 100%;
    overflow: hidden;
  }
  .lc-thinking-step {
    display: flex;
    align-items: center;
    gap: var(--global-dimension-100, 8px);
    min-height: 20px;
    /* F3: Explicitly LTR so spinner stays on the left even in Hebrew */
    direction: ltr;
    /* F6: must not overflow the block; min-width:0 lets it shrink */
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
  }
  .lc-thinking-label {
    font-family: var(--lc-font);
    font-size: 12px;
    line-height: var(--global-dimension-250, 20px);
    color: var(--semantic-text-secondary, #575757);
    /* F6: prevent label from pushing the thinking row wider than the container */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .lc-loading-spinner {
    display: inline-flex;
    flex-shrink: 0;
    color: var(--functional-icon-icon-primary, #666666);
    animation: lc-loading-spin 0.8s linear infinite;
    transform-origin: center;
  }
  @keyframes lc-loading-spin {
    to { transform: rotate(360deg); }
  }

  .lc-loading-wrapper {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: var(--spacing-spacing-large, 16px);
  }

  /* Loading Indicator */
  .loading-indicator {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 12px;
    color: var(--lc-text-muted);
    font-size: 13px;
  }

  .loading-spinner {
    width: 16px;
    height: 16px;
    border: 2px solid var(--lc-border);
    border-top-color: var(--brand-sefaria-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  /* Input Footer */
  .lc-chatbot-input {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    padding: 16px 16px 16px 18px;
    background: transparent;
    border-top: 1px solid var(--lc-border);
  }

  .lc-chatbot-input textarea {
    flex: 1;
    min-height: 40px;
    max-height: 120px;
    padding: 10px 14px;
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    font-family: var(--lc-font);
    font-size: var(--lc-font-size);
    resize: none;
    outline: none;
    transition: border-color 0.15s ease;
    line-height: 1.4;
  }

  .lc-chatbot-input textarea:focus {
    border-color: var(--brand-sefaria-blue);
  }

  .lc-chatbot-input textarea::placeholder {
    color: var(--lc-text-muted);
  }

  .lc-chatbot-input textarea:disabled {
    background: var(--lc-bg-secondary);
    cursor: not-allowed;
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    background: var(--brand-sefaria-blue);
    color: white;
    border: none;
    border-radius: var(--lc-radius-sm);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .send-btn:hover:not(:disabled) {
    background: var(--lc-primary-hover);
  }

  .send-btn:disabled {
    background: var(--lc-disabled-button);
    cursor: not-allowed;
  }

  .interface-hebrew .send-btn svg {
    transform: scaleX(-1);
  }

  .interface-hebrew .panel-close-icon {
    transform: scaleX(-1);
  }

  .send-btn:active:not(:disabled) {
    transform: scale(0.95);
  }

  /* Settings Panel */
  .settings-panel {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 16px 20px 20px;
    overflow: auto;
    flex: 1;
    background: transparent;
  }

  .settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .settings-title {
    font-size: var(--lc-font-size);
    font-weight: 600;
    color: var(--lc-text);
  }

  .settings-back {
    border: none;
    background: transparent;
    color: var(--brand-sefaria-blue);
    font-weight: 600;
    cursor: pointer;
    padding: 6px 0;
  }

  .settings-loading {
    font-size: var(--lc-font-size-sm);
    color: var(--lc-text-secondary);
  }

  .settings-error {
    font-size: var(--lc-font-size-sm);
    color: var(--lc-error);
  }

  .settings-fields {
    display: grid;
    gap: 12px;
  }

  .settings-field {
    display: grid;
    gap: 6px;
    font-size: var(--lc-font-size-sm);
    color: var(--lc-text-secondary);
  }

  .settings-field input {
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    padding: 8px 10px;
    font-size: 13px;
    font-family: var(--lc-font);
    color: var(--lc-text);
    background: var(--lc-bg-secondary);
  }

  .settings-field input:disabled {
    opacity: 0.6;
  }

  .settings-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: var(--lc-font-size-sm);
    color: var(--lc-text-secondary);
  }

  .settings-toggle input {
    width: 16px;
    height: 16px;
    accent-color: var(--lc-primary);
  }

  .settings-toggle input:disabled {
    opacity: 0.6;
  }

  .settings-note {
    font-size: var(--lc-font-size-sm);
    color: var(--lc-text-muted);
  }

  .settings-actions {
    display: flex;
    gap: 10px;
    align-items: center;
  }

  .settings-save,
  .settings-reset {
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    padding: 8px 12px;
    font-size: var(--lc-font-size-sm);
    font-weight: 600;
    font-family: var(--lc-font);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .settings-save {
    background: var(--brand-sefaria-blue);
    color: white;
    border-color: transparent;
  }

  .settings-save:hover:not(:disabled) {
    background: var(--lc-primary-hover);
  }

  .settings-reset {
    background: var(--lc-bg-tertiary);
    color: var(--lc-text-secondary);
  }

  .settings-reset:hover:not(:disabled) {
    background: var(--lc-bg-secondary);
    color: var(--lc-text);
  }

  .settings-save:disabled,
  .settings-reset:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  /* Clearing animation for message list */
  .lc-chatbot-messages.clearing {
    opacity: 0.5;
    transition: opacity 0.15s ease;
  }

  /* Feedback Modal */
  .feedback-modal-overlay {
position: absolute;
inset: 8px;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10001;
    animation: fadeIn 0.15s ease;
    border-radius: calc(var(--lc-radius) - 4px);
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .feedback-modal {
    background: var(--lc-bg);
    border-radius: var(--lc-radius);
    padding: 24px;
    width: 320px;
    max-width: calc(100% - 32px);
    box-shadow: var(--lc-shadow);
    animation: slideUp 0.2s ease;
  }

  @keyframes slideUp {
    from {
      opacity: 0;
      transform: translateY(10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .feedback-modal-title {
    font-size: var(--lc-font-size);
    font-weight: 600;
    color: var(--lc-sefaria-blue);
    margin: 0 0 8px 0;
  }

  .feedback-modal-subtitle {
    font-size: var(--lc-font-size);
    font-weight: 400;
    color: var(--lc-sefaria-blue);
    margin: 0 0 16px 0;
  }

  .interface-hebrew .feedback-modal-subtitle {
    font-size: var(--lc-font-size-sm);
    font-weight: 400px;
  }

  .feedback-modal-field {
    display: flex;
    flex-direction: column;
    height: 67px;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .feedback-modal-select-label {
    font-size: var(--lc-font-size);
    font-weight: 400;
  }

  .feedback-modal-select,
  .feedback-modal-input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    font-family: var(--lc-font);
    font-size: var(--lc-font-size);
    background: var(--lc-bg-secondary);
    outline: none;
    transition: border-color 0.15s ease;
    box-sizing: border-box;
  }

  .feedback-modal-input {
    min-height: 80px;
  }

  .feedback-modal-select {
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,...");
    background-repeat: no-repeat;
    background-position: right 12px center;
  }

  .feedback-modal-select:focus {
    border-color: var(--brand-sefaria-blue);
  }

  .feedback-modal-select.is-placeholder {
    color: var(--lc-disabled-text);
  }

  .feedback-modal-input:focus {
    border-color: var(--brand-sefaria-blue);
  }

  .feedback-modal-input::placeholder {
    color: var(--lc-disabled-text);
  }

  .feedback-modal-actions {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 16px;
  }

  .feedback-modal-btn {
    flex: 1;
    padding: 10px 16px;
    border-radius: var(--lc-radius-sm);
    font-family: var(--lc-font);
    font-size: var(--lc-font-size);
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .feedback-modal-btn.submit {
    background: var(--lc-sefaria-blue);
    color: var(--lc-submit-white);
  }

  .feedback-modal-btn.submit:hover:not(:disabled) {
    background: var(--lc-primary-hover);
  }

  .feedback-modal-btn.submit:disabled {
    background: var(--lc-disabled-button);
    color: var(--lc-disabled-text);
    cursor: not-allowed;
  }

  .feedback-modal-btn.skip {
    background: transparent;
    color: var(--lc-sefaria-blue);
    border: none;
  }

  .feedback-thanks {
    font-size: var(--lc-font-size-sm);
    color: var(--lc-sefaria-blue);
  }

  .message.assistant .message-content,
  .message.assistant .message-content :global(a) {
    color: var(--lc-sefaria-blue);
    font-size: var(--lc-font-size);
  }

  /* css for classes that come directly from server (via @html) —
     must use :global() so Svelte doesn't strip them */
  .message-content :global(.response-title) {
    font-size: var(--lc-font-size-lg);
    font-weight: 600;
    color: var(--brand-sefaria-blue);
    font-style: normal;
    line-height: normal;
  }
  .interface-hebrew .message-content :global(.response-title) {
    font-weight: 700;
  }

  .message-content :global(.response-generic) {
    color: var(--brand-sefaria-blue);
    font-size: var(--lc-font-size);
    font-style: normal;
    font-weight: 400;
    line-height: normal;
  }

  .message-content :global(.response-section) {
    color: var(--brand-sefaria-blue);
    font-size: var(--lc-font-size);
    font-style: normal;
    font-weight: 700;
    line-height: normal;
  }

  .message-content :global(.response-list) {
    color: var(--brand-sefaria-blue);
    font-size: var(--lc-font-size);
    font-style: normal;
    font-weight: 400;
    line-height: normal;
  }

  .message-content :global(.response-link) {
    width: 239px;
    text-decoration-line: underline;
    text-decoration-style: solid;
    text-decoration-skip-ink: none;
    text-decoration-thickness: auto;
    text-underline-offset: auto;
    text-underline-position: from-font;
    color: var(--brand-sefaria-blue);
    font-size: var(--lc-font-size);
    font-style: normal;
    font-weight: 700;
    line-height: normal;
  }

  .message-content :global(.response-signoff) {
    font-style: italic;
    line-height: 21px;
  }

  .interface-hebrew .message-content :global(.response-signoff) {
    font-style: normal;
  }

  .message-content :global(.response-quote) {
    /*  place holder */
  }

  :global(.progress-trail-toggle) {
    display: flex;
    align-items: center;
    gap: 4px;
    background: none;
    border: none;
    cursor: pointer;
    color: #888;
    font-size: 11px;
    padding: 4px 12px;
    font-family: inherit;
  }
  :global(.progress-trail-toggle:hover) {
    color: #555;
  }
  :global(.progress-trail-list) {
    list-style: none;
    margin: 0;
    padding: 4px 12px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  :global(.progress-trail-entry) {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    line-height: 1.4;
    color: #777;
  }
  :global(.progress-trail-entry--error) {
    color: #c62828;
  }
  :global(.progress-trail-entry--complete) {
    color: #666;
  }
  :global(.progress-trail-icon) {
    flex-shrink: 0;
    width: 14px;
    height: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  :global(.progress-trail-spinner) {
    width: 10px;
    height: 10px;
    border: 1.5px solid #ccc;
    border-top-color: #888;
    border-radius: 50%;
    animation: trail-spin 0.8s linear infinite;
  }
  @keyframes trail-spin {
    to { transform: rotate(360deg); }
  }
  :global(.progress-trail-text) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  :global(.trail-ref-link) {
    color: #18345D;
    font-weight: 600;
    text-decoration: underline;
    text-decoration-color: rgba(24, 52, 93, 0.3);
    text-underline-offset: 2px;
  }
  :global(.trail-ref-link:hover) {
    color: #465D7D;
    text-decoration-color: rgba(70, 93, 125, 0.6);
  }
  :global(.trail-ref-icon) {
    display: inline-block;
    vertical-align: middle;
    margin-inline-end: 2px;
    color: #18345D;
    opacity: 0.6;
  }

</style>
