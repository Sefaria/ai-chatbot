# Worked examples

Each example is a whole file except the line `/* SHIM */`: put the contents of
`references/shim.js` there, unchanged. Every ref in them was checked with `validate_refs`.

All three use the same small helper to compare refs within one book, so a place the reader sends
(`Genesis 1:7`) can be matched to the scene that covers it (`Genesis 1:6-8`):

```js
// "Genesis 1:6-8" -> [1006, 1008]; "Genesis 2:1" -> [2001, 2001]. Chapter:verse refs only.
function span(ref) {
  var m = /(\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$/.exec(ref || "");
  if (!m) return null;
  var c = +m[1], start = c * 1000 + +m[2];
  return [start, m[4] ? (m[3] ? +m[3] : c) * 1000 + +m[4] : start];
}
function covers(sceneRef, ref) {
  var a = span(sceneRef), b = span(ref);
  return !!(a && b && b[0] <= a[1] && b[1] >= a[0]);
}
```

## 1. A scroll-driven story: the seven days (Genesis 1:1-2:3)

The viewer scrolls through the days; the text follows. When the reader scrolls, the story jumps
to that day.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Seven Days</title>
<script type="application/json" id="sefaria-asset">
{
  "v": 1,
  "title": "The Seven Days",
  "description": "A scrolling story of creation, day by day, tied to Genesis 1:1-2:3.",
  "lang": "en",
  "refs": [
    { "ref": "Genesis 1:1-5", "label": "Day one: light" },
    { "ref": "Genesis 1:6-8", "label": "Day two: the firmament" },
    { "ref": "Genesis 1:9-13", "label": "Day three: land and plants" },
    { "ref": "Genesis 1:14-19", "label": "Day four: the lights" },
    { "ref": "Genesis 1:20-23", "label": "Day five: fish and birds" },
    { "ref": "Genesis 1:24-31", "label": "Day six: animals and humankind" },
    { "ref": "Genesis 2:1-3", "label": "The seventh day: rest" }
  ]
}
</script>
<style>
  body { margin: 0; font: 18px/1.5 Georgia, serif; color: #f4f1e8; background: #101820; }
  section { min-height: 100vh; display: grid; place-items: center; padding: 2rem;
            transition: background 0.8s; }
  section h2 { font-size: 2rem; margin: 0 0 0.5rem; }
  section.active h2 { color: #f2c14e; }
</style>
</head>
<body>
<main id="story"></main>
<script>/* SHIM */</script>
<script>
  var manifest = JSON.parse(document.getElementById("sefaria-asset").textContent);
  var colors = ["#0b1d2a", "#18435a", "#2f5d3a", "#3a2f5d", "#14506b", "#6b4a14", "#4a3b2a"];
  var story = document.getElementById("story");
  manifest.refs.forEach(function (scene, i) {
    var s = document.createElement("section");
    s.dataset.ref = scene.ref;
    s.style.background = colors[i];
    s.innerHTML = "<div><h2></h2><p></p></div>";
    s.querySelector("h2").textContent = scene.label;
    s.querySelector("p").textContent = scene.ref;
    story.appendChild(s);
  });
  var sections = Array.prototype.slice.call(document.querySelectorAll("section"));

  function span(ref) {
    var m = /(\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$/.exec(ref || "");
    if (!m) return null;
    var c = +m[1], start = c * 1000 + +m[2];
    return [start, m[4] ? (m[3] ? +m[3] : c) * 1000 + +m[4] : start];
  }
  function covers(sceneRef, ref) {
    var a = span(sceneRef), b = span(ref);
    return !!(a && b && b[0] <= a[1] && b[1] >= a[0]);
  }
  function activate(section) {
    sections.forEach(function (s) { s.classList.toggle("active", s === section); });
  }

  // The viewer scrolled: report the day now in the middle of the screen.
  var seen = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      activate(e.target);
      SefariaLink.setPlace(e.target.dataset.ref);   // dropped if the reader caused this scroll
    });
  }, { rootMargin: "-45% 0px -45% 0px" });
  sections.forEach(function (s) { seen.observe(s); });

  // The reader moved: jump to the day that covers that verse. A jump, not a smooth scroll: a
  // long smooth scroll would pass other days after the shim's quiet window and report them.
  SefariaLink.onPlace(function (place) {
    var target = sections.find(function (s) { return covers(s.dataset.ref, place.ref); });
    if (target) target.scrollIntoView({ block: "start" });
  });
</script>
</body>
</html>
```

## 2. A clickable timeline: Abram's journey (Genesis 12)

Click a stop and the text goes there; read on in the text and the stop lights up. Nothing
scrolls, so clicks are the only thing that sends a place.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Abram's Journey</title>
<script type="application/json" id="sefaria-asset">
{
  "v": 1,
  "title": "Abram's Journey",
  "description": "A clickable timeline of Abram's travels in Genesis 12, from Haran to Egypt.",
  "lang": "en",
  "refs": [
    { "ref": "Genesis 12:1-3", "label": "The call" },
    { "ref": "Genesis 12:4-5", "label": "Leaving Haran" },
    { "ref": "Genesis 12:6-7", "label": "Shechem" },
    { "ref": "Genesis 12:8", "label": "Between Bethel and Ai" },
    { "ref": "Genesis 12:9", "label": "Toward the Negev" },
    { "ref": "Genesis 12:10-20", "label": "Down to Egypt" }
  ]
}
</script>
<style>
  body { margin: 0; padding: 1.5rem; font: 16px/1.4 system-ui, sans-serif; background: #fbf7ee; }
  ol { list-style: none; margin: 0; padding: 0 0 0 1.5rem; border-left: 3px solid #c9b78f; }
  li { margin: 0 0 1rem; }
  button { all: unset; cursor: pointer; display: block; padding: 0.5rem 0.75rem;
           border-radius: 6px; }
  button:focus-visible { outline: 2px solid #18345d; }
  li.here button { background: #18345d; color: #fff; }
  small { display: block; opacity: 0.7; }
</style>
</head>
<body>
<h1>Abram's Journey</h1>
<ol id="stops"></ol>
<script>/* SHIM */</script>
<script>
  var manifest = JSON.parse(document.getElementById("sefaria-asset").textContent);
  var list = document.getElementById("stops");
  manifest.refs.forEach(function (stop) {
    var li = document.createElement("li");
    li.dataset.ref = stop.ref;
    var b = document.createElement("button");
    b.innerHTML = "<strong></strong><small></small>";
    b.querySelector("strong").textContent = stop.label;
    b.querySelector("small").textContent = stop.ref;
    b.addEventListener("click", function () {
      mark(stop.ref);
      SefariaLink.setPlace(stop.ref);   // the viewer's own click
    });
    li.appendChild(b);
    list.appendChild(li);
  });

  function span(ref) {
    var m = /(\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$/.exec(ref || "");
    if (!m) return null;
    var c = +m[1], start = c * 1000 + +m[2];
    return [start, m[4] ? (m[3] ? +m[3] : c) * 1000 + +m[4] : start];
  }
  function covers(sceneRef, ref) {
    var a = span(sceneRef), b = span(ref);
    return !!(a && b && b[0] <= a[1] && b[1] >= a[0]);
  }
  function mark(ref) {
    Array.prototype.forEach.call(list.children, function (li) {
      li.classList.toggle("here", covers(li.dataset.ref, ref));
    });
  }

  SefariaLink.onPlace(function (place) { mark(place.ref); });
</script>
</body>
</html>
```

## 3. A hop to another text: "In the beginning of the reign" (Genesis 1:1 and Jeremiah)

The word בראשית opens the Torah, and Jeremiah uses it for the start of a king's reign. Each card
uses `open` to take the linked text panel to that verse, or to Rashi's first comment.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bereshit Elsewhere</title>
<script type="application/json" id="sefaria-asset">
{
  "v": 1,
  "title": "Bereshit Elsewhere",
  "description": "Where else the Bible says בראשית: Genesis 1:1 beside four verses in Jeremiah.",
  "lang": "en",
  "refs": [
    { "ref": "Genesis 1:1", "label": "In the beginning" },
    { "ref": "Jeremiah 26:1", "label": "The beginning of Jehoiakim's reign" },
    { "ref": "Jeremiah 27:1", "label": "The beginning of Jehoiakim's reign, again" },
    { "ref": "Jeremiah 28:1", "label": "The beginning of Zedekiah's reign" },
    { "ref": "Jeremiah 49:34", "label": "A prophecy to Elam" }
  ]
}
</script>
<style>
  body { margin: 0; padding: 1.5rem; font: 16px/1.5 system-ui, sans-serif; }
  .word { font-size: 3rem; text-align: center; direction: rtl; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; margin: 0 0 0.75rem; }
  .card.here { border-color: #18345d; box-shadow: 0 0 0 2px #18345d33; }
  button { font: inherit; margin-right: 0.5rem; }
</style>
</head>
<body>
<div class="word" lang="he">בְּרֵאשִׁית</div>
<div id="cards"></div>
<script>/* SHIM */</script>
<script>
  var manifest = JSON.parse(document.getElementById("sefaria-asset").textContent);
  var cards = document.getElementById("cards");
  manifest.refs.forEach(function (entry) {
    var card = document.createElement("div");
    card.className = "card";
    card.dataset.ref = entry.ref;
    card.innerHTML = "<strong></strong> <span></span><div></div>";
    card.querySelector("strong").textContent = entry.label;
    card.querySelector("span").textContent = "(" + entry.ref + ")";
    var go = document.createElement("button");
    go.textContent = "Read it";
    go.addEventListener("click", function () { SefariaLink.open(entry.ref); });
    card.querySelector("div").appendChild(go);
    if (entry.ref === "Genesis 1:1") {
      var rashi = document.createElement("button");
      rashi.textContent = "Rashi's first comment";
      rashi.addEventListener("click", function () { SefariaLink.open("Rashi on Genesis 1:1:1"); });
      card.querySelector("div").appendChild(rashi);
    }
    cards.appendChild(card);
  });

  // Highlight the card for wherever the text panel is now; exact refs, so no range helper.
  SefariaLink.onPlace(function (place) {
    Array.prototype.forEach.call(cards.children, function (card) {
      card.classList.toggle("here", card.dataset.ref === place.ref);
    });
  });
</script>
</body>
</html>
```

Notes:

- `open` changes which text the panel shows; `setPlace` only moves within the linked text. Use
  `open` for a different book or a commentary, `setPlace` for the text the page is about.
- Rashi on Genesis 1:1:1 is not in the manifest: it is somewhere the page sends the reader, not
  a passage the page covers.
- `place.segments` lists the segment refs of the place, for pages that key scenes by verse.
