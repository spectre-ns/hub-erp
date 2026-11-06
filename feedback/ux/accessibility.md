# Accessibility lens (WCAG 2.2 AA)

Method: markup and CSS only, no browser. Pages fetched with curl as bob and
alice (`/`, `/inbox`, `/runs`, `/run/<id>`, `/record/<locator>`,
`/boards?type=bill`, `/files`, `/files?folder=...`, `/releases`, `/admin`,
`/why`, `/frames`, `/frame/<name>`, `/document/<locator>`, `/conformance`),
plus POSTs to `/decide` and `/start` to see the error and success pages.
Template and CSS read in `hubproto/shell.py` (`_CSS`, `_page`, `widget`,
`_decision_card`, `table`). `grep -c "aria-\|role=\|scope=\|<thead\|<fieldset\|skip"
hubproto/shell.py` returns 0: there is no ARIA, no `scope`, no fieldset and no
skip link anywhere in the shell. Contrast ratios below were computed from the
`:root` tokens for both the light palette and the `prefers-color-scheme: dark`
palette.

What passes and is not reported: `lang="en"` and a distinct `<title>` on
every page; `<nav>` and `<main>` landmarks; every Start-form and decision-form
control is wrapped in a `<label>`; native `required` on required fields;
status chips carry text ("overdue", "failed", "escalated"), so status is not
colour-only; no animation, so reduced-motion is moot; every control is a
native link, button, select, input or `<details>`, so nothing needs custom
keyboard handling.

### F-accessibility-1: Enter in the signature field approves; nothing confirms or reverses a decision, and the server signs for you when the field is empty
Severity: blocker
Heuristic: WCAG 3.3.4 Error Prevention (Legal, Financial, Data); 3.3.2 Labels or Instructions
Steps:
1. Open `http://localhost:8080/inbox?as=bob`.
2. Tab into the first decision card, to the input labelled "meaning of your signature *". Type "needs rework" intending to reject, and press Enter (the normal way to submit from a text field) or Tab once and press Enter without reading the button name.
3. Alternatively send the form with the required field blank: `curl -X POST localhost:8080/decide --data "as=bob&run=<id>&digest=<digest>&verdict=approve&meaning="`.
Expected: Enter from the signature field does not commit a financial approval; a decision on a 20,000 USD purchase is confirmed or reversible; an empty signature meaning is refused by the server, not only by the browser.
Actual: The form ends `<label class="f"><span>meaning of your signature <b>*</b></span><input name="meaning" placeholder="e.g. approved for posting" size="40" required></label> <button name="verdict" value="approve">Approve</button> <button class="secondary" name="verdict" value="reject">Reject</button>`. Implicit submission uses the first submit button in tree order, which is Approve. There is no confirmation step and no undo: after the POST the run page reads `Run 82007f4728 <span class="chip ok">completed</span>` and a purchase order was issued. The POST with `meaning=` was accepted; the Track shows `"meaning": "approved approve the purchase", "meaning_given": false`. The only guard on the signature is the client-side `required` attribute.
Impact: Every keyboard and screen-reader user who decides from the inbox. A reviewer who types a reason and presses Enter approves what they meant to reject, irreversibly, and a client that does not enforce `required` (older Safari, some voice-control and form-filling tools, any scripted client) produces a signed approval whose meaning the signer never wrote. The hub's central claim is "a clear signature at Gates".
Suggested fix (optional): Make verdict an explicit choice before the signature (radio buttons "Approve" / "Reject", required), or put Reject first and give both buttons distinct accessible names ("Approve PR-00004"); add a confirmation page that restates record, verdict and meaning; reject `meaning` of zero length on the server and return the form with the error.

### F-accessibility-2: The "Acting as" select submits on change, so arrowing through identities navigates away on the first keypress
Severity: major
Heuristic: WCAG 3.2.2 On Input
Steps:
1. Open `http://localhost:8080/inbox?as=alice` in Chrome, Edge or Safari.
2. Tab to the "Acting as" select (second tab stop on every page). Press Down once, intending to reach "dave".
Expected: Changing the selection does nothing until the "Go" button is pressed, or the user is told beforehand that choosing an identity reloads the page.
Actual: `<label>Acting as <select name="as" onchange="this.form.submit()">...</select></label> <button class="secondary">Go</button>`. On a closed select, Down fires `change`, the form submits, and the page reloads as `ap-reviewer`; reaching `dave` from `alice` takes four page loads, each resetting focus to the top of the document and discarding anything typed in a form on the page. The "Go" button exists but can never be reached after a change.
Impact: Every keyboard user and every screen-reader user in focus mode, on every page, every time they switch identity (which the panel does constantly, since identity is the only "login"). A reviewer halfway through filling a Start form who touches the select loses the form.
Suggested fix (optional): Drop `onchange`; keep the Go button. If auto-submit must stay, use `onchange` only after an explicit warning in the label ("changes the page") and move the switch after the main content.

### F-accessibility-3: No skip link; 36 tab stops precede the content on every page, and bob's inbox has several hundred before "Notifications"
Severity: major
Heuristic: WCAG 2.4.1 Bypass Blocks
Steps:
1. Open `http://localhost:8080/inbox?as=bob`. Count the focusable elements before `<main>`.
2. Tab from the top to the "Send a message" form at the bottom.
Expected: A "Skip to content" link as the first focusable element, and a way to jump between work items.
Actual: Before `<main>` every page has the "Hub" link, the identity select, "Go", 10 rail links and 23 folder links (`<nav><a class=cur href="/?as=bob">Home</a>...<h3>Folders</h3><ul>...`), 36 stops. Bob's inbox then has 44 decision cards, each with about 11 focusable elements (four links, four optional fields, the signature input, Approve, Reject), so roughly 480 stops before the notifications section. The `<main>` landmark lets a screen reader jump once; a sighted keyboard user, a switch user, or anyone without landmark navigation has no bypass at all.
Impact: Keyboard-only and switch users on every page; worst for approvers with a full inbox.
Suggested fix (optional): Add `<a class="skip" href="#main">Skip to content</a>` as the first child of `<body>` with `id="main"` on `<main>` (visually hidden until focused); give each work item a heading (see F-accessibility-4) so screen readers can jump card to card.

### F-accessibility-4: Work items are bold text in a `div`, not headings or named groups; 44 identical "Approve" buttons
Severity: major
Heuristic: WCAG 1.3.1 Info and Relationships; 2.4.6 Headings and Labels; 4.1.2 Name, Role, Value (forms without an accessible name)
Steps:
1. Open `http://localhost:8080/inbox?as=bob`. Use a screen reader's headings list (H key) or forms/buttons list.
2. Try to reach the decision card for PR-00008 without reading the 43 others.
Expected: Each pending decision is a heading ("Approve the purchase: PR-00008") or a named region; Approve/Reject buttons are distinguishable in a buttons list.
Actual: The only headings are `<h1>Inbox</h1>` and `<h2>Waiting for your decision <span class='muted'>44</span></h2>`. Each item is `<div class="card"><b>Approve the purchase</b> &middot; purchase-request &middot; <a ...>PR-00008</a> ...` followed by `<form method="post" action="/decide">` with no `aria-label`, `fieldset` or `legend`. The buttons list shows "Approve" 44 times and "Reject" 44 times. The same pattern holds for Start forms on `/` (`<div class="card"><b>calibration</b> ...<form ...>`), for notifications and for messages. The record page (`/record/rec:purchase-request:PR-00004?as=bob`) carries no decision form, so the inbox is the only place to decide.
Impact: Screen-reader users with more than a handful of pending items must read linearly through every card and its ten-row `<dl>` to find the right one, then trust that the "Approve" they pressed belongs to it.
Suggested fix (optional): `<h3>` per card ("Approve the purchase: PR-00008 [INV] PR-C-10000"); `<form aria-labelledby="<that h3 id>">`; button text "Approve PR-00008" / "Reject PR-00008" (or `aria-label`). Same for Start cards ("Start nonconformance" is already distinct, so the heading alone suffices there).

### F-accessibility-5: Server-side form errors land on a separate page with the form gone, the error unattached to any field, and a "Back" link that depends on the Referer header
Severity: major
Heuristic: WCAG 3.3.1 Error Identification; 3.3.3 Error Suggestion; 2.4.6 Headings and Labels
Steps:
1. As alice on `http://localhost:8080/?as=alice`, fill the nonconformance Start form with quantity `abc` and submit (equivalent: `curl -F as=alice -F process=nonconformance -F title=A11y -F description=x -F quantity=abc -F json= localhost:8080/start`).
2. Submit the same form with title and description empty but from a client that does not honour `required`.
Expected: The form is re-shown with values kept, the failing field named and linked (`aria-describedby`), focus moved to the error, and a heading that says what went wrong.
Actual: HTTP 400 with `<main><div class="card crit"><b>Not done.</b> quantity must be a number, not &#x27;abc&#x27;.</div><p><a href="http://localhost:8080/?as=alice">Back</a></p></main>`. There is no `<h1>`, no form, no field reference, and the `<title>` is "Not done - Hub" for every error. Step 2 returned `<b>Not done.</b> nonconformance needs title, description before it can start<div class="why">RunError</div>` with no Back link at all, because `_back_link()` emits one only when a Referer was sent. A decision with a stale digest produced the same page shape ("run 82007f4728 is not waiting for a decision (status completed)") with no route back to the inbox other than the rail.
Impact: Anyone whose Start fails validation; screen-reader users most, because the page announces no heading and they must return to a home page with eleven look-alike forms and find the one they were in. With a strict referrer policy, a bookmark or a new tab, the Back link is absent.
Suggested fix (optional): Re-render the originating page with the submitted values, an `<h1>` or `role="alert"` summary at the top, and the message attached to the control via `aria-describedby` and `aria-invalid="true"`. Keep an explicit `return` field in the form instead of relying on Referer.

### F-accessibility-6: Controls with no label at all: the Why query, attach-file, attach-existing, link-relation, folder upload and check-in file inputs
Severity: major
Heuristic: WCAG 4.1.2 Name, Role, Value; 1.3.1 Info and Relationships; 3.3.2 Labels or Instructions
Steps:
1. Open `http://localhost:8080/why?as=bob` and tab through the form.
2. Open `http://localhost:8080/record/rec:purchase-request:PR-00004?as=bob`; tab to the Documents and Links forms.
3. Open `http://localhost:8080/files?as=bob&folder=Purchasing%2FRequests`; tab to "Upload a file".
Expected: Every select and file input has an accessible name that says what it is for.
Actual: `/why` is a sentence with four bare selects: `May <select name="principal">...</select> <select name="action">...</select> <select name="object">...</select> in <select name="folder">...</select> <button>Ask</button>`; a screen reader announces "combo box, bob", "combo box, create", "combo box, bill", "combo box, Finance/Payables" with nothing to say which is which. Record page: `Upload and attach <input type="file" name="file"> <input name="title" placeholder="title" size="20"> or attach existing <select name="doc"><option value=""></option>...` (a 60-option select whose name is empty) and `Link as <select name="rel">...</select> to <input name="target" placeholder="record number or locator" ...>`. Folder page: `<input type="file" name="file" required> <input name="title" placeholder="title (defaults to the file name)" ...>`. Document page (shell.py line 1247): `<input type="file" name="file" required> <input name="note" placeholder="note" ...>`. The adjacent prose is not associated with any of them.
Impact: Screen-reader users attaching evidence, linking records, uploading documents or asking "why", that is, most of the recovery and audit flows.
Suggested fix (optional): Wrap each in `<label class="f"><span>...</span>...</label>` as `widget()` already does for record fields, or add `aria-label`.

### F-accessibility-7: Placeholder is the only label on every recovery form (retry, cancel, withdraw, hold, attach title, link target, upload title, check-in note)
Severity: major
Heuristic: WCAG 3.3.2 Labels or Instructions; 1.3.1 Info and Relationships
Steps:
1. Open `http://localhost:8080/inbox?as=alice`, section "Failed runs of yours".
2. Tab into the Retry and Cancel forms of the first card and start typing.
Expected: A visible, persistent label for "note" and "reason"; the instruction does not vanish once a character is typed.
Actual: `<form method="post" action="/retry" class="inline">...<input name="note" placeholder="what was fixed" size="28"> <button>Retry</button></form> <form method="post" action="/cancel" class="inline">...<input name="reason" placeholder="reason" size="20"> <button class="danger">Cancel the run</button></form>`. The same shape is used for "Withdraw", for "Put on hold" on the record page (`<input name="reason" placeholder="reason" size="24">`), and for the inputs listed in F-accessibility-6. Placeholder text disappears on input, is not an accessible-name technique WCAG accepts for 3.3.2, and Chrome's default placeholder colour on the light panel (#757575 on #fff, 4.6:1) is at the limit.
Impact: People with memory or attention impairments and screen-magnifier users who cannot see the whole form; everyone retrying or cancelling a failed run (alice has 35 of them).
Suggested fix (optional): Reuse `label.f` with a visible `<span>` for these inputs; keep placeholder only as an example.

### F-accessibility-8: Text contrast fails in the dark theme for primary and danger buttons, the top bar and links, and in the light theme for the ok and warn chips and the secondary button
Severity: major
Heuristic: WCAG 1.4.3 Contrast (Minimum)
Steps:
1. Read `_CSS` in `hubproto/shell.py`: `button{background:var(--accent);color:#fff}`, `button.secondary{background:#6c7a86}`, `button.danger{background:var(--crit)}`, `.top{background:var(--accent);color:var(--accent-fg)}`, `.chip{font-size:12px;font-weight:600}`, `.ok{...color:var(--ok)}`, `.warn{...}`.
2. Compute ratios for both `:root` palettes.
Expected: 4.5:1 for all text under 24px (12px bold chips are not "large text").
Actual: Dark theme: white on `--accent` #2f8fc4 is 3.59:1 (every Approve, Start, Retry, Send, Go and Filter button; the "Hub" link and "Acting as" label in the top bar; the crumb at `opacity:.9` is lower still); white on `--crit` #f09a93 is 2.15:1 ("Cancel the run", "Withdraw"); `--accent` links on a `--panel` card or table are 4.32:1 (every record, run and frame link in tables). Light theme: warn chip #9a6400 on #f7ebcf is 4.23:1 ("awaiting-...", "waiting", "open", "escalated"); ok chip #1f7a4d on #dcefe4 is 4.43:1 ("ordered", "completed", "passed"); secondary button white on #6c7a86 is 4.41:1 ("Reject", "Render snapshot", "Put on hold"). Passing: body text (13.9 and 14.6), muted text (4.74 and 7.86), crit chip (5.37 and 6.38), dark-theme chips (6.4 to 6.9).
Impact: Low-vision users; dark-theme users cannot read the Approve/Reject/Cancel button labels reliably, and in light theme the two most common state chips are below the line.
Suggested fix (optional): Dark theme: `--accent:#1f6f9c` or button text `#0b1620`; danger button text dark on the pink, or a darker `--crit` for backgrounds; light theme: `--warn:#7a5000`, `--ok:#186640`, secondary `#5c6a76`.

### F-accessibility-9: Input borders and the current-page marker are far below the 3:1 non-text contrast floor
Severity: major
Heuristic: WCAG 1.4.11 Non-text Contrast
Steps:
1. Read `_CSS`: `input,select,textarea{border:1px solid var(--line);background:var(--panel)}`, `.card{background:var(--panel)}`, `nav a.cur{background:var(--bg)}`, `nav{background:var(--panel)}`.
2. Open any page: every form sits inside a `.card`, so inputs have the same background as their container and only the border shows where they are.
Expected: The boundary of a text field, and the indicator of the current nav item, reach 3:1 against the adjacent background.
Actual: `--line` on `--panel` is 1.35:1 (light) and 1.46:1 (dark); card borders on the page background are 1.24:1 and 1.65:1. The current rail item is `<a class=cur ...>` whose only visual difference is `background:var(--bg)`, 1.07:1 against the panel in light and 1.26:1 in dark; it is the same style as `:hover`, so the current page and the hovered link look identical.
Impact: Low-vision users cannot find where text fields begin and end on the inbox (nine fields per card) or see which section they are in.
Suggested fix (optional): `--line:#8a98a4` (light) / `#6a7886` (dark) for control borders, or a 2px border; mark the current rail item with a left bar plus bold text and `aria-current="page"`.

### F-accessibility-10: The rail exposes no current page, no list, no name, and a level-3 heading precedes the page's `<h1>`
Severity: major
Heuristic: WCAG 1.3.1 Info and Relationships; 4.1.2 Name, Role, Value; 2.4.6 Headings and Labels
Steps:
1. Open `http://localhost:8080/runs?as=bob` with a screen reader; list landmarks and headings; read the navigation.
Expected: "navigation, main menu, list, 10 items, Runs, current page"; headings begin with the `<h1>`.
Actual: `<nav><a href="/?as=bob">Home</a>...<a class=cur href="/runs?as=bob">Runs</a>...<h3>Folders</h3><ul>...</ul></nav>`. The ten rail links are bare siblings, not a list, so the user hears ten links with no count; the current page is marked only by `class=cur` (see F-accessibility-9) with no `aria-current`; the nav has no `aria-label` to distinguish the rail from the folder tree it also contains; and the first heading in document order is `<h3>Folders</h3>`, before `<h1>Runs</h1>` in `<main>`, so the headings list reads h3 then h1 on every page.
Impact: Screen-reader users orienting on every page load (which, with the identity switch, is often).
Suggested fix (optional): `<nav aria-label="Hub"><ul>` for the rail with `aria-current="page"`; `<nav aria-label="Folders">` for the tree with an `<h2>`; or place the `<h1>` before the rail in source order and position with CSS.

### F-accessibility-11: Nothing tells the user that a Start, decision, retry, cancel or message succeeded
Severity: major
Heuristic: WCAG 4.1.3 Status Messages (the state change happens across a redirect, so its equivalent here is a visible, announced status line on the landing page)
Steps:
1. As alice, submit the task Start form on `/`. Follow the 303.
2. As bob, approve a decision in `/inbox`. Follow the 303.
Expected: The landing page opens with "Started task TASK-00xx; run 05739f08c3 is waiting at ..." or "Approved PR-00004; the run completed and issued PO-...", in a `role="status"` region, and the page title reflects it.
Actual: `/start` answers `303 Location: /run/05739f08c3?as=alice`; that page begins `<h1>Run 05739f08c3 <span class="chip warn">waiting</span></h1><div class="card">task started by alice at 2026-11-06T16:08:39+00:00; ...` with no sentence about what just happened. `/decide` answers `303 Location: /record/rec:purchase-request:PR-00004?as=bob`; the record page shows the new state only as a chip in the `<h1>`. `/msg`, `/retry` and `/cancel` behave the same. A screen-reader user hears a page title and a run id and must deduce success from the absence of a "Not done" card.
Impact: Everyone, especially screen-reader users and people with cognitive impairments, after every action; it also makes the silent default signature in F-accessibility-1 invisible.
Suggested fix (optional): Carry a one-line flash through the redirect (`?done=...` or a server-side one-shot) and render it as the first child of `<main>` inside `<p role="status">`; prefix the title ("Approved: PR-00004 - Hub").

### F-accessibility-12: Fixed-width inputs and a non-wrapping top bar force horizontal scrolling at 320 CSS px
Severity: major
Heuristic: WCAG 1.4.10 Reflow
Steps:
1. Open `http://localhost:8080/inbox?as=bob` at 400% zoom (viewport 320 px wide) or on a narrow phone.
Expected: Content reflows into one column with no horizontal scroll except for data tables and the kanban board.
Actual: The layout collapses correctly at 900 px (`@media(max-width:900px){.layout{grid-template-columns:1fr}}`), but `.top{display:flex}` has no `flex-wrap`, so "Hub", the crumb (e.g. "Purchasing/Requests") and the "Acting as" form (`.top form{margin-left:auto}`) overflow in one row. Every text field is sized in characters: `<input name="meaning" ... size="40">`, `<textarea name="reason" rows="2" cols="50">`, `<textarea name="body" rows="2" cols="60">`, `<input name="asset" value="" size="24">`; with `main{padding:18px 26px}` the content box is 268 px, and a `size="40"` input or `cols="50"` textarea is wider than that, so the inbox and every Start form scroll sideways. `<pre>` blocks in the Track table are `pre-wrap` (fine) but sit in a 4-column table that is itself wider than the viewport.
Impact: Screen-magnifier users and anyone on a phone; the fields they must type into are the ones that overflow.
Suggested fix (optional): Drop `size`/`cols` or add `input,select,textarea{max-width:100%}` and `label.f{max-width:100%}`; `.top{flex-wrap:wrap}`.

### F-accessibility-13: Field labels are code identifiers, format rules live only in placeholders, and the asterisk convention is never explained
Severity: major
Heuristic: WCAG 2.4.6 Headings and Labels; 3.3.2 Labels or Instructions
Steps:
1. Open `http://localhost:8080/?as=alice`; read the labels of the purchase-to-pay, goods-receipt and document-control Start forms with a screen reader.
Expected: Labels in words ("Purchase order reference", "Manufacturer part number", "Document type"), format instructions in the label or a linked description, and one line saying what "*" means.
Actual: `widget()` uses the field name verbatim: `<span>po_ref</span>`, `<span>mpn</span>`, `<span>doc_type</span>`, `<span>training_roles (comma list or JSON)</span>`, `<span>other fields (JSON object)</span>`. Screen readers read "p o underscore ref", "m p n", "doc underscore type". The goods-receipt `lines` field's only instruction is `placeholder="locator,quantity per line, e.g. rec:item:ITEM-00001,4"`, which disappears on the first keystroke; `user_needs` has `placeholder="one per line"`. Required fields show `<span>title <b>*</b></span>` with no legend; the server's own wording ("nonconformance needs title, description") is the first time the rule is stated.
Impact: Every person starting work; the brief's claim is that people do this "without reading the code or the policy grammar". Screen-reader users and people with reading difficulties are hit hardest.
Suggested fix (optional): A `label` map per field in `widget()` (the function already accepts `label=`); move format rules into the `<span>` or a `<small id>` linked by `aria-describedby`; add "* required" once above each form.

### F-accessibility-14: Links that do not make sense out of context: "open run" and "open record" 60 times, line-number links "1".."60", "download", "Back", a URL as link text
Severity: minor
Heuristic: WCAG 2.4.4 Link Purpose (In Context)
Steps:
1. Open `http://localhost:8080/inbox?as=bob`; open the screen reader's links list.
2. Open `http://localhost:8080/frame/purchasing-policy?as=bob` and `http://localhost:8080/boards?as=bob&type=bill`.
Expected: Each link's text, or its programmatically determined context (same sentence, paragraph, list item or table cell), says where it goes.
Actual: Each notification is `<div class='card crit'>... <b>Overdue: Approve the purchase on rec:purchase-request:PR-00038</b> at ...<br>... <a href='/run/fe0cccaa17?as=bob'>open run</a> &middot; <a href='/record/...'>open record</a></div>`; the subject is in the same `div` but not in the same paragraph, list item or cell, so the links list shows "open run, open record" thirty times each. The frame page renders every line as `<tr id='L45'><td class='why'><a href='#L45'>45</a></td><td><pre>...</pre></td></tr>`, 60 links named by number. The board filter shows `Saved filter: <a href="/boards?as=bob&type=bill">/boards?as=bob&amp;type=bill</a>`; the document page has `<a href="/download?...">download</a>` per version; the error page has `<a href="...">Back</a>`.
Impact: Screen-reader users who navigate by links list, the fastest way through a 30-item notification feed.
Suggested fix (optional): "Open run fe0cccaa17" / "Open PR-00038"; `aria-label="line 45"` on line links; "Download version 1"; "Back to Home".

### F-accessibility-15: Tables have no `scope`, `thead` or row headers; the releases table marks the release in effect with a bare "●" under an empty header; the frame listing has no header row
Severity: minor
Heuristic: WCAG 1.3.1 Info and Relationships; 1.1.1 Non-text Content
Steps:
1. Open `http://localhost:8080/releases?as=bob`, `http://localhost:8080/record/rec:purchase-request:PR-00004?as=bob` (Details table), `http://localhost:8080/admin?as=bob` (Record types, 8 columns).
Expected: Column headers with `scope="col"`, row headers for key/value tables, and a text alternative for the "current" marker.
Actual: `table()` emits `<table><tr><th>Digest</th>...</tr><tr><td>...` with no `scope`, no `<thead>`; the releases table starts `<tr><th></th><th>Digest</th>...` and the row is `<td>&#9679;</td><td><code>b7da4af94d3c571fae77</code></td>`, so the only indication that this is the release in effect is a "black circle" character under a nameless column. The record Details table is `<tr><th>Field</th><th>Value</th></tr><tr><td>amount</td><td>20000.0</td>` with the field name as a `td`, so a screen reader in a wide table cannot use the name as a row header. Simple header-row tables are read correctly by most screen readers, but the admin "Record types" table (8 columns, cells with 20 comma-separated values) is where header association is needed and `scope` is absent.
Impact: Screen-reader users on the admin, releases and record pages; minor because the first-row `<th>` heuristic covers the small tables.
Suggested fix (optional): `<thead>` and `scope="col"` in `table()`; `<th scope="row">` for Details; replace "●" with text "in effect" (or `<span aria-label="in effect">●</span>`) and name the column "Status".

### F-accessibility-16: The error page and the non-member page have no heading, and every error page has the same title
Severity: minor
Heuristic: WCAG 2.4.2 Page Titled; 2.4.6 Headings and Labels; 1.3.1 Info and Relationships
Steps:
1. GET `http://localhost:8080/document/DOC-0076?as=bob` (a wrong locator) or any failing POST; GET `http://localhost:8080/inbox?as=zed`.
Expected: An `<h1>` naming the problem and a title that distinguishes "No such page" from "quantity must be a number".
Actual: `<title>Not done - Hub</title>` ... `<main><div class="card crit"><b>Not done.</b> No such page.</div></main>`; the non-member inbox is `<h1>Inbox</h1><div class='card'><b>zed is not a member</b> ...`. The error text is the first thing in `<main>` but it is a bold run inside a `div`, so heading navigation finds nothing and the title does not say which error occurred; a user with several tabs open cannot tell them apart.
Impact: Screen-reader users after any failure; compounds F-accessibility-5.
Suggested fix (optional): `<h1>Not done: <short reason></h1>` and `<title>Not done: <short reason> - Hub</title>`; `role="alert"` on the card.

### F-accessibility-17: The acting identity is not in the page title or the `<h1>` outside Home
Severity: minor
Heuristic: WCAG 2.4.2 Page Titled; 2.4.6 Headings and Labels
Steps:
1. Open `http://localhost:8080/inbox?as=bob` and `http://localhost:8080/inbox?as=alice` in two tabs with a screen reader; switch tabs.
Expected: The title says whose inbox it is ("Inbox (bob) - Hub"), because identity is a query parameter and changes constantly in this prototype.
Actual: Both tabs are `<title>Inbox - Hub</title>` with `<h1>Inbox</h1>`; the only cue is the value of the `<select name="as">` in the top bar, which the user must tab to and read. Home is the only page that says `<h1>Welcome, alice ...`.
Impact: Screen-reader and cognitively impaired users switching identity; the risk is deciding as the wrong principal (bob's inbox and dave's both contain "Approve the purchase" cards).
Suggested fix (optional): Append the principal to the title and show "Acting as bob" as text in the top bar in addition to the select.

### F-accessibility-18: Focus visibility is left entirely to browser defaults, and hover and current share one style
Severity: question
Heuristic: WCAG 2.4.7 Focus Visible; 2.4.13 Focus Appearance (AAA, for reference)
Steps:
1. Search `_CSS` for `:focus` or `:focus-visible`.
Expected: An author-defined focus ring with at least 3:1 against the accent top bar and the accent buttons, since the defaults differ per browser and Safari suppresses the ring on buttons unless Tab-to-buttons is enabled.
Actual: No `:focus` rule exists; `button{border:0}` and `nav a{text-decoration:none}` remove the native borders and underlines, so the UA outline is the only indicator. Nothing sets `outline:none`, so 2.4.7 is not failed by the CSS, but the white-on-accent top bar (`Hub`, the select, `Go`) and the accent-filled primary buttons are where a default dark-blue ring is hardest to see, and it could not be checked without a browser.
Impact: Keyboard users; to be confirmed in a browser, especially Safari.
Suggested fix (optional): `:focus-visible{outline:3px solid var(--fg);outline-offset:2px}` and a white inner ring on the accent bar.

## Summary

Three fixes unblock the most users. First, make the decision form safe for keyboard use: an explicit verdict choice (radios or distinctly named buttons with Reject not after an implicitly-submitting Approve), a confirmation step, and server-side rejection of an empty signature meaning instead of inventing one (F-1), with a status line on the landing page saying what happened (F-11). Second, give the pages structure a screen reader can use: a skip link, a heading per work item and per Start form with the record number in it, `aria-current` and list semantics on the rail, and an `<h1>` on error pages (F-3, F-4, F-10, F-16), which turns a 480-stop inbox into a page one can jump through. Third, finish the labelling and palette: real labels for the Why selects, attach/link/upload inputs and the retry/cancel/hold fields, with errors re-rendered on the form they came from (F-5, F-6, F-7, F-13), and a dark-theme accent and crit colour that keep button text at 4.5:1 plus control borders at 3:1 in both themes (F-8, F-9). Also remove the `onchange` auto-submit on "Acting as" (F-2), a one-line change that stops every keyboard user losing their place on every page.
