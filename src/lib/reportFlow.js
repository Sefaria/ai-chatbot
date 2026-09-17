/**
 * The "Report an issue" flow: a reader on an unverified machine translation
 * flags a problem with one segment.
 *
 * The host page (Sefaria-Project) dispatches a `chatbot:start-flow` DOM event
 * carrying the segment's ref and its two texts. This module turns that into the
 * seed the reader finds waiting in the input box. Composing it here, rather than
 * on the host page, keeps the seed's shape next to the prompt that reads it —
 * changing the format is a widget deploy, not a Sefaria deploy.
 */

export const START_FLOW_EVENT = 'chatbot:start-flow';
export const REPORT_ISSUE_FLOW = 'report_issue';

/** Flows this widget knows how to start. */
const SUPPORTED_FLOWS = new Set([REPORT_ISSUE_FLOW]);

/**
 * Strip Sefaria's inline markup down to plain text.
 *
 * Segment text arrives as HTML — footnotes, <b>/<i>, <span> wrappers. The seed
 * goes into a plain <textarea> and then to the model, so tags are noise at best
 * and confusing at worst. Footnote bodies are dropped rather than inlined: they
 * are not part of the translation the reader is looking at.
 */
export function stripSegmentMarkup(html) {
  if (typeof html !== 'string' || !html) return '';
  return html
    .replace(/<i\s+class="footnote"[^>]*>[\s\S]*?<\/i>/gi, '')
    .replace(/<sup>[\s\S]*?<\/sup>/gi, '')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Validate and normalise a `chatbot:start-flow` event detail.
 *
 * Returns null for anything unusable so the widget can ignore a malformed or
 * stale event instead of opening an empty report.
 *
 * @param {unknown} detail
 * @returns {{flow: string, ref: string, en: string, he: string} | null}
 */
export function parseStartFlowDetail(detail) {
  if (!detail || typeof detail !== 'object') return null;

  const flow = typeof detail.flow === 'string' ? detail.flow.trim() : '';
  if (!SUPPORTED_FLOWS.has(flow)) return null;

  const ref = typeof detail.ref === 'string' ? detail.ref.trim() : '';
  if (!ref) return null;

  const en = stripSegmentMarkup(detail.en);
  const he = stripSegmentMarkup(detail.he);
  // With neither text there is nothing to discuss; the reader would have to
  // retype the segment, which defeats the point of seeding it.
  if (!en && !he) return null;

  return { flow, ref, en, he };
}

/** Room left for the reader's own comment when the seed has to be trimmed. */
const COMMENT_HEADROOM = 200;

/** Trim `text` to `budget` characters, marking that it was cut. */
function truncate(text, budget) {
  if (budget <= 0) return '';
  if (text.length <= budget) return text;
  return `${text.slice(0, Math.max(0, budget - 1)).trimEnd()}…`;
}

/**
 * Build the text the reader finds already in the input box.
 *
 * The shape is documented in prompts/prompt_text/report_issue.md — the two must
 * change together. The reader types after `Comment:` and sends.
 *
 * `maxLength` is the textarea's own limit (`max-input-chars`, which an admin can
 * lower via RemoteConfig). A seeded value set programmatically bypasses the
 * element's `maxlength`, so a long segment under a tight limit would leave the
 * reader unable to type at all. When that would happen, the two texts are
 * trimmed — proportionally, so a long Hebrew segment doesn't eat the English —
 * and the scaffolding is always kept intact.
 *
 * @param {{ref: string, en?: string, he?: string}} request
 * @param {number} [maxLength] - Optional character budget for the whole seed.
 * @returns {string}
 */
export function buildReportSeed(request, maxLength = 0) {
  const ref = (request?.ref || '').trim();
  let en = stripSegmentMarkup(request?.en);
  let he = stripSegmentMarkup(request?.he);

  const compose = (english, hebrew) => {
    const lines = [`${ref} says:`];
    if (english) lines.push(`English: "${english}"`);
    if (hebrew) lines.push(`Hebrew: ${hebrew}`);
    lines.push('', 'Comment: ');
    return lines.join('\n');
  };

  const seed = compose(en, he);
  const limit = Number(maxLength) || 0;
  if (limit <= 0 || seed.length <= limit - COMMENT_HEADROOM) {
    return seed;
  }

  // Budget for the two bodies once the scaffolding and the reader's headroom
  // are accounted for.
  const scaffolding = seed.length - en.length - he.length;
  const budget = limit - COMMENT_HEADROOM - scaffolding;
  if (budget <= 0) {
    // Pathological limit — keep the ref so the report is still identifiable.
    return compose('', '');
  }

  const total = en.length + he.length;
  const enBudget = Math.floor((budget * en.length) / total);
  en = truncate(en, enBudget);
  he = truncate(he, budget - enBudget);

  return compose(en, he);
}
