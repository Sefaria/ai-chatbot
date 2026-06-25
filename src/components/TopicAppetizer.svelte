<script>
  import { _, locale } from '../i18n/index.js';

  let { data, streaming = false, onClickTopic, collapsed = false } = $props();

  function attachClickHandler(node, topic) {
    function handler(e) {
      // preventDefault blocks the <a> navigation; we intentionally let the event
      // bubble to the host click tracker (data-feature-name) instead of stopping it.
      e.preventDefault();
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
   *
   * Oxford comma ("A, B, or C") is English-only. Hebrew never uses a comma
   * before the final connector: "A, B או C" for any count.
   */
  function buildTopicSegments(topics, orWord, isHebrew) {
    const last = topics.length - 1;
    const segments = [];
    topics.forEach((topic, i) => {
      if (i === 0) {
        segments.push({ type: 'topic', topic });
        return;
      }
      // Final connector: Hebrew always uses spaces only; English uses the
      // Oxford comma for 3+ items. Otherwise a plain ", " between items.
      const isFinalConnector = i === last;
      const useSpacesOnly = isHebrew || topics.length <= 2;
      const sep = isFinalConnector
        ? (useSpacesOnly ? ` ${orWord} ` : `, ${orWord} `)
        : ', ';
      segments.push({ type: 'sep', value: sep });
      segments.push({ type: 'topic', topic });
    });
    return segments;
  }

  let frame = $derived($_('assistant.appetizer.sentence'));
  let orWord = $derived($_('assistant.appetizer.or'));
  let frameParts = $derived(splitFrame(frame));
  let isHebrew = $derived($locale === 'he');
  let topicSegments = $derived(buildTopicSegments(data.topics, orWord, isHebrew));
</script>

{#snippet renderSegment(seg)}
  {#if seg.type === 'sep'}
    {seg.value}
  {:else}
    <button
      class="lc-topic-link"
      type="button"
      data-feature-name="related_topics_link"
      use:attachClickHandler={seg.topic}
    >{seg.topic.topicTitle}</button>
  {/if}
{/snippet}

<div class="topic-appetizer" class:topic-appetizer--collapsed={collapsed}>
  <p class="appetizer-sentence">
    {frameParts[0]}{#each topicSegments as seg}{@render renderSegment(seg)}{/each}{frameParts[1]}
  </p>
</div>

<style>
  .topic-appetizer {
    display: flex;
    align-items: flex-start;
    padding: var(--global-dimension-100) var(--global-dimension-150);
    border-radius: var(--global-dimension-0);
    border-inline-start: 2px solid var(--semantic-action-primary);
    background: var(--core-blue-tbr-100);
    width: 100%;
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
    line-height: var(--global-dimension-250);
    color: var(--semantic-text-secondary);
  }

  :global(.lc-topic-link) {
    color: var(--semantic-text-link);
    font-family: Roboto;
    font-size: 12px;
    font-weight: 600;
    line-height: var(--global-dimension-250);
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
