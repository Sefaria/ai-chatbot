# Sefaria-Project: Pass Origin Attribute to LC Chatbot Web Component

## Context

The LC Chatbot (ai-chatbot repo) is adding origin tagging to Braintrust traces so production user traffic can be distinguished from dev/eval/testing traffic. The chatbot web component (`<lc-chatbot>`) will accept a new `origin` attribute and forward it in API requests.

The chatbot defaults to `origin: "dev"` if no attribute is provided. Production Sefaria must pass `origin="sefaria-prod"` so its traces are correctly identified.

**This change is safe to deploy immediately** — the current chatbot component ignores unknown attributes. Once the chatbot repo deploys its side, the attribute will be read and forwarded automatically.

## What to Change

Find where sefaria-project renders the `<lc-chatbot>` web component. It currently looks something like:

```html
<lc-chatbot
  user-id="..."
  api-base-url="..."
></lc-chatbot>
```

Add the `origin` attribute:

```html
<lc-chatbot
  user-id="..."
  api-base-url="..."
  origin="sefaria-prod"
></lc-chatbot>
```

The `origin` attribute is a plain string passed as a web component attribute/prop. The chatbot component handles forwarding it in its API requests — sefaria-project doesn't need to touch any API calls.

If there are other Sefaria deployments (staging, Israel site, etc.) that also embed this chatbot, use different origin values for each, e.g. `"sefaria-staging"` or `"sefaria-il"`. The value is a free-form string — any value works, but only `"sefaria-prod"` is treated as production by the chatbot (no "dev" tag in Braintrust).

## Testing

After deploying both this change and the chatbot changes:
- Check Braintrust traces for the "On Site Agent" project
- Production traces should have `metadata.origin: "sefaria-prod"` and no tags
- Dev/eval traces should have `tags: ["dev"]`

Before the chatbot deploys its side: this attribute has no effect (unknown attributes are ignored by the current component). No risk.
