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
    const last = topics.length - 1;
    const segments = [];
    topics.forEach((topic, i) => {
      if (i > 0) {
        // ", " between items; " {or} " before the last (with a leading
        // comma only when there are 3+ topics): "A, B, or C" / "A or B".
        const sep = i === last ? `${topics.length > 2 ? ', ' : ' '}${orWord} ` : ', ';
        segments.push({ type: 'sep', value: sep });
      }
      segments.push({ type: 'topic', topic });
    });
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
    display: flex;
    align-items: flex-start;
    padding: var(--global-dimension-100, 8px) var(--global-dimension-150, 12px);
    border-radius: var(--global-dimension-0, 0);
    border-inline-start: 2px solid var(--semantic-action-primary, #18345D);
    background: var(--core-blue-tbr-100, #F0F7FF);
    width: 252px;
    max-width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }

  .topic-appetizer--collapsed {
    background: none;
    border-inline-start: none;
    padding: 0;
    height: auto;
    overflow: visible;
  }

  .appetizer-sentence {
    flex: 1 0 0;
    min-width: 0;
    margin: 0;
    font-family: Roboto, sans-serif;
    font-size: 12px;
    line-height: var(--global-dimension-250, 20px);
    color: var(--semantic-text-secondary, #575757);
  }

  :global(.lc-topic-link) {
    color: var(--semantic-text-link, #18345D);
    font-family: Roboto;
    font-size: 12px;
    font-weight: 600;
    line-height: var(--global-dimension-250, 20px);
    text-decoration: underline;
    text-decoration-style: solid;
    text-underline-position: from-font;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
  }

  :global(.interface-hebrew) :global(.lc-topic-link) {
    font-family: Heebo;
    font-weight: 600;
  }
</style>
