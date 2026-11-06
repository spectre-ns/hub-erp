# Operations lead: findings

Persona: Operations lead (shop floor and office operations). Drove tasks,
nonconformances, calibration, messaging, documents and boards as alice
(staff, process owner), dave (staff) and carol (quality manager) through the
shell at `http://127.0.0.1:8080/?as=<principal>` with curl, and the CLI
`docker compose exec hub hubproto ...`. All my records are titled `[OPS] ...`.
The hub was shared with other panelists during the session, so some record
numbers around mine belong to them; every number below is one I created or
decided unless stated.

Severity counts: blocker 1, major 11, minor 11, question 1.

---

### F-ops-1: Shell and CLI hand out the same document number; a shell attach overwrote a CLI-created working document
Severity: blocker
Area: records | DMS
Steps:
1. CLI as alice: `hubproto doc-new --as alice --title "[OPS] Line 2 pre-use checklist (working)" --class working --folder Tasks --file /tmp/line2-checklist.md` -> `DOC-0106 rec:document:DOC-0106 state=open rev 1.0`.
2. Shell as alice, within the same second: `POST /attach` on `rec:ncr:NCR-2026-0003` with a 73-byte PNG and title `[OPS] photo of scratched housing` -> HTTP 400 "Not done. immutable: external documents cannot be changed; file a new one that supersedes it (KernelGuardError)".
3. Open `/document/rec:document:DOC-0106?as=alice`.
4. Repeat: CLI `hubproto upload --as alice --folder Quality/Nonconformances --file /tmp/scratch-photo.png --attach-to NCR-2026-0003` -> `DOC-0121 ... attached`; then shell `POST /attach` on NCR-2026-0003 with `housing-L77-photo.png` -> HTTP 400, same message.
Expected: each creation gets its own number; the shell attach files a new external document and attaches it; the working document keeps its class and stays in Tasks.
Actual: DOC-0106 is now class `external`, type `received`, folder `Quality/Nonconformances`, title `[OPS] photo of scratched housing`, but its only version is `line2-checklist.md` (54 bytes) and it is "Not attached to any record". The working document no longer appears in `hubproto docs --folder Tasks`. DOC-0121 (uploaded as `scratch-photo.png`) is now titled `housing-L77-photo.png`. The photo bytes from step 2 were never filed.
Evidence: `/document/rec:document:DOC-0106?as=alice` Activity: event 1253 `record.created` with `doc_class: working`, 1254 `document.version_added line2-checklist.md`, 1256 `record.created` with `doc_class: external` on the same aggregate; `hubproto record DOC-0106`; `hubproto docs --folder Tasks` (DOC-0100, DOC-0132, DOC-0170 only); NCR-2026-0003 Documents table lists DOC-0121 as `housing-L77-photo.png`. A later shell attach with nothing else running (DOC-0145, DOC-0146) worked, which points at the running shell process holding a stale number counter while the CLI process advances it on disk.
Suggested fix: allocate numbers inside the kernel transaction (SQLite) rather than in process memory; refuse `record.created` on an existing aggregate.

### F-ops-2: A Gate accepts a signature that cannot close the record, then fails the run and strands the record (NCR and CAPA)
Severity: major
Area: workflow
Steps:
1. As carol, decide run `43e038db3e` (NCR-2026-0004 "[OPS] Cracked guard on press 2", risk high) with `verdict=approve`, `meaning=scrap the guard, fit new one before restart` and no `disposition`/`disposition_rationale` (POST /decide; the inbox form marks them `required` but the server does not check).
2. Open `/record/rec:ncr:NCR-2026-0004?as=carol` and `/run/43e038db3e`.
3. As carol, decide run `25dc946c00` (CAPA-2026-0001) "Verify the actions" with a meaning only.
Expected: the decision is refused with "closed needs disposition, disposition_rationale" and the item stays in carol's queue; nothing downstream runs.
Actual: 303 to the record. The run accepted the signature, `capa-opener` opened CAPA-2026-0001 and linked it, `document-renderer` rendered a snapshot, then `run.failed: cannot close as closed: required-fields: NCR-2026-0004 cannot enter 'closed' without disposition, disposition_rationale`. NCR-2026-0004 is in state `failed`, assignees none, no disposition, and is no longer in anyone's queue, while its CAPA is live. The same happened to CAPA-2026-0001: `run.failed: required-fields: CAPA-2026-0001 cannot enter 'awaiting-verify-effectiveness' without root_cause` at step `schedule-the-effectiveness-check`; the corrective action for a cracked press guard is now `failed` with no owner queue.
Evidence: run 43e038db3e Track event 15; run 25dc946c00 Track event 12; `/boards?type=ncr` shows 2 in `failed`; `/boards?type=capa` CAPA-2026-0001 in `failed`.
Suggested fix: validate `required_missing(rec, next_state)` in `runner.decide` before recording the signature; never run side-effect steps (capa-opener) before the transition is known to be legal.

### F-ops-3: Deciding a record that is on hold fails the run for good; releasing the hold does not bring it back
Severity: major
Area: workflow
Steps:
1. As alice: `POST /hold` on `rec:task:TASK-0003` with `on=1`, reason "checklist rev B withdrawn, wait for rev C" -> 303.
2. As carol (assignee): decide run `8017b69e13` approve, meaning "reviewed rev B" -> 303 (no error shown).
3. As alice: `POST /hold` with `on=0`. As carol: decide again -> HTTP 400 "run 8017b69e13 is not waiting for a decision (RunError)".
Expected: step 2 is refused with "TASK-0003 is on hold" and the item stays queued; after the hold is lifted carol can complete it.
Actual: run 8017b69e13 `run.failed: cannot close as done: hold: TASK-0003 is on hold`. The record stays at `awaiting-complete-the-task` with assignee carol but the run is `failed`, the item is gone from carol's inbox (0 forms for that run) and cannot be decided. The task is a zombie.
Evidence: `/run/8017b69e13?as=alice` Track event 7; `/record/rec:task:TASK-0003?as=alice` header "awaiting-complete-the-task ... run failed".

### F-ops-4: Any nonconformance with a quantity is scored risk "high" with purchasing facts; "low" is unreachable from the normal form
Severity: major
Area: workflow | policy
Steps:
1. As alice start `nonconformance`: title `[OPS] Scratched housings in lot L-77`, description "Incoming inspection found 12 of 200 aluminium housings scratched on the visible face, lot L-77 from acme-metals, on rack B3. Cosmetic only, nobody could be hurt.", other fields `{"source":"inspection","lot":"L-77","quantity":12}` -> NCR-2026-0003.
2. As dave start `nonconformance` with severity `low` selected in the form, description "One housing with a light cosmetic scratch found at incoming inspection.", `{"source":"inspection","quantity":1}` -> NCR-2026-0006.
3. As dave start the same with no `quantity` -> NCR-2026-0009.
Expected: 1 and 2 are low risk and go to the quality-assistant cog per ncr-sop line 21.
Actual: NCR-2026-0003 `severity medium, risk high`; NCR-2026-0006 `severity low, risk high`. Both carry `conditions {"item_known": false, "risk_low": false, "risk_not_high": false, "supplier_approved": false}` (purchasing facts on a nonconformance) and went to carol. Only NCR-2026-0009, with no quantity, got `risk low` and was dispositioned by the cog. Another panelist's NCR-2026-0001 ("Cosmetic scratch") got medium for the same reason the classifier bumps anything containing the word "lot".
Evidence: `/record/rec:ncr:NCR-2026-0003`, `-0006`, `-0009` Details; run 02b0c6a65d Track event 8 `routing_ctx.risk: "high"`; `hubproto/cogs.py` RiskScorer takes the purchase-request branch when `rec.get("quantity")` is set; NCRClassifier sets `medium` when "lot" appears in the text.
Suggested fix: branch the scorer on record type, not on field presence; do not key severity on the substring "lot".

### F-ops-5: Containment wording is one boilerplate sentence for every nonconformance; I would not act on it
Severity: major
Area: workflow
Steps:
1. Compare the `containment` field on NCR-2026-0003 (scratched housings), NCR-2026-0004 (cracked safety guard on a press, machine tagged out), NCR-2026-0007 (out-of-tolerance micrometer, raised from CAL-00002) and NCR-2026-0009.
Expected: containment that fits the finding: quarantine the lot and rack for housings; lock-out/tag-out and do not restart for the press guard; withdraw the instrument, label it, and list the measurements made since the last good calibration for the micrometer.
Actual: all four say exactly "Quarantine affected items and notify the process owner." A press guard is not an "item" to quarantine. The WI tells staff to "quarantine the affected items as the containment text says", so staff would do the wrong thing for a machine or an instrument. For the cog-dispositioned NCR-2026-0009 the closing rationale is "Proposed by the classifier for a low-risk nonconformance; confirmed at the Gate." although nobody inspected anything and the cog's meaning is "approved within fixed criteria". The record is closed with a disposition `rework` that no one chose.
Evidence: `/record/rec:ncr:NCR-2026-0003`, `-0004`, `-0007`, `-0009?as=carol` Details; `hubproto/cogs.py` NCRClassifier uses a constant string.

### F-ops-6: "Please file this" by email to the quality-assistant files a nameless NCR from the attachment filename, the run fails, and the reply says it was filed
Severity: major
Area: workflow | CLI
Steps:
1. `hubproto msg --from alice --to quality-assistant --channel email --subject "[OPS] please file this nonconformance" --body "Please file this: 4 bent brackets found at final inspection on line 2, lot L-91, held in the red bin. No safety impact. Photo attached." --attach /hub/examples/invoice-text.json`
2. Open `/record/rec:ncr:NCR-2026-0008?as=carol` and `/run/dbbcc41ede`.
3. Send the same body as a DM without an attachment.
Expected: an NCR titled from the subject, description from the body, the attachment attached, waiting for disposition; or a reply saying what is missing.
Actual: reply "Filed invoice-text.json as rec:ncr:NCR-2026-0008 (run dbbcc41ede)". NCR-2026-0008 has no title and no description, only `reported_by quality-assistant`; nothing attached; run `dbbcc41ede` failed at `open-the-nonconformance` with "nonconformance has no description". Nobody is told it failed; the record sits in the `failed` column of the NCR board. The DM without attachment gets "I can file an attached record into a process, or report status." so the body text is never read.
Evidence: run dbbcc41ede Track event 4 `run.failed`; alice's inbox `email/reply` item; `/boards?as=carol&type=ncr` failed = 2.

### F-ops-7: Asking the cog for status returns a dump of every item waiting at a Gate in the whole hub
Severity: major
Area: workflow
Steps:
1. `hubproto msg --from alice --to quality-assistant --channel dm --subject "[OPS] status?" --body "What is the status of the nonconformance I asked you to file for lot L-91?"`
2. `hubproto msg --from alice --to quality-assistant --channel dm --subject "[OPS] status of NCR-2026-0003?" --body "What is the status of NCR-2026-0003, the scratched housings in lot L-77?"`
Expected: the status of the thing I asked about (NCR-2026-0008 failed; NCR-2026-0003 waiting for carol, due 2026-11-11, escalated to bob).
Actual: both replies are "41 item(s) are waiting at a Gate: rec:task:TASK-0002 (task), ... rec:ncr:NCR-2026-0007 (nonconformance)." (44 the second time), listing bills, designs and purchase requests that have nothing to do with the question or with the quality-assistant. The reply lands in alice's Messages as one unreadable paragraph.
Evidence: `/inbox?as=alice` two `dm/reply` items from quality-assistant at 14:46:19 and 14:53.

### F-ops-8: The inbox is not usable as a work queue: no read/done state, no filters, decided items stay in Messages
Severity: major
Area: shell
Steps:
1. As dave complete TASK-0001 and reject TASK-0004 from `/inbox?as=dave`.
2. Reload `/inbox?as=dave`; open `/inbox?as=carol`.
Expected: decided items disappear or are marked done; a count per type; filter by process, due date, overdue; sort by due; mark messages read.
Actual: "Waiting for your decision" is a flat list in creation order (carol: 11 decision forms, the NCRs due 2026-11-11 interleaved with documents due 2026-11-16). "Messages" keeps every `dm/approval` ever sent (dave still shows TASK-0001 and TASK-0004 after deciding them; carol has 20 approval DMs), with no read, done, archive or filter. The only count anywhere is "approvals waiting for me" on Home. The CLI `hubproto inbox --as dave` prints only messages, not the decisions. `hubproto/inbox.py` is an append-only JSONL with no state per message.
Evidence: `/inbox?as=dave` Messages after 14:44:14; `/inbox?as=carol` (11 `/decide` forms, 20 `dm/approval`).

### F-ops-9: A task's due date is stored but ignored: no due on the Gate, no overdue, no escalation
Severity: major
Area: workflow | policy
Steps:
1. As alice start `task` with title `[OPS] Clear bay 3 racking`, other fields `{"assignee":"dave","due":"2026-11-10","priority":"high"}` -> TASK-0001, run 667dd4e7f7.
2. Open `/record/rec:task:TASK-0001?as=alice`; open dave's inbox card.
3. In the container: `hubproto tick --today 2099-01-01`.
Expected: the record header and the inbox card show due 2026-11-10; on 2099-01-01 the task is overdue and escalates (to the filer or a team lead).
Actual: Details show `due 2026-11-10` but the header says `due -`, the Track `gate.pending` has `"due": null`, the inbox card has no due, and `tick` prints nothing for any task (it escalated training, complaints, NCRs and purchase requests for other records). `tasks-sop` has no "is due within" sentence and the record's own `due` field is not used.
Evidence: run 667dd4e7f7 Track event 4; `tick` output at 14:49:00; `/boards?type=task` cards show no due.
Suggested fix: let a record-level `due` field set the Gate due (or add "Complete the task in task is due by the task's due date" to the grammar) and an escalation sentence to tasks-sop.

### F-ops-10: No way to reassign a task; a rejected task is dead
Severity: major
Area: workflow | shell | CLI
Steps:
1. TASK-0004 (assigned dave): as dave reject with meaning "not my area, belongs to facilities".
2. Look for a reassign control on `/record/rec:task:TASK-0004?as=alice` and in `hubproto --help`.
3. Try the only assignee-shaped control: `POST /link` on TASK-0002 with `rel=assigned-to`, `target=dave` -> HTTP 400 "exists: no record 'dave'".
Expected: the filer (or the assignee) can hand the task to someone else, with the change in the Activity; a rejected task returns to the filer.
Actual: missing. State `rejected`, assignees `-`, nothing routed back to alice, no CLI command, no form, `assigned-to` only accepts records. The only option is to file a new task and lose the history.
Evidence: `/record/rec:task:TASK-0004?as=alice`; `hubproto --help` (no reassign); 400 on the link.

### F-ops-11: The filer is never told a task was completed or rejected
Severity: major
Area: workflow
Steps:
1. As alice file TASK-0001 for dave and TASK-0004 for dave; dave completes one and rejects the other.
2. Open `/inbox?as=alice`.
Expected: a message to alice "TASK-0001 done by dave: pallets moved to quarantine" and "TASK-0004 rejected by dave: not my area".
Actual: nothing. alice's Messages contain only her own approval DMs and purchasing updates. The `notifier` cog exists ("Tells the record owner ... what happened") but the task process has no notify step, and the task SOP cannot express one.
Evidence: `/inbox?as=alice` Messages at 14:44 onward; `frames/iso9001/tasks-sop.frame.md` Process section.

### F-ops-12: Calibration-triggered NCR carries no instrument data and locks the quality manager out of its disposition
Severity: major
Area: workflow | policy
Steps:
1. As alice start `calibration` with other fields `{"asset":"AST-00007","procedure":"CAL-PROC-07","standard":"Gauge block set GB-12 (NIST traceable cert 44812)","result":"out-of-tolerance","measured":{"nominal_mm":25.0,"reading_mm":25.031,"tolerance_mm":0.01},"next_due":"2027-05-06"}` -> CAL-00002.
2. As carol approve "Review the calibration" (run 5a1f52f171), meaning "reviewed, out of tolerance confirmed, instrument withdrawn".
3. Open `/record/rec:ncr:NCR-2026-0007?as=carol`.
Expected: an NCR that names the instrument, the reading and the tolerance, with containment "withdraw the instrument and assess measurements since the last calibration" (the SOP's stated purpose), waiting for the quality manager.
Actual: NCR-2026-0007 title "From CAL-00002: [OPS] Calibration of micrometer MIC-07", description "Raised from CAL-00002 (calibration)", no asset, no measured values, containment boilerplate (F-ops-5), `reported_by carol` because the system filed it under carol's decision, so segregation of duties sends it to bob (assignee bob) and carol cannot disposition any out-of-tolerance NCR. The `asset` field accepted the free string `AST-00007` although no asset record exists, so the link to the instrument is text. The link on CAL-00002 reads "concerned-by: From CAL-00002: ..." instead of "has NCR".
Evidence: `/record/rec:calibration:CAL-00002?as=carol` (ncr = NCR-2026-0007, link `concerned-by`); `/record/rec:ncr:NCR-2026-0007?as=carol` Details.

### F-ops-13: Every signature is listed twice in the Decisions table
Severity: minor
Area: shell
Steps:
1. Open `/record/rec:task:TASK-0001?as=alice`, `/record/rec:ncr:NCR-2026-0004?as=carol`, `/record/rec:calibration:CAL-00002?as=carol`, `/record/rec:ncr:NCR-2026-0009?as=dave`.
Expected: one row per signature.
Actual: each decision appears as two identical rows (same signature `3c27bb299a66c8bd8760d817` on TASK-0001, `76ffd3f89a31ef861f25616c` on NCR-2026-0004, `ec6228794cf49aa0f895349f` on CAL-00002, `1c2db986f00f036a2a5a85fb` on NCR-2026-0009).
Evidence: `hubproto/shell.py` line 709 concatenates `record.decision` events with `rec["decisions"]`.

### F-ops-14: One generic Start form for every process; task and calibration fields only via a JSON box
Severity: minor
Area: shell
Steps:
1. Open `/?as=alice`, find "Start task" and "Start calibration".
2. Try to set assignee, due, priority on a task; asset, result, measured on a calibration.
3. CLI: `hubproto run task --as alice --record-json '{"title":"...","assignee":"carol","priority":"normal"}'`.
Expected: a form built from the record type's fields (task: title, description, assignee (member list), due (date), priority (low/medium/high); calibration: asset (asset list), procedure, standard, result (enum), measured, next_due) with validation messages.
Actual: every process shows the same fields: title, description, amount, currency, date, po_ref, supplier, change_type, severity, training_roles and "other fields (JSON object)". A task has no assignee, due or priority inputs; a calibration has none of its fields; staff must type JSON. The `severity` select on the form is honoured for an NCR but the risk scorer overrides it (F-ops-4). The CLI gives a good error (`task.priority must be one of low,medium,high, not 'normal'`); the shell has no guidance at all. After Start the user lands on the run page, not the record page (WI says record page).
Evidence: `/?as=alice` form `process=task`; CLI error text.

### F-ops-15: Boards: "Saved filter" is only a link to the current URL; nothing a team lead needs is there
Severity: minor
Area: shell
Steps:
1. `/boards?as=alice&type=task&assignee=carol` (works), `&text=racking` (works), `&folder=Quality` (works).
2. Click "Saved filter".
3. Complete TASK-0001 as dave, then `/boards?as=alice&type=task&assignee=dave`.
Expected: named, persisted, per-user saved filters; cards with due date and priority; WIP limit per column; swimlanes by assignee or folder; cycle/lead time per column; multi-select with bulk reassign/complete/hold; overdue highlighting; sort.
Actual: "Saved filter" prints the current query string as a link (`/boards?as=alice&type=task`); it is not saved anywhere. Cards show title and "folder · owner · assignee" only (no due, no priority, no age). Columns are exactly the workflow states (open, awaiting-complete-the-task, done, rejected, failed) with counts, which is right, but there is no WIP limit, no swimlane, no time-in-column, no bulk action, no sort. When a task is done its assignee is cleared, so `assignee=dave` returns 0 and nobody can see what dave completed; the done card reads "Tasks · alice" (the filer). Missing (not broken).
Evidence: `/boards?as=alice&type=task` HTML `Saved filter: <a href="/boards?as=alice&type=task">`; done column card for TASK-0001.

### F-ops-16: No comments on a record; the only free text is the signature meaning
Severity: minor
Area: shell | records
Steps:
1. Open `/record/rec:task:TASK-0002?as=alice` and `/record/rec:ncr:NCR-2026-0003?as=carol`; look for a comment or note control.
Expected: a comment thread on the record (who, when, text), visible to the assignee and on the board card, part of the Activity.
Actual: missing. The record page offers snapshot, hold, attach and link only. Messages live in the inbox and are not linked to a record, so "what did we say about this NCR" has no home.
Evidence: `/record/...` page controls.

### F-ops-17: Subtasks via part-of: the parent can close while the child is open and the board shows no hierarchy
Severity: minor
Area: records | shell
Steps:
1. `POST /link` as alice: record `rec:task:TASK-0002`, rel `part-of`, target `TASK-0001` -> 303.
2. As dave complete TASK-0001 (run 667dd4e7f7).
3. `/boards?as=alice&type=task`.
Expected: either TASK-0001 cannot close with an open part, or the card shows "1 open subtask"; the board shows the nesting.
Actual: the link is stored and shown both ways (TASK-0001 "has-part TASK-0002 in"), but TASK-0001 closed as done at 14:44:14 with TASK-0002 still awaiting; the board shows two unrelated cards. The link form's target accepts a bare number (good) but there is no validation of sensible relations (a task can be `supersedes` of a bill).
Evidence: `/record/rec:task:TASK-0001?as=alice` Links; `/boards?type=task`.

### F-ops-18: Overdue is a one-shot escalation message; nothing on the record or board says "overdue"
Severity: minor
Area: shell | workflow
Steps:
1. `hubproto tick --today 2099-01-01`.
2. Open `/record/rec:ncr:NCR-2026-0003?as=carol` and `/boards?as=carol&type=ncr`.
Expected: the record header and the card show overdue (red), and the escalation appears in the record Activity.
Actual: bob got an "Overdue: Disposition the nonconformance on rec:ncr:NCR-2026-0003" card in Escalations and was added to assignees (header "assignees carol, bob"), which is correct per ncr-sop line 25, but the record still reads "due 2026-11-11" with no overdue flag and the board card is unchanged. Running tick a second time (another panelist did at 14:48:53, I did at 14:49:00) does not re-escalate the same items, which is fine, but there is no audit of the first escalation on the record.
Evidence: tick output 14:49:00; `/inbox?as=bob` Escalations; NCR-2026-0003 header.

### F-ops-19: Shell messaging has no attachment, no threads and no reply from a message
Severity: minor
Area: shell
Steps:
1. `/inbox?as=alice`, "Send a message" form.
2. `hubproto msg --from alice --to dave --channel dm --subject "[OPS] bay 3 done?" --body "..."` -> "delivered to dave via dm"; open `/inbox?as=dave`.
Expected: attach a file in the shell form (the cog only files from attachments, F-ops-6); a reply control on a received message; a thread view; email vs DM behaving differently.
Actual: the form has to, via, subject, body only; "attach" exists only on the CLI. dave sees `dm/message [OPS] bay 3 done? from alice` with no reply button; he must compose a new message by hand. DM and email are indistinguishable except for the label.
Evidence: `/inbox` form fields; `/inbox?as=dave` Messages.

### F-ops-20: WI "file a nonconformance" does not match the shell
Severity: minor
Area: docs
Steps:
1. Follow `frames/iso9001/wi-file-a-nonconformance.frame.md` step by step as alice.
Expected: the steps match the screens.
Actual: step 1 "Open the Quality/Nonconformances board and choose New nonconformance" — the board (`/boards?type=ncr`) and the folder page have no such control; Start forms are only on Home. Step 2 "You will see the record page with its number" — Start redirects to the run page (`/run/02b0c6a65d`), the number is a link in the first line. Step 4 "the quality-assistant cog for a low-risk item" — only when no quantity is entered (F-ops-4). Step 5 "Press Why on any routing or decision" — there is no Why button on a routing; the inbox shows the sentence as plain text and `/why` only answers "may X do Y in folder Z".
Evidence: `/boards?as=alice&type=ncr` (no "New"), 303 Location after `/start`, `/why?as=alice` form.

### F-ops-21: WI "disposition nonconforming stock" sends the quality manager to the record page, where there is no decision form, and describes a low-risk case she never sees
Severity: minor
Area: docs
Steps:
1. Follow `wi-disposition-stock.frame.md` as carol for NCR-2026-0003.
Expected: steps match.
Actual: step 1/3 "Open the record page ... Decide approve with the fields disposition and disposition_rationale" — `/record/rec:ncr:NCR-2026-0003?as=carol` has no decide form (0 `/decide` forms); the fields exist only on the inbox card (where they are rendered as "closed needs: disposition, disposition_rationale", good). Step 3 "For a low-risk item the classifier already proposed rework; change it if you disagree" — low-risk items are approved by the cog and never reach carol (NCR-2026-0009). Step 2 "Confirm the containment was done" — there is nowhere to record that.
Evidence: NCR-2026-0003 record page; run a0ec466168 cog decision.

### F-ops-22: WI "respond to a cog escalation" describes a gate.cog_review event that does not exist when the cog is bypassed
Severity: minor
Area: docs | workflow
Steps:
1. NCR-2026-0003 (risk high) run 02b0c6a65d: read the Track as carol, looking for `gate.cog_review` with reasons as the WI says.
Expected: per `wi-respond-to-a-cog-escalation` steps 1-2, the cog reviews, records reasons ("risk not low"), and escalates.
Actual: the Track has events 1-8 and goes straight from `score-the-risk` to `gate.pending` for carol by route rule ncr-sop line 22; the quality-assistant is never consulted, so there is no `gate.cog_review` and no reasons to read. The WI's "They are facts the risk scorer recorded" is only visible as `risk_reasons` in the run payload, not on the inbox card (the card shows `risk high` and `severity medium`, not why). The cog_review event does appear when the route hits the cog (run a0ec466168, "risk_low holds").
Evidence: run 02b0c6a65d Track; run a0ec466168 Track event 8.

### F-ops-23: WI "acknowledge training" step 4 cannot be done: no way to message the author from the record page
Severity: minor
Area: docs | shell
Steps:
1. As alice open `/record/rec:training:TRN-00001?as=alice` (Read WI-18 ESD handling rev A), follow the link to `/document/rec:document:DOC-0041`.
2. Look for a message control on either page.
3. Decide approve with meaning "I have read and understood WI-18 rev A" (run 26dc52168c).
Expected: step 4 "Message the document's author from the record page" works; steps 1-3 match.
Actual: steps 1-3 match (item in inbox due 2026-11-20, record waits for alice alone, approve closes it as `acknowledged`). Neither page has a message form (0 `/msg` forms); the author's name is shown but not linked. Escalation at tick went to carol as written.
Evidence: TRN-00001 state `acknowledged`; page source grep.

### F-ops-24: What does "Reject" mean on a task?
Severity: question
Area: workflow | policy
Steps:
1. As dave reject TASK-0004 ("not my area, belongs to facilities").
Expected: a defined meaning: decline and return to the filer, or cancel.
Actual: the task goes to `rejected`, terminal, nobody notified, no reassign (F-ops-10, F-ops-11). The tasks SOP says a task "closes as done when they complete it" and says nothing about rejection; the compiler adds `reject -> rejected` to every human step. For tasks I would expect "decline" to return the task to the filer with the reason, not to close it.
Evidence: TASK-0004 record; `release.render_workflow` rejection return noted in README.

---

## Things that worked as expected (for the triager's calibration, not praise)

- Assignee enforcement: alice deciding dave's task is refused with "alice is not an assignee of this decision (assigned: dave)".
- Segregation of duties: carol's own NCR-2026-0005 went to bob.
- Out-of-tolerance calibration opens and links an NCR; the cog path, when reached, records the delegating sentence and its reason ("risk_low holds").
- Check-out/check-in on a working document via the CLI (DOC-0170: alice checks out, dave's check-in and check-out are refused with "checked out by alice", alice checks in to rev 1.1); the `/document/` page has Check out / Upload a new version controls.
- Shell attach works when no CLI process has created records in between (DOC-0132 on TASK-0002, DOC-0145/DOC-0146 on NCR-2026-0003).
- Escalation of overdue NCR/training/complaint/change items at tick to the roles the SOPs name.
