"""Claude assets: interactive HTML pages made in Claude, saved to Sefaria Social as the user.

An asset is one self-contained HTML file that declares the Sefaria refs it covers in a manifest
(``<script type="application/json" id="sefaria-asset">``) and talks to the reader over the
linked-panel protocol (skill ``sefaria-linked-asset``). Social stores it, checks its refs with
Sefaria, and serves it sandboxed. This package is the MCP side: the ``save_asset`` tool and the
small client that reaches Social. No Django imports; it runs in the MCP process.

Plan: Sefaria-Project ``LINKED_PANELS_AND_ASSETS_PLAN.md`` sections 5 to 7. Social's contract:
sefaria-social specs 060 (save) and 064 (MCP tokens).
"""
