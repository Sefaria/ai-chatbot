# The linked asset protocol (v1)

A linked asset is an interactive HTML page that opens as a panel beside a text panel in the
Sefaria reader. The two are linked: they share a **place**, a ref in the base text. Scroll the
text and the page follows; move through the page and the text follows.

## 1. The file

One self-contained HTML file. The manifest is inside it:

```html
<script type="application/json" id="sefaria-asset">
{
  "v": 1,
  "title": "The Seven Days",
  "description": "A scrolling story of creation, tied to Genesis 1.",
  "lang": "en",
  "refs": [
    { "ref": "Genesis 1:1-5", "label": "Day one" },
    { "ref": "Genesis 1:6-8", "label": "Day two" }
  ]
}
</script>
```

- Exactly one such block, valid JSON. `v` is `1`. `title` (up to 200 characters) and at least
  one ref are required; `description` (up to 1000) and `lang` are optional. At most 100 refs.
- `refs` is what the page covers. It feeds Sefaria's index and the reader's sidebar: a published
  asset is listed beside every segment its refs cover. Ranges are expanded to segments; a single
  ref may not cover more than 2,000 segments (cite chapters, not whole books).
- Sefaria checks and normalizes every ref when the file is saved. An invalid ref fails the save
  with a message that names the ref. Check refs with `validate_refs` before writing them.
- Sefaria reads the manifest from the file. Nothing outside the file can add refs to it.
- The file is stored as an immutable version. Saving again makes a new version.

## 2. The connection

The page runs in a sandboxed iframe (`sandbox="allow-scripts"`) with an opaque origin, so
origin checks are not available. The page and the reader use a private message channel:

1. The page posts `{type: "sefaria:ready", v: 1}` to `window.parent`.
2. The reader accepts it only if it came from its own iframe (`event.source`), then answers
   `{type: "sefaria:connect", v: 1}` and transfers one end of a `MessageChannel`. The target
   origin has to be `"*"` because the frame's origin is opaque; that is safe because the message
   holds no secret and the port is the capability.
3. Everything after that travels on the port.

The page accepts a connect only from `window.parent`, and only the first one. If no answer
comes (Claude's preview, a plain browser tab) the page works on its own. The shim posts
`ready` up to four times in the first few seconds, in case the reader was not yet listening;
the reader answers the first and ignores the rest.

## 3. Messages

| Direction | Message | Meaning |
|---|---|---|
| Reader to page | `{type: "init", v, theme, interfaceLang, dir, context, place}` | First message on the port. `context` is `shiur`, `sidebar` or `panel`. `place` may be null. |
| Reader to page | `{type: "place", ref, segments, cause}` | The linked text panel settled here. `cause` is `user`, `link` or `replay`. |
| Page to reader | `{type: "place", ref}` | The page is now showing this; move the linked text panel there. |
| Page to reader | `{type: "open", ref}` | Take the linked text panel to a different text. |

What the reader does with them:

- Every incoming ref is checked and normalized; anything that fails is dropped.
- Messages are rate limited (about 10 a second) and unknown types are dropped.
- The page's `place` is ignored while a recorded shiur is replaying, and for a short time after
  the reader sent the page a `place`.
- The page can only move its linked panel. It gets no reader state, no URL and no data about
  the viewer.

So the page must send `place` only for the viewer's own movement, and a page that floods the
channel is cut off.

## 4. The shim

`references/shim.js`, about 1 kB, does all of the above. Paste it unchanged in a `<script>`
before your own code. It exposes `window.SefariaLink`:

```js
SefariaLink.onPlace(function (place) {   // place: {ref, segments, cause}
  // scroll or change my scene to place.ref
});
SefariaLink.setPlace("Genesis 1:6");     // only on the viewer's own scroll or click
SefariaLink.open("Rashi on Genesis 1:6:1");
SefariaLink.connected();                 // true once the reader answered
SefariaLink.info;                        // the init message (theme, dir, context...), or null
```

- `setPlace` calls made while an `onPlace` handler runs, and for half a second after (long
  enough for a smooth scroll the handler starts), are dropped, and so is a repeat of the last
  place. A page can therefore scroll itself from `onPlace` with the same code that reports the
  viewer's scrolling.
- Outside the reader every call does nothing.

## 5. Where it runs

| Context | Network | Notes |
|---|---|---|
| A shiur (a class recording) | Allowed | Still sandboxed. On replay the reader moves and the link drives the page. |
| The sidebar and public pages | None | Inline everything. Sefaria may later allow a short list of trusted CDNs. |

In every context: no cookies, no storage, no pop-ups, no forms, no top navigation.
