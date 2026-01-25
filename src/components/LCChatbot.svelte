<svelte:options customElement="lc-chatbot" />

<script>
  import { getStorage, setStorage, STORAGE_KEYS } from '../lib/storage.js';
  import { getOrCreateSession, updateSessionActivity, generateMessageId, generateSessionId } from '../lib/session.js';
  import { sendMessage, sendMessageStream, loadHistory } from '../lib/api.js';
  import { renderMarkdown } from '../lib/markdown.js';
  import { formatDateMarker, formatTime, getDateKey, isSameDay } from '../lib/dates.js';

  // Props (attributes)
  let {
    'user-id': userId = '',
    'api-base-url': apiBaseUrl = '',
    'default-open': defaultOpen = false,
    placement = 'right'
  } = $props();

  // State
  let isOpen = $state(false);
  let messages = $state([]);
  let inputText = $state('');
  let isSending = $state(false);
  let isLoadingHistory = $state(false);
  let hasMoreHistory = $state(true);
  let sessionId = $state('');
  let panelWidth = $state(380);
  let panelHeight = $state(520);
  let isResizing = $state(false);
  let resizeEdge = $state(null);
  
  // Agent progress state
  let currentProgress = $state(null);
  let toolHistory = $state([]);

  // Turn limit state
  let maxTurns = $state(null);
  let limitReached = $state(false);
  let isClearing = $state(false);

  // Dynamic text based on turn limit (default to single-turn mode when unknown)
  let newSessionButtonText = $derived(maxTurns === 1 || maxTurns === null ? 'New Question' : 'New Conversation');
  let newSessionHintText = $derived(
    maxTurns === 1 || maxTurns === null
      ? 'Start a new question to continue'
      : 'Start a new conversation to continue'
  );

  // Refs
  let messageListRef = $state(null);
  let inputRef = $state(null);

  // Size constraints
  const MIN_WIDTH = 320;
  const MIN_HEIGHT = 420;
  const MAX_WIDTH_RATIO = 0.9;
  const MAX_HEIGHT_RATIO = 0.9;

  // Initialize on mount
  $effect(() => {
    // Initialize session
    const { sessionId: sid } = getOrCreateSession();
    sessionId = sid;

    // Restore UI state
    const savedUI = getStorage(STORAGE_KEYS.UI, null);
    if (savedUI?.isOpen !== undefined && defaultOpen === false) {
      isOpen = savedUI.isOpen;
    } else if (defaultOpen) {
      isOpen = true;
    }

    // Restore size
    const savedSize = getStorage(STORAGE_KEYS.SIZE, null);
    if (savedSize) {
      panelWidth = Math.max(MIN_WIDTH, Math.min(savedSize.width, window.innerWidth * MAX_WIDTH_RATIO));
      panelHeight = Math.max(MIN_HEIGHT, Math.min(savedSize.height, window.innerHeight * MAX_HEIGHT_RATIO));
    }

    // Restore draft
    const savedDraft = getStorage(STORAGE_KEYS.DRAFT, null);
    if (savedDraft?.text) {
      inputText = savedDraft.text;
    }

    // Load messages from local storage
    const savedMessages = getStorage(STORAGE_KEYS.MESSAGES + ':' + sid, []);
    messages = savedMessages;
  });

  // Save draft on input change
  $effect(() => {
    if (inputText) {
      setStorage(STORAGE_KEYS.DRAFT, { text: inputText });
    }
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
    setStorage(STORAGE_KEYS.UI, { isOpen: true, placement });
    dispatchEvent('opened');

    // Focus input after panel opens
    setTimeout(() => {
      inputRef?.focus();
    }, 100);

    // Always sync session state from server (for turn limit info)
    if (sessionId && apiBaseUrl) {
      syncSessionState();
    }
  }

  function closePanel() {
    isOpen = false;
    setStorage(STORAGE_KEYS.UI, { isOpen: false, placement });
    dispatchEvent('closed');
  }

  function handleNewChat() {
    if (isSending) return;

    const { sessionId: newSessionId } = getOrCreateSession(true);
    sessionId = newSessionId;
    messages = [];
    inputText = '';
    isLoadingHistory = false;
    hasMoreHistory = false;
    currentProgress = null;
    toolHistory = [];

    setStorage(STORAGE_KEYS.DRAFT, { text: '' });
    setStorage(STORAGE_KEYS.MESSAGES + ':' + newSessionId, []);
  }

  async function loadInitialHistory() {
    if (!userId || !sessionId || !apiBaseUrl) return;
    
    isLoadingHistory = true;
    try {
      const result = await loadHistory(apiBaseUrl, userId, sessionId, null, 20);
      messages = result.messages;
      hasMoreHistory = result.hasMore;
      // Update turn limit state from history response
      if (result.session) {
        maxTurns = result.session.maxTurns;
        limitReached = result.session.limitReached;
      }
      saveMessagesToStorage();
      scrollToBottom();
    } catch (e) {
      console.warn('[lc-chatbot] Failed to load history:', e);
    } finally {
      isLoadingHistory = false;
    }
  }

  async function syncSessionState() {
    if (!userId || !sessionId || !apiBaseUrl) return;

    try {
      const result = await loadHistory(apiBaseUrl, userId, sessionId, null, 20);

      // Update turn limit state from server
      if (result.session) {
        maxTurns = result.session.maxTurns;
        limitReached = result.session.limitReached;
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

  function scrollToBottom() {
    setTimeout(() => {
      if (messageListRef) {
        messageListRef.scrollTop = messageListRef.scrollHeight;
      }
    }, 50);
  }

  async function handleSend() {
    const text = inputText.trim();
    if (!text || isSending || !userId || !apiBaseUrl) return;

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
    updateSessionActivity(sessionId);

    try {
      const response = await sendMessageStream(apiBaseUrl, userId, sessionId, text, {
        onProgress: (progress) => {
          currentProgress = progress;
          
          // Track tool usage in history
          if (progress.type === 'tool_start') {
            toolHistory = [...toolHistory, {
              toolName: progress.toolName,
              description: progress.description,
              status: 'running',
              startTime: Date.now()
            }];
          } else if (progress.type === 'tool_end') {
            toolHistory = toolHistory.map((t, i) => 
              i === toolHistory.length - 1 
                ? { ...t, status: progress.isError ? 'error' : 'complete', duration: Date.now() - t.startTime }
                : t
            );
          }
          
          scrollToBottom();
        },
        onError: (error) => {
          console.error('[lc-chatbot] Stream error:', error);
        }
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
        toolCalls: response.toolCalls,
        stats: response.stats
      };

      messages = [...messages, assistantMessage];
      saveMessagesToStorage();
      scrollToBottom();

      // Update turn limit state from response
      if (response.session) {
        maxTurns = response.session.maxTurns;
        limitReached = response.session.limitReached;
      }

      dispatchEvent('message_sent', {
        messageId: userMessage.messageId,
        sessionId,
        toolCalls: response.toolCalls,
        stats: response.stats
      });

    } catch (e) {
      console.error('[lc-chatbot] Send failed:', e);

      // Handle turn limit reached error
      if (e.code === 'turn_limit_reached') {
        limitReached = true;
        if (e.maxTurns) {
          maxTurns = e.maxTurns;
        }
        // Remove the unsent message since turn limit was already reached
        messages = messages.filter(m => m.messageId !== userMessage.messageId);
        saveMessagesToStorage();

        dispatchEvent('error', {
          type: 'turn_limit_reached',
          messageId: userMessage.messageId,
          maxTurns: e.maxTurns
        });
        return;
      }

      // Mark message as failed for other errors
      messages = messages.map(m =>
        m.messageId === userMessage.messageId
          ? { ...m, status: 'failed' }
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

  function handleScroll(e) {
    const el = e.target;
    // Load more when near top (within 50px)
    if (el.scrollTop < 50 && hasMoreHistory && !isLoadingHistory) {
      loadMoreHistory();
    }
  }

  async function retryMessage(messageId) {
    const failedMessage = messages.find(m => m.messageId === messageId && m.status === 'failed');
    if (!failedMessage) return;

    // Remove the failed message and resend
    messages = messages.filter(m => m.messageId !== messageId);
    inputText = failedMessage.content;
    await handleSend();
  }

  function startNewSession() {
    // Trigger fade animation
    isClearing = true;

    setTimeout(() => {
      // Generate new session
      const newSessionId = generateSessionId();
      sessionId = newSessionId;

      // Clear messages
      messages = [];

      // Reset state
      limitReached = false;
      maxTurns = null;
      hasMoreHistory = false;

      // Save new session
      setStorage(STORAGE_KEYS.SESSION, {
        sessionId: newSessionId,
        lastActivity: new Date().toISOString()
      });

      // Clear old messages from storage
      setStorage(STORAGE_KEYS.MESSAGES + ':' + newSessionId, []);

      // Allow clearing animation to complete before resetting flag
      setTimeout(() => {
        isClearing = false;
        // Focus input after animation completes
        inputRef?.focus();
      }, 150);

      dispatchEvent('session_started', { sessionId: newSessionId });
    }, 150); // Animation start delay
  }

  // Resize handling
  function startResize(edge, e) {
    e.preventDefault();
    isResizing = true;
    resizeEdge = edge;

    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = panelWidth;
    const startHeight = panelHeight;

    function onMouseMove(e) {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const maxWidth = window.innerWidth * MAX_WIDTH_RATIO;
      const maxHeight = window.innerHeight * MAX_HEIGHT_RATIO;

      if (resizeEdge.includes('w') || resizeEdge.includes('e')) {
        const widthDelta = resizeEdge.includes('w') ? -dx : dx;
        panelWidth = Math.max(MIN_WIDTH, Math.min(startWidth + widthDelta, maxWidth));
      }

      if (resizeEdge.includes('n') || resizeEdge.includes('s')) {
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

    const sefariaPath = anchor.getAttribute('href');
    if (!sefariaPath) return;

    e.preventDefault();
    e.stopPropagation();

    const path = sefariaPath;
    console.log('[lc-chatbot] Link clicked:', anchor.getAttribute('href'));
    document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', {
      detail: {
        url: path,
        replaceHistory: true
      }
    }));
  }
</script>

<div class="lc-chatbot-container" class:placement-left={placement === 'left'}>
  {#if !isOpen}
    <!-- Floating Button -->
    <button class="lc-chatbot-trigger" onclick={openPanel} aria-label="Open chat">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
      <span class="trigger-label">Chat</span>
    </button>
  {:else}
    <!-- Chat Panel -->
    <div 
      class="lc-chatbot-panel"
      class:resizing={isResizing}
      style="width: {panelWidth}px; height: {panelHeight}px;"
      role="dialog"
      aria-label="Chat window"
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
      <header class="lc-chatbot-header">
        <h2>Chat</h2>
        <div class="header-actions">
          <button class="new-chat-btn" onclick={handleNewChat} disabled={isSending} aria-label="Start a new chat">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12h18"></path>
              <path d="M12 3v18"></path>
            </svg>
            <span>New chat</span>
          </button>
          <button class="close-btn" onclick={closePanel} aria-label="Close chat">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </header>

      <!-- Message List -->
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <div
        class="lc-chatbot-messages"
        class:clearing={isClearing}
        bind:this={messageListRef}
        onscroll={handleScroll}
        onclick={handleMessageLinkClick}
        onkeydown={handleMessageLinkClick}
        role="log"
        aria-label="Chat messages"
        aria-live="polite"
      >
        {#if isLoadingHistory}
          <div class="loading-indicator">
            <div class="loading-spinner"></div>
            <span>Loading messages...</span>
          </div>
        {/if}

        {#if messages.length === 0 && !isLoadingHistory}
          <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
            <p>Start a conversation</p>
          </div>
        {/if}

        {#each messagesWithMarkers as item (item.key)}
          {#if item.type === 'date-marker'}
            <div class="date-marker">
              <span>{item.date}</span>
            </div>
          {:else}
            <div class="message" class:user={item.role === 'user'} class:assistant={item.role === 'assistant'} class:failed={item.status === 'failed'}>
              <div class="message-content">
                {#if item.role === 'assistant'}
                  {@html renderMarkdown(item.content)}
                {:else}
                  <p>{item.content}</p>
                {/if}
              </div>
              <div class="message-meta">
                <span class="message-time">{formatTime(item.timestamp)}</span>
                {#if item.status === 'sending'}
                  <span class="message-status sending">Sending...</span>
                {:else if item.status === 'failed'}
                  <button class="retry-btn" onclick={() => retryMessage(item.messageId)}>
                    Retry
                  </button>
                {/if}
              </div>
            </div>
          {/if}
        {/each}

        {#if isSending}
          <div class="message assistant thinking">
            <div class="message-content thinking-content">
              <!-- Progress Status -->
              <div class="thinking-status">
                {#if currentProgress?.type === 'status'}
                  <div class="status-text">
                    <div class="thinking-spinner"></div>
                    <span>{currentProgress.text}</span>
                  </div>
                {:else if currentProgress?.type === 'tool_start'}
                  <div class="status-text tool-running">
                    <div class="thinking-spinner"></div>
                    <span>{currentProgress.description || `Running ${currentProgress.toolName}...`}</span>
                  </div>
                {:else if currentProgress?.type === 'tool_end'}
                  <div class="status-text" class:tool-error={currentProgress.isError}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      {#if currentProgress.isError}
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="15" y1="9" x2="9" y2="15"></line>
                        <line x1="9" y1="9" x2="15" y2="15"></line>
                      {:else}
                        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                        <polyline points="22 4 12 14.01 9 11.01"></polyline>
                      {/if}
                    </svg>
                    <span>{currentProgress.isError ? 'Tool error' : 'Done'}</span>
                  </div>
                {:else}
                  <div class="status-text">
                    <div class="thinking-spinner"></div>
                    <span>Thinking...</span>
                  </div>
                {/if}
              </div>
              
              <!-- Tool History -->
              {#if toolHistory.length > 0}
                <div class="tool-history">
                  {#each toolHistory as tool, i}
                    <div class="tool-item" class:running={tool.status === 'running'} class:error={tool.status === 'error'}>
                      <span class="tool-icon">
                        {#if tool.status === 'running'}
                          <div class="mini-spinner"></div>
                        {:else if tool.status === 'error'}
                          ✗
                        {:else}
                          ✓
                        {/if}
                      </span>
                      <span class="tool-desc">{tool.description || tool.toolName}</span>
                      {#if tool.duration}
                        <span class="tool-duration">{(tool.duration / 1000).toFixed(1)}s</span>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
        {/if}
      </div>

      <!-- Input Footer -->
      <footer class="lc-chatbot-input">
        {#if limitReached}
          <div class="limit-reached">
            <p class="limit-hint">{newSessionHintText}</p>
            <button class="new-session-btn" onclick={startNewSession}>
              {newSessionButtonText}
            </button>
          </div>
        {:else}
          <textarea
            bind:this={inputRef}
            bind:value={inputText}
            onkeydown={handleKeydown}
            placeholder="Type a message..."
            rows="1"
            disabled={isSending}
          ></textarea>
          <button
            class="send-btn"
            onclick={handleSend}
            disabled={!inputText.trim() || isSending}
            aria-label="Send message"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        {/if}
      </footer>
    </div>
  {/if}
</div>

<style>
  /* CSS Custom Properties for theming */
  :host {
    --lc-primary: #6366f1;
    --lc-primary-hover: #4f46e5;
    --lc-bg: #ffffff;
    --lc-bg-secondary: #f8fafc;
    --lc-bg-tertiary: #f1f5f9;
    --lc-text: #1e293b;
    --lc-text-secondary: #64748b;
    --lc-text-muted: #94a3b8;
    --lc-border: #e2e8f0;
    --lc-user-bg: #6366f1;
    --lc-user-text: #ffffff;
    --lc-assistant-bg: #f1f5f9;
    --lc-assistant-text: #1e293b;
    --lc-error: #ef4444;
    --lc-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
    --lc-radius: 16px;
    --lc-radius-sm: 8px;
    --lc-font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;

    display: block;
    font-family: var(--lc-font);
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  .lc-chatbot-container {
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 9999;
  }

  .lc-chatbot-container.placement-left {
    right: auto;
    left: 24px;
  }

  /* Trigger Button */
  .lc-chatbot-trigger {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 20px;
    background: var(--lc-primary);
    color: white;
    border: none;
    border-radius: 9999px;
    cursor: pointer;
    font-family: var(--lc-font);
    font-size: 14px;
    font-weight: 500;
    box-shadow: var(--lc-shadow);
    transition: all 0.2s ease;
  }

  .lc-chatbot-trigger:hover {
    background: var(--lc-primary-hover);
    transform: scale(1.02);
  }

  .lc-chatbot-trigger:active {
    transform: scale(0.98);
  }

  .trigger-label {
    font-weight: 600;
  }

  /* Chat Panel */
  .lc-chatbot-panel {
    display: flex;
    flex-direction: column;
    background: var(--lc-bg);
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
    background: var(--lc-bg);
    border-bottom: 1px solid var(--lc-border);
  }

  .lc-chatbot-header h2 {
    font-size: 16px;
    font-weight: 600;
    color: var(--lc-text);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .new-chat-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    background: var(--lc-bg-tertiary);
    border: 1px solid var(--lc-border);
    border-radius: var(--lc-radius-sm);
    color: var(--lc-text-secondary);
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    font-family: var(--lc-font);
    transition: all 0.15s ease;
  }

  .new-chat-btn:hover:not(:disabled) {
    background: var(--lc-bg-secondary);
    color: var(--lc-text);
  }

  .new-chat-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
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

  /* Message List */
  .lc-chatbot-messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: var(--lc-bg-secondary);
  }

  /* Date Markers */
  .date-marker {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px 0;
  }

  .date-marker span {
    font-size: 12px;
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
    max-width: 85%;
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
    align-self: flex-end;
  }

  .message.assistant {
    align-self: flex-start;
  }

  .message-content {
    padding: 12px 16px;
    border-radius: var(--lc-radius);
    word-wrap: break-word;
  }

  .message.user .message-content {
    background: var(--lc-user-bg);
    color: var(--lc-user-text);
    border-bottom-right-radius: 4px;
  }

  .message.assistant .message-content {
    background: var(--lc-bg);
    color: var(--lc-assistant-text);
    border-bottom-left-radius: 4px;
    border: 1px solid var(--lc-border);
  }

  .message.failed .message-content {
    border: 1px solid var(--lc-error);
    background: #fef2f2;
  }

  .message-content p {
    margin: 0;
    line-height: 1.5;
  }

  /* Markdown Styles */
  .message-content :global(h1),
  .message-content :global(h2),
  .message-content :global(h3),
  .message-content :global(h4),
  .message-content :global(h5),
  .message-content :global(h6) {
    margin-top: 12px;
    margin-bottom: 8px;
    font-weight: 600;
    line-height: 1.3;
  }

  .message-content :global(h1) { font-size: 1.25em; }
  .message-content :global(h2) { font-size: 1.15em; }
  .message-content :global(h3) { font-size: 1.05em; }

  .message-content :global(p) {
    margin-bottom: 8px;
  }

  .message-content :global(p:last-child) {
    margin-bottom: 0;
  }

  .message-content :global(a) {
    color: var(--lc-primary);
    text-decoration: underline;
  }

  .message-content :global(ul),
  .message-content :global(ol) {
    margin: 8px 0;
    padding-left: 20px;
  }

  .message-content :global(li) {
    margin-bottom: 4px;
  }

  .message-content :global(code) {
    font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace;
    font-size: 0.9em;
    background: var(--lc-bg-tertiary);
    padding: 2px 6px;
    border-radius: 4px;
  }

  .message-content :global(pre) {
    margin: 8px 0;
    padding: 12px;
    background: #1e293b;
    border-radius: var(--lc-radius-sm);
    overflow-x: auto;
  }

  .message-content :global(pre code) {
    background: transparent;
    padding: 0;
    color: #e2e8f0;
  }

  .message-content :global(blockquote) {
    margin: 8px 0;
    padding-left: 12px;
    border-left: 3px solid var(--lc-primary);
    color: var(--lc-text-secondary);
    font-style: italic;
  }

  .message-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 4px;
    padding: 0 4px;
  }

  .message-time {
    font-size: 11px;
    color: var(--lc-text-muted);
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

  /* Thinking/Progress Indicator */
  .thinking-content {
    min-width: 200px;
    padding: 12px 16px !important;
  }

  .thinking-status {
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

  .thinking-spinner {
    width: 14px;
    height: 14px;
    border: 2px solid var(--lc-border);
    border-top-color: var(--lc-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  /* Tool History */
  .tool-history {
    display: flex;
    flex-direction: column;
    gap: 4px;
    border-top: 1px solid var(--lc-border);
    padding-top: 8px;
    margin-top: 4px;
  }

  .tool-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--lc-text-muted);
    padding: 4px 0;
  }

  .tool-item.running {
    color: var(--lc-primary);
  }

  .tool-item.error {
    color: var(--lc-error);
  }

  .tool-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }

  .tool-item:not(.running):not(.error) .tool-icon {
    color: #22c55e;
  }

  .mini-spinner {
    width: 12px;
    height: 12px;
    border: 1.5px solid var(--lc-border);
    border-top-color: var(--lc-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  .tool-desc {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-duration {
    font-size: 11px;
    color: var(--lc-text-muted);
    opacity: 0.7;
  }

  /* Empty State */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--lc-text-muted);
    gap: 12px;
  }

  .empty-state p {
    font-size: 14px;
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
    padding: 12px 16px;
    background: var(--lc-bg);
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
    font-size: 14px;
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
    opacity: 0.5;
    cursor: not-allowed;
  }

  .send-btn:active:not(:disabled) {
    transform: scale(0.95);
  }

  /* Clearing animation for message list */
  .lc-chatbot-messages.clearing {
    opacity: 0.5;
    transition: opacity 0.15s ease;
  }

  /* Limit Reached UI */
  .limit-reached {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 8px 0;
  }

  .limit-hint {
    font-size: 13px;
    color: var(--lc-text-secondary);
    text-align: center;
    margin: 0;
  }

  .new-session-btn {
    padding: 10px 20px;
    background: var(--lc-primary);
    color: white;
    border: none;
    border-radius: var(--lc-radius-sm);
    font-family: var(--lc-font);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .new-session-btn:hover {
    background: var(--lc-primary-hover);
  }

  .new-session-btn:active {
    transform: scale(0.98);
  }
</style>
