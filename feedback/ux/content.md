# Content lens: navigation, naming and words

Reviewer: content designer / information architect. Method: fetched every
page as alice, bob, carol, dave, ap-reviewer and the non-member `nobody` with
curl; submitted bad and blank POSTs to every action endpoint; read
`hubproto/shell.py` for strings that only appear on error paths. All quotes
are verbatim from the HTML returned on 2026-11-06 against release
`b7da4af9…`. Note for the brief: the document page route is `/document/`,
not `/doc/` (`/doc/<anything>` answers "Not done. No such page.").

## Terminology table

| Term as shown | Where it appears (exact) | What a user would call it | Proposed single name |
|---|---|---|---|
| run / process / workflow / step / op | Rail "Runs"; home "Processes you can start", "runs I started"; record tabs "Workflow"; run page "Steps"; CLI "op" | "the approval", "where my bill is" | **Process** for the definition, **Run** only inside the Activity log; show the user a *status* on the item, never a run id |
| record / item / document / files / folder | Rail "Files", "Boards"; home "47 of my runs waiting"; message "41 item(s) are waiting at a Gate"; record page "No documents attached."; files page "Records" table; admin "Record types" | "my bill", "the NCR", "the document" | **Item** for anything with a number (BILL-0001, NCR-2026-0003); **Document** for uploaded files; **Folder** for the tree |
| Gate / checkpoint / decision / approval / signature / guard | Inbox "Waiting for your decision", "approval leads to"; releases "Checkpoint"; message "waiting at a Gate"; run Steps column "Gate: pass/fail/not reached"; record tab "Decisions", "No signatures yet."; inbox "Guard findings:" | "it needs my approval", "I signed it" | **Approval** for the human stop; **Check** for an automatic stop; **Signature** only for the recorded act |
| release / policy release / Frame / profile / org model | Home "Policy release in effect: b7da4af94d3c571f", "Frames behind this release"; rail "Releases", "Frames"; conformance "profile iso9001"; error "Ask a policy owner to add you to the org model." | "the policy", "the rule book", "the SOP" | **Policy** (current set of rules); **Policy version** for a release; **Rule book** or keep the SOP's own title ("Finance policy") instead of the slug `finance-policy` |
| locator / number / reference / digest | Forms "(record number or locator)"; messages "rec:bill:BILL-0047"; URLs `/record/rec:bill:BILL-0001`; run page "pinned to release b7da…(64 chars)"; runs table "b7da4af94d3c"; home "b7da4af94d3c571f"; releases "b7da4af94d3c571fae77" | "the number", "BILL-0001" | **Number** (BILL-0001) everywhere a person reads; locators and digests only in exports and the Activity log |
| cog | "Acting as … ap-reviewer (cog)"; admin "Cog catalog"; frames "Small, clean bills may be approved by the payables reviewer cog" | "the automatic reviewer", "the bot" | **Assistant** (automatic reviewer) |
| failed / returned / rejected / cancelled / failure | Home "of mine returned or failed"; inbox "Failed runs of yours"; notifications kind "failure"; board columns "returned", "rejected", "failed", "cancelled" | "it bounced", "sent back to me" | **Needs your attention** as the inbox group; keep *Rejected* (a person said no), *Stopped* (a check failed), *Cancelled* |
| bill / invoice / purchase-to-pay | Form "Start purchase-to-pay … bill filed in Finance/Payables"; error "invoice has no supplier"; extractor "Reads a supplier invoice and proposes a bill" | "a supplier invoice" | **Supplier bill**; the button "File a supplier bill" |
| ncr / nonconformance / training / training-record / review / management-review | Board type select "ncr", "training"; Why select "nonconformance", "training-record"; home "create review in Quality/Management Review" vs process "management-review" | "an NCR", "a training sign-off" | One display name per item type, used in every select, column and sentence (e.g. **Nonconformance (NCR)**, **Training sign-off**, **Management review**) |
| meaning (of your signature) | Every Gate: "meaning of your signature *", placeholder "e.g. approved for posting" | "why I'm approving" | **Your note for the record** (required); placeholder tuned per Gate |
| Track / Activity / Detail / payload / manifest / admission | Record tabs "Activity", "Track"; detail JSON `"inputs_digest"`, `"manifest_digest"`, `"payload_digest"`, `"admission"` | "history" | One **History** tab; JSON behind a "technical details" disclosure |
| dm / email / DM / update / reply / approval | Inbox "dm/update PO-0010 issued …", "email/reply Re: [ACCT] invoice from ap-reviewer", "dm/approval Acknowledge the document: …"; send form "via DM / email" | "a message", "a notification" | **Message** with a plain subject line; channel shown as an icon or omitted |

## Findings

### F-content-1: "Fix the record (its page has the details)" sends the clerk to a page that does not contain the details
Severity: blocker
Heuristic: Nielsen 9, help users recognise, diagnose and recover from errors; WCAG 3.3.3 Error Suggestion
Steps:
1. `GET /inbox?as=alice`, section "Failed runs of yours 35".
2. Read the card for BILL-0008; follow its record link `/record/rec:bill:BILL-0008?as=alice`.
3. Read the whole record page, then open the run `/run/9a1b75ad90?as=alice`.
Expected: the inbox card, or at least the record page, names the problem ("the purchase order PO-1001 is for a different supplier; NewCo Tools is not an approved supplier") and says which field to change.
Actual: inbox card: "failed at match-the-bill-to-purchase-orders : step result has error-severity problems / Fix the record (its page has the details) and retry, or cancel the run." Record page: "The run failed at match-the-bill-to-purchase-orders: step result has error-severity problems / Fix the record and retry the run from the failed step, or cancel it." The Details table shows only `match {"po_found": true, "received": true, "supplier_approved": false, …}`. The actual reasons appear only on the run page, in the Steps table's "Problems" column: "po-supplier purchase order is for a different supplier / supplier supplier is not on the approved list". Thirteen of alice's 35 failed cards carry the identical sentence "step result has error-severity problems".
Impact: every author of a bounced item (35 items for alice today) is told the answer is on a page that does not have it; the one page that has it is two clicks away behind a hex run id. Recovery, one of the four product claims, fails for the clerk persona.
Suggested fix: carry the step's problem list into the inbox card and the record banner, in the step's own words, and name the field each problem is about.

### F-content-2: Error pages speak in Python: "Not done. run '': 238 matches RunError"
Severity: blocker
Heuristic: Nielsen 9; WCAG 3.3.1 Error Identification, 3.3.3 Error Suggestion
Steps:
1. `POST /decide?as=alice` with an empty body (what a double-submit or stale tab produces).
2. `POST /attach?as=alice` with an empty body.
3. `GET /run/nope?as=alice`; `POST /decide?as=carol` with `run=9a1b75ad90&verdict=approve&meaning=ok`.
Expected: a sentence saying what happened and what to do ("That approval has already been recorded. Go back to your inbox.").
Actual: (1) "Not done. run '': 238 matches" with the class name "RunError" under it; (2) "Not done. exists: no record '' KernelGuardError"; (3) "Not done. run 'nope': 0 matches RunError" and "Not done. run 9a1b75ad90 is not waiting for a decision (status failed) RunError". `shell.py` line 473 appends `type(e).__name__` to every kernel error, and line 481 makes the exception class the headline of a 500 ("`{type(e).__name__}:` … error id … (in the server log)"). The page title and crumb are "Not done"; a "Back" link appears only when the browser sent a Referer (`_back_link`, line 551).
Impact: anyone who hits a race, a stale tab or a bad link; "238 matches" and "KernelGuardError" are meaningless and slightly alarming to a clerk. There is no next step on any kernel error page.
Suggested fix: map each kernel error to a sentence with a verb ("This item is no longer waiting for a decision; it failed on 6 Nov. Open the item."); drop class names; always offer Home and Inbox links.

### F-content-3: Locators and digests are the identifiers users read
Severity: blocker
Heuristic: Nielsen 2, match between system and the real world; WCAG 2.4.4 Link Purpose
Steps:
1. `GET /inbox?as=alice`, section "Messages 30", message from quality-assistant.
2. `GET /?as=alice`, card "Policy release in effect".
3. `GET /run/9a1b75ad90?as=alice`, first line; `GET /runs?as=alice`, "Release" column; `GET /releases?as=alice`, "Digest" column.
Expected: items are named by their number and title ("TASK-0002 Order new label stock"); the policy version is named by something a person can say ("Policy v3, 6 Nov 2026, released by Bob").
Actual: (1) "41 item(s) are waiting at a Gate: rec:task:TASK-0002 (task), rec:task:TASK-0003 (task), rec:design:DSN-2026-0002 (design-release), rec:requirement:REQ-2026-0003 (requirements-capture), … rec:tra…" (truncated mid-locator). Notification headings: "failure purchase-to-pay rec:bill:BILL-0047 failed at match-the-bill-to-purchase-orders at 2026-11-06T15:58:22". (2) "Policy release in effect: b7da4af94d3c571f effective from 2026-11-06T14:38:17+00:00, released by bob". (3) The same digest is shown at 12 characters on `/runs` ("b7da4af94d3c"), 16 on home, 20 on `/releases` ("b7da4af94d3c571fae77"), 64 on the run page ("pinned to release b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef") and on `/conformance`. Run ids are 10 characters in headings ("Run 9a1b75ad90") and 8 in tables ("9a1b75ad"). Form hints say "(record number or locator)" and link targets say "record number or locator".
Impact: every reader of a message or a release reference; a finance clerk cannot say which policy version applied to a bill, and cannot tell two truncations of one digest are the same thing. The locator prefix `rec:bill:` is pure noise for a person.
Suggested fix: numbers and titles in all prose and links; give releases a sequence number and date; keep digests and locators in the Activity log and exports only.

### F-content-4: The navigation rail is the engineer's menu, shown unchanged to every role
Severity: major
Heuristic: Nielsen 2; Nielsen 8, aesthetic and minimalist design; Nielsen 6, recognition rather than recall
Steps:
1. `GET /?as=alice` (finance clerk) and `GET /?as=dave` (buyer); read the rail.
2. `GET /?as=nobody`.
Expected: a clerk sees Home, Inbox, Files, maybe Boards; policy and admin pages appear for policy owners; labels say what the page is for.
Actual: every principal, including the non-member, sees "Home Inbox Boards Files Runs Releases Frames Why Conformance Admin". "Releases" is a policy-version list (page title "Policy releases"), not product releases. "Frames" is a list of SOP files by slug. "Why" is a permission oracle whose page heading is "Why?". "Conformance" is an ISO clause map. "Admin" lists cogs and record-type schemas and is fully readable as alice. "Runs" is explained only on its own page: "Runs whose record folder you may read, plus your own and those waiting for you." None of the labels carry a tooltip or a one-line description on the page they lead to. The non-member rail is identical (with "no folders" under it).
Impact: all four personas on every page; the rail teaches the system's internals (frames, runs, releases) before it teaches the user's job (bills, NCRs, approvals). A clerk has six of ten entries she will never need.
Suggested fix: rail by role; rename "Releases" to "Policy versions", "Frames" to "Policies and SOPs", "Why" to "Check a permission", "Runs" to "In progress"; move Admin and Conformance under a "Policy owner" heading shown only to bob and carol.

### F-content-5: Each process has three names (process slug, record-type slug, folder) and the UI switches between them
Severity: major
Heuristic: Nielsen 4, consistency and standards; Nielsen 2
Steps:
1. `GET /?as=alice`, "Processes you can start": read a few headers and buttons.
2. `GET /boards?as=alice`: read the "type" select. `GET /why?as=alice`: read the type select.
3. `GET /?as=dave`, table "Not available to you".
Expected: one display name per item type ("Nonconformance (NCR)") used in every heading, select, column and sentence.
Actual: home headers read "purchase-to-pay bill filed in Finance/Payables", "nonconformance ncr filed in Quality/Nonconformances", "training-acknowledgement training-record …" (process slug, then type slug, then folder), with buttons "Start purchase-to-pay", "Start requirements-capture", "Start document-control". The boards select says "ncr" and "training"; the Why select says "nonconformance" and "training-record" for the same things. Dave's "Not available to you" says "create review in Quality/Management Review" for the process "management-review". Inbox cards head with "Acknowledge the document · training-acknowledgement · TRN-00009". The blank-bill run failure calls the item an "invoice" ("invoice has no supplier; invoice has no amount") while the form called it a "bill".
Impact: every person choosing a filter, reading an inbox card or reading a denial; the clerk must learn that bill, purchase-to-pay and Finance/Payables are one thing and that ncr, nonconformance and Nonconformances are one thing.
Suggested fix: a single display-name table per type and per process, used by every select, heading and sentence; buttons phrased as the user's act ("File a supplier bill", "Report a nonconformance").

### F-content-6: The human stop is called Gate, checkpoint, decision, approval, signature and guard
Severity: major
Heuristic: Nielsen 4; WCAG 3.1.3 Unusual Words
Steps:
1. `GET /inbox?as=bob`: heading, card footer, "Guard findings:" block, the form label.
2. `GET /releases?as=bob`: "Routing in effect" column headers.
3. `GET /run/9a1b75ad90?as=alice`: Steps table column "Gate" with values "passed", "fail", "not reached". `GET /record/rec:bill:BILL-0008?as=alice`: tabs "Decisions" and empty text "No signatures yet."
Expected: one word for the thing a person does ("Approval") and one for the automatic check.
Actual: "Waiting for your decision 2", "approval leads to acknowledged", "Guard findings: check-the-supplier: [warning] supplier: …", "meaning of your signature *", releases column "Checkpoint", message "41 item(s) are waiting at a Gate", run Steps column "Gate: passed / fail / not reached" (where the step had no human gate at all), record tab "Decisions" over the table "No signatures yet.", admin record-types column "Decision fields". The capitalised "Gate" appears only in messages and the noop cog description ("Carries a Gate when two checkpoints follow each other").
Impact: reviewers (bob 44 items, dave 25, carol 13) and policy owners; the product claim "decide at Gates with a clear signature" is undermined by the word Gate meaning both a human approval and an automatic pass/fail in the same run table.
Suggested fix: "Approval" for the human step everywhere a person reads; "Check" for automatic steps; rename the run Steps column to "Result" with "passed / stopped / not reached".

### F-content-7: Every "why" is a Python list repr, not a sentence
Severity: major
Heuristic: Nielsen 2; WCAG 3.3.3
Steps:
1. `GET /?as=dave`, table "Not available to you", column "Why".
2. `GET /files?as=carol&folder=Finance%2FPayables`.
3. `GET /record/rec:bill:BILL-0008?as=alice`, line starting "Why:".
Expected: "Only controllers and payables reviewers may approve bills. Your roles: buyer, payables reviewer." with the rule line linked.
Actual: "no rule in the release lets any of ['buyer', 'payables-reviewer', 'staff'] create capa in Quality/CAPA" (nine times on dave's home); "You cannot read this folder. no rule in the release lets any of ['document-controller', 'policy-owner', 'quality-manager', 'reviewer', 'staff'] read * in Finance/Payables" (note "read *"); the hold refusal "A hold needs an approve permission: no rule in the release lets any of [...] approve bill in Finance/Payables". The positive form is better but still doubled: "Why: may bob approve purchase-request in Purchasing/Requests? allowed allowed by purchasing-policy line 33: …" ("allowed allowed").
Impact: every denial a person sees; a clerk reads square brackets and quotes and the wildcard `*`. None of the denials say who *can* do it or whom to ask.
Suggested fix: one template: "<Roles> may <verb> <type> in <folder>. You are <roles>. Ask <policy owner> to change <frame> line N." with the frame line linked as the inbox already does (`/frame/training-sop#L22`).

### F-content-8: A record's headline "Why:" line answers a question the reader did not ask
Severity: major
Heuristic: Nielsen 2; Nielsen 8
Steps:
1. `GET /record/rec:bill:BILL-0008?as=alice` (alice is the owner; the item failed).
Expected: the header tells alice the item's status and what she can do (fix fields, retry, cancel).
Actual: the third line of the page, before the tabs, is "Why: may alice approve bill in Finance/Payables? denied no rule in the release lets any of ['author', 'design-engineer', 'finance-clerk', 'process-owner', 'staff'] approve bill in Finance/Payables". Alice is not trying to approve her own bill; the line reads as a refusal on a page where her real next step (Retry) is lower down.
Impact: every owner of every item; the most prominent line on the record is a denial of an action nobody attempted.
Suggested fix: show the permission line only when a person tries the action, or phrase it as capabilities: "You can: edit, retry, cancel. Approval is for controllers and payables reviewers."

### F-content-9: Forms expose field slugs, JSON hints and a one-size placeholder for the signature
Severity: major
Heuristic: Nielsen 2; WCAG 3.3.2 Labels or Instructions
Steps:
1. `GET /?as=alice`, Start forms for change-control, purchase-request, purchase-to-pay.
2. `GET /inbox?as=bob`, first card, text after "You may also set:" and the signature field.
3. `GET /inbox?as=alice`, card "Complete the task".
Expected: labels in words ("Affected documents", "Needed by", "Unit cost"); no JSON on a clerk's form; a signature prompt that fits the decision.
Actual: labels "affected_documents (comma list or JSON)", "training_roles (comma list or JSON)", "needed_by", "unit_cost", "po_ref (record number or locator)", "doc_type", "change_class", and on every form "other fields (JSON object)". Inbox: "You may also set: needed_by, reason, supplier, unit_cost" and for the NCR "closed needs: disposition, disposition_rationale". The signature field is "meaning of your signature *" with placeholder "e.g. approved for posting" on all 84 waiting cards, including "Complete the task" for [OPS] Order new label stock and "Acknowledge the document", where "approved for posting" is wrong. Buttons are "Approve" / "Reject" even on an acknowledgement and a task.
Impact: every form on home and every Gate; the home page alone shows ~100 raw field names.
Suggested fix: a label map per field; drop "other fields (JSON object)" from the clerk view; per-Gate signature prompts ("I have read this document", "Task done: what was ordered?"); button text from the step name ("Acknowledge", "Mark done").

### F-content-10: Money, quantities and dates are printed as floats and ISO timestamps, and one date is truncated by the system itself
Severity: major
Heuristic: Nielsen 2; Nielsen 4
Steps:
1. `GET /inbox?as=bob`, first three cards: "unit cost", "amount", "quantity".
2. `GET /releases?as=bob`, table "Thresholds". `GET /frame/purchasing-policy?as=bob` line 45 (quoted in inbox).
3. `GET /inbox?as=alice`, failed cards for BILL-0014, BILL-0021, TRN-00009 card footer.
4. `GET /?as=alice`, "Policy release in effect"; `GET /files?as=alice&folder=Finance%2FPayables`, "Updated" column; `GET /record/rec:purchase-request:PR-00004?as=bob`, Decisions "At".
Expected: "USD 20,000.00", "200,000 ea", "6 Nov 2026 14:38", one date style everywhere.
Actual: "unit cost 0.1", "amount 20000.0 USD", "0.06 USD", "10000.01 USD", "quantity 200000 ea"; thresholds "cog-purchase-limit 500.0 USD", "controller-approval-threshold 10000.0 USD" while the policy prose next to them says "over 10,000 USD"; failure text "balanced-journal: debits 0.0 do not equal credits 0.0"; "period-open: period 06/11/2 is not open" (a dd/mm/yyyy input cut to seven characters by the period check); "escalated 2099-01-01" shown as a real escalation date on TRN-00009. Dates appear as "2026-11-06T14:38:17+00:00" (home), "2026-11-06 14:43:32" (files), "2026-11-06T16:04:19" (decisions), "2026-11-06" (due), and the "Updated" column is built by `updated_of` slicing `[:19]` so the zone is silently dropped on one page and kept on another.
Impact: controllers deciding on amounts near a 10,000 threshold ("10000.01 USD"); anyone comparing timestamps across pages.
Suggested fix: one money formatter (currency, thousands, two decimals), one quantity formatter, one date formatter with a short style for tables and a long style for headers; never echo a half-parsed value in an error.

### F-content-11: The home page is a form dump, not a front door
Severity: major
Heuristic: Nielsen 8; Nielsen 6; WCAG 2.4.6 Headings and Labels
Steps:
1. `GET /?as=alice`; count forms and read the headings in order.
2. Read the count cards; try the third card.
3. Scroll to the end.
Expected: "Welcome, Alice", a short list of things to do today, a short list of things to start (collapsed), and nothing about policy internals.
Actual: heading "Welcome, alice person" (the principal type leaks); four count cards "2 approvals waiting for me", "35 of mine returned or failed", "47 of my runs waiting" (the only card that is not a link, `shell.py` line 671), "199 runs I started"; then "Processes you can start" renders all eleven Start forms fully expanded (every field, every select, a file input and "other fields (JSON object)" on each) before "Not available to you" and a list "Frames behind this release" of 24 slug-version lines including "quality-manual v0.1.0 (company, maintained by {{MANAGEMENT_REPRESENTATIVE}})", an unexpanded template placeholder. Each form header cites its permission line ("allowed by calibration-sop line 13: Staff may create calibrations in Quality/Calibration.") whether or not the user asked why.
Impact: every login; a clerk filing one bill scrolls past ten other processes' forms; the "waiting" count she most wants to click is the one that is not a link.
Suggested fix: cards first, one line per startable process with a "Start" that expands or navigates, policy provenance behind a "Why can I do this?" disclosure, and drop "Frames behind this release" from a non-policy-owner's home.

### F-content-12: Notifications and messages read as log lines, not as something a colleague wrote
Severity: major
Heuristic: Nielsen 2; Nielsen 1, visibility of system status
Steps:
1. `GET /inbox?as=alice`, sections "Notifications 6" and "Messages 30".
2. Read the "Send a message" form at the foot.
Expected: "PO-0010 was issued for your request PR-00011 (10 × RC0603FR-0710KL from Acme Metals, USD 0.20). Open the order." with a sender and a time in words.
Actual: "dm/update PO-0010 issued for PR-00011 from hub at 2026-11-06T14:47:12 / Ordered 10 x RC0603FR-0710KL from acme-metals (0.2 USD). open run · open record"; "failure purchase-to-pay rec:bill:BILL-0047 failed at match-the-bill-to-purchase-orders at 2026-11-06T15:58:22"; "email/reply Re: [ACCT] invoice from ap-reviewer at 2026-11-06T14:48:36 / Filed acct_inv.json as rec:bill:BILL-0029 (run a70d2e7702)."; "dm/approval Acknowledge the document: training-acknowledgement rec:training:TRN-00009 from hub". The kind prefix "dm/update", "email/reply", "dm/approval" is the channel-and-type key. The sender is "hub". The reply form asks "via DM / email", with recipients labelled "(cog)". Supplier is shown by slug "acme-metals" in messages but by name "Acme Metals" on the same page's cards.
Impact: everyone, thirty times on alice's inbox; nothing distinguishes a message from a person (carol) from an automatic update, and nothing is phrased as a request.
Suggested fix: subject-line templates per event ("Order PO-0010 issued", "Bill BILL-0047 stopped: amount exceeds PO-0024"), sender shown as a name with "(automatic)" where relevant, relative times, and the channel as an icon.

### F-content-13: Breadcrumbs stop at "Hub"; there is no path back to folder, process or inbox
Severity: major
Heuristic: WCAG 2.4.8 Location; Nielsen 3, user control and freedom
Steps:
1. Open `/record/rec:bill:BILL-0008?as=alice`, `/run/9a1b75ad90?as=alice`, `/frame/finance-policy?as=alice`, `/files?as=alice&folder=Finance%2FPayables`; read the top bar.
2. From the inbox, open a record, then try to return without the browser button.
3. Open `/record/rec:bill:NOPE-1?as=alice` with no Referer.
Expected: "Hub › Files › Finance › Payables › BILL-0008"; a run shows its item; errors show a way out.
Actual: crumbs are "Hub BILL-0008", "Hub Run 9a1b75ad90", "Hub finance-policy", "Hub Finance/Payables", "Hub Not done". The record page's only way to its folder is the small subtitle link "folder Finance/Payables"; the run page has no link to the inbox the user came from; the frame page is titled by slug. The error page has no "Back" at all without a Referer and offers no Home or Inbox link beyond the rail.
Impact: anyone who arrives from a message link or a bookmark; policy owners landing on `/frame/<slug>#L22` cannot see which policy family it belongs to.
Suggested fix: real breadcrumbs (folder path for items, item for runs, "Policies" for frames) and a "Back to inbox" link on every page reached from an inbox card.

### F-content-14: Boards opens on "calibration" with no explanation, labels its saved filter with a URL, and names columns by state slug
Severity: major
Heuristic: Nielsen 1; Nielsen 2; WCAG 2.4.4
Steps:
1. `GET /boards?as=alice` (rail link, no type).
2. Read the line under the Filter button. Read the column headers.
3. `GET /boards?as=alice&type=journal-reversal`.
Expected: a board of the user's own items, or a prompt to choose a type; a saved-filter control with a name; columns titled in words; an empty board that says so.
Actual: heading "Boards calibration" (the first type in a non-alphabetical list: "task, document, bill, journal, ncr, capa, …"); "Saved filter: /boards?as=alice&type=calibration" where the link text is the raw URL; columns "open 0", "awaiting-review-the-calibration 0", "closed 2", "rejected 0", "returned 0", "failed 0", "cancelled 0". The journal-reversal board shows only "open 0 / returned 0 / failed 0 / rejected 0 / cancelled 0" with no sentence; the state select for one type lists "awaiting-review-the-calibration" as an option.
Impact: any clerk clicking Boards; the slug "awaiting-review-the-calibration" is the step name with hyphens, not a column name.
Suggested fix: default to "my items" across types or to the user's main type by role; "Copy link to this filter" instead of a URL as text; column titles from the step's display name ("Awaiting review"); an empty-board sentence ("No journal reversals yet. Finance clerks can create one from Home.").

### F-content-15: The releases page is a diff view for engineers; a policy owner cannot see what changed, when, or do anything
Severity: major
Heuristic: Nielsen 2; Nielsen 6; WCAG 3.1.3
Steps:
1. `GET /releases?as=bob` (bob is a policy owner and released the current release).
2. Read the first table, then "Routing in effect", then "Thresholds".
Expected: a list of policy versions ("v3, effective 6 Nov 2026, released by Bob: finance-policy 1.2.0 raised the controller threshold"), a readable routing summary ("Purchase requests over USD 10,000 go to the controller"), and links from each rule to the policy text.
Actual: title "Policy releases"; columns "Digest / Effective from / Released by / Parent / Frames"; one row "● b7da4af94d3c571fae77 / 2026-11-06T14:38:17+00:00 / bob / - / org-model@1.1.0, design-control-sop@1.0.0, … purchasing-policy@1.0.0" (24 slug@version pairs in one cell; the "●" has no legend; "Parent -" is unexplained). "Routing in effect" columns "Process / Checkpoint / Condition / Goes to / Source" with rows like "calibration / * / overdue == True / role:management-representative / calibration-sop:19" and "capa / verify-the-actions / default / field:owner / capa-sop:23"; the Source cell is plain text, unlike the inbox which links "training-sop line 22". "Thresholds" rows "cog-purchase-limit 500.0 USD", "design-rule-error-limit 0.0 percent". There is no button; the page has only the "Go" of the identity switcher.
Impact: bob and carol, the two people who own policy; the page cannot answer "what did I change last time" or "who approves what", and gives no way to propose or release a change.
Suggested fix: version number and date as the row identity, a "changes since parent" column, routing rendered as sentences with the frame line linked, thresholds with formatted values and the sentence they come from.

### F-content-16: The admin page is a kernel catalog and is shown identically to a finance clerk
Severity: major
Heuristic: Nielsen 2; Nielsen 8; WCAG 3.1.3
Steps:
1. `GET /admin?as=bob` and `GET /admin?as=alice`; compare.
2. Read "Cog catalog" columns and three descriptions; read "Record types".
Expected: a page a policy owner can use: who is a member, which roles they hold, which automatic reviewers exist and what they may approve; hidden or read-only for a clerk.
Actual: the two pages are byte-for-byte the same tables. "Cog catalog" columns "Cog / Version / Kind / Reaches / Tasks / Description" with values "code", "context", "kernel:records", "kernel:documents, worker:cad", "run", "review, inbox"; descriptions such as "Writes the step's proposed fields onto the run's record under the owner's admission." and "Carries a Gate when two checkpoints follow each other; passes the prior payload through." "Members" has a "Source" column "org-model:23". "Record types" has columns "Prefix / Object / Fields / Required by state / System fields / Decision fields / Retention" with cells like "effective: title, owner, revision, review_date; in-review: title, owner" and a 19-slug field list for document.
Impact: policy owners get no actions and no plain-language explanation; clerks get a page that explains internals they should never need; the word "Admin" promises management, the page delivers a schema dump.
Suggested fix: split into "People and roles" (members with role names in words and an "add member" path, even if it links to the org-model frame line) and "Automatic reviewers" (name, what it approves, its limit from thresholds); move cog kinds, reaches and record schemas to a developer page.

### F-content-17: The conformance page prints raw JSON to the quality manager
Severity: major
Heuristic: Nielsen 2; WCAG 1.3.1 Info and Relationships
Steps:
1. `GET /conformance?as=carol`.
Expected: a clause-by-clause table ("7.1.5 Monitoring and measuring resources: covered by Calibration SOP"; "8.5.1 Control of production: not covered") with links to the policies.
Actual: a two-column "item / value" table whose rows are `covered {"10.1": ["quality-manual"], "10.2": ["capa-sop", "ncr-sop"], …}`, `uncovered ["8.2", "8.2.2", …]`, `titles {"10.1": "Improvement: general", …}`, `release b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef`, `profile iso9001`. The clause titles exist in the data but are never joined to the clause numbers in the display.
Impact: carol (quality manager) and bob (management representative), before an audit; the one page that maps policy to the standard is unreadable without a JSON parser.
Suggested fix: one row per clause: number, title, status chip, covering policies linked to `/frame/<name>`.

### F-content-18: Record history is two tabs of JSON with system vocabulary
Severity: minor
Heuristic: Nielsen 2; Nielsen 8
Steps:
1. `GET /record/rec:bill:BILL-0008?as=alice`, tabs "Activity" and "Track".
Expected: one "History" list: "6 Nov 14:44 Alice filed the bill (amount USD 1,500.00, supplier NewCo Tools)".
Actual: "Activity" rows "record.created / alice / 9a1b75ad / {"fields": {"amount": 1500.0, "currency": "USD", … "folder": "Finance/Payab" (cut by `short()` mid-word); "record.fields_set / three-way-matcher …". "Track" repeats the run's events with `"inputs_digest"`, `"manifest_digest"`, `"payload_digest"`, `"admission"`, `"owner_type": "person"`, `"parent_run": null`. The same Track table is shown again on the run page. The Decisions table shows "Signature 5236f60133cd2a6219652118ccd60a9b" and "Payload 1cfa81b8b338".
Impact: anyone auditing an item; the auditor persona (bob) has to read JSON to learn who changed what.
Suggested fix: event sentences from a template per event kind; digests behind a "verify" disclosure.

### F-content-19: Record header chips and link relations are slugs run together
Severity: minor
Heuristic: Nielsen 2; WCAG 1.3.1
Steps:
1. `GET /record/rec:bill:BILL-0008?as=alice`, second line and the "Links" form; the hold control.
Expected: "Retention: financial reporting · Sensitivity: internal"; relation names in words; a hold control that says who may hold.
Actual: "retention: financial-reporting sensitivity: internal · owner alice · assignees - · due - · process purchase-to-pay"; "Link as concerns / caused / fulfils / settles / supersedes / custodian-of / evidence-for / has-document / part-of / defines / realized-by / affects / trains-on / assigned-to / references / defined-by / produces"; buttons "Render snapshot" and "Put on hold" with the note "Holds need an approve permission here."
Impact: every record page; "Render snapshot" means nothing to a clerk ("Save a PDF of this item" would).
Suggested fix: label map for chips and relations; "Save a snapshot (PDF)"; "Only approvers can put this on hold."

### F-content-20: A blank Start is accepted, produces a failed run and an item titled by its own number
Severity: minor
Heuristic: Nielsen 5, error prevention; WCAG 3.3.1
Steps:
1. `POST /start?as=alice` with body `process=purchase-to-pay` and no other field (the purchase-to-pay form marks no field required).
2. Follow the 303 to `/run/bd3fa8d77c?as=alice`.
Expected: the form refuses to submit without supplier, amount, currency and date, naming them.
Actual: the run is created and immediately "failed": "Failed at extract-the-bill: invoice has no supplier; invoice has no amount; invoice has no currency; invoice has no date"; the record is "BILL-0050 BILL-0050" (title defaults to the number); the home counter "of mine returned or failed" goes up by one and the item now needs a Retry or a Cancel with a "reason". The same gap puts "DSN-2026-0005 DSN-2026-0005" and "REQ-2026-0010 REQ-2026-0010" in alice's failed list.
Impact: clerks who submit early; each empty submission becomes a failed item to clean up, and the failure names an "invoice" where the form said "bill".
Suggested fix: mark required fields on the form from the step's required-field rule, validate before creating a run, and use the same noun as the form.

## Summary

The three changes with the best payoff. First, put the problem where the person is: carry each failed step's problem list (and the field it concerns) into the inbox card and the record banner, replace the kernel's "Not done … RunError" pages with sentences that say what to do next, and render every "why" as "Who may, you are, ask whom" instead of a Python list. Second, adopt one display name per thing and use it everywhere: item number and title instead of `rec:bill:` locators and hex digests, "Approval" instead of Gate/checkpoint/decision/signature/guard, "Supplier bill" instead of bill/invoice/purchase-to-pay, and a policy version number instead of five truncations of one digest. Third, rebuild the front door and rail by role: a home page of cards and one-line "Start" entries with policy provenance behind a disclosure, and a rail where a clerk sees Home, Inbox, Files and Boards while Policy versions, Policies, Conformance and People and roles sit under a policy-owner heading, with the releases and admin pages rewritten as sentences a policy owner can read and act on.
