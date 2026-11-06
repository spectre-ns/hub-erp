# Visual lens: layout, hierarchy, density, responsiveness

Reviewed from `hubproto/shell.py` (the `_CSS` block and the page renderers) and
from pages served on 2026-11-06 as `bob` and `dave`: `/`, `/inbox`, `/runs`,
`/run/82007f4728`, `/record/rec:purchase-request:PR-00004`,
`/document/rec:document:DOC-0130`, `/boards?type=purchase-request`, `/files`,
`/files?folder=Finance/Payables`, `/releases`, `/admin`. No browser; widths and
heights below are computed from the CSS (14.5px system-ui body, 230px rail,
26px main padding, 16px card padding) and the markup counts. Contrast ratios
are WCAG 2.x relative-luminance ratios computed from the `:root` tokens.

Reference widths used: 390px phone (main content 338px, inside a card 306px);
1280px laptop (main content 1280 - 230 - 52 = 998px).

### F-visual-1: The inbox is a 21,000px stack of open decision forms
Severity: blocker
Heuristic: Nielsen "aesthetic and minimalist design"; "recognition rather than recall"; Fitts/scan cost; WCAG 2.4.1 bypass blocks (no way to skip 44 repeated blocks)
Steps:
1. GET `http://localhost:8080/inbox?as=bob`.
2. Count `<div class="card">` under `<h2>Waiting for your decision`.
3. For each card count `<dt>`, `<label class="f">`, `<textarea>`.
Expected: A person with forty items sees a list (one row per item: title, record, amount, risk, due, owner) and opens one item at a time; the forty-item page fits in two or three screens.
Actual: 44 cards, every one with its full `/decide` form expanded: 4 to 11 `<dt>` rows, 1 to 6 labelled inputs, up to 3 textareas (`rows="2" cols="50"`), a required "meaning" input and two buttons. Average visible text per card is 767 characters; the estimated height is ~480px per card (range 290 to 556px), ~21,000px for the section, which is about 30 laptop screens or 25 phone screens before `<h2>Notifications` appears. dave's 25 cards estimate at ~11,700px. There is no collapse, no per-card anchor, no "next item", no filter by process or due date, and the "You may also set: needed_by, reason, supplier, unit_cost" inputs are repeated in all 15 purchase cards even though nobody sets them on 15 items.
Impact: Every approver, every visit. bob cannot see what is in his queue without scrolling for a minute; items 20 to 44 are effectively invisible, so the ones that get decided are the ones at the top.
Suggested fix (optional): Render the queue as a table (`title | record | summary | amount/risk | due | owner | Open`) or as `.card` headers only, and open one `_decision_card` at a time (`/inbox?open=<run id>` or a `<details>` per card with the header as `<summary>`, which `details summary` is already styled for). Inside the card, put `needs`/optional inputs in a `<details><summary>Also set fields</summary>` block so the default card is header, brief, meaning, Approve/Reject.

### F-visual-2: A decision card has no hierarchy; the amount and the risk are rows 6 and 9 of a 13.5px list
Severity: blocker
Heuristic: Visual hierarchy / three-second scan; Nielsen "visibility of system status"; Gestalt proximity
Steps:
1. GET `/inbox?as=bob`, first card (PR-00004, 20,000 USD, risk high).
2. Read the card top to bottom in source order.
Expected: In under three seconds an approver sees: what (Approve the purchase), for whom (PR-00004, 200000 x 10k resistor), the deciding numbers (20,000 USD, risk high, supplier approved), when (due in 3 days), and where to click.
Actual: The first line is one run-on sentence of equal-size text with mixed emphasis: `<b>Approve the purchase</b> &middot; purchase-request &middot; <a>PR-00004</a> <span class="muted">[INV] 200000 x 10k resistor</span> &middot; run <a>82007f4728</a> started by alice &middot; due <b>2026-11-09</b>`. The title is a `<b>`, not a heading, and the same weight as the due date and later `<b>Guard findings:</b>`. The deciding facts are inside `<dl class="kv">` at 13.5px: `amount` is the 6th row and `risk` the 9th, after `title`, `item`, `mpn`, `quantity`, `unit cost`. The run id and the routing sentence ("Routed to you by purchasing-policy line 45: ...") sit above the brief. The Approve/Reject buttons come after four optional inputs and a textarea.
Impact: Every decision. The approver reads nine rows and a policy citation to find two numbers; across 44 cards that is the whole afternoon, and skimming the wrong row (unit cost 0.1 vs amount 20000.0) is an approval error waiting to happen.
Suggested fix (optional): Make the card an `<article>` with `<h3 class="t">Approve the purchase <span class="chip">PR-00004</span></h3>`, a headline line in 16px with the summary, then a "facts" strip (`.facts{display:flex;gap:18px;flex-wrap:wrap}` with `b{font-size:18px}`) carrying amount, risk chip, supplier-approved chip, due; move run id and the routing sentence under the brief in `.why`; keep the kv as secondary detail. Put the verdict row in a `.actions{display:flex;gap:8px;align-items:center;border-top:1px solid var(--line);padding-top:8px;margin-top:8px}`.

### F-visual-3: Risk level, supplier-approved and gate status chips are uncoloured and identical to the type chip
Severity: blocker
Heuristic: WCAG 1.4.1 use of colour (the inverse: the one place colour is relied on, it is absent); Nielsen "match between system and the real world"; consistency
Steps:
1. GET `/inbox?as=bob`; search for `risk</dt>`.
2. GET `/run/82007f4728?as=bob`; look at the Gate column.
3. Read `chip()` in `hubproto/shell.py`.
Expected: `risk: high` is red, `medium` amber, `low` green; `supplier approved: no` is red; a pending gate is amber. The one chip style that is grey means "neutral label".
Actual: `chip()` only colours a fixed list of workflow states. `risk` renders as `<span class="chip ">high</span>`, supplier approval as `<span class="chip ">no</span>`, gate as `<span class="chip ">gate-pending</span>`: all `var(--chip)` grey, the same as `<span class="chip">purchase-request</span>`, `<span class="chip">person</span>`, `<span class="chip">retention: financial-reporting</span>`. On bob's inbox there are 59 of these uncoloured `chip ` spans against 40 `ok` and 7 `warn`. The only coloured chip on a purchase card is the green `ordered` in "approval leads to ordered", so the greenest thing on a high-risk, unapproved-supplier request is the outcome of approving it.
Impact: Every approver on every card; the signal the policy computed (risk, supplier approved) is visually silent while decoration is loud. The PR-00005 card (unapproved supplier, risk high) looks the same as PR-00004 (approved supplier).
Suggested fix (optional): In `chip()`, add `if s in ("high", "no", "outside tolerance", "error"): cls = "crit"`, `if s in ("medium", "gate-pending", "not run", "warning"): cls = "warn"`, `if s in ("low", "yes", "within tolerance"): cls = "ok"`; render the `risk` row with `chip(f"risk {f['risk']}")` so the chip reads on its own. Add `.chip.neutral{background:transparent;border:1px solid var(--line);font-weight:500}` and use it for type, role and label chips so they stop competing with status.

### F-visual-4: Informational cards are louder than the ones that need a decision
Severity: blocker
Heuristic: Nielsen "visibility of system status" and "consistency"; signal/noise; Gestalt similarity (same style = same kind)
Steps:
1. GET `/inbox?as=bob`.
2. Compare the class of the 44 decision cards with the 30 Notifications cards and the 30 Messages cards.
Expected: The things bob must act on are visually distinct from, and stronger than, the things he may read. The eye can tell "decide" from "FYI" without reading.
Actual: Decision cards are `<div class="card">` (white, grey 1px border): the same class as the 30 `Messages` cards (`<div class='card'><span class='chip'>dm/approval</span> ...`), which are notifications of the very same items. The 30 `Notifications` cards are all `<div class='card crit'>` with a red `escalation` chip, so the loudest block on the page is informational and repeats "Overdue: Approve the purchase on rec:purchase-request:PR-00038" 30 times. An overdue decision card would also be `.card crit`, identical to a failed run and to an escalation notice. Section headings carry no colour or icon. Messages use raw locators (`rec:audit:AUD-2026-002`) where decision cards use numbers, so the same item does not even look like the same item.
Impact: Everyone with an inbox, continuously. The approver's attention is pulled to the red informational block; the quiet white block is the work. Decide/informational is the one distinction an inbox must draw.
Suggested fix (optional): Give decision cards a left accent (`.card.decide{border-left:4px solid var(--accent)}`) and a heading icon, make notifications `.card` with a 3px left border in `var(--warn)` and no filled chip, and collapse Notifications and Messages into `<details>` with the count in the summary. Render message subjects with `_record_link()` instead of the locator.

### F-visual-5: On a 390px phone every page starts with ~900px of navigation and the forms overflow the card
Severity: blocker
Heuristic: Responsive layout; WCAG 1.4.10 reflow (no two-dimensional scroll at 320px CSS width); Nielsen "flexibility and efficiency"
Steps:
1. Read `_CSS`: `.layout{grid-template-columns:230px 1fr}` and `@media(max-width:900px){.layout{grid-template-columns:1fr}nav{border-right:0;border-bottom:...}}`.
2. GET `/inbox?as=bob`; count links in `<nav>`: 10 rail links, `<h3>Folders</h3>`, 23 `<li>`.
3. Find `cols="50"`, `size="40"`, `cols="60"` in the same page.
Expected: On a phone the rail collapses to a bar or a menu button; the first thing on screen is the page's `<h1>` and the first item; inputs fit the card.
Actual: Below 900px the grid becomes one column and the whole `<nav>` (10 block links at ~31px each, a heading, and the complete 23-entry folder tree at ~21px each: roughly 850 to 900px) is rendered above `<main>` on every page, so the approver scrolls past two phone screens to reach "Inbox". Nothing hides it. Inside a card the content width is 306px, but `textarea cols="50"` (about 390px at this font) appears 129 times, `input size="40"` (about 310px) 69 times, and the message textarea is `cols="60"`; `.card` has no `overflow` rule and the inputs have no `max-width`, so cards and the page scroll horizontally. The `.top` bar is a flex row of "Hub", the crumb and a form with a `<select>` plus "Go" and has no wrap rule; at 390px it clips or overflows.
Impact: Anyone approving from a phone (the brief's premise is a workspace people start work in); the primary task is slower by two screens per page and the signature field may be half off-screen.
Suggested fix (optional): In the media query add `nav{display:flex;flex-wrap:wrap;gap:4px;padding:8px 12px} nav h3,nav ul{display:none} nav a{padding:4px 8px}` (or wrap the folder tree in `<details>`), add `.top{flex-wrap:wrap;padding:8px 12px}`, and replace `size`/`cols` with `input:not([type=file]),select,textarea{width:100%;max-width:28em}` plus `label.f{display:block}` under 600px.

### F-visual-6: Reject looks like a cancel button and sits one space from Approve
Severity: major
Heuristic: Nielsen "error prevention"; affordance/semantic colour consistency; WCAG 2.5.8 target spacing (24px)
Steps:
1. GET `/inbox?as=bob`; at the end of any decision card read `<button name="verdict" value="approve">Approve</button> <button class="secondary" name="verdict" value="reject">Reject</button>`.
2. Compare with `<button class="secondary">Go</button>` (top bar), `<button class="secondary">Render snapshot</button>` and `Put on hold` (record page), and `<button class="danger">Cancel the run</button>` / `Withdraw`.
Expected: The two verdicts are distinct from each other and from utility buttons, spaced so a thumb cannot hit the wrong one, and the destructive one reads as such.
Actual: `Reject` is `.secondary` (grey `#6c7a86`), the same style as "Go", "Render snapshot" and "Put on hold", which are utilities; `.danger` (red) is reserved for withdrawing a run. So grey means both "harmless" and "reject this 20,000 USD request", and red is on the author's buttons, not the approver's. The buttons are inline with one text space between them, the `button` rule sets no `font-size`, so they render at the browser's 13.3px default, smaller than body text. White on `#6c7a86` is 4.41:1, under AA for text that size.
Impact: Every decision; a misclick is a signed rejection that returns the item to the author. No confirmation step exists on either button.
Suggested fix (optional): `button{font:inherit;font-weight:600;min-height:36px}`; add `button.reject{background:var(--panel);color:var(--crit);border:1px solid var(--crit)}` and use it for Reject; put both in `.actions{display:flex;gap:12px}` with Approve on the right; use `.danger` only for destructive verbs.

### F-visual-7: The record page has no place to decide, and its state is a hyphenated machine string in the title
Severity: major
Heuristic: Visual hierarchy / call to action; Nielsen "visibility of system status", "match between system and the real world"
Steps:
1. As bob, follow "open record" from a Notifications card, or GET `/record/rec:purchase-request:PR-00004?as=bob`.
2. Look for a decide form (`action="/decide"`): there is none.
Expected: The record page of an item waiting for me shows a prominent "Waiting for your decision" region with Approve/Reject, and the state reads as words.
Actual: The `<h1>` is `PR-00004 <span class="chip">purchase-request</span> <span class="chip warn">awaiting-approve-the-purchase</span>`; the only hints that bob may act are the muted 12.5px line `<b>Why:</b> may bob approve purchase-request in Purchasing/Requests? <span class="chip ok">allowed</span> ...` inside the summary card and `run 82007f4728 <span class="chip warn">waiting</span>`. There is no verdict control on the page (0 `/decide` forms); the resubmit form appears only for `kind == "resubmit"`. The first action card on the page is `Render snapshot` / `Put on hold`. Escalation notifications link here, so an escalated item leads the approver to a page where they cannot decide and must find the card in the 21,000px inbox.
Impact: Every approver who arrives from a notification, the runs table, a board or a link (that is, any route except scrolling the inbox).
Suggested fix (optional): In `page_record`, when `run["pending"]` is a decision and `user in assignees`, insert `self._decision_card(run, today)` directly under the summary card as `.card.decide`. Render state chips with `s.replace("awaiting-", "awaiting: ").replace("-", " ")` for display.

### F-visual-8: Tables have no overflow strategy; 7 to 9 column tables with hashes and JSON overflow the page
Severity: major
Heuristic: WCAG 1.4.10 reflow; responsive tables; readability
Steps:
1. GET `/files?folder=Finance/Payables&as=bob`: records table with 9 `<th>` (Number, Type, State, Title / summary, Owner, Assignees, Due, Updated, Run), 78 rows.
2. GET `/record/rec:purchase-request:PR-00004?as=bob`: Decisions table 8 columns incl. `<code>` signature and payload digest; Activity table with 300-char JSON `Detail` cells; Track table with `<pre style='margin:0'>` of up to 4,000 chars per row.
3. GET `/run/82007f4728?as=bob`: summary card contains `pinned to release <code>b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef</code>` (64 chars).
4. GET `/admin?as=bob`: Record types table, 8 columns, cells up to 244 characters.
Expected: Tables scroll inside their own container or drop secondary columns; long tokens wrap; at 998px (laptop) the summary column keeps room to read.
Actual: `table{width:100%}` with no wrapping element and no `overflow-x`; `th,td` have no `white-space` or `overflow-wrap` rule; `code` has no wrap rule. A 64-char hex string is ~480px at 12.5px monospace, wider than the 338px phone main and wider than a column at 998px, so it forces the table or card wider than `<main>` and the page scrolls sideways (`main{min-width:0}` does not constrain a table's intrinsic width). The 9-column records table at 998px leaves "Title / summary" with roughly 150px, so summaries like "1 x RC0603FR-0710KL for DSN-2026-0024" wrap to four lines while "Run" shows an 8-char id.
Impact: Everyone on Files, Runs, record pages and Admin; on a phone these pages scroll in two dimensions, on a laptop the one column a person reads is the narrowest.
Suggested fix (optional): In `table()` emit `<div class="tbl"><table>...</table></div>` with `.tbl{overflow-x:auto;-webkit-overflow-scrolling:touch}`; add `code,td{overflow-wrap:anywhere}`, `td.num{white-space:nowrap}` for ids/dates, and `th.sum{width:40%}` for the summary column; drop `Run` and `Updated` into a second `.why` line under the number below 900px.

### F-visual-9: Field widths come from `size`/`cols` attributes, not from the layout
Severity: major
Heuristic: Consistent spacing/alignment; responsive forms; WCAG 1.4.4/1.4.10
Steps:
1. Count attributes across the served pages: `cols="50"` 129, `size="24"` 75, `size="40"` 69, `size="18"` 3, `size="30"` 2, `cols="60"` 2, `rows="2"` 129.
2. Read `widget()` and `_decision_card()` in `shell.py`.
Expected: One CSS rule decides control width; labels line up in a grid; the same field has the same width on every card.
Actual: Every control carries a hard character count; `label.f{display:inline-block}` packs them left to right so a card's inputs wrap wherever the window edge falls ("needed_by" date, "reason" textarea, "supplier" text, "unit_cost" number in one line, then the next card wraps differently because its textarea is wider). The signature input is `size="40"` while the fields it sits beside are `size="24"`, and the "meaning" label is the same 12px muted style as the optional ones. Nothing changes between 1280px and 390px except where the line breaks.
Impact: Every form (start, decide, resubmit, upload); the card the approver signs in looks different every time, which slows the eye and hides the required field.
Suggested fix (optional): Remove `size=`/`cols=` from `widget()`; add `.fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px 14px}` around the inputs, `input,select,textarea{width:100%}`, `textarea{min-height:3.2em}`, and `label.f.req span{color:var(--fg);font-weight:600}` for the required signature field.

### F-visual-10: The dark theme keeps hard-coded light-theme colours in the bar, the buttons and the diagram
Severity: major
Heuristic: WCAG 1.4.3 contrast (minimum), 1.4.11 non-text contrast; consistency
Steps:
1. Read `_CSS`: `button{background:var(--accent);color:#fff}`, `button.secondary{background:#6c7a86}`, `.top{color:var(--accent-fg)}` with `--accent:#2f8fc4` in dark; `mermaid_for()` emits `classDef current fill:#f7ebcf,stroke:#9a6400,...,color:#1c2730`.
2. Compute contrast for the dark `:root`.
Expected: Every text/background pair in both themes meets 4.5:1; the diagram's "current state" highlight follows the theme.
Actual (dark): white on `#2f8fc4` is 3.59:1 (top bar text at 14.5px/600, Approve button); white on `#f09a93` (danger button, "Cancel the run", "Withdraw") is 2.15:1; white on `#6c7a86` (Reject) is 4.41:1; accent links on the panel are 4.32:1. The mermaid "current" node is a pale cream box with dark text dropped into a dark-themed diagram. Light theme: `ok` chip 4.43:1, `warn` chip 4.23:1 (12px bold is not "large text"), secondary button 4.41:1. Both themes: input and card borders (`--line`) are 1.35:1 light / 1.46:1 dark against the panel, so the border of the required signature input is nearly invisible (1.4.11 asks 3:1 for control boundaries), and `nav a.cur` differs from the panel by 1.09:1 so the current rail item is not visibly current.
Impact: Anyone on a dark OS theme (the default on most phones at night) and anyone with low contrast sensitivity; the buttons they must press are the lowest-contrast text on the page.
Suggested fix (optional): Add `--danger-fg` and `--btn-fg` tokens; in dark set `--accent:#5fb0dd` and `--accent-fg:#0d1a24` (or keep the accent and set `.top,button{color:#0d1a24}`), set `button.danger{color:#1b0f0e}` in dark, `--line:#aab4bd` (light) / `#55636f` (dark) for `input,select,textarea` borders only (`--ctl-line`), `nav a.cur{background:var(--chip);font-weight:600}`, and pass the theme into `mermaid_for()` (`fill:var(--warn-bg)` is not supported by mermaid; emit the dark hex when `prefers-color-scheme` is dark via two `classDef` lines and `theme` variables, or drop the custom colours and use mermaid's `:::current` default).

### F-visual-11: Without CSS the rail is one word and the inbox has no outline
Severity: major
Heuristic: Progressive enhancement; WCAG 1.3.1 info and relationships, 2.4.6 headings and labels, 2.4.1 bypass blocks
Steps:
1. GET any page and view the `<nav>` markup: `<nav><a href="/?as=bob">Home</a><a class=cur href="/inbox?as=bob">Inbox</a><a ...>Boards</a>...` (built with `"".join`).
2. On `/inbox?as=bob` list heading elements: `h1 Inbox`, `h2 Waiting for your decision 44`, `h2 Notifications 30`, `h2 Messages 30`, `h2 Send a message`, plus `h3 Folders` in the nav.
Expected: Unstyled, the rail is a list of ten links, each decision item is a landmark or heading, and a screen-reader user can jump item to item.
Actual: With CSS off the rail renders as `HomeInboxBoardsFilesRunsReleasesFramesWhyConformanceAdmin` (ten inline anchors with no separators and no list), followed by the folder `<ul>`. The 44 decision cards are `<div>`s whose title is a `<b>`, so the document outline is one `h2` followed by ~21,000px of undifferentiated text; there is no `<article>`, no per-card heading, no skip link, no `aria-label` distinguishing the rail `<nav>` from the folder tree inside it. Chips survive as plain words, which is fine, but `due <b>2026-11-09</b>` and the title `<b>` become indistinguishable bold runs. The record page's `.tabs` are a row of anchors that still work, which is the one part that degrades well.
Impact: Screen-reader users and anyone on a text browser or with a failed stylesheet; also search/skim via the heading list in any assistive tool.
Suggested fix (optional): `<nav aria-label="Workspace"><ul class="rail"><li><a ...>Home</a></li>...</ul></nav>` and a separate `<nav aria-label="Folders">`; wrap each decision card in `<article aria-labelledby="c-<run>">` with `<h3 id="c-<run>">Approve the purchase: PR-00004</h3>`; add `<a class="skip" href="#main">Skip to content</a>` and `id="main"` on `<main>`.

### F-visual-12: The home page is a wall of nine fully expanded Start forms
Severity: major
Heuristic: Nielsen "aesthetic and minimalist design"; progressive disclosure
Steps:
1. GET `/?as=bob`: count `action="/start"` (9) and `<label class="f">` (62); as dave: 7 forms, 51 fields.
Expected: The landing page gives orientation (counts, what is waiting) and a "+ New" affordance that opens one Start form on demand, as Nextcloud's "+ New" and SharePoint's "+ New" do.
Actual: After four count tiles, "Processes you can start" renders every startable process as a `.card` with its complete form: all declared inputs, a file input, an "other fields (JSON object)" textarea (`rows="2" cols="50"`) and a Start button. For bob that is nine cards with 62 fields, about 4,000px, before "Frames behind this release". The JSON textarea with placeholder `{"lot": "L-42"}` appears in all nine cards on the landing page of a workspace that claims nobody needs to read the policy grammar. The four count tiles are the only thing above the fold; two of the four ("of my runs waiting") are not links.
Impact: Everyone, on every arrival; the page that should take someone to their work makes them scroll through forms they did not ask for.
Suggested fix (optional): Render each process as `<details class="card"><summary><b>calibration</b> <span class="chip neutral">calibration</span> filed in Quality/Calibration</summary>...form...</details>`, or a "Start" `<select>` + button that opens `/start?process=<name>` with one form. Move the JSON textarea into a `<details>Advanced</details>` inside the form. Make all count tiles links.

### F-visual-13: "Tabs" that do not tab, over a page that gives JSON dumps the same weight as the record
Severity: major
Heuristic: Affordance (looks like a tab strip, behaves as a table of contents); hierarchy; Nielsen "minimalist design"
Steps:
1. GET `/record/rec:purchase-request:PR-00004?as=bob`.
2. Look at `<div class="tabs"><a href="#details">Details</a><a href="#workflow">Workflow</a>...<a href="#track">Track</a></div>` and the seven `<h2>` sections that follow on one page.
Expected: A tab strip switches panels and shows which one is current; the first screen holds the record's facts and the action; audit material (Activity, Track) is secondary.
Actual: The pills are in-page anchors; all seven sections render in sequence and there is no `.cur` state, so clicking "Track" scrolls and leaves no trace of where you are. "Details" is a two-column table of raw keys in alphabetical order (`conditions {"item_known": true, ...}`, `supplier_approved True`), while the inbox brief shows the same record with curated labels, so the record looks different in the two places it appears. Below it, `Activity` (6 columns, 300-char JSON detail), `Decisions` (8 columns), and `Track` (a `<pre style='margin:0'>` of up to 4,000 chars per event; 23 rows on the run page) get the same `h2`, the same table style and the same width as Details. The workflow diagram is a `<pre class="mermaid">` whose raw `stateDiagram-v2` source is visible until the CDN script runs, and stays visible if the CDN is blocked (the hide-on-load script only hides it when `window.mermaid` is undefined after load).
Impact: Approvers and auditors; the record page is read on every item and its first screen is 40% mechanics.
Suggested fix (optional): Give the strip a real current state (`.tabs a.cur{background:var(--accent);color:#fff}` with `?tab=` in the query and only that section rendered, or `:target`-based CSS `section:not(:target){display:none}` with a default), reuse `_brief()` for Details, and put Activity/Track in `<details>` with a muted summary. Add `pre.mermaid{color:transparent}` until rendered via a `[data-processed]` rule, or `pre.mermaid:not([data-processed]){font-size:12px;color:var(--muted)}`.

### F-visual-14: No icons, no sortable columns, no selection, no breadcrumb: the "Files" page is a report, not a library
Severity: major
Heuristic: Comparison with Nextcloud Files / SharePoint document library; recognition rather than recall; affordance
Steps:
1. GET `/files?as=bob` and `/files?folder=Finance/Payables&as=bob`; `/boards?type=purchase-request&as=bob`; any page's `.top` and `<nav>`.
2. Search the served pages for `<svg`, `<img`, `aria-sort`, `type="checkbox"`, breadcrumb markup: none.
Expected (what Nextcloud/SharePoint give at a glance): file-type icons, a breadcrumb trail (Finance > Payables), sortable column headers, a selection checkbox and bulk actions, a "+ New" button in a fixed place, a per-row "..." actions menu, thumbnails, humanised size ("12 KB") and relative dates ("2 hours ago"), favourites/recent, and an icon beside each rail entry.
Actual: The rail is ten text links; the Files root is a three-column table (Folder, Access, Why) where the third column is a policy sentence; a folder page's `<h1>` is the raw path `Finance/Payables` with no links to parents; the top bar crumb is only the page title; tables have no sort, no selection, no row actions; size is bytes (`194`), dates are ISO timestamps (`2026-11-06T14:49:21`); the only "new" affordance is an "Upload a file" card at the bottom of a 78-row table. Boards cards show `PR-00004 [INV] 200000 x 10k resistor` plus a muted `folder · owner · due` line: no state, no risk, no amount, no avatar. Primary buttons everywhere are text-only.
Impact: Everyone; the claim under test is a workspace feel, and the parts people recognise from those products (icons, crumbs, sort, select, "+ New") are the parts missing, so users fall back to reading.
Suggested fix (optional): Inline a small SVG sprite (home, inbox, board, folder, play, tag, question, shield, gear; file, pdf, csv, image) and prefix rail links and document rows; breadcrumb in `.top` built from `folder.split("/")`; `<th><a href="?sort=due">Due</a></th>` with `aria-sort`; humanise size/date in `updated_of()`; move "Upload" to a toolbar under the `<h1>` (`.toolbar{display:flex;gap:8px;margin:8px 0 14px}`).

### F-visual-15: The Kanban board overflows a laptop and buries the actionable column among seven
Severity: minor
Heuristic: Responsive layout; hierarchy; Nielsen "minimalist design"
Steps:
1. GET `/boards?type=purchase-request&as=bob`: columns `open 0`, `awaiting-approve-the-purchase 23`, `ordered 23`, `rejected 0`, `returned 0`, `failed 6`, `cancelled 3`; 55 `.kcard`.
2. Read `.board{grid-auto-flow:column;grid-auto-columns:minmax(210px,1fr);gap:12px;overflow-x:auto}`.
Expected: The columns that matter fit the viewport, or the empty/terminal ones are compressed; the waiting column stands out.
Actual: Seven columns at a 210px minimum plus six 12px gaps need 1,542px; the laptop main is 998px, so `failed` and `cancelled` are off-screen behind a horizontal scrollbar at the bottom of a column that is ~23 cards (about 1,400px) tall; the scrollbar is therefore two screens down. `open 0`, `rejected 0` and `returned 0` each take a full 210px column. "awaiting-approve-the-purchase" is the column heading as a warn chip, the same chip as in every other page. The filter card above the board exposes a 22-entry type select and a 23-entry folder select inline.
Impact: Anyone using boards to find waiting items; moderate frequency.
Suggested fix (optional): `.board{grid-auto-columns:minmax(200px,1fr)}` plus `.col.empty{grid-auto-columns:120px;opacity:.6}` (add class when `len(items)==0`), put terminal states in a `<details>` row, add `position:sticky;top:0` to `.col h3`, and show a state/risk chip on `.kcard`.

### F-visual-16: Nine font sizes and twelve margins between 12px and 26px: no type or spacing scale
Severity: minor
Heuristic: Visual consistency; typographic scale
Steps:
1. Read `_CSS` and list `font-size` values: 12, 12.5, 13, 13.5, 14, 14.5, 16, 21, 22 (px). Margins/paddings: 3, 4, 5, 6, 8, 9, 10, 12, 14, 16, 18, 22, 26.
2. Compare `h2` (16px) with `.counts b` (22px) and `h1` (21px): a tile number is larger than the page title.
Expected: Three or four text sizes with clear roles (caption, body, heading, display) and a 4- or 8-px spacing step, expressed as tokens.
Actual: Half-pixel sizes (`12.5`, `13.5`, `14.5`) sit one step apart, so chips, `.why`, `code`, table text and labels all look "slightly smaller than body" without a readable hierarchy; `h2` has a bottom border and 22px top margin while `h3` is 14px, the same as a `.kcard`; two inline `style=` attributes (`pre style='margin:0'`, `pre style='margin:0;background:none;padding:0'`) patch the `pre` rule instead of a class.
Impact: Everyone, low severity on its own; it is the reason the pages feel like a log rather than a product.
Suggested fix (optional): `:root{--fs-s:12px;--fs-m:14px;--fs-l:16px;--fs-xl:22px;--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px}`; map `.why,.chip,th,code` to `--fs-s`, body/table/labels to `--fs-m`, `h2` to `--fs-l`, `h1` and `.counts b` to `--fs-xl`; replace the two inline styles with `pre.cell{margin:0}` and `pre.src{margin:0;background:none;padding:0}`.

### F-visual-17: Due dates all look alike until the day they are overdue
Severity: minor
Heuristic: Visibility of status; time-pressure affordance
Steps:
1. GET `/inbox?as=bob`; grep `due <b>`: 15 cards due 2026-11-09, 18 due 2026-11-11, 6 due 2026-11-16, 2 due 11-20, 2 due 12-06; `overdue` appears 0 times.
Expected: A 3-day SLA item (due 2026-11-09, today 2026-11-06) is visibly different from one due in a month; relative wording ("due in 3 days") and a warn tone before the deadline, crit after.
Actual: Every due date is the same bold ISO date in the run-on header line; the sort puts sooner items first but nothing marks the boundary, and the only visual change is binary: `chip("overdue")` plus a red card border the day after. The same bold is used for the card title.
Impact: Approvers prioritising a long queue; daily.
Suggested fix (optional): In `_decision_card()` compute `days = (date.fromisoformat(due) - today).days` and render `<span class="chip warn">due in 3 days</span>` for `0 <= days <= sla_warn`, `<span class="chip crit">overdue 2 days</span>` for negative, plain text otherwise; keep the ISO date in a `title=` attribute.

### F-visual-18: A sentinel "escalated 2099-01-01" chip appears on eleven cards
Severity: minor
Heuristic: Nielsen "match between system and the real world"; signal/noise
Steps:
1. GET `/inbox?as=bob`; grep `escalated`: `escalated 2026-11-12` on 7 cards, `escalated 2099-01-01` on 11 cards, inside `<span class='chip warn'>`.
Expected: A chip is shown only when it carries meaning; "escalated" means it happened, with a date in the past.
Actual: `_decision_card()` renders `p.get("escalated")` whenever it is truthy, so a placeholder date of 2099 and a future date (2026-11-12, six days from now) both appear as an amber "escalated" chip in the header line, in the same style as real status. On dave's inbox 13 of 25 cards carry a warn chip for this reason.
Impact: Approvers reading the header line; it dilutes the only amber signal in the card and misreports status.
Suggested fix (optional): Show the chip only when `p["escalated"] <= today`; otherwise render `<span class="why">escalates on 2026-11-12</span>` (or nothing for sentinels).

### F-visual-19: Six different meanings share one grey pill
Severity: minor
Heuristic: Consistency and standards; Gestalt similarity
Steps:
1. GET `/?as=bob`: `Welcome, bob <span class="chip">person</span>`, roles as chips.
2. GET `/record/...PR-00004?as=bob`: `<span class="chip">purchase-request</span>` (type), `<span class="chip warn">awaiting-...</span>` (state), `<span class="chip">retention: financial-reporting</span> <span class="chip">sensitivity: internal</span>` (labels).
3. GET `/admin?as=bob`: document classes as chips; `/releases`: record type and every workflow state as chips in a row.
Expected: One visual form per meaning: status is a filled coloured pill; categories (type, role, label) are quiet outlined tags; counts are numbers.
Actual: `.chip` (filled grey, 600 weight, `border-radius:999px`) is used for member type, roles, record type, labels, document class, risk, yes/no, gate status and workflow state, and `.tabs a` on the record page is a pill of the same shape, so tabs look like chips and chips look clickable. In `<h1>` a type chip and a state chip sit side by side in identical shape, and the state is the only one that matters.
Impact: Everyone; low individual cost, but it is why the status chips in F-visual-3 do not read as status.
Suggested fix (optional): Add `.tag{display:inline-block;padding:0 6px;border:1px solid var(--line);border-radius:4px;font-size:12px;color:var(--muted)}` for type/role/label/class and keep `.chip` for state, risk and gate; square the corners of `.tabs a` (`border-radius:6px 6px 0 0;border-bottom:0`).

### F-visual-20: `/doc/<locator>` from the brief is "No such page"
Severity: question
Heuristic: Consistency between the stated map and the shell
Steps:
1. GET `http://localhost:8080/doc/rec:document:DOC-0130?as=bob`.
Expected: The document page (the brief lists `/doc/<locator>`).
Actual: `<main><div class="card crit"><b>Not done.</b> No such page.</div></main>`; the route in `_route()` is `/document/`, and that page renders. The error page keeps the shell but has no link back when there is no Referer. Out of this lens except that the error card is the same `.card crit` as a failed run.
Impact: Reviewers following the brief; one-off.
Suggested fix (optional): Either alias `/doc/` in `_route()` or correct the brief; give the error page a "Home" link regardless of Referer.

## Summary: the three visual changes with the best payoff

First, turn the inbox from a stack of forty open forms into a scannable queue: a compact header per item (title, record, summary, amount, risk chip, due) with one decision card open at a time, optional fields behind a `<details>`, and the verdict row anchored at the bottom with a distinct Reject style and real spacing; this removes about twenty screens of scrolling per visit and makes the decision the first thing on screen. Second, make status honest and loud and everything else quiet: colour risk/supplier/gate values in `chip()` (high = crit, medium = warn, low/yes = ok), demote type/role/label pills to outlined tags, give decision cards an accent edge and notifications a thin warn edge, and show the same decision card on the record page when the viewer is the assignee, so the three-second scan works wherever the approver lands. Third, fix the responsive and theme basics in one CSS pass: collapse the rail and folder tree to a bar below 900px, replace `size`/`cols` with a fluid field grid, wrap tables in an `overflow-x:auto` container with `overflow-wrap:anywhere` on code, and raise the dark-theme button, top-bar and control-border contrasts to AA so the signature field and the Approve button are visible on a phone at night.
