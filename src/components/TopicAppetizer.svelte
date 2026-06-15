<script>
  import { _ } from '../i18n/index.js';

  let { data, streaming = false, onClickTopic, collapsed = false } = $props();

  function attachClickHandler(node, topic) {
    function handler(e) {
      e.preventDefault();
      e.stopPropagation();
      if (onClickTopic) onClickTopic(topic.topicSlug, topic.topicUrl);
    }
    node.addEventListener('click', handler);
    return {
      destroy() { node.removeEventListener('click', handler); }
    };
  }

  /**
   * Split the sentence frame on the `{topics}` placeholder and return
   * [before, after] strings so the template can interleave clickable buttons.
   */
  function splitFrame(frame) {
    const idx = frame.indexOf('{topics}');
    if (idx === -1) return [frame, ''];
    return [frame.slice(0, idx), frame.slice(idx + '{topics}'.length)];
  }

  /**
   * Build the list of topic segments: each topic button, separated by
   * ", " between items and " {or} " before the last one.
   * Returns an array of { type: 'topic'|'sep', value } objects.
   */
  function buildTopicSegments(topics, orWord) {
    const segments = [];
    for (let i = 0; i < topics.length; i++) {
      segments.push({ type: 'topic', topic: topics[i] });
      if (i < topics.length - 1) {
        if (i < topics.length - 2) {
          // between non-final items: ", "
          segments.push({ type: 'sep', value: ', ' });
        } else {
          // before the last item: ", or " (EN) / " או " (HE)
          // For 2 topics: " or T2" (no leading comma); for 3: ", or T3"
          if (topics.length === 2) {
            segments.push({ type: 'sep', value: ' ' + orWord + ' ' });
          } else {
            segments.push({ type: 'sep', value: ', ' + orWord + ' ' });
          }
        }
      }
    }
    return segments;
  }

  let frame = $derived($_('appetizer.sentence'));
  let orWord = $derived($_('appetizer.or'));
  let frameParts = $derived(splitFrame(frame));
  let topicSegments = $derived(buildTopicSegments(data.topics, orWord));
</script>

<div class="topic-appetizer" class:topic-appetizer--collapsed={collapsed}>
  <p class="appetizer-sentence">
    {frameParts[0]}{#each topicSegments as seg}{#if seg.type === 'sep'}{seg.value}{:else}<button
        class="lc-topic-link"
        type="button"
        use:attachClickHandler={seg.topic}
      >{seg.topic.topicTitle}</button>{/if}{/each}{frameParts[1]}
  </p>
</div>

<style>
  .topic-appetizer {
    background: var(--lc-topics-bg, #f0f7ff);
    border-inline-start: 2px solid var(--lc-primary, #18345d);
    padding: 8px 12px;
    width: 100%;
    box-sizing: border-box;
    height: auto;
    overflow: visible;
    animation: fadeIn 0.3s ease-in;
  }

  .topic-appetizer--collapsed {
    background: none;
    border-inline-start: none;
    padding: 0;
    height: auto;
    overflow: visible;
  }

  .appetizer-sentence {
    margin: 0;
    font-size: 12px;
    line-height: 20px;
    color: var(--lc-text-secondary, #575757);
  }

  :global(.lc-topic-link) {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: 12px;
    line-height: 20px;
    font-family: Roboto, sans-serif;
    color: var(--lc-primary, #18345d);
    text-decoration: underline;
    text-decoration-style: solid;
    text-underline-position: from-font;
    font-weight: 600;
  }

  :global(.interface-hebrew) :global(.lc-topic-link) {
    font-family: Heebo, sans-serif;
    font-weight: 600;
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
</style>
