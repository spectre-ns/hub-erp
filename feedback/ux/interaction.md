# Lens: interaction (forms, feedback and flow)

Method: every form in `hubproto/shell.py` was read, then exercised against the
live hub with curl and a urllib multipart poster as alice, bob, carol and dave
on 2026-11-06. Records created for this review carry the prefix `UXIX`
(TASK-0010/0011, BILL-0067, PR-00060..62, DOC-0292, DSN-2026-0027, DOC-0294/0295,
DOC-0311, DOC-0316/0317). Findings are ranked blockers first.

### F-interaction-1: A rejected design is stranded: no inbox item, no notification, no resubmit
Severity: blocker
Heuristic: Visibility of system status; User control and freedom (error recovery). The frame promises "Rejections return to Submit the design" (`frames/engineering/design-control-sop.frame.md:67`).
Steps:
1. As alice, POST `/start` with `process=design-release`, `title=UXIX design`, `kind=mechanical`, one file. Redirected to `/run/007f82082c?as=alice`: "Waiting at Review the design for bob until 2026-11-16".
2. As bob, POST `/decide` with `run=007f82082c`, `verdict=reject`, `meaning=needs a drawing`. 303 to the record.
3. As alice, GET `/inbox?as=alice`, `/run/007f82082c?as=alice`, `/record/rec:design:DSN-2026-0027?as=alice`.
4. As alice, POST `/resubmit` with `run=007f82082c`.
Expected: the item appears under "Returned to you for rework" with the resubmit form, or at least a notification saying bob rejected it and what to do.
Actual: the inbox has no mention of DSN-2026-0027 at all (zero matches, no "returned" and no "rejection" card). The run page says `Run 007f82082c rejected` with no pending card; its track ends `run.rejected {"by": "bob", "step": "score-the-risk", "to": "awaiting-submit-the-design"}`. The record header reads `DSN-2026-0027 design awaiting-submit-the-design ... assignees - ... run 007f82082c rejected`. The resubmit POST answers `Not done. run 007f82082c is not waiting for a resubmit (status rejected)`. Cause in code: `release.py:115` maps `reject_to` to `awaiting-submit-the-design` because the name matches a step, `runner._rewind` finds no human gate for it and returns silently, and the `rejection` notification is only sent on the other branch.
Impact: every design engineer whose design is rejected (the only process in this release with a declared return step) loses the item: it is in no list, no one is assigned, and the only remaining action is to start a new design-release. The reviewer meanwhile believes it went back.
Suggested fix: when `reject_to` is not a human checkpoint, fall through to `returned` (pending kind `resubmit`, owner assigned, `returned` notification); add a test that every `reject_to` in a release resolves to a resubmit or a rewind.

### F-interaction-2: "Fix the record and retry" is offered on a page with no way to fix the record
Severity: blocker
Heuristic: Help users recognize, diagnose and recover from errors; Consistency between message and available controls.
Steps:
1. As alice, POST `/start` with `process=purchase-to-pay` and no fields (nothing is marked required on that form). 303 to `/run/a47963c0c0?as=alice`.
2. GET `/record/rec:bill:BILL-0067?as=alice` and list its forms.
3. POST `/retry` with `run=a47963c0c0`, `note=nothing changed`. Repeat once.
Expected: the failure card lets alice supply supplier, amount, currency and date (the resubmit form already exists for returned items) and retry.
Actual: the record page says `The run failed at extract-the-bill: invoice has no supplier; invoice has no amount; invoice has no currency; invoice has no date` followed by `Fix the record and retry the run from the failed step, or cancel it.` The only forms on the page are `/attach`, `/cancel`, `/hold`, `/link`, `/retry`, `/snapshot`; there is no field editor and no JSON box. Retry redirects back to the record, the run fails again with the identical message, and the activity table now shows two `run.retried` events. The same dead end appears for PR-00060 after dave's approval: `failed at issue-the-purchase-order: no purchase order: no supplier named ... Fix the record (its page has the details) and retry`.
Impact: every failed run in the inbox (alice has 35, carol 1) can only be retried unchanged or cancelled from the shell; "fix the record" requires the CLI. Each retry writes a misleading `retried: nothing changed` transition into the quality record.
Suggested fix: render `_resubmit_form` (fields prefilled, note, files) inside the failed card and route it to `/retry`, or redirect failed-at-required-field runs into the `returned` path.

### F-interaction-3: Reject cannot be pressed until the approve-only required fields are filled, and whatever is typed is written to the record
Severity: blocker
Heuristic: User control and freedom; Error prevention (forced false input).
Steps:
1. As carol, GET `/inbox?as=carol`, card "closed needs: disposition, disposition_rationale" (run 02b0c6a65d). Bob has 5 such cards plus 11 that need `statement, acceptance_criteria, verification_method`.
2. In a browser, leave disposition blank and press Reject.
Expected: Reject needs only a reason; the "needs" fields apply to Approve.
Actual: the form is `<select name="disposition" required><option value=""></option>...</select>` and `<textarea name="disposition_rationale" rows="2" cols="50" required></textarea>` with `<button class="secondary" name="verdict" value="reject">Reject</button>` in the same form and no `formnovalidate`. A browser blocks the submit until a disposition such as "scrap" and a rationale are entered. On the server, `post_decide` collects every non-empty field and `runner.decide` calls `kernel.set_fields(...)` before it looks at the verdict, so the invented disposition is persisted and tracked (`record.fields_set`) on a record that was just rejected.
Impact: quality managers and the controller cannot reject an NCR, CAPA, audit or requirement at the "closed/accepted" gates without falsifying the disposition or statement first; 18 of the 57 waiting cards in bob's and carol's inboxes are in this state.
Suggested fix: `formnovalidate` on Reject and skip field writes when `verdict == "reject"`; give Reject its own "reason" input.

### F-interaction-4: Every create-style form submits twice without protest (Back, double-click, refresh)
Severity: major
Heuristic: Error prevention; Help users recover from mistakes.
Steps:
1. As alice, POST `/start` twice with `process=task`, `title=UXIX dup test`, `description=same`.
2. POST `/upload` twice to `Finance/Payables` with the same `up.txt`.
3. POST `/snapshot` twice with `record=TASK-0003`.
4. As alice, POST `/msg` to bob, then press the browser Back button and Send again (no token, nothing distinguishes the second post).
Expected: a second identical submission is refused or de-duplicated, or the landing page says "already started".
Actual: two runs and two records (`/run/f75d2314e2` -> TASK-0010, `/run/b4cd87a412` -> TASK-0011), two documents DOC-0294 and DOC-0295 with the same file, two evidence documents `DOC-0316 TASK-0003 snapshot at awaiting-complete-the-task` and `DOC-0317 ...`, two messages. Each POST answers `303` with no cache headers, so Back lands on the still-filled form. Only `/decide` is protected, and its refusal after a legitimate Back+Approve is alarming: `run 6475dd4929 is not waiting for a decision (status failed)`.
Impact: any user who double-clicks or uses Back creates duplicate tasks, bills, purchase requests and evidence documents in a system of record; cleanup needs a cancel per duplicate with a reason.
Suggested fix: a per-form nonce in a hidden field, rejected on reuse, plus `Cache-Control: no-store` on form pages; or detect an identical submission from the same user within a minute and land on the existing run.

### F-interaction-5: Required marks do not match what the run actually needs
Severity: major
Heuristic: Error prevention; Visibility of system status (requirements shown up front).
Steps:
1. GET `/?as=alice`, form "Start purchase-to-pay" (chip `bill`): no field carries `<b>*</b>` or `required`.
2. Submit it empty (see F-interaction-2): the run fails on four fields.
3. GET `/?as=alice`, form "Start task": nothing is required; the type needs `title` to reach `done` (`config/record-types.json`).
4. GET `/?as=alice`, form "Start purchase-request": `item *` and `quantity *` are marked, but `supplier` is not; approving without it fails the run (PR-00060: `no purchase order: no supplier named`).
Expected: the fields a submission needs to succeed are marked, and the server names the same set.
Actual: `required_at_start` marks only what the first Gate's state lists, so bill marks nothing while the invoice-extractor cog refuses `invoice has no supplier; ... no amount; ... no currency; ... no date`. Server-side refusal for purchase-request is a separate page `Not done. purchase-request needs item, quantity before it can start`, reached only by bypassing the browser attribute.
Impact: finance clerks learn the bill's required fields only after the run has failed and the record is stuck (F-interaction-2); buyers learn supplier is required only after an approver has signed.
Suggested fix: derive required marks from the union of the first cog's declared required inputs and the first Gate's state; show a "needed before the PO can issue: supplier" hint on the decision card.

### F-interaction-6: After Approve the approver lands on a page headed "failed" with no acknowledgement of the approval
Severity: major
Heuristic: Visibility of system status; Match between system and the real world.
Steps:
1. As dave, GET `/inbox?as=dave`; the card for PR-00060 says `approval leads to <span class="chip ok">ordered</span>` and offers `supplier (record number or locator)` as an optional field.
2. POST `/decide` with `run=6475dd4929`, `verdict=approve`, `meaning=ok to buy`.
Expected: "Your approval of PR-00060 is recorded. The purchase order could not be issued: no supplier named. Alice has been told." or at least a success line before the failure.
Actual: 303 to `/record/rec:purchase-request:PR-00060?as=dave`, whose header is `PR-00060 purchase-request failed` and whose first card is the failure (`Failed at issue-the-purchase-order: no purchase order: no supplier named`). Nothing on the page says a decision was just recorded or by whom; the signature is visible only in the run page's Steps table (`approve by dave (person) ok to buy`). The same silence follows every successful action: `/start` lands on `Run f75d2314e2 waiting` (a hex id, not "TASK-0010 was created"), `/cancel` lands on a record whose only change is the chip `cancelled`, `/hold` on a chip `on hold: `.
Impact: approvers (dave 25 cards, bob 44) cannot tell whether a click took effect without opening the run; a page headed "failed" after a successful approve reads as "my approval failed".
Suggested fix: a one-line result banner on the landing page (`?done=approved&run=...`) stating the action, the record and what happens next.

### F-interaction-7: Validation errors land on a bare "Not done" page: inputs are not preserved, the field is not pointed at, and the page's own controls are broken
Severity: major
Heuristic: Help users recognize, diagnose and recover from errors; WCAG 3.3.1 Error Identification, 3.3.3 Error Suggestion.
Steps:
1. As alice, POST `/start` with `process=task`, `title=T`, `json={"lot": L-42}` (one quote missing), without a Referer header.
2. Repeat with `Referer: http://localhost:8080/?as=alice`.
3. On the error page, use the "Acting as" switcher (it is a GET form whose action is the current route): GET `/start?as=alice`.
Expected: the Start form redisplays with the typed values, the JSON box outlined and the parser message beside it.
Actual: a 400 page whose whole main is `<div class="card crit"><b>Not done.</b> The JSON fields do not parse: Expecting value: line 1 column 9 (char 8)</div>` and, only when the browser sent a Referer, `<p><a href="http://localhost:8080/?as=alice">Back</a></p>`. The typed title, description, files and JSON survive only if the browser restores them on Back (file inputs never are). The switcher on that page submits `GET /start` and gets `<b>Not done.</b> No such page.` Every ShellError, RunError and KernelGuardError follows this pattern (`quantity must be a number, not 'abc'.`, `lines: each line is 'locator,quantity', not '10k resistor x 4'.`, `exists: no record 'NOPE-1'` with the class name `KernelGuardError` printed under it).
Impact: every typo in a multi-field Start, resubmit or decision form costs the whole form; a user without a Back link (bookmark, new tab, privacy mode) has no path at all.
Suggested fix: re-render the originating form with the posted values and the message next to the failing control; never render the Acting-as switcher with a POST route as its action.

### F-interaction-8: The message form defaults to a cog, needs no body, and the sender never sees what was sent
Severity: major
Heuristic: Error prevention (defaults); Visibility of system status.
Steps:
1. As alice, GET `/inbox?as=alice`, section "Send a message".
2. POST `/msg` with `to=bob`, `subject=UXIX ping`, `body=hello`; then GET `/inbox?as=alice` and `/inbox?as=bob`.
3. POST `/msg` with `subject=` (empty) and `to=ap-reviewer`.
Expected: the recipient list starts blank or on a person, the body is required or the subject is, and the sender gets "Sent to bob" or a sent-items list.
Actual: `<select name="to"><option value='ap-reviewer'>ap-reviewer (cog)</option><option value='bob'>bob</option>...` so the preselected recipient is a cog; `<textarea name="body" ...></textarea>` is optional; `<input name="subject" ... required>` is browser-only and the server stores `(no subject)`. After Send the sender is redirected to `/inbox?as=alice`, which contains zero occurrences of "UXIX ping"; bob's inbox shows `dm/message UXIX ping from alice`. Sending to `ap-reviewer` also returns 303 with no feedback.
Impact: a clerk pressing Send on the default form messages a cog with an empty body and cannot see that, or anything, was sent.
Suggested fix: blank first option with `required`, people listed before cogs, a "Sent" section or a landing banner.

### F-interaction-9: The only reason an owner receives for a rejection is the "meaning of your signature", whose placeholder suggests approval text, and a blank one is rendered as "rejectd"
Severity: major
Heuristic: Match between system and the real world; Error prevention.
Steps:
1. As dave, the decision card for PR-00061 shows `<input name="meaning" placeholder="e.g. approved for posting" size="40" required>` next to both Approve and Reject.
2. POST `/decide` with `run=8598c4dd0e`, `verdict=reject`, `meaning=` (empty; the `required` attribute is client-only).
3. GET `/inbox?as=alice` and `/run/8598c4dd0e?as=alice`.
Expected: Reject asks for a reason in its own words; a blank reason is refused for a rejection.
Actual: the run page records `reject by dave (person) rejectd approve the purchase` and alice's notification reads `rejection purchase-request rec:purchase-request:PR-00061 rejected ... dave rejected it: rejectd approve the purchase` (`runner.py:567`: `f"{verdict}d {pending['title'].lower()}"`). Even with a reason typed, what the reviewer thinks of as the signature meaning becomes the rejection reason verbatim, and the card never says whether Reject returns the item for rework or ends it (for purchase-request it ends it: status `rejected`, nothing alice can do).
Impact: owners receive garbled or approval-flavoured text as the reason their item was rejected; reviewers cannot tell what Reject will do before pressing it.
Suggested fix: separate "reason" (required on reject) from "meaning", refuse a blank reason server-side, and print "Reject returns this to alice for rework" / "Reject closes this request" on the card.

### F-interaction-10: A hold with no reason is accepted and then blocks the approver with a live button that always fails
Severity: major
Heuristic: Error prevention; Visibility of system status; Consistency.
Steps:
1. As bob, POST `/hold` with `record=PR-00062`, `on=1`, `reason=` (the input is `<input name="reason" placeholder="reason" size="24">`, optional).
2. GET `/record/rec:purchase-request:PR-00062?as=bob` and `/inbox?as=dave`.
3. As dave, press Approve on that card (POST `/decide` with `run=f2595d84ae`).
Expected: a hold requires a reason; the card for a held item disables Approve/Reject and offers "Release hold" to those who may.
Actual: record header `PR-00062 purchase-request awaiting-approve-the-purchase on hold: ` (blank after the colon). Dave's card shows `<div class='chip crit'>on hold: ; lift the hold first</div>` immediately above an enabled `<button name="verdict" value="approve">Approve</button>`; pressing it yields a separate page `Not done. PR-00062 is on hold (no reason given); lift the hold first`. The release-hold control exists only on the record page. For users who may not hold, the record page keeps the reason input enabled next to a `disabled` button with the explanation `Holds need an approve permission here.` after it.
Impact: approvers are invited to click a button that cannot work and must guess where "lift the hold" lives; blank hold reasons go into the audit trail.
Suggested fix: `required` reason server-side; on held cards replace the decision buttons with "Release hold" (when permitted) or a plain statement of who can.

### F-interaction-11: Check-out has no undo, check-in works without a check-out, and in this release nobody can do either
Severity: major
Heuristic: User control and freedom; Consistency and standards (the feature is named after SharePoint/Nextcloud locking but does not behave like it).
Steps:
1. Read `page_document` and the POST routes in `shell.py`: routes are `/checkout` and `/checkin` only; there is no cancel/undo of a check-out.
2. `dms.checkin` only refuses when `locked_by and locked_by != by`, so an unlocked document accepts a check-in from anyone permitted; the page labels that button `Upload a new version`.
3. As alice, carol, bob and dave, try `/checkout` on DOC-0273 (draft, Finance/Payables), DOC-0294, DOC-0311 (draft, Engineering/Designs, attached to alice's own design); scan 61 documents across every folder as carol and alice for a page containing `action='/checkout'`.
Expected: a checked-out document can be released without uploading; a check-in requires a check-out; the people who can upload to a folder can check out what they uploaded.
Actual: every check-out answers 403 `You cannot change documents in Engineering/Designs: no rule in the release lets any of ['author', 'design-engineer', 'finance-clerk', 'process-owner', 'staff'] create document in Engineering/Designs`, although `/files?as=alice&folder=Engineering%2FDesigns` offers the Upload form in that same folder. Zero of 61 documents expose a check-out button to anyone. Had it worked, a user who checked out by mistake would have to upload a file to release the lock (`checked_out_by` is only cleared in `add_version`).
Impact: the document-control feature is unreachable for all four people, and the model behind it would let a mistaken check-out lock a document until a dummy version is uploaded.
Suggested fix: add `/undo-checkout`; require a check-out before check-in (or drop check-out); align the upload and check-out permissions.

### F-interaction-12: Cancel is a destructive action with no confirmation, an optional reason, and three different labels
Severity: major
Heuristic: Consistency and standards; Error prevention.
Steps:
1. Inbox returned card: `<input name="reason" placeholder="reason" size="20"> <button class="danger">Withdraw</button>` (`shell.py:1037`); failed card and record page: `... <button class="danger">Cancel the run</button>` (`shell.py:1011`). Both POST `/cancel`.
2. As alice, POST `/cancel` with `run=b4cd87a412`, `reason=` (blank).
Expected: one label, a confirmation step, a required reason.
Actual: 303 to `/record/rec:task:TASK-0011?as=alice`, header `TASK-0011 task cancelled`; the transition is recorded as `{"from": "awaiting-complete-the-task", "reason": "", "to": "cancelled"}`. A second press gives `Not done. run 776bd01dda is cancelled and cannot be cancelled`. Related: `<button class="secondary">Render snapshot</button>` on every record page creates a permanent evidence document each time it is pressed (DOC-0316, DOC-0317) with no explanation on the button or confirmation after.
Impact: a one-click, unexplained terminal state on quality records, with an empty reason in the audit trail; "Withdraw" and "Cancel the run" look like different actions.
Suggested fix: one label ("Cancel this <type>"), reason required server-side, and a confirm step (a second page or a checkbox) for cancel and snapshot.

### F-interaction-13: Placeholder text is the only label on nine inputs
Severity: major
Heuristic: WCAG 1.3.1 Info and Relationships, 3.3.2 Labels or Instructions; the placeholder vanishes as soon as the user types.
Steps: read the forms on `/inbox`, `/record/<id>`, `/document/<id>`, `/files?folder=`:
`<input name="note" placeholder="what was fixed" size="28"> <button>Retry</button>`, `<input name="reason" placeholder="reason" size="20"> <button class="danger">Cancel the run</button>`, `<input name="reason" placeholder="reason" size="24"> <button class="secondary">Put on hold</button>`, `Upload and attach <input type="file" name="file"> <input name="title" placeholder="title" size="20">`, `<input name="title" placeholder="title (defaults to the file name)" size="30"> <button>Upload</button>`, `<input type="file" name="file" required> <input name="note" placeholder="note" size="20">`, `to <input name="target" placeholder="record number or locator" size="26" required>`, `<input name="note" size="40" placeholder="what changed">` on resubmit (this one has a label), `<input name="meaning" placeholder="e.g. approved for posting" ...>` (labelled).
Expected: a visible label per control, as the Start forms already do with `label.f`.
Actual: as quoted; the file inputs in the attach and check-in forms have no label either.
Impact: screen-reader users hear "edit text" with no name; sighted users lose the hint after one keystroke and cannot tell "reason" for hold from "reason" for cancel when both sit on one card.
Suggested fix: wrap each in the existing `<label class="f"><span>...</span>...</label>` pattern.

### F-interaction-14: The JSON escape hatch exposes the policy grammar and its own example is rejected
Severity: major
Heuristic: Match between system and the real world; Error prevention (the product claim is "without reading the code or the policy grammar").
Steps:
1. Every Start and resubmit form ends with `<textarea name="json" ... placeholder='{"lot": "L-42"}'></textarea>` labelled `other fields (JSON object)`.
2. As alice, POST `/start` with `process=task`, `title=UXIX json`, `json={"lot": "L-42"}` (the placeholder, verbatim).
3. POST `/start` with `process=purchase-request`, `json={"foo": 1}`.
Expected: an example that works on that form, or no example; an error that names the fields the user may set.
Actual: `Not done. task has no field 'lot'; its fields are title, description, assignee, due, priority.` and `Not done. purchase-request has no field 'foo'; its fields are title, item, mpn, manufacturer, quantity, unit, unit_cost, amount, currency, supplier, supplier_approved, needed_by, reason, design, requested_by, risk, conditions, po.` The latter list includes `po, amount, supplier_approved, risk, conditions`, which the next refusal explains are `set by the system, not at submission; leave it out of the JSON`. The box is the only way to set declared-but-unrendered fields (e.g. `manufacturer`, `unit` on a purchase request), so ordinary users must learn field names and JSON.
Impact: a typed example that fails on 16 of 17 forms teaches users to distrust the hint; the error lists fields they are then refused.
Suggested fix: render every settable non-system field as a control (an "advanced fields" fold) and drop the JSON box from the shell; if kept, generate the placeholder from a real settable field of that type and list only settable fields in the error.

### F-interaction-15: Refusal wording speaks the engine's language
Severity: minor
Heuristic: Match between system and the real world; Help users recognize errors.
Steps: as dave, on a live card, POST `/decide` with `digest=deadbeef`; with `mpn=XYZ`; as alice POST `/link` with `target=NOPE-1`; as alice POST `/retry` on a waiting run.
Expected: "This item changed after you opened it; reload and look again." / "Bob may only set needed_by, reason, supplier or unit_cost here." / "No record NOPE-1."
Actual: `the payload you saw differs from the pending payload; the item changed` + `RunError`; `A decision may not set mpn; this Gate accepts needed_by, reason, supplier, unit_cost.`; `exists: no record 'NOPE-1'` + `KernelGuardError`; `run 82007f4728 is not failed (status completed)` + `RunError`. The class name is printed on every page as `<div class="why">RunError</div>`.
Impact: all roles; most often after Back or a concurrent decision, which is exactly when they are least sure what happened.
Suggested fix: a message map from engine errors to user sentences; drop the class name.

### F-interaction-16: Every enum, person and yes/no select opens on an unlabelled blank
Severity: minor
Heuristic: Error prevention; WCAG 3.3.2.
Steps: GET `/?as=alice`: `<select name="severity"><option value=""></option><option value="low">low</option>...`, `<select name="assignee"><option value=""></option><option value="alice">...`, bool fields render `<option value="">-</option><option value="true">true</option><option value="false">false</option>`; required decision selects `<select name="disposition" required><option value=""></option>...`; attach: `<select name="doc"><option value=""></option>...`.
Expected: a first option that says what blank means ("choose...", "leave unset", "no document") and, for `bool`, yes/no.
Actual: as quoted; a blank required select produces only the browser's generic "Please select an item in the list" with no field name.
Impact: users cannot tell "not set" from "I forgot"; `true`/`false` is developer vocabulary.
Suggested fix: `<option value="">(choose)</option>` or `(leave unset)`, `yes`/`no` for booleans.

### F-interaction-17: Formats are implied, never stated: "lines", comma lists, money and dates
Severity: minor
Heuristic: Recognition rather than recall; Help and documentation at the point of need.
Steps:
1. goods-receipt: `<textarea name="lines" ... placeholder="locator,quantity per line, e.g. rec:item:ITEM-00001,4" required>`; POST `lines=10k resistor x 4` -> `Not done. lines: each line is 'locator,quantity', not '10k resistor x 4'.`
2. List fields: `affected_documents (comma list or JSON)`, `training_roles (comma list or JSON)`: a value containing a comma can only be entered as JSON; on a resubmit lists come back joined with `", "` (`prefill`).
3. purchase-to-pay: `amount` is `<input type="number" step="any">` with `currency` a free text box beside it (no list, no default shown, though the type defaults to USD elsewhere); `date` is just `date` (invoice date? due date?); `quantity` on purchase requests is entered bare and shown as `200000 ea`.
Expected: the format next to the field, a list widget or one-per-line box for lists, and units/currencies as selects with the default visible.
Actual: as quoted; the lines format lives only in a placeholder that disappears on typing and asks for a `rec:item:` locator the user has to look up.
Impact: receiving clerks and buyers; each miss costs the whole form (F-interaction-7).
Suggested fix: `kind="lines"` for list fields, a small item picker for `lines`, a currency select, and field labels that say which date.

### F-interaction-18: Resubmit and retry carry no required marks and silently drop blanks
Severity: minor
Heuristic: Consistency and standards (the Start form marks required fields, the rework form of the same type does not).
Steps: read `_resubmit_form`: `self._start_inputs(rt, values)` is called without `required=`, and `_collect_fields(..., current=...)` skips any empty or unchanged value, so clearing a field on rework leaves the old value in place.
Expected: the same marks as the Start form, and a way to clear a field.
Actual: no `*` on any rework control; the note says `change what needs changing (unchanged fields are left alone)` but does not say a cleared field is also left alone.
Impact: owners reworking a returned item (when the return works, see F-interaction-1) cannot remove a wrong supplier or date; they can only overwrite it.
Suggested fix: pass the required set, and treat a field cleared on rework as "unset" with an explicit checkbox.

### F-interaction-19: Completion, escalation and ownership changes are reported in no words, or in words that mislead
Severity: minor
Heuristic: Visibility of system status.
Steps:
1. `NOTIFICATION_KINDS = ("escalation", "failure", "returned", "rejection")` in `shell.py:53`; the runner sends no notification when a run completes, so an owner learns of completion only by visiting `/runs` or the record.
2. Home: `<div class="card"><b>N</b>of my runs waiting</div>` has no link, unlike the three cards around it.
3. Inbox cards: `<span class='chip warn'>escalated 2026-11-12</span>` on bob's cards dated 2026-11-06, next to `due 2026-11-09`; the escalation notification reads `Overdue: Approve the purchase on rec:purchase-request:PR-00038 ... Due 2026-11-09; escalated by Overdue items in purchase-request go to the controller.`
Expected: "Completed: BILL-0046 posted" in the owner's notifications; "escalated to bob on <date>" on the card; a link on the waiting count.
Actual: as quoted; the chip gives a date with no "to whom" and the date reads as future.
Impact: owners refresh `/runs` to learn an outcome; approvers cannot tell whether an escalation landed on them or left them.
Suggested fix: a `completed` notification kind, `escalated to <who> on <date>`, and the missing link.

### F-interaction-20: "Start purchase-to-pay" is the button for filing a bill
Severity: minor
Heuristic: Match between system and the real world (button labels say what they do).
Steps: GET `/?as=alice`: `<b>purchase-to-pay</b> <span class="chip">bill</span> filed in Finance/Payables ... <button>Start purchase-to-pay</button>`; likewise `Start nonconformance` (chip `ncr`), `Start design-release` (chip `design`), `Start requirements-capture`.
Expected: "File a bill", "Raise a nonconformance", "Submit a design for release".
Actual: process names from the frames are used as verbs on the only button that creates the record.
Impact: a finance clerk looking for "new bill" scans eleven process names; the chip is the only clue.
Suggested fix: a per-process verb phrase in the frame (or a `label` in START_INPUTS) used for the button and the card title.

## Summary: three fixes with the best payoff

First, re-render the originating form on every refusal with the typed values kept and the message beside the failing control, and land every successful POST on a page that opens with one line saying what just happened ("Your approval of PR-00060 is recorded; the purchase order could not be issued: no supplier named"); that single change removes the separate "Not done" page, the lost multipart input, the broken Acting-as switcher on error pages, the silent message send and the "failed" headline after a successful approve (F-7, F-6, F-8, F-15). Second, make the return path real: when a frame's reject target is not a human gate, return the item to its owner as a `resubmit` with the existing rework form, and render that same form inside failed-run cards routed to `/retry`, so "Fix the record and retry" and "Rejections return to Submit the design" are both true (F-1, F-2, F-5). Third, separate Reject from Approve in the decision form (a `formnovalidate` Reject with its own required reason, no field writes on reject, and a sentence saying where Reject sends the item) and add a one-use token to the create forms (start, upload, msg, snapshot) so Back and double-clicks stop minting duplicate records (F-3, F-4, F-9, F-12).
