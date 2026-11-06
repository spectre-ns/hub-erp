# Lens: first-time-user

A competent new employee with no training opens the hub for the first time as
alice, bob, carol and dave and tries one real task per role, using only what
the pages say. Method: every page fetched with curl as each person; Start
forms submitted via multipart (purchase-to-pay, design-release,
document-control, nonconformance, one deliberately empty bill); decisions
submitted via `/decide` as dave and bob (confirm a receipt, approve a
purchase, two stale items, one blank signature, one wrong digest); a retry, a
cancel, and a message. All on 2026-11-06, release `b7da4af94d3c571f`. Other
reviewers were creating and cancelling runs in the same state at the same
time, which is why some inbox items went stale within minutes; a real office
behaves the same way.

Findings are ranked by how often a real employee would hit them, blockers
first.

### F-first-time-user-1: A failed run says "Fix the record" but the record cannot be edited anywhere
Severity: blocker
Heuristic: Nielsen 9, help users recognise, diagnose and recover from errors; user control and freedom
Steps:
1. As alice, on `/?as=alice`, fill the "purchase-to-pay" form with supplier `Acme Metals`, amount `100`, currency `USD`, date `2026-11-06`, po_ref blank, one attached file; submit.
2. Land on `/run/2003ae847f?as=alice` (status failed). Click `BILL-0048`.
3. On `/record/rec:bill:BILL-0048?as=alice`, look for any way to set po_ref or correct the supplier.
4. As carol, open `/inbox?as=carol`, "Failed runs of yours": CAPA-2026-0001 "cannot enter 'awaiting-verify-effectiveness' without root_cause". Click the record and look for a root_cause field.
Expected: A failed item of mine offers an edit form for the fields the failure names (po_ref, supplier, root_cause), then Retry.
Actual: The record page says `<b>The run failed at match-the-bill-to-purchase-orders:</b> no purchase order None <div class="why">Fix the record and retry the run from the failed step, or cancel it.</div>` and offers only Retry, Cancel, Render snapshot, a disabled Put on hold, Upload and attach, and Add link. The only text inputs on the whole page are `note` ("what was fixed"), `reason`, `title` (for an attachment) and `target` (for a link). Details is a read-only table (`<tr><td>supplier</td><td>Acme Metals</td></tr>`). Pressing Retry with nothing changed (step 3) re-ran the step, failed again (`run.retried`, `step.started`, `step.result`, `run.failed` in Track) and redirected back to the same record page with no message. Source confirms the only field-editing form is the resubmit form, shown only for items a reviewer has *returned* (`shell.py` `_resubmit_form`), never for a *failed* run. Carol's CAPA page likewise has no root_cause input.
Impact: Every person whose run fails (alice has 35 failed runs in her inbox today) reaches a dead end: the UI tells them to fix something it gives them no way to fix. Their only working button is Cancel, which throws the record away.
Suggested fix (optional): Show the resubmit form (fields pre-filled, failing fields highlighted) on failed runs too, and make Retry say what happened when it fails again.

### F-first-time-user-2: Filing a bill with no required fields and a free-text supplier drops the clerk onto a "failed" run page written in system vocabulary
Severity: blocker
Heuristic: Nielsen 5, error prevention; WCAG 3.3.2 Labels or Instructions; WCAG 3.3.3 Error Suggestion
Steps:
1. As alice on `/?as=alice`, find "purchase-to-pay". Note that no field is marked `*` (compare "design-release", where title and kind are).
2. Submit with every field blank (I did: run `08b8219191`).
3. Submit again with supplier `Acme Metals` (the company name as a clerk knows it), amount, currency, date (run `2003ae847f`).
Expected: The form refuses an empty bill before creating anything; the supplier field offers the known suppliers; a missing PO reference is asked for on the form; a problem is reported in words a clerk uses.
Actual: Both submissions return 303 to a run page. The empty one: `<h1>Run 08b8219191 <span class="chip crit">failed</span></h1>` ... `<b>Failed at extract-the-bill:</b> invoice has no supplier; invoice has no amount; invoice has no currency; invoice has no date`, and a permanent record BILL-0049 now exists in Finance/Payables (cancelled). The named-supplier one fails with `<b>Failed at match-the-bill-to-purchase-orders:</b> no purchase order None` and a Steps row `<span class='chip warn'>supplier</span> supplier is not on the approved list` even though Acme Metals is an approved supplier (dave's inbox shows `approved supplier Acme Metals` for locator `acme-metals`); the clerk typed the display name, the system wanted a locator, and nothing said so. The page header reads `purchase-to-pay started by alice ... pinned to release <code>b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef</code>`, the table columns are `Step | Cog | Gate | Problems | Decision`, and the Track table is raw JSON (`"manifest_digest": "7207eeb..."`, `"inputs_digest"`, `"owner_type"`).
Impact: The commonest task in the brief (file a bill) fails on first try for anyone who does not already know locator syntax, and leaves a junk record behind each time.
Suggested fix (optional): Mark required inputs, use a supplier picker (the decide forms already use selects for people), and validate before creating a record.

### F-first-time-user-3: Home tells carol she cannot start document-control; the server lets her, and the result goes live with no human seeing it
Severity: blocker
Heuristic: Nielsen 1, visibility of system status; Nielsen 4, consistency
Steps:
1. As carol (the document controller) open `/?as=carol`. "Processes you can start" has no document-control card. In "Not available to you": `document-control | Quality/Controlled Documents | you do not hold the role 'author' that starts it`.
2. POST `/start` multipart as carol with process `document-control`, title `UX test SOP change`, doc_type `sop`, change_type `minor`, body `revised`.
3. Open the redirect `/run/a61302b623?as=carol`, then `/document/rec:document:DOC-0274?as=carol`.
Expected: Either the page lets carol start a document change, or the server refuses as the page said. Whatever happens, a change to a controlled SOP is shown to a person before it becomes effective, and carol is told what happened to it.
Actual: The POST was accepted (303). The run page reads `<h1>Run a61302b623 <span class="chip ok">completed</span></h1>` ... `<div class='card'>Grant <code>e69e421e</code> decided by doc-reviewer (cog) at 2026-11-06T16:04:38+00:00</div>` and the Decision cell `approve by doc-reviewer (cog) approved within fixed criteria under document-control-sop line 34`. The document page: `<span class="chip ok">effective</span>` ... `<dt>approved by</dt><dd>doc-reviewer</dd>` ... `DOC-0274 is effective; a controlled document changes only through a new revision`. Source (`shell.py` around line 682) shows the home page hides the form when the *first step's role* is not held while policy still allows the create. Nothing told carol a "cog" approved it, what a "Grant" is, or that her one-word SOP is now the effective revision.
Impact: The document controller, the person this process exists for, is told she cannot do her job by the page and then finds the system did it without review. Trust in both the page's "allowed/not available" text and the approval trail is lost on the first use.
Suggested fix (optional): Make the home page and the server agree; after a cog approval, show the owner a plain banner ("Approved automatically by the doc-reviewer cog because the change is minor; now effective").

### F-first-time-user-4: The first item in dave's and bob's inboxes was already gone, and deciding it ends in "Not done. ... RunError" with nowhere to go
Severity: blocker
Heuristic: Nielsen 1, visibility of system status; Nielsen 9, error recovery; WCAG 3.3.1 Error Identification
Steps:
1. As dave open `/inbox?as=dave`; the first "Approve the purchase" card is run `0e98b5b362` (PR-00005). Fill `meaning` = `approved`, press Approve.
2. As bob open `/inbox?as=bob`; the first card is run `82007f4728` (PR-00004). Fill meaning `ok`, press Approve.
3. As dave, submit a decision for run `7db49e2470` with a wrong digest.
Expected: If the item changed since the page loaded, say so in plain words ("Someone else decided this at 15:58; here is the current item") with a link back to the inbox.
Actual: Step 1: HTTP 400, `<main><div class="card crit"><b>Not done.</b> run 0e98b5b362 is not waiting for a decision (status cancelled)<div class="why">RunError</div></div></main>`. Step 2: `run 82007f4728 is not waiting for a decision (status completed)`. Step 3: `the payload you saw differs from the pending payload; the item changed`. No link to the record, the inbox or the run; the class name "RunError" is shown as help text. The inbox has no "last refreshed" or ordering by due date, so the user cannot tell which of 25 (dave) or 44 (bob) cards are still live.
Impact: Any approver in a busy office hits this on the first stale card; both approvers in this study hit it on their very first click.
Suggested fix (optional): On conflict, re-render the inbox with that card marked "already decided by X / cancelled", keep the other cards, and never show internal class names.

### F-first-time-user-5: "Meaning of your signature" is a free-text riddle; blank is accepted and the system writes words the signer never typed
Severity: major
Heuristic: Nielsen 2, match between system and real world; Nielsen 5, error prevention; WCAG 3.3.2
Steps:
1. As dave, on `/inbox?as=dave`, the "Confirm the receipt" card for GR-00018 ends with `<label class="f"><span>meaning of your signature <b>*</b></span><input name="meaning" placeholder="e.g. approved for posting" size="40" required>`.
2. POST `/decide` for run `5d89958250` (PR-00008, 10,000 USD) with `meaning` = `` (empty) and verdict approve.
3. Open `/record/rec:purchase-request:PR-00008?as=dave#decisions`.
Expected: A short pick-list of meanings that fit the step (received in full / received with discrepancy; approved / approved with conditions), or at least a required value enforced on the server; what is stored is what the signer wrote.
Actual: The placeholder "approved for posting" is a finance phrase shown on a goods-receipt confirmation and on a training acknowledgement alike. The blank submission returned 303 to the record, and the Decisions table now shows `<td>dave (person)</td><td><span class="chip ">approve</span></td><td>approved approve the purchase</td>`: a meaning invented by the server. The record was moved to `<span class="chip ok">ordered</span>` and PO-0026 was issued.
Impact: Every approval requires this box; new approvers either type nonsense or leave it blank, and the signature trail the product sells ("decide at Gates with a clear signature") records text no one wrote.

### F-first-time-user-6: Nothing confirms what you just did; the only feedback is landing on a page that looks the same as before
Severity: major
Heuristic: Nielsen 1, visibility of system status; WCAG 4.1.3 Status Messages
Steps:
1. As dave, approve GR-00018 (run `f3514d9276`) with meaning `received`.
2. As alice, press Retry on run `2003ae847f` with the note blank.
3. As alice, press "Cancel the run" on run `08b8219191` with the reason blank.
4. As alice, send a DM to carol from `/inbox?as=alice` ("Send a message").
Expected: A banner after each POST: "You confirmed GR-00018 as received", "Retry failed again at the same step", "Run cancelled", "Message sent to carol".
Actual: All four return 303 to a page with no status message. Step 1 lands on `/record/rec:receipt:GR-00018?as=dave` whose header merely shows `<span class="chip ok">received</span>`; alice, the owner, receives no notification (her inbox has no GR-00018 entry; her Messages list has only cog/hub updates about POs). Step 2 lands on the record page, still `failed`, identical to before; only the Track table (eighth row onward) shows `run.retried` ... `run.failed`. Step 3 lands on `BILL-0049 ... cancelled`; the blank reason was accepted. Step 4 redirects to `/inbox?as=alice`, which contains no copy of the sent message (`grep` for the subject returns 0), so the sender cannot tell it went.
Impact: Every decision, retry, cancel and message; users double-submit or go looking for proof that something happened.

### F-first-time-user-7: You cannot find the item you created five minutes ago without already knowing its number
Severity: major
Heuristic: Nielsen 6, recognition rather than recall; Nielsen 7, flexibility and efficiency
Steps:
1. As alice, two minutes after filing BILL-0048, open `/runs?as=alice`.
2. Open `/files?as=alice&folder=Finance%2FPayables`.
3. Open `/boards?as=alice`.
4. Look for a search box on any page.
Expected: A "My recent items" list, a search field, or newest-first lists.
Actual: `/runs` is one table of 276 rows ("Runs whose record folder you may read, plus your own and those waiting for you") with no filter or search; my run was already row 43 because other people's runs are interleaved. The folder listing is oldest-first: 93 rows starting at `BILL-0001`, mine at row 75. `/boards` opens on `<h1>Boards <span class="chip">calibration</span></h1>` for a finance clerk; only after picking type `bill` does the card `<div class="kcard"><a href="/record/rec:bill:BILL-0048?as=alice">BILL-0048</a> UX test bill from new clerk` appear. No page has a search input. The Files top page is only a permissions table (`Folder | Access | Why`), not files.
Impact: Everyone, several times a day; the product claims "find their items".

### F-first-time-user-8: The home page does not say what to do first, and the one count that matters to a submitter has no link
Severity: major
Heuristic: Nielsen 6, recognition rather than recall; Nielsen 8, aesthetic and minimalist design
Steps:
1. Open `/?as=alice` and read from the top.
2. Try to click `47 of my runs waiting`.
3. As alice, after starting design-release, try to find from Home or Inbox that DSN-2026-0025 is waiting for bob.
Expected: A short "Start here" (your 2 approvals, your 35 failed items, what each means), then processes as a list of links; a page that shows my items waiting on other people.
Actual: After `Welcome, alice person` and the roles, four count cards appear; the third is `<div class="card"><b>47</b>of my runs waiting</div>` with no link while the other three link. Then eleven complete Start forms are rendered inline on the home page (27 KB of HTML), followed by a "Not available to you" table whose reasons are policy grammar (`no rule in the release lets any of ['author', 'design-engineer', 'finance-clerk', 'process-owner', 'staff'] create capa in Quality/CAPA`), then "Frames behind this release" (24 links, one listing `maintained by {{MANAGEMENT_REPRESENTATIVE}}`). The inbox sections are `Waiting for your decision`, `Failed runs of yours`, `Notifications`, `Messages`: there is no section for my items waiting on someone else, so the only route to "my design is with bob until 2026-11-16" is to find run `67c4589a70` in the 276-row Runs table.
Impact: Every person, every login; submitters cannot answer "where is my thing" without the run id.

### F-first-time-user-9: The first pages a user reaches are written in the system's own words: Gate, Cog, Track, release, digest, locator, manifest, Grant
Severity: major
Heuristic: Nielsen 2, match between system and real world
Steps:
1. Start any process; read the run page you are redirected to.
2. Open the record page; read the tab bar and the "Why" line.
3. Read an inbox card's "Routed to you by" line.
Expected: Words a clerk or quality manager uses: step, automatic check, history, policy, item.
Actual: Run page: table header `<th>Step</th><th>Cog</th><th>Gate</th><th>Problems</th><th>Decision</th>`; gate values `pass-with-problems`, `gate-pending`, `not reached`; `pinned to release <code>b7da...</code>`; `Grant <code>14937445</code> decided by quality-assistant (cog)`; a `<h2>Track</h2>` of JSON with `manifest_digest`, `payload_digest`, `owner_type`. Record page: tabs `Details Workflow Documents Links Activity Decisions Track`; `<b>Why:</b> may alice approve bill in Finance/Payables? <span class="chip crit">denied</span> no rule in the release lets any of [...]` shown unasked at the top of her own bill. Start forms: `asset (record number or locator)`, `other fields (JSON object)` with placeholder `{"lot": "L-42"}`, `lines` placeholder `locator,quantity per line, e.g. rec:item:ITEM-00001,4`, `affected_documents (comma list or JSON)`. Inbox: `<span class="chip warn">escalated 2099-01-01</span>`. Nothing on any page defines these.
Impact: Every user on every page; the brief's claim is "without reading the code or the policy grammar".

### F-first-time-user-10: "Open finance-policy at line 9" opens the wrong sentence: every policy citation is two lines off
Severity: major
Heuristic: Nielsen 4, consistency and standards; Nielsen 10, help and documentation
Steps:
1. On `/?as=alice`, the purchase-to-pay card says `allowed by finance-policy line 9: Finance clerks may create bills in Finance/Payables.`
2. On `/why?as=alice` the answer card ends with `<a href='/frame/finance-policy?as=alice#L9'>Open finance-policy at line 9</a>`; follow it.
3. Compare also carol's home (`denied by finance-policy line 15: Quality managers may not create bills`) and dave's inbox card (`finance-policy line 24: "Approvals in purchase-to-pay go to the payables reviewer."`).
Expected: The anchor lands on the quoted sentence.
Actual: On `/frame/finance-policy?as=alice`, row `L9` is `Finance clerks may create journal reversals in Finance.`; the quoted sentence is row `L11`. Row `L15` is `Controllers may approve bills in Finance/Payables.` (quoted text is `L17`); row `L24` is `Approvals in purchase-to-pay over 10,000 USD go to the controller.` (quoted text is `L26`). The "Why" feature, the only help a new user is offered, therefore points at a different, sometimes contradictory, rule.
Impact: Anyone who follows a "why" link; it teaches them that the explanations are unreliable.

### F-first-time-user-11: Confirming a goods receipt shows raw JSON in a 24-character box and makes the risky choice the default button
Severity: major
Heuristic: Nielsen 2, real-world match; Nielsen 5, error prevention
Steps:
1. As dave open `/inbox?as=dave`, card "Confirm the receipt" GR-00018.
Expected: A lines table I can correct (item, ordered, received), the over-receipt stated plainly beside it, and a neutral or secondary "Confirm" when the system itself has flagged a problem.
Actual: The editable field is `<label class="f"><span>lines (comma list or JSON)</span><input name="lines" value="[{&quot;item&quot;: &quot;rec:item:ITEM-00001&quot;, &quot;quantity&quot;: 5}]" size="24">`. The warning is tucked into muted text: `<div class="why"><b>Guard findings:</b><br>check-the-order: [warning] over-receipt: received 5 of rec:item:ITEM-00001, ordered 1</div>`, while the primary (accent-coloured) button is `<button name="verdict" value="approve">Approve</button>` and Reject is `class="secondary"`. "Guard findings" is never explained. I approved it with meaning `received`; the receipt became `received` and the over-delivery was accepted with no further prompt.
Impact: The buyer confirms every receipt this way; the one with a discrepancy looks the same as a clean one.

### F-first-time-user-12: The inbox cannot tell an approver what is actually theirs, what is urgent, or why a future "escalated" date is shown
Severity: major
Heuristic: Nielsen 1, visibility of system status; WCAG 2.4.6 Headings and Labels
Steps:
1. Open `/inbox?as=bob` (44 cards under "Waiting for your decision").
2. Read the second card (PR-00005) and the alice training card.
Expected: Cards sorted by due date, a visible reason this reached *me*, and "escalated" only for things that have escalated.
Actual: Cards are in no stated order; due dates vary (`due <b>2026-11-09</b>` mixed with none). The second card says `Routed to you by purchasing-policy line 47: "Approvals in purchase-request go to the buyer."` although bob is not the buyer (dave is) and nothing says it reached bob by escalation. The chip reads `<span class='chip warn'>escalated 2026-11-12</span>`, six days in the future; alice's card says `escalated 2099-01-01`. Bob's "Approve the purchase" cards show `<dt>risk</dt><dd><span class="chip ">high</span></dd>` with no colour (`class="chip "`), so high risk looks like any other chip.
Impact: Bob, the person with the most decisions, cannot triage 44 cards; the wrong-role text makes him doubt the item is his.

### F-first-time-user-13: Every Start form demands locators and JSON for ordinary fields
Severity: major
Heuristic: WCAG 3.3.2 Labels or Instructions; Nielsen 6, recognition rather than recall
Steps:
1. On `/?as=alice`, read the purchase-request, goods-receipt, calibration and complaint forms.
Expected: Pickers for item, supplier, PO, asset, customer, procedure; a real lines grid; no JSON.
Actual: `item <b>*</b> (record number or locator)`, `supplier (record number or locator)`, `po <b>*</b> (record number or locator)`, `asset (record number or locator)`, `customer (record number or locator)`, `procedure (record number or locator)`, `lines <b>*</b>` with placeholder `locator,quantity per line, e.g. rec:item:ITEM-00001,4`, and on all eleven forms `<label class="f"><span>other fields (JSON object)</span><textarea name="json" ... placeholder='{"lot": "L-42"}'>`. No form links to where those numbers could be looked up. The `task` form, by contrast, offers `<select name="assignee">` with the four people, proving selects are available.
Impact: Everyone who starts anything other than a task or an NCR; the pattern in F-2 (wrong supplier string) repeats for items, POs, assets and customers.

### F-first-time-user-14: A failure is reported three times and grows with each retry; the inbox count never goes down
Severity: major
Heuristic: Nielsen 8, minimalist design; Nielsen 1, system status
Steps:
1. As alice open `/inbox?as=alice` after F-1 steps.
Expected: One entry per failed item, cleared when the item is cancelled or fixed.
Actual: BILL-0048 appears under `Failed runs of yours` (a crit card with Retry/Cancel) and twice under `Notifications` (`<b>purchase-to-pay rec:bill:BILL-0048 failed at match-the-bill-to-purchase-orders</b>` once per attempt, so the plain Retry in F-6 added a second one). Home shows `35 of mine returned or failed`; nothing marks any of them as read or resolved, and the retry/cancel cards carry no date, so the newest failure sits among 34 old ones.
Impact: Submitters whose runs fail (all four roles today); the inbox becomes noise within a day.

### F-first-time-user-15: Carol's NCR closes itself before she can disposition it, and says a Gate "confirmed" a disposition no one confirmed
Severity: major
Heuristic: Nielsen 1, visibility of system status; Nielsen 3, user control and freedom
Steps:
1. As carol, POST `/start` process `nonconformance`, title `UX test NCR scratched housing`, description, severity `low`, source `inspection`.
2. Open `/run/5b792d76ee?as=carol` and `/record/rec:ncr:NCR-2026-0015?as=carol`.
Expected: The NCR opens and waits for the quality manager (carol) to disposition it, as the inbox's "Disposition the nonconformance" cards suggest; if a cog closes it, say so plainly and say what "Open corrective action" did.
Actual: Within a second the run is `completed` and the record is `<span class="chip ok">closed</span>`, with `<td>disposition</td><td>rework</td>` and `<td>disposition_rationale</td><td>Proposed by the classifier for a low-risk nonconformance; confirmed at the Gate.</td>`; the only decision is `approve by quality-assistant (cog)`. The step `Open corrective action` shows `passed` but the Links tab lists only the snapshot document, so carol cannot tell whether a CAPA now exists. No message or notification about any of this appears in her inbox.
Impact: The quality manager's core task resolves without her, with wording that implies a human confirmation.

### F-first-time-user-16: With no `?as=`, you are silently alice; the "Acting as" box mixes people with cogs and never says what a cog is
Severity: minor
Heuristic: Nielsen 1, visibility of system status; Nielsen 2, real-world match
Steps:
1. Open `http://localhost:8080/` with no query.
2. Open the top-bar select.
3. Open `/?as=zed`.
Expected: A clear "You are alice (prototype default)" or a prompt to choose; a people-only switcher; cogs explained.
Actual: `/` returns byte-identical HTML to `/?as=alice` with `<h1>Welcome, alice <span class="chip">person</span></h1>`; nothing marks it as a default. The switcher is `<label>Acting as <select name="as" onchange="this.form.submit()"><option value="alice" selected>alice</option><option value="ap-reviewer">ap-reviewer (cog)</option>...` with a `Go` button beside it; "cog" is undefined anywhere in the UI. (Positive: the roles line under the welcome does answer "who am I", and `/?as=zed` gives a clear `zed is not a member of the organization` card.)
Impact: Every session start; minor in a prototype without SSO, but it sets the tone for the vocabulary problems above.

### F-first-time-user-17: Error pages say "Not done." and expose class names and match counts
Severity: minor
Heuristic: WCAG 3.3.1 Error Identification; Nielsen 9
Steps:
1. Open `/run/nope?as=alice`, `/doc/rec:document:DOC-0274?as=carol`, `/boards?type=nonsense&as=alice`.
Expected: "We could not find run 'nope'" with a link to Runs; a consistent 404 page.
Actual: `<div class="card crit"><b>Not done.</b> run 'nope': 0 matches<div class="why">RunError</div></div>` (HTTP 400); `<b>Not done.</b> No such page.`; `<b>Not done.</b> No record type 'nonsense'.` Only the left nav offers a way out. (`/doc/` is the path the panel brief names; the pages themselves link to `/document/`.)
Impact: Occasional, but every stale link produces one.

### F-first-time-user-18: Starting a design silently sets fields the engineer never chose and lists problems with no next step
Severity: minor
Heuristic: Nielsen 1, visibility of system status; Nielsen 9
Steps:
1. As alice, POST `/start` process `design-release`, title `UX test bracket`, kind `mechanical`, change_class blank, user_needs blank.
2. Open `/run/67c4589a70?as=alice` and `/record/rec:design:DSN-2026-0025?as=alice`.
Expected: Either change_class is required on the form or the page says "set to 2 by default"; the two warnings link to where user needs or CAD files are added.
Actual: Record Details show `<td>change_class</td><td>2</td>` and `<td>risk</td><td>high</td>` with no explanation. The run page warns `the design traces to no requirement; add user needs or link accepted requirements` and `no FreeCAD or KiCad document is attached to the design record`, the risk step sits at `gate-pending`, and the run is `Waiting at Review the design for bob until 2026-11-16`; there is no control on either page to add user needs. (Positive: the "Waiting at ... for bob until ..." card is the clearest status line in the product.) Similarly the text file attached to the bill in F-2 was filed as `DOC-0273 note.txt controlled document A.0 draft`, a controlled-document revision for a scanned invoice.
Impact: Design engineers on every design; they learn their inputs were changed only by reading the record.

### F-first-time-user-19: The "Why" page and "Not available to you" explain permissions as policy grammar and role arrays
Severity: minor
Heuristic: Nielsen 2, real-world match
Steps:
1. Open `/why?as=alice`; read the default answer and the form.
2. On `/?as=dave`, read "Not available to you".
Expected: "You can file bills because you are a finance clerk" / "Only quality managers can open a CAPA; ask carol".
Actual: `/why` is a sentence-builder (`May [alice] [create] [bill] in [Finance/Payables]`) with objects like `journal-reversal` and `*`, answering `allowed by finance-policy line 9: ...` (wrong line, see F-10). Dave's table: `no rule in the release lets any of ['buyer', 'payables-reviewer', 'staff'] create capa in Quality/CAPA`, a Python list literal. Who *can* do it is never stated.
Impact: Whenever a user wonders why something is missing; frequent for new staff.

### F-first-time-user-20: Activity and Track show JSON, so a user cannot tell from the history who did what
Severity: minor
Heuristic: Nielsen 2, real-world match; WCAG 1.3.1 Info and Relationships
Steps:
1. Open `/record/rec:bill:BILL-0048?as=alice#activity` and `#track`.
Expected: "alice created the bill (amount 100 USD) at 16:04", "the matcher found no purchase order".
Actual: Activity rows such as `<td><code>record.created</code></td><td>alice</td>...<td>{"fields": {"amount": 100.0, "currency": "USD", ...}, "folder": "Finance/Payables"...` truncated mid-JSON; Track rows are `<code>step.result</code>` with a `<pre>` of `"gate": "fail", "ok": true, "payload_digest": "fa9beb..."`, where `ok: true` sits beside `gate: fail`.
Impact: Anyone trying to reconstruct what happened to their item, which after F-1, F-6 and F-14 is everyone with a failed run.

## Summary

Three changes would most improve first use. First, make a failed or problematic item fixable where the failure is shown: put the resubmit form (fields pre-filled, failing fields and the supplier/item/PO pickers highlighted) on failed runs and on records, validate required fields before a record is created, and make Retry and Cancel report what happened. Second, give every POST a plain status message and give every person a "mine" view: a banner after start/decide/retry/cancel/message, a "waiting on others" section in the inbox, newest-first and searchable lists, and an inbox that re-renders a stale card as "already decided" instead of a RunError page. Third, replace system vocabulary with office vocabulary on the pages a new employee lands on: hide Gate/Cog/Track/digest/manifest/Grant behind an "advanced" disclosure, replace "record number or locator" and JSON boxes with pickers, turn "meaning of your signature" into a per-step pick-list that is enforced server-side, fix the policy line anchors, and state in one sentence when a cog decided something and what that means.
