/* SefariaLink v1: links this page to the Sefaria reader. Paste it, unchanged, in a <script>
   before your own code. Outside the reader it does nothing and the page works on its own. */
(function (w) {
  var port = null, handlers = [], quietUntil = 0, tries = 0, last = null;
  var parent = w.parent !== w ? w.parent : null;
  function deliver(place) {
    // Moves caused by the reader must not be sent back: quiet while handling, and for
    // half a second after, so a smooth scroll the handler starts is not echoed.
    quietUntil = Date.now() + 500;
    for (var i = 0; i < handlers.length; i++) {
      try { handlers[i](place); } catch (e) { console.error(e); }
    }
    quietUntil = Date.now() + 500;
  }
  function onPort(e) {
    var m = e.data || {};
    if (m.type === "init") {
      w.SefariaLink.info = m;
      if (m.place && m.place.ref) deliver(m.place);
    } else if (m.type === "place" && m.ref) {
      last = m.ref;
      deliver({ ref: m.ref, segments: m.segments || [], cause: m.cause });
    }
  }
  function send(type, ref) {
    if (port && typeof ref === "string" && ref) port.postMessage({ type: type, ref: ref });
  }
  w.SefariaLink = {
    info: null,
    connected: function () { return !!port; },
    onPlace: function (fn) { if (typeof fn === "function") handlers.push(fn); },
    setPlace: function (ref) {
      if (Date.now() < quietUntil || ref === last) return;
      last = ref;
      send("place", ref);
    },
    open: function (ref) { send("open", ref); }
  };
  if (!parent) return;
  w.addEventListener("message", function (e) {
    var m = e.data;
    if (port || e.source !== parent || !m || m.type !== "sefaria:connect" || !e.ports || !e.ports[0]) return;
    port = e.ports[0];
    port.onmessage = onPort;
  });
  (function ready() {
    if (port || tries++ > 3) return;
    parent.postMessage({ type: "sefaria:ready", v: 1 }, "*");
    setTimeout(ready, 400 * tries);
  })();
})(window);
