"""The sefaria-linked-asset shim (references/shim.js), run in Node against a fake reader.

Skipped where Node is not installed. The harness gives the shim a fake ``window`` whose parent
records what the page posts, then plays the reader's side of the handshake with a real
``MessageChannel``.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REFERENCES = Path(__file__).parent / "skills" / "sefaria-linked-asset" / "references"
SHIM = REFERENCES / "shim.js"

HARNESS = r"""
const shim = require("fs").readFileSync(process.argv[2], "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function makeWindow(framed) {
  const listeners = [];
  const parent = { posted: [], postMessage(m, origin) { this.posted.push([m, origin]); } };
  const w = { addEventListener: (t, fn) => t === "message" && listeners.push(fn), listeners };
  w.parent = framed ? parent : w;
  new Function("window", shim)(w);
  return { w, parent, fire: (e) => listeners.forEach((fn) => fn(e)) };
}
(async () => {
  const out = {};

  // Standalone: no parent, every call is a no-op.
  const solo = makeWindow(false);
  solo.w.SefariaLink.setPlace("Genesis 1:1");
  solo.w.SefariaLink.open("Genesis 2");
  out.standalone = { connected: solo.w.SefariaLink.connected(), listeners: solo.w.listeners.length };

  // Framed but the reader never answers: ready is posted, calls do nothing.
  const { w, parent, fire } = makeWindow(true);
  out.ready = parent.posted[0];
  w.SefariaLink.setPlace("Genesis 1:1");

  const places = [];
  w.SefariaLink.onPlace((p) => {
    places.push(p);
    w.SefariaLink.setPlace("Genesis 1:9");  // an echo: must be suppressed
  });

  // A connect from anyone but the parent is ignored.
  const stranger = new MessageChannel();
  fire({ source: {}, data: { type: "sefaria:connect", v: 1 }, ports: [stranger.port2] });
  out.connectedAfterStranger = w.SefariaLink.connected();

  const channel = new MessageChannel();
  const fromAsset = [];
  channel.port1.onmessage = (e) => fromAsset.push(e.data);
  fire({ source: parent, data: { type: "sefaria:connect", v: 1 }, ports: [channel.port2] });
  out.connected = w.SefariaLink.connected();

  channel.port1.postMessage({ type: "init", v: 1, theme: "light", interfaceLang: "en", dir: "ltr",
    context: "panel", place: { ref: "Genesis 1:3", segments: ["Genesis 1:3"], cause: "link" } });
  await sleep(20);
  channel.port1.postMessage({ type: "place", ref: "Genesis 1:6", segments: ["Genesis 1:6"], cause: "user" });
  await sleep(20);
  out.info = w.SefariaLink.info && w.SefariaLink.info.context;
  out.places = places;
  out.echoes = fromAsset.slice();

  await sleep(600);  // past the quiet window
  w.SefariaLink.setPlace("Genesis 1:14");
  w.SefariaLink.setPlace("Genesis 1:14");  // same place twice: sent once
  w.SefariaLink.open("Rashi on Genesis 1:14:1");
  w.SefariaLink.setPlace(42);  // not a ref
  await sleep(20);
  out.sent = fromAsset;
  out.readyCount = parent.posted.length;
  console.log(JSON.stringify(out));
  channel.port1.close();
  stranger.port1.close();
})();
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    harness = tmp_path_factory.mktemp("shim") / "harness.js"
    harness.write_text(HARNESS)
    done = subprocess.run(
        [node, str(harness), str(SHIM)], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(done.stdout)


def test_standalone_page_is_left_alone(run):
    assert run["standalone"] == {"connected": False, "listeners": 0}


def test_handshake(run):
    assert run["ready"] == [{"type": "sefaria:ready", "v": 1}, "*"]
    assert run["connectedAfterStranger"] is False
    assert run["connected"] is True
    assert run["info"] == "panel"
    assert run["readyCount"] == 1  # answered at once, so ready was not posted again


def test_incoming_places_reach_the_page_and_are_not_echoed(run):
    assert [p["ref"] for p in run["places"]] == ["Genesis 1:3", "Genesis 1:6"]
    assert run["places"][1] == {"ref": "Genesis 1:6", "segments": ["Genesis 1:6"], "cause": "user"}
    assert run["echoes"] == []


def test_set_place_and_open(run):
    assert run["sent"] == [
        {"type": "place", "ref": "Genesis 1:14"},
        {"type": "open", "ref": "Rashi on Genesis 1:14:1"},
    ]


def test_shim_stays_small():
    assert SHIM.stat().st_size < 2500


def _examples() -> list[str]:
    text = (REFERENCES / "examples.md").read_text()
    return re.findall(r"```html\n(.*?)```", text, re.S)


def test_examples_have_valid_manifests_and_scripts(tmp_path):
    examples = _examples()
    assert len(examples) == 3
    node = shutil.which("node")
    for i, page in enumerate(examples):
        manifest = re.search(
            r'<script type="application/json" id="sefaria-asset">(.*?)</script>', page, re.S
        )
        data = json.loads(manifest.group(1))
        assert data["v"] == 1 and data["title"] and data["refs"]
        assert "/* SHIM */" in page
        assert "http://" not in page and "https://" not in page
        if node:
            script = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
            path = tmp_path / f"example{i}.js"
            path.write_text(SHIM.read_text() + "\n" + script)
            subprocess.run([node, "--check", str(path)], check=True, capture_output=True)
