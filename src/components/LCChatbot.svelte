<svelte:options customElement="lc-chatbot" />

<script>
  import { getStorage, setStorage, STORAGE_KEYS } from '../lib/storage.js';
  import { getOrCreateSession, updateSessionActivity, generateMessageId } from '../lib/session.js';
  import { sendMessageStream, loadHistory, fetchPromptDefaults, sendFeedback } from '../lib/api.js';
  import { tick } from 'svelte';
  import { renderMarkdown } from '../lib/markdown.js';
  import { formatDateMarker, formatTime, getDateKey, isSameDay } from '../lib/dates.js';
  import HeaderButton from './HeaderButton.svelte';
  import SourceSuggestion from './SourceSuggestion.svelte';
  import ProgressTrail from './ProgressTrail.svelte';
  import { setLocale, _ } from '../i18n/index.js';

  const DEFAULT_MAX_PROMPTS = 100;
  const DEFAULT_MAX_INPUT_CHARS = 10000;

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
  let currentProgress = $state(null);
  let toolHistory = $state([]);
  let trailEntryId = $state(0);

  // Tools that fetch a specific text by reference — the only ones worth suggesting to read
  const SOURCE_PROVIDING_TOOLS = new Set([
    'get_text', 'get_english_translations'
  ]);
  let firstSourcePreview = $state(null);
  let sourcePreviewMessageId = $state(null);

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

  $effect(() => { setLocale(interfaceLang); });

  let welcomeMessage = $derived($_('welcome.message'));
  let restartMessage = $derived($_('welcome.restart'));
  let newSessionMessage = $derived($_('welcome.newSession'));

  // Feedback issue options for dislikes — labels resolved reactively from the i18n store
  const DISLIKE_REASON_KEYS = [
    { value: 'inaccurate', key: 'feedback.reason.inaccurate' },
    { value: 'disrespectful', key: 'feedback.reason.disrespectful' },
    { value: 'unhelpful', key: 'feedback.reason.unhelpful' },
    { value: 'overly_definitive', key: 'feedback.reason.overlyDefinitive' },
    { value: 'tech_issue', key: 'feedback.reason.techIssue' },
    { value: 'other', key: 'feedback.reason.other' }
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
      // If a response link was clicked — capture the link text
      const link = path.find(
        el => el instanceof Element && el.tagName === 'A' && el.getAttribute('href')
      );
      if (link) {
        if (typeof window.gtag === 'function') {
          const raw = link.getAttribute('href');
          const link_url = raw.startsWith('http') ? new URL(raw).pathname + (new URL(raw).search || '') : raw;
          window.gtag('event', 'assistant_click', { feature_name: 'Response link', text: link.textContent.trim(), link_url });
        }
        return;
      }

      // Otherwise walk up the path for the nearest aria-label
      const target = path.find(
        el => el instanceof Element && el.getAttribute('aria-label')
      );
      if (!target) return;
      if (typeof window.gtag === 'function') {
        window.gtag('event', 'assistant_click', { feature_name: target.getAttribute('aria-label') });
      }
    }

    host.addEventListener('click', trackClick);
    return () => host.removeEventListener('click', trackClick);
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
      window.gtag('event', 'assistant_click', { feature_name: `Toggle to ${newMode}` });
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
    currentProgress = null;
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
      messageListRef.scrollTop = messageListRef.scrollHeight;
    }
  }

  async function scrollToResponseStart() {
    await tick();
    if (!messageListRef) return;
    const contents = messageListRef.querySelectorAll('.message.assistant .message-content');
    const lastResponse = contents[contents.length - 1]?.closest('.message.assistant');
    if (lastResponse) {
      const containerRect = messageListRef.getBoundingClientRect();
      const msgRect = lastResponse.getBoundingClientRect();
      messageListRef.scrollTop += msgRect.top - containerRect.top;
    }
  }

  async function handleSend() {
    const text = inputText.trim();
    const isConfigured = userId && apiBaseUrl;
    const isReadyToSend = text && !isSending && !limitReached;
    if (!isConfigured || !isReadyToSend) return;
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'assistant_message_sent', { length: text.length });
    }
    // Clear input and draft
    inputText = '';
    setStorage(STORAGE_KEYS.DRAFT, { text: '' });

    // Create user message
    const userMessage = {
      messageId: generateMessageId(),
      sessionId,
      userId,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
      status: 'sending'
    };

    messages = [...messages, userMessage];
    saveMessagesToStorage();
    scrollToBottom();

    isSending = true;
    currentProgress = null;
    toolHistory = [];
    firstSourcePreview = null;
    sourcePreviewMessageId = null;
    trailEntryId = 0;
    updateSessionActivity(sessionId);

    try {
      const response = await sendMessageStream(apiBaseUrl, userId, sessionId, text, {
        onProgress: (progress) => {
          let displayText;
          if (progress?.type === 'status') {
            displayText = progress.text;
            toolHistory = [...toolHistory, {
              id: trailEntryId++,
              type: 'status',
              text: displayText?.replace(/…|\.\.\./, '') || '',
              status: 'running',
              startTime: Date.now()
            }];
          } else if (progress?.type === 'tool_start') {
            displayText = progress.description || `Running ${progress.toolName}`;
            toolHistory = [...toolHistory, {
              id: trailEntryId++,
              type: 'tool',
              toolName: progress.toolName,
              description: displayText?.replace(/…|\.\.\./, '') || '',
              status: 'running',
              startTime: Date.now()
            }];
          } else if (progress.type === 'tool_end') {
            const isFirstSuccessfulSourceTool = (
              !progress.isError &&
              firstSourcePreview === null &&
              SOURCE_PROVIDING_TOOLS.has(progress.toolName)
            );
            if (isFirstSuccessfulSourceTool) {
              const matchingTool = toolHistory.findLast(t => t.type === 'tool' && t.toolName === progress.toolName);
              firstSourcePreview = {
                toolName: progress.toolName,
                description: matchingTool?.description || '',
                toolInput: progress.toolInput || null,
              };
            }
            toolHistory = toolHistory.map((t, i) =>
              i === toolHistory.length - 1 && t.type === 'tool'
                ? { ...t, status: progress.isError ? 'error' : 'complete', duration: Date.now() - t.startTime }
                : t
            );
          }
          displayText = displayText?.replace(/…|\.\.\./, '');
          currentProgress = {...progress, displayText};
        },
        onError: (error) => {
          console.error('[lc-chatbot] Stream error:', error);
        }
      }, promptSlugs, originProp, isModerator, promptSlugs.labs === true, {
        messageId: userMessage.messageId,
        timestamp: userMessage.timestamp
      });

      // Update user message status
      messages = messages.map(m => 
        m.messageId === userMessage.messageId 
          ? { ...m, status: 'sent' }
          : m
      );

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
        toolHistory: [...toolHistory]
      };

      messages = [...messages, assistantMessage];
      sourcePreviewMessageId = assistantMessage.messageId;
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
      currentProgress = null;
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

  // Message grouping with date markers
  function getMessagesWithMarkers() {
    const result = [];
    let lastDateKey = null;

    for (const msg of messages) {
      const dateKey = getDateKey(msg.timestamp);
      if (dateKey !== lastDateKey) {
        result.push({
          type: 'date-marker',
          date: formatDateMarker(new Date(msg.timestamp)),
          key: 'date-' + dateKey
        });
        lastDateKey = dateKey;
      }
      result.push({
        type: 'message',
        ...msg,
        key: msg.messageId
      });
    }

    return result;
  }

  let messagesWithMarkers = $derived(getMessagesWithMarkers());

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

    const path = resolvedUrl.pathname + resolvedUrl.search + resolvedUrl.hash;
    console.log('[lc-chatbot] Link clicked:', href);
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
>
  {#if !isOpen}
    <!-- Floating Button -->
    <button aria-label={$_('header.openAssistant')} class="lc-chatbot-trigger" onclick={openPanel}>
      <img src="{staticIconsBaseUrl}/logo.svg"/>
      <span class="trigger-label">{$_('header.triggerLabel')}</span>
    </button>
  {:else}
    <!-- Chat Panel -->
    <div 
      class="lc-chatbot-panel"
      class:resizing={isResizing}
      style="width: {panelWidth}px;{mode === 'docked' && isOpen ? '' : ` height: ${panelHeight}px;`}"
      role="dialog"
      aria-label={$_('header.chatWindow')}
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
          <h2>{$_('header.title')} {#if testingVersion}(V{testingVersion}){/if}
          <img src="{staticIconsBaseUrl}/AI.svg"/>
          </h2>
        </div>
        <div class="header-actions">
          <HeaderButton
            className="panel-btn"
            title={(mode === 'floating') ? $_('header.dock') : $_('header.undock')}
            onClick={(e) => { e.stopPropagation(); toggleMode(); }}
          >
            <img
              src="{staticIconsBaseUrl}/{(mode === 'floating') ? 'panel-right-close' : 'minimize'}.svg"
              alt=""
              width="16"
              height="16"
            />
          </HeaderButton>
          <div class="menu-container" bind:this={menuContainer}>
            <HeaderButton className="menu-btn" onClick={toggleMenu} title={$_('header.moreOptions')} aria-expanded={showMenu}>
              <img src="{staticIconsBaseUrl}/ellipsis-vertical.svg" alt="" width="18" height="18" />
            </HeaderButton>
            {#if showMenu}
              <div class="menu-dropdown" role="menu">
                {#if isModerator}
                  <button class="menu-item" aria-label={$_('menu.settings.aria')} onclick={() => { openSettings(); closeMenu(); }} role="menuitem">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <circle cx="12" cy="12" r="3"></circle>
                      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .64.38 1.22.97 1.49.22.1.46.15.7.15H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                    </svg>
                    {$_('menu.settings')}
                  </button>
                {/if}
                <button class="menu-item" aria-label={$_('menu.restart.aria')} onclick={handleRestartConvo} disabled={isSending} role="menuitem">
                  <img src="{staticIconsBaseUrl}/rotate-ccw.svg" alt="" width="16" height="16" />
                  {$_('menu.restart')}
                </button>
                <a class="menu-item" aria-label={$_('menu.feedback')} href="https://sefaria.formstack.com/forms/sefaria_ai_library_assistant_early_access_and_evaluation" target="_blank" rel="noopener noreferrer" role="menuitem" onclick={closeMenu}>
                  {@html FEEDBACK_ICON}
                  {$_('menu.feedback')}
                </a>
                <a class="menu-item" aria-label={$_('menu.help.aria')} href="https://help.sefaria.org/hc/en-us/articles/26006423836828" target="_blank" role="menuitem" onclick={closeMenu}>
                  <img src="{staticIconsBaseUrl}/info.svg" alt="" width="16" height="16" />
                  {$_('menu.help')}
                </a>
                <a class="menu-item" aria-label={$_('menu.optOut.aria')} href="/settings/account" role="menuitem" onclick={closeMenu}>
                  <img src="{staticIconsBaseUrl}/toggle-right.svg" alt="" width="16" height="16" />
                  {$_('menu.optOut')}
                </a>
              </div>
            {/if}
          </div>
          <HeaderButton className="close-btn" onClick={closePanel} title={$_('header.close')}>
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
            <button class="settings-back" onclick={closeSettings} aria-label={$_('settings.back.aria')}>
              {$_('settings.back')}
            </button>
            <div class="settings-title">{$_('settings.title')}</div>
          </div>

          {#if isLoadingSettings}
            <div class="settings-loading">{$_('settings.loading')}</div>
          {/if}

          {#if settingsError}
            <div class="settings-error">{settingsError}</div>
          {/if}

          <div class="settings-fields">
            <label class="settings-field">
              <span>{$_('settings.corePromptSlug')}</span>
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
              <span>{$_('settings.labs')}</span>
            </label>
          </div>

          <div class="settings-actions">
            <button class="settings-save" onclick={saveSettings} disabled={isLoadingSettings}>
              {$_('settings.save')}
            </button>
            <button class="settings-reset" onclick={resetSettings} disabled={isLoadingSettings}>
              {$_('settings.reset')}
            </button>
          </div>

          <p class="settings-note">{$_('settings.note')}</p>
        </div>
      {:else}
      <!-- Message List -->
      <div
        class="lc-chatbot-messages"
        class:clearing={isClearing}
        bind:this={messageListRef}
        onscroll={handleScroll}
        onclick={handleMessageLinkClick}
        role="log"
        aria-label={$_('messages.aria')}
        aria-live="polite"
      >
        {#snippet assistantBubble(content, showFeedback, feedbackProps)}
          <div class="message assistant" class:failed={feedbackProps?.status === STATUS_FAILED}>
            <div class="message-content">
              {@html renderMarkdown(content)}
            </div>
            <div class="message-meta">
              {#if feedbackProps?.status === STATUS_FAILED}
                <button class="retry-btn" aria-label={$_('messages.retry')} onclick={() => retryMessage(feedbackProps.messageId)}>
                  {$_('messages.retry')}
                </button>
              {/if}
              {#if showFeedback && feedbackProps}
                <div class="feedback">
                  <div class="feedback-buttons">
                    <button
                      class="feedback-btn"
                      class:active={feedbackProps.feedback === FEEDBACK_UP}
                      onclick={() => handleFeedback(feedbackProps.messageId, 1)}
                      aria-label={$_('messages.like')}
                    >
                      {@html THUMBUP}
                    </button>
                    <button
                      class="feedback-btn"
                      class:active={feedbackProps.feedback === FEEDBACK_DOWN}
                      onclick={() => handleFeedback(feedbackProps.messageId, 0)}
                      aria-label={$_('messages.dislike')}
                    >
                      {@html THUMBDOWN}
                    </button>
                  </div>
                  {#if feedbackProps.feedback}
                    <p class="feedback-thanks">{$_('messages.feedbackThanks')}</p>
                  {/if}
                </div>
              {/if}
            </div>
          </div>
        {/snippet}

        {#if isLoadingHistory}
          <div class="loading-indicator">
            <div class="loading-spinner"></div>
            <span>{$_('messages.loadingHistory')}</span>
          </div>
        {/if}

        {#if messages.length === 0 && !isLoadingHistory}
          <div class="empty-state">
            {@render assistantBubble(getEmptyStateMessage(), false, null)}
          </div>
        {/if}

        {#each messagesWithMarkers as item (item.key)}
          {#if item.type === 'date-marker'}
            <div class="date-marker">
              <span>{item.date}</span>
            </div>
          {:else if item.role === 'assistant'}
            {#if firstSourcePreview && item.messageId === sourcePreviewMessageId}
              <SourceSuggestion preview={firstSourcePreview} streaming={false} />
            {/if}
            {#if item.toolHistory?.length > 0}
              <ProgressTrail entries={item.toolHistory} collapsed={true} />
            {/if}
            {@render assistantBubble(item.content, item.status === 'sent' && !!item.traceId, item)}
          {:else}
            <div class="message user">
              <div class="message-content">
                <p>{item.content}</p>
              </div>
              <div class="message-meta">
                {#if item.status === STATUS_FAILED}
                  <button class="retry-btn" aria-label={$_('messages.retry')} onclick={() => retryMessage(item.messageId)}>
                    {$_('messages.retry')}
                  </button>
                {/if}
              </div>
            </div>
          {/if}
        {/each}

        {#if isSending}
          <div class="message assistant">
            <ProgressTrail entries={toolHistory} collapsed={false} />
          </div>
        {/if}

        {#if isSending && firstSourcePreview}
          <SourceSuggestion preview={firstSourcePreview} streaming={true} />
        {/if}

        {#if limitReached}
          <div class="message assistant limit-message">
            <div class="message-content">
              <p>
                {$_('limit.reached')}
              </p>
              <p>
                 <button aria-label={$_('limit.maxTurnsRestart.aria')} type="button" class="link-like" onclick={handleRestartConvo}>{$_('limit.startNew')}</button>
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
          placeholder={limitReached ? "" : $_('input.placeholder')}
          aria-label={$_('input.aria')}
          rows="1"
          disabled={isSending || limitReached}
        ></textarea>
        <button
          class="send-btn"
          onclick={handleSend}
          disabled={!inputText.trim() || isSending || limitReached}
          aria-label={$_('input.send.aria')}
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
            <h3 class="feedback-modal-title">{$_('feedback.modal.title')}</h3>
            <p class="feedback-modal-subtitle">{$_('feedback.modal.subtitle')}</p>
            {#if feedbackType === FEEDBACK_DOWN}
              <div class="feedback-modal-field">
                <label for="select" class="feedback-modal-select-label">{$_('feedback.modal.issueLabel')}</label>
                <select
                  id="select"
                  class="feedback-modal-select"
                  class:is-placeholder={!feedbackReason}
                  bind:value={feedbackReason}
                >
                  <option value="" disabled>{$_('feedback.modal.selectIssue')}</option>
                  {#each DISLIKE_REASONS as issue}
                    <option value={issue.value}>{issue.label}</option>
                  {/each}
                </select>
              </div>
            {/if}
            <textarea
              class="feedback-modal-input"
              bind:value={feedbackComment}
              placeholder={feedbackType === FEEDBACK_DOWN ? $_('feedback.modal.placeholder.detailed') : $_('feedback.modal.placeholder.optional')}
            />
            <div class="feedback-modal-actions">
              <button
                class="feedback-modal-btn submit"
                onclick={() => submitFeedback(true)}
                disabled={feedbackType === FEEDBACK_DOWN && !feedbackReason}
              >
                {$_('feedback.modal.submit')}
              </button>
              <button class="feedback-modal-btn skip" onclick={() => submitFeedback(false)}>
                {$_('feedback.modal.skip')}
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
    --lc-primary: #18345D;
    --lc-primary-hover: #465D7D;
    --lc-bg: #ffffff;
    --lc-body-bg: #F9FAFB;
    --lc-bg-secondary: #FAFAFA;
    --lc-bg-tertiary: #f1f5f9;
    --lc-text: #1e293b;
    --lc-text-secondary: #64748b;
    --lc-text-muted: #999999;
    --lc-border: #e2e8f0;
    --lc-user-bg: #18345D;
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
    background: var(--lc-primary);
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
    font-weight: 600;
    color: var(--lc-text);
    margin: 0;
    line-height: 1.1;
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
    right: 0;
    margin-top: 4px;
    min-width: 200px;
    background: var(--lc-bg);
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    box-shadow: var(--lc-shadow);
    z-index: 100;
    overflow: hidden;
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
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  /* Date Markers */
  .date-marker {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px 0;
  }

  .date-marker span {
    font-size: var(--lc-font-size-sm);
    color: var(--lc-text-muted);
    background: var(--lc-bg);
    padding: 4px 12px;
    border-radius: 9999px;
    border: 1px solid var(--lc-border);
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

  .empty-state .message.assistant .message-content,
  .empty-state .message.assistant .message-content :global(a) {
    color: #575757;
  }
  .empty-state .message.assistant .message-content :global(ul) {
    padding-inline-start: 20px;
  }
  .empty-state .message.assistant .message-content :global(ul li) {
    margin-bottom: 5px;
  }

  .message-content {
    max-width: 560px;
  }

  .message.user .message-content {
    padding: 12px 16px;
    border-radius: var(--lc-radius);
    font-size: var(--lc-font-size);
    word-wrap: break-word;
    background: var(--lc-user-bg);
    color: var(--lc-user-text);
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

  .message-status {
    font-size: 11px;
    color: var(--lc-text-muted);
  }

  .message-status.sending {
    color: var(--lc-primary);
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
}

  .status-text {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--lc-text-secondary);
  }

  .status-text.tool-running {
    color: var(--lc-primary);
  }

  .status-text.tool-error {
    color: var(--lc-error);
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
    border-top-color: var(--lc-primary);
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
    border-color: var(--lc-primary);
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
    background: var(--lc-primary);
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
    color: var(--lc-primary);
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
    background: var(--lc-primary);
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
    font-style: italic;
    color: var(--lc-sefaria-blue);
    margin: 0 0 16px 0;
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
    padding-right: 36px;
  }

  .feedback-modal-select:focus {
    border-color: var(--lc-primary);
  }

  .feedback-modal-select.is-placeholder {
    color: var(--lc-disabled-text);
  }



  .feedback-modal-input:focus {
    border-color: var(--lc-primary);
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
  }

  .message-content :global(.response-generic) {
    line-height: 21px;
  }

  .message-content :global(.response-section) {
    font-weight: 600;
    line-height: 20px;
  }

  .message-content :global(.response-list) {
    line-height: 20px;
  }

  .message-content :global(.response-link) {
    width: 239px;
    line-height: 21px;
    text-decoration-line: underline;
    text-decoration-style: solid;
    text-decoration-skip-ink: none;
    text-decoration-thickness: auto;
    text-underline-offset: auto;
    text-underline-position: from-font;
  }

  .message-content :global(.response-signoff) {
    font-style: italic;
    line-height: 21px;
  }

  .message-content :global(.response-quote) {
    /*  place holder */
  }

  /* SourceSuggestion styles — must live here so they are injected into the shadow DOM */
  :global(.source-suggestion) {
    margin: 6px 12px 0;
    border-radius: 8px;
    border: 1px solid #e9d96a;
    background: #fffde7;
    font-size: 12px;
    font-family: inherit;
  }
  :global(.source-header) {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    min-height: 30px;
    padding: 6px 10px;
    background: none;
    border: none;
    cursor: pointer;
    text-align: start;
    color: #4a4700;
    font-size: 12px;
    font-family: inherit;
    line-height: 1.4;
  }
  :global(.source-header[disabled]) {
    cursor: default;
  }
  :global(.source-header:not([disabled]):hover) {
    background: #fff9c4;
  }
  :global(.source-book-icon) {
    flex-shrink: 0;
    color: #8a7a00;
  }
  :global(.source-header-text) {
    flex: 1;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  :global(.source-chevron) {
    flex-shrink: 0;
    color: #8a7a00;
    transition: transform 0.2s ease;
  }
  :global(.source-chevron.rotated) {
    transform: rotate(180deg);
  }
  :global(.source-body) {
    padding: 6px 10px 8px;
    border-top: 1px solid #f0e68c;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  :global(.source-link) {
    color: #18345D;
    text-decoration: underline;
    font-size: 12px;
    align-self: flex-start;
  }
  :global(.source-link:hover) {
    color: #465D7D;
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

</style>
