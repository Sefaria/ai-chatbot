# Sefaria Skills Store and Shared Findings

**Date:** 2026-11-25
**Status:** Steps 0–2 built on `claude/jolly-heisenberg-sd54an`, plus the findings browse pages; sidebar on Sefaria-Project `reader-next` (step 3) in progress. See "Build status".
**Builds on:** `docs/plans/2026-10-18-mcp-consolidation.md` (the MCP at `chat.sefaria.org/mcp`, `/mcp/user`, `/mcp/admin`)

## Summary

Ship **skills** from the Sefaria MCP: packaged methods of learning (for example, one
rabbi's systematic way of comparing texts) that any skills-capable client can load. When
a learner finishes a skill run, the AI offers to **save the finding** to a shared store.
A saved finding is a source sheet (the editable artifact) plus an indexed row in our
Postgres (the queryable record: which skill, which method, which refs). A public API
answers "what findings touch this ref?", so the Sefaria reader sidebar can show a new
kind of connection: *this passage appears in a finding made with Rabbi X's method*.

Three pieces, each useful without the next:

1. **Skill serving** over the MCP Skills Extension.
2. **Findings data layer**: two tables, two tools, one read API.
3. **Skill store**: a repository and review pipeline so others can submit skills.

## What was researched

| Source | State |
|---|---|
| MCP Skills Extension, SEP-2640 | Read in full from `modelcontextprotocol/modelcontextprotocol` (`seps/2640-skills-extension.md`) and `modelcontextprotocol/ext-skills` at their 2026-11-25 heads. |
| This repo | `server/mcp_server/`, `server/mcp_auth/`, `chat/V2/agent/tool_schemas.py`, the consolidation plan. |
| Sefaria-Project | Shallow clone of `master` at `0d35bcd` (2026-11-25): `sefaria/sheets.py`, `sourcesheets/views.py`, `reader/views.py`, `sefaria/client/wrapper.py`, `sefaria/system/middleware.py`. Read as the "Ari project" in the request; say so if a different repo was meant. |
| FastMCP | Source and docs read from `PrefectHQ/fastmcp` at its 2026-11-25 head (`docs/servers/skills.mdx`). Not run: `fastmcp` was not installed in the planning container, so step 0 still loads a skill end to end. |
| Example skill | A real skill written by a rabbi was shown as an illustration of the kind of skill meant: an interpretive reading of a halacha or a dispute between poskim. It is **not** part of this project. The plan was revised against its shape (see "What the example skill changed"); development and tests use dummy skills, authors and methods only. |

## Part 1: What the spec gives us

Skills over MCP is an **official extension**, not part of the core protocol:
`io.modelcontextprotocol/skills`, SEP-2640, status Final, merged 2026-09-13, written
against protocol revision 2026-07-28.

- A skill is an [Agent Skills](https://agentskills.io/specification) directory: a
  `SKILL.md` with YAML frontmatter (`name`, `description` required) plus optional
  supporting files. The extension only defines transport.
- Each file is an MCP **resource** at `skill://<skill-path>/<file>`. The last path
  segment must equal the frontmatter `name`; earlier segments are ours to organise
  (`skill://sefaria/<method>/<skill>/SKILL.md`).
- A server declaring the extension must implement `skills/list` (paginated; each entry
  has `uri`, verbatim `frontmatter`, and `resources` as `{uri, digest, size}` triples)
  and `skills/get` (one entry by URI). `resources/directory/read` is optional. It must
  also declare the `resources` capability. Files are read with plain `resources/read`.
- Limits every host must accept: 512 files and 16 MiB per skill.
- Capability: `capabilities.extensions["io.modelcontextprotocol/skills"] = {}`.

Rules in the spec that shape our design:

| Spec rule | Consequence for us |
|---|---|
| Skill content is untrusted input; hosts ignore `allowed-tools` from MCP skills and gate any code execution behind per-skill approval. | Our skills are **instructions and reference files only, no scripts**. That also makes community submissions reviewable. |
| A skill cannot do anything itself; it only instructs the model. | "Save this finding" must be a **tool** (`save_finding`). The skill tells the model when to offer it. |
| Approval is bound to the exact digest set; any changed byte revokes it and re-prompts the user. | Skills are versioned and released deliberately, not edited live. Static files baked into the image give stable digests. |
| `"resources": "dynamic"` is allowed but hosts may refuse such skills. | No database-generated skills. The catalog is files. |
| Names collide across servers; hosts namespace by server. | Users see ours as `/<server>:<skill>` (Claude Code) or under the connector's name. Short, plain skill names. |
| Hosts must not fetch files until a skill is used. | Listing can be large without cost; no pressure to keep the catalog small. |

**Who can use it today** (from the ext-skills implementations table): Claude connectors
(public beta, on by default on paid plans), Claude Code since v2.1.331 (per-version
approval), VS Code Copilot since 1.147, MCP Inspector, and others. ChatGPT and Codex are
listed as planned. **FastMCP 4.2.0** ships a `SkillCatalogProvider` (experimental; the 4.1
skills API is deprecated). We pin `fastmcp>=4.0.3,<5`, so this is a floor bump, not a
migration.

Clients without the extension still need a path. Other servers (Capgo, FastMCP Skill
Drop) expose **mirror tools**; we do the same with `list_skills` and `load_skill`, which
also gives the Library Assistant on sefaria.org access to the same catalog.

## Part 2: Where we are

Already in place and reused as is:

- `/mcp` (public, stateless Streamable HTTP, 19 tools) and `/mcp/user` (OAuth, adds
  `search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`,
  `update_source_sheet`). Tools are selected by a `surfaces` marker on each schema.
- `/mcp/user` knows the Sefaria user id from the access token and mints a 5-minute
  `mcp` token per call. Sefaria accepts that token only on the sheet routes.
- Django on the same host with Postgres (`chat`, `mcp_auth` apps), Braintrust evals in
  `evals/`.

Missing:

| Gap | Part |
|---|---|
| No resources, no skills extension, no skill files | 3 |
| No store for skill output; nothing ties a sheet to a skill or a method | 4 |
| No read API by ref | 4 |
| No sidebar surface in Sefaria-Project | 5 |
| No way to submit, validate or review a skill | 6 |

## Part 3: Skill serving

**Catalog.** A directory of skills in git, loaded at process start:

```
server/skills_catalog/
  methods.yaml                       # the methods (see below)
  skills/<skill-name>/SKILL.md
  skills/<skill-name>/references/*.md
```

**Method** is the unit the request calls "this rabbi's systematic way of thinking". A
method has a slug, English and Hebrew titles, an attribution and a description, and one
or more skills belong to it. It lives in `methods.yaml`, not in the database: it changes
by pull request, like the skills.

**Skill format.** Standard Agent Skills frontmatter, with our fields under `metadata`
(the spec passes `metadata` through untouched):

```yaml
---
name: example-halachic-reading            # dummy data
description: Read a halacha or dispute between poskim through Rabbi Ploni's approach, ...
license: CC-BY-4.0
metadata:
  org.sefaria/method: example-method
  org.sefaria/version: 1.0.0
  org.sefaria/author: Ploni Almoni
---
```

**The body belongs to the author.** The example skill is continuous prose under its own
headings and tells the model not to impose a fixed template on its answer; a store that
forced a section layout on authors would fight skills like it. The store asks for one
thing only: the skill ends with the standard **Sefaria findings** section, copied
verbatim from the template and checked by the validator (Part 6). It says:

- Before starting, call `search_findings` for the anchor passage, and build on what is
  there instead of repeating it.
- After delivering the reading, ask the user: *"Would you like to save this to Sefaria's
  shared findings?"* Call `save_finding` only on a yes, marking which sources are the
  anchor, which are the positions in a dispute, and which are support. If the tool is
  absent (public `/mcp`), say that saving needs the signed-in connector.

### What the example skill changed

| In the skill | Change to the plan |
|---|---|
| Free prose, own headings, "do not impose a fixed template" | No required section layout; one standard closing section instead (above). |
| The output is an interpretive reading anchored in one ruling, sometimes two positions (Shulchan Arukh and Rema), with biblical and Hasidic support | Refs carry a **role**: `anchor`, `position`, `support`. The sidebar shows a finding on its anchor and position passages only. A verse quoted in support should not light up as a finding on that verse. |
| Retrieval is written against a different text service | A catalog skill names Sefaria's tools (`get_text`, `get_links_between_texts`, `semantic_search`, `validate_response_links`). Skills written elsewhere need porting, which the author approves. |
| Insists on separating what a source says from the further reading, and on not attributing teachings to the rabbi beyond what is supplied | The same line holds in the product: a finding is labelled *AI-assisted reading in the approach of <name>*, never as that person's statement. The label text is part of the method entry. |
| One skill is the whole method for now | A method with a single skill is the normal starting case. |

**Serving.**

- `/mcp` and `/mcp/user` both serve the catalog with FastMCP 4.2's
  `SkillsDirectoryProvider(root)` (`fastmcp.skills.provider`), which serves every
  subfolder holding a `SKILL.md`, registers `skills/list` and `skills/get`, and refuses
  to start if any skill is invalid. `/mcp/admin` serves none. The 4.1 skills API is
  removed in 4.3, so we build on 4.2's only.
- For MCP clients without the extension, FastMCP's `ResourcesAsTools` transform adds
  `list_resources` and `read_resource` tools over the same files; no mirror tools of our
  own. The Library Assistant does not go through FastMCP, so it gets one small
  `load_skill` tool on the `agent` surface reading the same directory.
- `skills/list` is a plain request, so the stateless transport and multi-replica
  deployment are unchanged. Digests are computed once at start from the files in the
  image; all replicas of a release agree.
- A test pins the served skill list, like `test_public_surface_is_exactly_the_reviewed_set`
  does for tools.

## Part 4: Findings data layer

### Recommendation on sheets versus refs

**Use both, with different jobs.** The source sheet is the artifact a person reads and
edits. A Postgres row is the record we query. The sidebar never queries sheets.

Sheets alone do not work, for reasons found in Sefaria-Project:

- **MCP-created sheets are invisible to the sidebar.** `_limit_mcp_sheet_save` forces
  `status: "unlisted"` and drops `displayedCollection`; `get_sheets_for_ref` returns only
  `status: "public"` sheets. Using sheets as the index means loosening a restriction that
  was added on purpose (consolidation plan 2.6) and asking every user to publish.
- **A sheet has nowhere to say which skill or method made it.** Tags became `topics`:
  free text, editable by the owner, and saving them creates topic links
  (`update_sheet_topics`). Using them as our key would pollute topics and let anyone
  attach a sheet to a method by typing its name. `generatedBy` exists but nothing queries it.
- **No control point.** Any public sheet on a passage would count, whatever made it.
  We need to admit only output of authorized skills, and to be able to take one down.

Refs alone lose the things sheets are good at: the user can open, correct and extend the
result, and the tools to create and update sheets already exist and are tested.

The sheet also solves a problem for the index. Our service has no Sefaria text library,
so it cannot expand `Genesis 1:1-5` into segments. Sefaria does that on every sheet save
(`includedRefs`, `expandedRefs` in `save_sheet`) and returns the saved sheet. We store
what it returns, so our refs are normalised by the same code the sidebar uses.

Sefaria has one native mechanism close to what is wanted: a Collection with a `toc`
field makes its sheets appear in the sidebar as a connection category
(`get_collections_in_library`, `format_sheet_as_link`). It is a possible later
integration (one collection per method), but it needs public sheets and collection
membership, so it inherits the three problems above. Not for the first version.

### Tables

A new Django app `findings` in the existing Postgres. Same database, own tables: it
needs the same users, deploys and backups as `mcp_auth`, and a second database buys
nothing at this size.

**`Finding`**

| Field | Notes |
|---|---|
| `id` | UUID |
| `skill`, `skill_version`, `method` | slugs from the catalog at save time |
| `sefaria_user_id` | from the access token; no name or email stored |
| `title`, `conclusion` | the finding in a sentence and in a paragraph |
| `sheet_id`, `sheet_modified` | the Sefaria sheet and its `dateModified` when last synced |
| `hidden` | false by default; staff set it to take a finding down |
| `created`, `updated` | |

**`FindingRef`**: one row per segment the finding touches.

| Field | Notes |
|---|---|
| `finding` | FK |
| `segment_ref` | from the sheet's `expandedRefs`, e.g. `Berakhot 2a:3`. Indexed. |
| `section_ref` | the segment's section, e.g. `Berakhot 2a`. Indexed. The reader loads by section. |
| `source_ref` | the ref as the sheet states it (`includedRefs`), for display |
| `role` | `anchor` (the ruling studied), `position` (one side of a dispute), `support`. Given per source in `save_finding` and matched to the sheet's sources in order. |

### Tools

| Tool | Surface | Does |
|---|---|---|
| `save_finding` | `mcp_user`, `agent` | Input: `skill`, `title`, `conclusion`, `sources` (ref, optional role and note), optional `sheet_id`. Creates the sheet through the existing `create_source_sheet` path unless one is given, then writes `Finding` and its refs from Sefaria's response. Refuses a skill that is not authorized. Returns the sheet URL and says the finding is now public. |
| `search_findings` | `mcp`, `agent` | Input: `ref` and/or `method`. Returns public findings. This is the read side of the "shared brain": skills call it first. |

`/mcp/user` runs without Django or a database today (it checks tokens statelessly). These
tools are the first that need Postgres. Two ways: give the MCP process Django settings
and the DB secret, or have it call a small internal endpoint on the Django app. **The
internal endpoint is recommended**: the MCP pods stay stateless and without database
credentials, which the consolidation plan chose deliberately.

### Read API (the sidebar's call)

On the Django app, public, cacheable, CORS open to the Sefaria origins:

```
GET https://chat.sefaria.org/api/findings/ref/<ref>
```

`<ref>` is a section (`Shulchan Arukh, Orach Chayim 1`) or a segment, in Sefaria's
normal form. Matches `section_ref` or `segment_ref`. Only findings that are not hidden, and only
on refs whose role is `anchor` or `position`; `?roles=all` includes support.

```json
{
  "ref": "Berakhot 2a",
  "findings": [
    {
      "id": "…",
      "method": {"slug": "…", "title": {"en": "…", "he": "…"}},
      "skill": "example-halachic-reading",
      "title": "…",
      "conclusion": "…",
      "label": "AI-assisted reading in the approach of Rabbi Ploni",
      "anchorRefs": ["Shulchan Arukh, Orach Chayim 1:1"],
      "refs": ["Shulchan Arukh, Orach Chayim 1:1", "Psalms 16:8"],
      "sheetUrl": "https://www.sefaria.org/sheets/123456"
    }
  ]
}
```

The shape follows Sefaria's slim sheets-for-ref response: one request per section, and
the client uses `anchorRefs` to count and filter per selected segment. The pattern
follows `/api/related/<ref>/websites`, a separate lazy call rather than a new key in
`/api/related`, whose response is Varnish-cached and must not wait on another service.

Also `GET /api/findings/<id>` and `GET /api/methods`.

A ranged query (`Berakhot 2a:3-2b:5`) cannot be expanded on our side. The reader does
not need it; if another client does, Sefaria can proxy the call and expand first.

### Review and sync

- **Trust is decided at the skill, not the finding** (decided 2026-11-25). A finding
  saved with an authorized skill is public at once; there is no review queue. A skill is
  authorized when its author is an authorized author, or when the skill itself has been
  authorized (Part 6). Staff can hide a single finding afterwards, in Django admin.
- **Consequence to accept knowingly:** anyone signed in can run an authorized skill and
  save, so what appears beside the text is only as good as that run. The safeguards are
  the label on every finding, the takedown switch, and withdrawing a skill's
  authorization, which hides all of its findings.
- **Sheet edits.** A nightly job re-reads sheets behind findings. If `dateModified`
  moved, it replaces the finding's refs. A deleted sheet hides the finding.
- **Privacy.** Sheets stay unlisted (reachable by link). The API returns no user data.
  The save prompt tells the user the finding and its sheet become public on saving.

## Part 5: Sidebar (Sefaria-Project, separate plan)

Out of scope here beyond the contract above. Sketch: the connections panel fetches
`/api/findings/ref/<section>` alongside its other lazy calls, shows a "Methods" entry
with a count per selected segment, lists findings grouped by method, and opens the sheet.
The chatbot widget, already on those pages, can offer "run this method on this passage".

## Part 6: The skill store

**Source of truth is a git repository, submission is a pull request.**

- First-party skills start in this repo (`server/skills_catalog/`).
- When outside submissions open, the catalog moves to a public repo
  (`Sefaria/sefaria-skills`), and the chatbot image vendors a pinned commit. Outside
  contributors should not be opening pull requests against a repo whose `main` deploys
  to production through semantic-release.

**What a submitter does:** copy the template skill, write `SKILL.md` and references, add
or pick a method in `methods.yaml`, add three example prompts with expected refs, open a
pull request.

**What CI checks** (`python -m skills_catalog.validate`):

- Agent Skills validity: frontmatter, naming rules, `name` equals directory.
- Spec limits (512 files, 16 MiB) and ours (much smaller: markdown only, no scripts, no
  `allowed-tools`, no links outside Sefaria domains).
- The standard Sefaria findings section is present and unmodified, and `metadata` names
  an existing method.
- Only tools on the public or user surface are named.
- An eval run in Braintrust over the example prompts: every cited ref passes
  `validate_refs`, and the run ends by offering to save.

**Authors and authorization** (decided 2026-11-25). Every skill has an author, listed in
`authors.yaml` with their GitHub login and Sefaria user id. Authorization is granted two
ways, independently:

- **An authorized author.** Staff mark the author as authorized once. Any skill of
  theirs goes in when CI passes, with no further review, including later changes.
- **An authorized skill.** A skill from anyone else is reviewed and authorized on its
  own. That says nothing about the author's next skill.

A pull request may only touch the author's own skills to take the first path. What staff
look at when authorizing an author or a skill: the method is described faithfully; a
skill named for a person is submitted by that person or with their agreement; licence;
nothing in the text reads as instructions aimed at anything but the task (it is a
prompt-injection surface).

**Release.** Merging bumps `org.sefaria/version`. Because a changed digest re-prompts
every user of that skill, skill changes ship in batches, not per typo.

**Browse.** A page listing methods and skills with a count of findings each,
generated from the catalog and `GET /api/methods`. Later.

## Product

**Users**

| Who | Wants |
|---|---|
| Learner in Claude or another client | A guided, rigorous way to study, and a result worth keeping |
| Reader on sefaria.org | To see that a passage has been studied through a known method |
| Skill author (educator, scholar) | Their method used and credited |
| Sefaria content staff | Control over what appears beside the text |

**Journey.** A learner connects the Sefaria connector, picks a skill, works through a
comparison with the AI, and is asked whether to save. On yes they get a sheet in their
account, and the finding is public. A reader on the anchor
passage sees the finding under its method and opens the sheet.

**Not in the first version:** payments or ratings, skills with code, user-uploaded
skills without review, findings without a sheet, editing a finding outside its sheet.

**Measures:** skill loads per week; share of runs that end in a save; findings hidden by staff;
passages with at least one finding; sidebar opens of findings.

**Risks**

| Risk | Handling |
|---|---|
| Wrong or low-quality conclusions shown as Sefaria content | Only authorized skills can save; "AI-assisted" label on every finding; staff takedown; sheet is editable |
| A living rabbi's method misrepresented | A skill named for a person comes from that person as author, or with their agreement |
| Client support is beta and uneven | Mirror tools; the Library Assistant uses the same catalog |
| FastMCP's skills API is new (4.2) and changed once already | Step 0 spike; pin the minor version; fallback is custom `skills/list` and `skills/get` handlers, as pre-4.2 servers do |

## Build status

0. **Spike.** [x] FastMCP 4.2.6 serves skills over the stateless endpoint. Verified with
   FastMCP's client (`read_skill` checks every digest) and with the MCP Inspector CLI
   (`--protocol-era modern --method skills/list --verify --require-digests`: no
   conformance errors). [x] `GET /api/sheets/<id>` returns `includedRefs` and
   `expandedRefs` (checked live against sefaria.org). [ ] Load from claude.ai and Claude
   Code: needs a deployed or reachable endpoint (chat-dev).
1. **Serve skills.** [x] `server/skills_catalog/` (two dummy skills under two dummy
   methods, covering both authorization paths), served on `/mcp` and `/mcp/user`,
   validator in CI.
2. **Findings.** [x] `findings` app, `POST /api/findings`, `save_finding`,
   `search_findings`, read API, `hide_finding`, `sync_findings`, `seed_demo_findings`.
   [x] Browse pages at `/findings/` (added on request: methods, skills, passages, the
   cluster of refs behind each finding).
3. **Sidebar.** [ ] Sefaria-Project `reader-next` (see Part 5).
4. **Store.** [ ] Public repo, contribution guide, eval gate. The browse page is done.

Not done, for deployment (AppliedAI-Infrastructure and settings): a nightly CronJob for
`sync_findings`; `FINDINGS_API_URL` if the MCP pods should reach Django in-cluster
instead of through the public host; a CODEOWNERS rule so only staff change
`catalog.yaml`; a live check from claude.ai and Claude Code on chat-dev.

### Decisions made while building

| Decision | Why |
|---|---|
| Skills are flat (`skill://<name>/SKILL.md`); the method is metadata, not a path segment. | One less thing to keep in sync; hosts namespace by server anyway. |
| No custom mirror tools: FastMCP's `ResourcesAsTools` adds `list_resources` and `read_resource`. | `skills/list` needs the 2026-07-28 protocol; clients on the older one can still read skills. |
| Not on the agent surface: the Library Assistant gets neither the skills nor the findings tools. | Adding tools changes every production conversation and needs a Braintrust eval first. A later change. |
| The MCP process records findings with its own 5-minute write token (`aud: findings`, signed with `MCP_OAUTH_SIGNING_KEY`), not an internal unauthenticated endpoint and not the user's access token. | The MCP pods stay without database credentials; nothing is passed through. |
| Django reads the sheet from Sefaria itself and takes the segments from its `expandedRefs`; the request only says which cited source has which role. | A caller cannot attach a finding to passages its sheet does not hold, and refs are normalized by Sefaria's own code. |
| Each segment takes the strongest role of the sources covering it. A cited source that matches no segment keeps a row only while the sheet still includes it. | Overlapping citations stay correct, and a source removed from the sheet stops showing. |
| One finding per sheet; saving again updates it. | The sheet is the unit; makes saving idempotent. |
| Withdrawing authorization (author or skill) hides that skill's findings at once; staff hide single findings with `hide_finding`. | The chat app has no Django admin; a management command is the smallest takedown. |
| 20 new findings per user per 24 hours (`FINDINGS_DAILY_LIMIT`). | Findings are public at once, with no per-finding review. |
| `fastmcp>=4.2.6,<4.3`. | Its skills API replaced 4.1's and is marked experimental. |
| `save_finding` asks the API to check the save (`dry_run`) before it creates a sheet. | A refused save (daily limit, another user's sheet) leaves no orphan sheet; a failure after the sheet exists names it and says to retry with `sheet_id`. |
| Sync hides a finding only when Sefaria answers "Couldn't find sheet" (HTTP 200) or 404. | Any other error, such as a maintenance page, must not hide findings overnight. |
| Cited sources the sheet does not hold are dropped from the finding; a save whose anchors are all missing is refused. | The public page shows only what the sheet shows. |
| Rows are capped at 2,000, anchors and positions first; public lists prefetch only anchor and position rows. | A sheet citing a whole book can't push its anchors out, or make a sidebar call load every support row. |
| A ref search on `/findings/` goes to the passage page only if that passage has findings. | "Rule 3" stays a word search. |
| Re-saving a finding brings it back if sync hid it (sheet deleted or changed owner), never if staff hid it. | |

## Open questions

1. Decided: build and test with dummy skills, authors and methods. Which real skill and
   author launch first is a later decision.
2. Decided: no per-finding review; authorization is per author or per skill.
3. Decided: the sidebar calls `chat.sefaria.org` directly.
4. Decided (as proposed): `search_findings` is on the anonymous `/mcp`.
5. Public catalog repo from day one, or only when outside submissions open?
