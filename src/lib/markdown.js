/**
 * Markdown rendering with sanitization
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true
});

// Custom renderer for links to open in new tab
const renderer = new marked.Renderer();
renderer.link = function({ href, title, text }) {
  const titleAttr = title ? ` title="${title}"` : '';
  return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`;
};

marked.use({ renderer });

// Configure DOMPurify
DOMPurify.addHook('afterSanitizeAttributes', function(node) {
  // Ensure all links open in new tab
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

/**
 * Render markdown to sanitized HTML
 * @param {string} markdown - Markdown content
 * @returns {string} Sanitized HTML
 */
export function renderMarkdown(markdown) {
  if (!markdown) return '';
  
  try {
    const html = marked.parse(markdown);
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'br', 'hr',
        'strong', 'em', 'b', 'i', 'u', 's', 'del',
        'ul', 'ol', 'li',
        'a',
        'code', 'pre',
        'blockquote',
        'table', 'thead', 'tbody', 'tr', 'th', 'td'
      ],
      ALLOWED_ATTR: ['href', 'title', 'target', 'rel', 'class']
    });
  } catch (e) {
    console.warn('[lc-chatbot] Markdown render error:', e);
    return DOMPurify.sanitize(markdown);
  }
}

