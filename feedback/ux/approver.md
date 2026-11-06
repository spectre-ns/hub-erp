# Approver lens: decision throughput and decision confidence

Reviewer persona: bob (controller, design authority, policy owner) and dave
(payables reviewer, buyer), 17:30 on a Friday, inbox of 51 (bob) and 26 (dave)
items. All evidence is from `curl`/urllib against `http://localhost:8080` on
2026-11-06; HTML quoted verbatim. Items I created: BILL-0063 (11,500 USD, bob),
BILL-0064 (8,000 USD, dave), BILL-0068 (2,100 USD, dave), PR-00063 (12,000 USD,
bob), DSN-2026-0026 with REQ-2026-0034/0035 (bob), NCR-2026-0016/0023/0024
(bob), DOC-0282 (bob and carol), plus six bills that failed matching against
PO-1002/PO-1003 and sit in alice's "Failed runs".

Throughput baseline measured: one approval = load `/inbox` (0.19–0.26 s,
77–153 KB, every card fully expanded), find the card by scrolling or Ctrl-F
(no anchors, filters or collapse), type a free-text "meaning", click Approve,
land on the record page, click Inbox, reload the whole list. Two clicks and one
mandatory sentence per decision is cheap; everything around it is not.

### F-approver-1: An approval signs a payload that no longer matches the record
Severity: blocker
Heuristic: Visibility of system status; error prevention (ISO 9241-110 conformity with expectations)
Steps:
1. As dave, GET `/inbox?as=dave`; copy the hidden digest of the BILL-0068 card (`<input type="hidden" name="digest" value="36bf1f07a9b7…">`).
2. As alice, POST `/attach` (multipart) to `rec:bill:BILL-0068` with a file "Credit note CN-77" whose text says the amount is corrected to 1,900.00 USD. `/record/rec:bill:BILL-0068` now lists `DOC-0309 | Credit note CN-77 | external | received`.
3. As dave, POST `/decide` with `run=ec20003e40`, the digest from step 1, `meaning=approved for posting`, `verdict=approve`.
Expected: refused with "the item changed" (the hub has this message: a wrong digest yields `the payload you saw differs from the pending payload; the item changed`), or at least the card re-rendered with the new document.
Actual: `HTTP 303 /record/rec:bill:BILL-0068?as=dave`; record header reads `BILL-0068 bill posted`. The digest only covers the cog envelope captured at routing time (`pending["payload_digest"]`), not the record's fields, documents or links, so anything the preparer or a cog changes after routing is invisible to the signature check.
Impact: every approver, on every item that is edited, annotated or re-matched while it waits. A signer can post a bill at 2,100 USD when the file on the record says 1,900 USD.
Suggested fix: fold `record_digest` (already stored on the decision) into the check and refuse when it differs; show "changed since routed: documents +1" on the card.

### F-approver-2: The approver can silently re-price the item at signature time
Severity: blocker
Heuristic: Match between system and the real world; segregation of duties as stated in purchasing-policy
Steps:
1. As alice, start purchase-request "1000 x 10k resistor for line 2", quantity 1000, unit_cost 12 (amount 12,000 USD -> routed to bob by line 45 "over 10,000 USD go to the controller").
2. As bob, GET `/inbox?as=bob`; the PR-00063 card shows `You may also set: needed_by, reason, supplier, unit_cost` and a prefilled `<input type="number" step="any" name="unit_cost" value="12.0">` and `<input name="supplier" value="acme-metals">`.
3. POST `/decide` with the card's digest, `verdict=approve`, `meaning=approved`, `unit_cost=1`.
Expected: refused (the preparer's price is not the approver's to change), or re-routed, or at minimum flagged to the preparer.
Actual: `303`; record now shows `amount | 1000.0`, `unit_cost | 1.0`, `po | rec:purchase-order:PO-0027`; Decisions row `bob (person) | approve | approved | … | 9b8f562a6716` carries the digest of the 12,000 USD payload; alice's inbox only gets `dm/update PO-0027 issued for PR-00063`. The signature attests one amount, the order goes out at another, and the item left the controller band it was routed into.
Impact: controllers and buyers on every purchase-request; auditors reading the decision later cannot reconcile the signed digest with the ordered amount.
Suggested fix: decision inputs should be limited to fields the next state is *missing* (the code comment already says "never rewrites what is there"); render existing values read-only.

### F-approver-3: Reject has no reason field; an empty "meaning" produces "rejectd approve the bill" for the submitter
Severity: blocker
Heuristic: Help users recognize, diagnose and recover from errors; error prevention
Steps:
1. As bob, GET `/inbox?as=bob`, card BILL-0063. The only free-text control is `<input name="meaning" placeholder="e.g. approved for posting" size="40" required>` followed by `<button name="verdict" value="approve">Approve</button> <button class="secondary" name="verdict" value="reject">Reject</button>`.
2. POST `/decide` with `verdict=reject`, `meaning=` (empty; the `required` attribute is client-side only).
Expected: a rejection needs a reason, distinct from the meaning of an approval signature, and the server should insist on it.
Actual: `303`; Decisions row: `bob (person) | reject | rejectd approve the bill`. Alice's inbox notification: `rejection purchase-to-pay rec:bill:BILL-0063 rejected at 2026-11-06T16:10:04 bob rejected it: rejectd approve the bill`. Same with approve: an empty meaning on NCR-2026-0023 was recorded as `approved disposition the nonconformance`, i.e. the hub fabricates the sentence the signer "meant".
Impact: every rejection; submitters receive a typo as the reason, and the audit trail records a meaning the signer never wrote.
Suggested fix: separate "reason for rejection" (required on reject, shown to the submitter) from the approval statement; refuse an empty meaning server-side (`meaning_given` is already tracked).

### F-approver-4: A design rejection with a written reason reaches nobody
Severity: blocker
Heuristic: Visibility of system status; closure of the feedback loop
Steps:
1. As bob, POST `/decide` on run `02e3a48208` (DSN-2026-0026) with `verdict=reject`, `meaning=TVS D3 is not from an approved manufacturer; swap to an AVL part and resubmit`.
2. As alice, GET `/inbox?as=alice`.
Expected: a "Returned to you for rework" card with the reason, or at least a rejection notification.
Actual: `grep -c DSN-2026-0026` on alice's inbox returns 0; no "Returned" section, no notification. `/record/rec:design:DSN-2026-0026?as=alice` reads `awaiting-submit-the-design · assignees - · due -`; `/run/02e3a48208` is `rejected`, `"to": "awaiting-submit-the-design"`. The reason exists only in the run track. (Bills rejected to the terminal `rejected` state at least produce a notification, but offer no resubmit; see F-approver-3.)
Impact: every design-release rejection; the designer never learns the item came back, the approver assumes it is being reworked.
Suggested fix: on `on_reject` that rewinds to a prior gate, assign the owner, put it in "Returned to you for rework" with the reason, and notify.

### F-approver-5: Bills have no due date, never go overdue, and sort to the bottom of the inbox
Severity: major
Heuristic: Visibility of system status; consistency with the policy the user is shown
Steps:
1. GET `/frame/finance-policy?as=bob`: line 28 `Approve the bill in purchase-to-pay is due within 5 days.`, line 29 `Overdue items in purchase-to-pay are reported to the policy owner.`
2. GET `/inbox?as=bob`; find the BILL-0063 card.
Expected: `due 2026-11-11`, overdue styling after that, escalation to the policy owner, and a position near the top like every other dated item.
Actual: card header is `run <a …>8e4c31f216</a> started by alice` with no due; the record page transition shows `"due": null`; the Workflow table row `human: Approve the bill` carries no SLA while a purchase request shows `human: Approve the purchase (SLA 3 days)`. The inbox sorts by `(overdue, due or "9999", created)`, so the 11,500 USD bill is card 51 of 51, after training acknowledgements, tasks and a December audit.
Impact: controller and payables reviewer on every bill; the highest-value items are the easiest to miss and the only ones the SLA never protects.

### F-approver-6: The card quotes the wrong policy sentence for escalated items
Severity: major
Heuristic: Match between system and the real world; trust in explanations
Steps:
1. As bob (not a buyer), GET `/inbox?as=bob`, card PR-00005.
Expected: "Routed to you by purchasing-policy line 62: Overdue items in purchase-request go to the controller."
Actual: `Routed to you by <a href="/frame/purchasing-policy?as=bob#L47">purchasing-policy line 47</a>: "Approvals in purchase-request go to the buyer." · approval leads to ordered … <span class='chip warn'>escalated 2026-11-12</span>`. The same sentence is shown to dave, who is the buyer. The chip also shows a date in the future (another card: `escalated 2099-01-01`) against `due 2026-11-09` on a day that is 2026-11-06.
Impact: bob cannot tell whether he is the deciding controller or a courtesy copy, nor why an item that is not yet due is "escalated".

### F-approver-7: Co-assignees are invisible; one person's decision silently removes the other's card
Severity: major
Heuristic: Visibility of system status; coordination (who else holds this)
Steps:
1. Start document-control "WI-014 Soldering rework" as alice; it routes by line 29 "go to the reviewer" (bob and carol).
2. GET `/inbox?as=bob`: the DOC-0282 card shows no assignee list. `/record/rec:document:DOC-0282` knows (`pending: assigned to bob; role …`), the card does not.
3. As carol, POST `/decide` approve on run `e6ee4b0a0f`.
4. GET `/inbox?as=bob`.
Expected: the card says "also assigned to carol"; after carol decides, bob sees "carol approved this" rather than nothing.
Actual: the card is gone; the only mention of DOC-0282 left in bob's inbox is a new "Acknowledge the document … Read WI-014 Soldering rework rev A" card. Nothing tells bob who decided or why his item vanished. The same holds for the 13 purchase-request cards that bob and dave share after escalation: two people can both type a meaning and race.
Impact: every shared or escalated item (NCRs to quality manager + management representative, documents to "the reviewer", escalations).

### F-approver-8: Two identical items look identical, and nothing says so
Severity: major
Heuristic: Error prevention; recognition rather than recall
Steps:
1. As carol, start nonconformance "Bent pins on connector J4", severity medium, lot C-9, quantity 3, twice.
2. GET `/inbox?as=bob`.
Expected: a "possible duplicate of NCR-2026-0023" note on the second card (the hub already detects over-billing against a PO, so it can compare).
Actual: two cards whose only difference is the number and run id: `NCR-2026-0023 Bent pins on connector J4 · run 623e67c415 started by carol · due 2026-11-11` and `NCR-2026-0024 … run ff28996b46`; same title, description, source, severity, lot, quantity, risk, routing sentence. Bob also has `TASK-0005 HOSTILE dup-click task` and `TASK-0006 HOSTILE dup-click task` side by side, and `PR-00008 [ACCT] PR-C-10000` next to `PR-00009 [ACCT] PR-D-10000.01`.
Impact: approvers deciding twice (two dispositions, two POs) or deciding the wrong twin.

### F-approver-9: The card does not say what the policy requires of the human, nor why the cog declined
Severity: major
Heuristic: Help and documentation at the point of decision; visibility of constraints
Steps:
1. As dave, GET `/inbox?as=dave`, card BILL-0064.
Expected: "The ap-reviewer cog may approve under 2,000 USD when the match is within tolerance and the supplier is approved (line 32). This bill is 8,000 USD, variance 20 %, goods not received. You are asked to accept or reject that variance."
Actual: `Routed to you by finance-policy line 24: "Approvals in purchase-to-pay go to the payables reviewer."` plus `match result | outside tolerance variance 20.0%, PO found, not received, supplier approved` and `Guard findings: … [warning] price-variance … [warning] receipt: goods have not been received`. No threshold band (2,000 / 10,000) is shown to dave, no approval criterion for people exists, and Approve is enabled with no acknowledgement of the warnings. Dave approved it in one POST and the record shows `conditions | {"match_within_tolerance": false, "risk_low": false, "risk_not_high": false, …}` next to `posted`.
Impact: payables reviewers approve outside-tolerance, unreceived bills without the policy ever telling them that is the exception they are signing.

### F-approver-10: Items blocked by a precondition show an enabled Approve and fail only after signing
Severity: major
Heuristic: Error prevention; visibility of system status
Steps:
1. As bob, GET `/inbox?as=bob`, card DSN-2026-0026: `risk | high requirements not all accepted`, `Guard findings: trace-the-requirements: [warning] requirements-accepted: 2 requirement(s) not yet accepted by the design authority`, Approve button enabled.
2. POST `/decide` approve with the card's digest and `meaning=design reviewed, release`.
Expected: the card says "Blocked: accept REQ-2026-0034 and REQ-2026-0035 first (both are in this inbox)", with links, and no Approve until then.
Actual: `400 Not done. requirements: the design cannot be approved: requirements not accepted: REQ-2026-0034, REQ-2026-0035`. The two requirement cards sit 20 cards earlier in the same inbox, assigned to the same bob. Same pattern for a held record: after `POST /hold` the card shows `<div class='chip crit'>on hold: query with supplier; lift the hold first</div>` and still renders the form; Approve yields `400 Not done. BILL-0063 is on hold (query with supplier); lift the hold first`, and the lift control is on another page.
Impact: wasted signatures and re-typing; the approver learns the order of work only by failing.

### F-approver-11: Required decision fields are advertised, but submitting without them is a dead-end error page
Severity: major
Heuristic: Error recovery; keep users' input
Steps:
1. As bob, card NCR-2026-0016 shows `closed needs: disposition, disposition_rationale` with `<select name="disposition" required>` and a required textarea.
2. POST `/decide` with the digest, `meaning=dispositioned`, `verdict=approve`, no disposition (the `required` attributes are browser-only).
Expected: the card re-rendered with the fields highlighted and the typed meaning kept.
Actual: `400` page: `Not done. closed needs disposition, disposition_rationale before approval; supply them with the decisionRunError` (the exception class name is appended to the text). The "Back" link is emitted only when a Referer header is present; without one there is no link at all. All typed text is lost and the user must reload a 51-card page and find the card again.
Impact: anyone whose browser or extension drops the `required` check, or who hits a server-side rule (hold, stale, precondition) that the form never pre-checks.

### F-approver-12: No "not me", delegate or reassign
Severity: major
Heuristic: User control and freedom
Steps:
1. As bob, GET `/inbox?as=bob`; cards TRN-00006 and TRN-00010 are routed "to the assignee" (bob) by training-sop line 22; cards PR-00017…PR-00036 arrived by escalation.
2. Search the page and `hubproto/shell.py` for a reassign/delegate control: the only POST routes are `/start /decide /msg /attach /snapshot /hold /resubmit /retry /cancel /link`.
Expected: "Not mine", "Delegate to…", or at least "Ask the policy owner to re-route" on the card.
Actual: the only ways to make a card leave are approve or reject. A wrong routing (`PR-00005 … Routed to you by … go to the buyer` on bob's inbox) can only be cleared by signing something.
Impact: every mis-routed or escalated item; the approver signs to clear noise.

### F-approver-13: No batch, no next item, no filters: every decision returns to the top of a 150 KB page
Severity: major
Heuristic: Flexibility and efficiency of use
Steps:
1. As dave, POST `/decide` approve on run `869d78db08`.
2. Observe `Location: /record/rec:bill:BILL-0064?as=dave`.
3. GET `/inbox?as=dave` again (77 KB, 26 cards; bob's is 153 KB, 51 cards).
Expected: land on the next waiting item, or back on the inbox at the card below; checkboxes for "approve all cog-passed acknowledgements"; a filter by process, amount or due.
Actual: the record page has no "next" or "back to inbox" link (only the left nav); the inbox has no `id` anchors, no filters, no collapse; the Boards page has filters but no decide form. Each card repeats the full form, so 51 `meaning` inputs are on one page. The eight `Acknowledge the document`/`Complete the task` cards each demand a typed signature sentence.
Impact: bob's 51 decisions cost 51 full reloads and 51 typed sentences; nothing can be cleared in bulk.

### F-approver-14: "Meaning of your signature" is unexplained and identical on every card type
Severity: major
Heuristic: Help and documentation; match between system and the real world
Steps:
1. GET `/inbox?as=bob`; every card ends with `<label class="f"><span>meaning of your signature <b>*</b></span><input name="meaning" placeholder="e.g. approved for posting" size="40" required></label>`, including `Acknowledge the document … Read WI-17 Hand soldering work instruction rev A`, `Complete the task`, `Disposition the nonconformance` and the Reject button.
2. `grep -rn "meaning of your signature"` across `hubproto/`, `frames/`, `profiles/`: only the placeholder; `/why` does not mention it.
Expected: one sentence saying what the field is for (it is stored, hashed into the signature and shown to the submitter on reject) and a per-gate default such as "reviewed the three-way match and accept the variance".
Actual: the same "approved for posting" hint on a training acknowledgement; no help text; and because the server substitutes a sentence when it is blank (F-approver-3), the field reads as busywork.
Impact: every signer; the one field that gives the signature legal meaning is the one nobody understands.

### F-approver-15: Risk and match chips carry no colour, so "high" and "low" look the same
Severity: major
Heuristic: Visibility of system status; WCAG 1.4.1 Use of Color (inverse: no cue at all)
Steps:
1. GET `/inbox?as=bob`, BILL-0063 card: `<dt>match result</dt><dd><span class="chip ">outside tolerance</span> …</dd> … <dt>risk</dt><dd><span class="chip ">high</span> three-way match failed; amount at or above 10000</dd>`; REQ-2026-0034: `<span class="chip ">low</span>`.
2. `chip()` in `hubproto/shell.py` colours only state words (`posted`, `failed`, …).
Expected: red for high / outside tolerance / not received, green for low / within tolerance, so a 51-card page can be scanned.
Actual: every risk, match and "supplier approved: no" chip is the neutral grey `chip`; the only colour on a card is the "approval leads to" state. The risk text also says `at or above 10000` while the policy says `over 10,000 USD`.
Impact: approvers scanning on Friday evening cannot tell a clean item from a flagged one without reading every line.

### F-approver-16: Notifications shout "Overdue" for items that are not due, and Messages duplicate every card
Severity: major
Heuristic: Signal versus noise; consistency
Steps:
1. GET `/inbox?as=bob`, section `Notifications 30`: 30 `crit` cards such as `escalation Overdue: Approve the purchase on rec:purchase-request:PR-00038 at 2026-11-06T14:56:15 / Due 2026-11-09; escalated by Overdue items in purchase-request go to the controller.` sent on 2026-11-06 for a 2026-11-09 due date.
2. Section `Messages 38`: one `dm/approval Approve the bill: purchase-to-pay rec:bill:BILL-0063 from hub … Routed by finance-policy line 22 …` per waiting card.
Expected: notifications only for real overdue/escalation, with "you were added" distinguished from "for information" (finance line 29 is report-only); messages that merely mirror the cards suppressed.
Actual: 30 red cards that are all wrong about "overdue"; 38 messages that add nothing; the links go to `/run` and `/record`, never to the card, so the message cannot be acted on.
Impact: the notification area trains approvers to ignore red.

### F-approver-17: Self-acknowledgements and tasks are dressed as approvals with Approve/Reject and an "assessed_by" list that includes yourself
Severity: minor
Heuristic: Match between system and the real world
Steps:
1. GET `/inbox?as=bob`, card TRN-00006: `Acknowledge the document … started by bob … <dt>document</dt><dd>rec:document:DOC-0040</dd> … You may also set: assessed_by <select name="assessed_by"><option value="bob">bob</option>…` then `Approve`/`Reject` and "meaning of your signature".
Expected: an "I have read rev A" button, the document linked, no self-assessment.
Actual: raw locator text instead of a link; the reader can record himself as assessor; "Reject" of your own training record is offered.
Impact: every trainee; it inflates the decision count and dilutes the meaning of Approve.

### F-approver-18: The digest is optional on the server
Severity: minor
Heuristic: Error prevention (defence in depth for F-approver-1)
Steps:
1. As bob, POST `/decide` on run `ff28996b46` (NCR-2026-0024) with `verdict=approve`, `meaning=use as is`, `disposition=use-as-is`, `disposition_rationale=cosmetic only` and **no** `digest` field.
Expected: refused; a signature must name what it signs.
Actual: `303`; `NCR-2026-0024 ncr closed`. `runner.decide` checks `if payload_digest and payload_digest != pending[...]`, so an omitted digest passes. Any bookmarklet, CLI wrapper or mail-in path can sign blind.
Impact: low today (the HTML form always sends one), but it makes the stale check advisory rather than binding.

### F-approver-19: "What changed" is never on the card
Severity: minor
Heuristic: Recognition rather than recall
Steps:
1. GET `/inbox?as=bob`, card DOC-0282 (document-control, `change type | major`, `revision | A.0`): the brief lists title, type, change type, revision, training roles, risk. No diff, no previous revision, no link to the body, no attached-document count.
2. Card BILL-0068 after alice attached a credit note (F-approver-1): the brief has no "documents" line (`_brief` for `bill` lists supplier, amount, date, PO, description, match, billed to date, risk only).
Expected: a "changed since last revision" or "documents: 1 (new since routed)" line.
Actual: approvers must open `/document/…` or `/record/…` to find out what they are approving, which the brief claims they should not need to do.
Impact: document reviewers on every revision; bill approvers whenever a credit note or dispute lands after routing.

## Summary

Three changes would most raise throughput and confidence. First, make the signature mean what the card shows: include the record digest (fields, documents, links) in the stale check, make the digest and a non-empty meaning mandatory server-side, and stop the decision form from rewriting fields that already have values (F-1, F-2, F-3, F-18); until then an approver can post a bill whose attached credit note says a different amount, or re-price a 12,000 USD request to 1,000 USD under a signature that names 12,000. Second, give the card the three facts a decider needs and the inbox the ordering a decider expects: a due date on every gate (bills have none and sink to the bottom), a coloured risk/match chip, the policy band and the human criterion in one sentence ("8,000 USD, between the 2,000 cog limit and the 10,000 controller threshold; variance 20 % against a 2 % tolerance; goods not received"), the co-assignees, a duplicate hint, and a blocked state that disables Approve and links to the prerequisite (F-5 to F-10, F-15). Third, close the loop: a separate required rejection reason that reaches the submitter in "Returned to you for rework" (design rejections currently reach nobody), redirect to the next item instead of the record page, a "not me / delegate" control, and a notifications panel that only fires when something is actually overdue (F-4, F-11 to F-14, F-16).
