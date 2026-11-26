---
name: sefaria-linked-asset
description: >-
  Builds an interactive HTML page about a Jewish text (a scrolling story, timeline, map,
  diagram or visual explainer) that opens beside the text in the Sefaria reader and moves with
  it both ways, and saves it to Sefaria. Use when the user asks for an interactive page,
  artifact, visualization, animation, timeline, map or app about a passage, book or theme in
  Sefaria's library, asks for one "linked to" or "synced with" the text, or asks to save such a
  page to Sefaria. Do not use for plain text answers, summaries, translations or explanations
  without an interactive page, and not for source sheets (use the sheet tools for those).
compatibility: >-
  Needs the Sefaria connector and a client with the MCP Skills extension (or the read_resource
  tool). Saving needs the signed-in connector with permission to save to Sefaria.
metadata:
  org.sefaria/kind: system
  org.sefaria/version: 1.0.0
---

# Sefaria linked asset

## Critical

Every interactive page about a Jewish text must do both of these:

1. **Declare its passages** in a manifest inside the file:
   `<script type="application/json" id="sefaria-asset">` with `v`, `title`, `description`,
   `lang` and `refs` (each `{ref, label}`). Sefaria indexes the page by these refs and refuses a
   file without them.
2. **Be linked both ways.** Paste [references/shim.js](references/shim.js) unchanged in a
   `<script>`. Move the page when the reader moves (`SefariaLink.onPlace`), and call
   `SefariaLink.setPlace(ref)` only when the viewer moves the page themselves (their own scroll
   or click), never in reaction to `onPlace`.

Check every ref with `validate_refs` before you write it into the file, and write the
`normalized` form it returns. One unknown ref makes the save fail.

## Steps

1. Find the passages with the Sefaria tools (`get_text`, `search_in_book`, …) and split the
   text into scenes, one ref or range per scene. Quote only text the tools returned.
2. Run `validate_refs` on every ref; fix or drop any that fail.
3. Build one self-contained HTML file: the manifest, the shim, then your code. Keep each
   scene's ref on its element (`data-ref`). See [references/examples.md](references/examples.md).
4. Rules for the file:
   - Everything inline: no external scripts, fonts, images or requests. In the reader's sidebar
     the page has no network at all.
   - The page runs sandboxed with no origin: no cookies, `localStorage`, pop-ups, forms or top
     navigation. Keep state in memory.
   - At most 5 MB. It must still work on its own, outside the reader (the shim does nothing
     there).
   - Use `SefariaLink.open(ref)` to take the reader to another text (a commentary, a parallel).
5. Offer to save it: "Shall I save this to Sefaria, so it opens beside the text in the
   reader?" Only on a yes, call `save_asset` with the whole file as `html` (and `source_url` if
   the user gave the Claude artifact link; `asset_id` to save a new version of an asset already
   saved). Give the user the reader link and the asset link it returns, and its note on
   publishing. If it refuses the file, fix every problem it lists and call it again.
6. If `save_asset` is not available, say that saving needs the signed-in Sefaria connector
   with permission to save to Sefaria, or that they can download the file and upload it on
   their Sefaria Social profile.

The protocol in full is in [references/protocol.md](references/protocol.md).
