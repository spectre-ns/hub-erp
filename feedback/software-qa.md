# Software QA findings

Panelist: software QA engineer. Everything below was reproduced over HTTP against
the Docker hub (`http://127.0.0.1:8080`) and, where stated, the container CLI
(`docker compose exec -T hub hubproto ...`), on 2026-11-06 between 14:43 and
15:00 UTC, release `b7da4af94d3c571f`. All records I created carry `[QA]` in the
title except the child records the engine raises itself (purchase requests,
receipts, POs, journals), which are named by number below.

Shorthand used in the steps: `POST /x as=U k=v` means
`curl -s -w '%{http_code} %{redirect_url}' 'http://127.0.0.1:8080/x' --data-urlencode as=U --data-urlencode k=v ...`
(form-urlencoded). `digest=<from inbox>` means the hidden `digest` value copied
from that run's card on `/inbox?as=<assignee>`. The design payload referred to as
`design.json` is `{"kind":"electrical","change_class":"3","user_needs":[...two
strings...],"attachments":[{"name":"sensor.kicad_sch","content":<file text>,
"doc_class":"controlled","doc_type":"schematic"},{"name":"box.FCStd",
"content_b64":<base64 of examples/box.FCStd>,"doc_class":"controlled",
"doc_type":"model"}]}`.

## Scenario result (feedback/README.md steps 1-6)

| Step | Result |
|---|---|
| 1 design-release | Works only when `kind` is put in the JSON textarea (F-qa-2) and the CAD files are base64-pasted into the JSON (F-qa-14). Then: two requirements waiting for bob, trace CSV, BOM, ERC report, STEP on the design, design waits for bob with risk `high`. Verified on DSN-2026-0006 / run `d1c8479da2`. |
| 2 accept requirements, review design | Acceptance with criteria works (REQ-2026-0011). Acceptance *without* criteria is also accepted and then kills the requirement (F-qa-3). Approval released PN-00005 and raised PR-00022/PR-00023; no item was created because other panelists' approvals had already created ITEM-00001/2 (duplicates are created otherwise, F-qa-13). |
| 3 purchasing | Cog approved a 0.50 USD request from `acme-metals` on a known item (PR-00025 -> PO-0016, delegating sentence on the Track). 12,000 USD went to bob (PR-00026). `newco-tools` was escalated by the cog, never approved by it (PR-00027). Dave's supplier and unit cost given at the Gate do not reach the PO (F-qa-4); dave can order from `newco-tools` (F-qa-5). |
| 4 goods-receipt | Works: GR-00017 on PO-0014, ITEM-00002 stock 127 -> 129, PO-0014 `received`. Negative quantities are booked (F-qa-6). |
| 5 purchase-to-pay | Works on PO-0016: BILL-0040 matched, approved by ap-reviewer within criteria, JE-00024 balanced (0.50 / 0.50). The journal is in `Finance/Payables`, not on `/files?folder=Finance` (F-qa-15). |
| 6 Track, Why, snapshots, boards | Track and delegating sentences present; Why gives the sentence and a line link for explicit denials; snapshots are immutable (check-out and check-in refused with `KernelGuardError`); boards filter by type/state/folder/assignee/text. Run pages leak to anyone (F-qa-7). |

Verified and not a finding: owner cannot approve own item (SoD, `alice`
refused on every Gate); a cog-filed request skips the cog as approver and goes to
dave (PR-00045); stale `digest` refused; double decision refused; concurrent
approvals by dave and bob on PR-00029 serialized (one 303, one 400, one
signature); `<script>`/`<img onerror>` in title, description, supplier, message
subject and body are escaped on boards, record, inbox, run and runs pages;
`hubproto tick --today 2099-01-01` escalates PR-00038 to bob with an escalation
card and is idempotent on a second tick; evidence documents cannot be checked
out or in; `/download` refuses principals without read permission (403).

## Findings

### F-qa-1: A Gate on a failed run stays decidable; approving it released a failed design
Severity: blocker
Area: workflow
Steps:
1. `POST /start as=alice process=design-release title="[QA] Sensor board" kind=electrical json=<design.json without the "kind" key>` -> 303 to `/run/f21db81ca8`.
2. The record DSN-2026-0004 is in state `failed` (Activity: `record.transitioned ... reason "required-fields: DSN-2026-0004 cannot enter 'awaiting-review-the-design' without kind", to "failed"`); the run status is `failed`; yet `/run/f21db81ca8` says "Waiting at Review the design for bob until 2026-11-16" and `hubproto run-show f21db81ca8` prints `pending: Review the design -> bob`.
3. `POST /decide as=bob run=f21db81ca8 verdict=approve meaning="[QA] approving a failed run"` -> 303.
4. Open `/record/rec:design:DSN-2026-0004?as=bob`.
Expected: step 3 refused ("run is not waiting for a decision" or "run failed"); a failed record is never released.
Actual: Decision accepted. Grant `cde25cb2` issued, `part-releaser` and `shortage-requester` ran: DSN-2026-0004 is `released`, part PN-00004 `released`, ITEM-00003/ITEM-00004 created, PR-00020/PR-00021 raised and waiting for dave. The design has no `kind`.
Evidence: run `f21db81ca8`, DSN-2026-0004, PN-00004, PR-00020, PR-00021. Second reproduction: run `ba72ecd961` / DSN-2026-0021 (state `failed`) is offered for approval on `/inbox?as=bob` (card with `name="run" value="ba72ecd961"`) and counted in "45 approvals waiting for me" on bob's home page. Left undecided.
Suggested fix: `runner._fail` must clear `state["pending"]` (or `decide` must refuse when `status != "waiting"`), and `_waiting_for` in the shell should only list runs with status `waiting`.

### F-qa-2: Start form silently drops fields it does not know; required-by-state fields are not asked for, so the run fails after every automatic step
Severity: major
Area: shell
Steps:
1. On `/?as=alice` the `design-release` card offers title, description, amount, currency, date, po_ref, supplier, change_type, severity, training_roles and a JSON textarea. There is no `kind` input although `design.required_by_state["awaiting-review-the-design"] = ["title","kind"]`.
2. `POST /start as=alice process=design-release title="[QA] Sensor board" kind=electrical json=<design.json without "kind">` -> 303 (the `kind=electrical` form field is ignored because `post_start` only reads `COMMON_INPUTS`).
3. Watch the run: record-filer, requirements-tracer, cad-exporter, bom-reconciler and risk-scorer all run (two requirement records REQ-2026-0008/0009 routed to bob, ten documents DOC-0029..DOC-0038 filed, CAD worker called), then the transition to `awaiting-review-the-design` fails and the record goes to `failed`.
Expected: the Start form shows the fields the first waiting state requires; unknown form fields are rejected or kept; missing required fields are refused at submit, before any cog runs.
Actual: Silent drop, expensive side effects, then `failed`. The two requirement records stay waiting for bob (due 2026-11-11) for a dead design, and bob is not told the design failed.
Evidence: run `f21db81ca8`, DSN-2026-0004 Activity seq 267; `hubproto records --q "[QA]"` shows REQ-2026-0008/0009 `awaiting-accept-the-requirement assignees=bob`.
Suggested fix: validate `required_by_state` for the first `awaiting-*` state in `runner.submit`; render those fields on the Start form; refuse unknown form keys.

### F-qa-3: Required fields at a Gate are enforced only when the next state is immediate; an approval without them is signed and the run then fails at close
Severity: major
Area: workflow
Steps:
1. REQ-2026-0010 (run `33dc010f39`) waits for bob; the inbox card says "accepted needs: acceptance_criteria" and renders the input with HTML `required` only.
2. `POST /decide as=bob run=33dc010f39 verdict=approve meaning=x` (no `acceptance_criteria`, no `verification_method`).
Expected: 400 "accepted needs acceptance_criteria before approval; supply them with the decision" (the message the runner has for this case).
Actual: 303. The decision is recorded as bob's signature (Decisions table: `bob (person) approve x`), `document-renderer` runs and files DOC-0079, then the close fails: `record.transitioned ... reason "cannot close as accepted: required-fields: REQ-2026-0010 cannot enter 'accepted' without acceptance_criteria", to "failed"`. Run `33dc010f39` is `failed`; a second decision is refused ("not waiting"). The requirement is dead and the design DSN-2026-0006 still traces to it as `awaiting-accept-the-requirement`.
Evidence: run `33dc010f39`, REQ-2026-0010. Cause: `runner.decide` checks `required_missing` only `if self._immediate_next(...)`; in `requirements-capture` an automatic step (`Render the record`) follows the checkpoint, so the check is skipped.
Suggested fix: check `required_missing` for the next human-visible state (or the terminal state when only automatic steps remain) before recording the decision.

### F-qa-4: The buyer's supplier and unit cost supplied at the Gate do not reach the purchase order; the PO is issued with amount 0
Severity: major
Area: workflow
Steps:
1. PR-00023 (2 x RC0603FR-0710KL for DSN-2026-0006, raised by shortage-requester, `unit_cost 0.0`, `amount 0.0`, `supplier_note "no supplier named"`) waits for dave.
2. `POST /decide as=dave run=2208e37a9d verdict=approve meaning="[QA] ordered from Acme" digest=<from inbox> supplier=acme-metals unit_cost=0.05`.
3. Open `/record/rec:purchase-request:PR-00023?as=dave` and `/record/rec:purchase-order:PO-0014?as=dave`.
Expected: PR amount 0.10 USD, `supplier_approved true`; PO-0014 supplier `acme-metals`, line unit_cost 0.05, amount 0.10.
Actual: PR-00023 shows `supplier acme-metals` but `amount 0.0`, `unit_cost 0.0`, `supplier_approved False`, `supplier_note "no supplier named"` (stale). PO-0014: `amount 0.0`, line `unit_cost 0.0, amount 0.0`. Consequence downstream: `POST /start as=alice process=purchase-to-pay supplier=acme-metals amount=0.10 currency=USD date=2026-11-06 po_ref=PO-0014` -> BILL-0041 "price-variance 100.00% exceeds tolerance 2.0%", waits for dave (run `1162eb00b6`). The test suite passes the same flow only because the test's item already carries a unit cost.
Evidence: PR-00023, PO-0014, runs `2208e37a9d`, `1162eb00b6`.
Suggested fix: `po-issuer` reads `prior` (the supplier-checker payload) instead of the record; re-run the supplier check with the decision's fields, or read the record after the decision.

### F-qa-5: A buyer can issue a purchase order to a supplier that is not approved
Severity: major
Area: policy
Steps:
1. PR-00022 (1 x CL10B104KB8NNNC) waits for dave; `purchasing-policy` prose: "The supplier must be on the approved list"; `newco-tools` is seeded as registered, not approved.
2. `POST /decide as=dave run=f846e3a890 verdict=approve meaning="[QA] ordered from NewCo (not approved)" digest=<from inbox> supplier=newco-tools unit_cost=0.10`.
Expected: refused (supplier not approved), or at least a Guard error before the PO is issued.
Actual: 303; PO-0015 issued with `supplier newco-tools`, `approved_by dave`. No problem is recorded on the Track for the approval step. (The cog path is correct: PR-00027 naming `newco-tools` got `gate.cog_review` "condition supplier_approved not met" and went to the buyer.)
Evidence: PR-00022, PO-0015, run `f846e3a890`; compare run `197dfa550e`.
Suggested fix: make the supplier check a Guard with error severity at `Issue the purchase order`, evaluated against the supplier actually on the record at decision time.

### F-qa-6: Negative quantities are accepted, approved by the cog "within fixed criteria", and booked as negative stock
Severity: major
Area: workflow
Steps:
1. `POST /start as=alice process=purchase-request title="[QA] PR negative qty" json='{"item":"rec:item:ITEM-00002","quantity":-5,"supplier":"acme-metals","unit_cost":0.05}'` -> run `a7131616e6`.
2. `POST /start as=alice process=goods-receipt title="[QA] GR negative qty on PO-0015" json='{"po":"rec:purchase-order:PO-0015","lines":[{"item":"rec:item:ITEM-00001","quantity":-3}]}'` -> run `814aaa7231`; then `POST /decide as=dave run=814aaa7231 verdict=approve digest=<from inbox>`.
Expected: both refused at submit (quantity must be > 0); at minimum the cog must not approve.
Actual: PR-00030 `quantity -5`, `amount -0.25`, approved by purchasing-assistant (cog) "approved within fixed criteria" under purchasing-policy line 53 (the amount is "under 500 USD"), PO-0018 issued. GR-00022 passed `receipt-checker` with no problem and dave's confirmation moved ITEM-00001 stock from 4.0 to 1.0.
Evidence: PR-00030, PO-0018, GR-00022, runs `a7131616e6`, `814aaa7231`, ITEM-00001 Activity `stock.received quantity -3`.

### F-qa-7: Run pages and the Runs list are not permission-trimmed; any principal, member or not, can read every run's payload and Track
Severity: major
Area: security
Steps:
1. `curl -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8080/record/rec:design:DSN-2026-0006?as=mallory'` -> 403 (mallory is not a member).
2. `curl -w '%{http_code}' 'http://127.0.0.1:8080/run/d1c8479da2?as=mallory'` -> 200: the page shows the design's record fields (BOM, requirement texts, documents), the grant, bob's decision and the full Track.
3. `curl 'http://127.0.0.1:8080/runs?as=mallory'` -> 200 with every run (16 design-release rows at the time), record numbers and summaries.
4. Same for a member without read permission: `/run/d1c8479da2?as=dave` -> 200 while `/download?as=dave&doc=rec:document:DOC-0029` -> 403 and `/why?as=dave&action=read` on Engineering is denied.
Expected: `/run/<id>` and `/runs` apply the same read check as `/record/<ref>` (folder of the run's record), and non-members get nothing.
Actual: no check in `page_run` or `page_runs`.
Evidence: run `d1c8479da2`.

### F-qa-8: The approver can overwrite any record field with the decision (including `requested_by` and `risk`)
Severity: major
Area: security
Steps:
1. PR-00028 waits for dave (`requested_by alice`, `risk low`).
2. `POST /decide as=dave run=dad22bd2a8 verdict=approve meaning="[QA] inject" digest=<from inbox> risk=low requested_by=dave po=rec:purchase-order:PO-0001 supplier_approved=false amount=1`.
Expected: only the fields the next state requires (or an allow-list declared for the checkpoint) are accepted; `requested_by`, `risk`, `conditions`, `supplier_approved`, `po` are engine-owned.
Actual: 303; the record now shows `requested_by dave` (the approver is now also the requester, defeating the segregation-of-duties evidence), `risk low` written by dave; the Track has `record.fields_set by dave fields [amount, po, requested_by, risk, supplier_approved]`. `po` and `amount` were later overwritten by po-issuer, which hides the injection on the final record.
Evidence: PR-00028, run `dad22bd2a8` Track seq after `gate.decided`. Cause: `post_decide` accepts every form key that is in the type's field list (`k in td`).

### F-qa-9: A hold does not block the decision; the signature and grant are recorded, the next cog refuses, and the run dies
Severity: major
Area: records
Steps:
1. `POST /hold as=dave record=rec:purchase-request:PR-00027 on=1 reason="[QA] supplier under review"` -> record shows "on hold".
2. `POST /decide as=bob run=197dfa550e verdict=approve meaning="[QA] approve while on hold" digest=<from inbox>` -> 303.
3. `POST /hold as=dave record=rec:purchase-request:PR-00027 on=0`, then `POST /decide as=dave run=197dfa550e verdict=approve`.
Expected: step 2 refused ("PR-00027 is on hold"); after the hold is released the Gate is still pending.
Actual: step 2 recorded bob's decision and grant `f405570f`; `po-issuer` failed with `guard-refused hold: PR-00027 is on hold`; run `197dfa550e` is `failed`; the record stays `awaiting-approve-the-purchase` with no pending Gate; step 3 -> 400 "run 197dfa550e is not waiting for a decision". The request is now unrecoverable.
Evidence: PR-00027, run `197dfa550e` Track (`gate.decided`, `run.failed reason "hold: PR-00027 is on hold"`).
Suggested fix: `decide` checks `rec["hold"]` first; a cog refusal on hold should park the run, not fail it.

### F-qa-10: "Rejections return to Submit the design" is a dead end: no assignee, no pending Gate, no notification, no way to resubmit
Severity: major
Area: workflow
Steps:
1. DSN-2026-0017 (run `9ac82d7eb9`) waits for bob.
2. `POST /decide as=bob run=9ac82d7eb9 verdict=reject meaning="[QA] package incomplete, resubmit" digest=<from inbox>` -> 303.
3. `/record/rec:design:DSN-2026-0017?as=alice`, `/inbox?as=alice`, then `POST /decide as=alice run=9ac82d7eb9 verdict=approve`.
Expected: the design is back with alice as an actionable item (inbox card, a resubmit action that re-runs the automatic steps with the edited package), and alice is told why it was returned.
Actual: record state `awaiting-submit-the-design` (a state that is not in the workflow's state list; the design board shows it as an extra column), assignees `-`, run status `rejected`, no `pending`; alice's inbox has no message about DSN-2026-0017 (the "(returned)" notification only exists for rejections that return to a *checkpoint*); alice's decide -> 400 "not waiting". The CLI `hubproto decide 9ac82d7eb9 --as alice --verdict approve` gives the same error. The same applies to a rejected requirement (REQ-2026-0020 -> `rejected`, alice not notified).
Evidence: DSN-2026-0017, run `9ac82d7eb9` (`run.rejected to awaiting-submit-the-design`), REQ-2026-0020 run `107c998c52`.

### F-qa-11: Released (terminal) records stay mutable: attachments, cross-folder documents, non-documents and self-links can be added after the signature
Severity: major
Area: records
Steps (all on the released design DSN-2026-0006, as alice):
1. `curl -F as=alice -F record=rec:design:DSN-2026-0006 -F title="[QA] late attachment to a released design" -F file=@x http://127.0.0.1:8080/attach` -> 303.
2. `POST /attach as=alice record=rec:design:DSN-2026-0006 doc=rec:document:DOC-0040` (WI-17, a Quality/Controlled Documents work instruction) -> 303.
3. `POST /attach as=alice record=rec:design:DSN-2026-0006 doc=rec:part:PN-00005` (a part, not a document) -> 303.
4. `POST /link as=alice record=rec:design:DSN-2026-0006 rel=supersedes target=DSN-2026-0006` -> 303.
Expected: a record in its terminal state is frozen (or changes need a new Gate); `/attach` with `doc` checks the target is a document; a record cannot supersede itself.
Actual: all accepted. The Documents table of the released design now lists `PN-00005 [QA] Sensor board v2` as a document, WI-17 from another folder, and a new external upload; Links shows `supersedes DSN-2026-0006 out`. The released snapshot (DOC-0171) no longer matches the record. Nonexistent targets and unknown relations are refused (400), so only type and state checks are missing.
Evidence: DSN-2026-0006 Documents and Links tabs.

### F-qa-12: BOM proposals computed at submission are applied at approval without re-checking, creating duplicate inventory items
Severity: major
Area: workflow
Steps:
1. Submit a design while no item exists for its MPNs (DSN-2026-0004, 14:43:58: `bom_unresolved` has both lines with `proposal` fields).
2. Let another approval create ITEM-00001 (CL10B104KB8NNNC) and ITEM-00002 (RC0603FR-0710KL) in the meantime (happened at ~14:44 on this hub).
3. Approve the first design (here via F-qa-1; by the same code path it would happen for any two designs submitted before either is approved, which I did not reproduce separately).
Expected: `shortage-requester` re-resolves the proposals by MPN and manufacturer before creating items.
Actual: ITEM-00003 (CL10B104KB8NNNC, Samsung) and ITEM-00004 (RC0603FR-0710KL, Yageo) created, duplicating ITEM-00001/00002; the item board now has two records per MPN and later reconciliations will match either.
Evidence: `/boards?as=bob&type=item`; DSN-2026-0004 `items_created ["rec:item:ITEM-00003","rec:item:ITEM-00004"]`.

### F-qa-13: The scenario cannot be driven as written from the shell: no file input on Start, no way to maintain inventory items
Severity: major
Area: shell
Steps:
1. `/?as=alice`: the `design-release` Start card has no `<input type="file">`; the only way to attach the two example files at submission is to paste `{"attachments":[{"name":"box.FCStd","content_b64":"<3.2 KB base64>"...}]}` into the "other fields (JSON object)" textarea. Attaching after Start via `/attach` is too late: `cad-exporter` has already run and reports "no FreeCAD or KiCad document is attached".
2. Step 3 of the scenario needs a *known item from an approved supplier with a unit cost*. No shell page, Start form or CLI command creates or edits an `item` (no process has record type `item`; `hubproto --help` has no record-create command); items only appear as `shortage-requester` proposals with `stock 0`, no supplier, no unit cost. I reached the cog path only by naming `supplier` and `unit_cost` directly in a purchase request's JSON.
Expected: a file input on the Start form (the shell's upload code already handles multipart), and a way for a design engineer (who holds `create items in Inventory/Items`) to create an item with supplier and unit cost.
Actual: missing.
Evidence: `hubproto/shell.py` `page_home` form; `/boards?as=alice&type=item` was empty before any approval.

### F-qa-14: The journal is filed in `Finance/Payables`; `/files?folder=Finance` shows nothing
Severity: minor
Area: shell
Steps:
1. After BILL-0040 posted: `/files?as=alice&folder=Finance`.
Expected (README step 5): "a balanced journal in `/files?folder=Finance`".
Actual: the Finance page lists only records whose folder is exactly `Finance` (none) and sub-folder links; JE-00024 is on `/files?as=alice&folder=Finance%2FPayables`. Either the README or the page's folder scoping (no roll-up of sub-folders) is wrong.
Evidence: JE-00024 (`lines [{5000 Purchases debit 0.5},{2000 Accounts payable credit 0.5}]`, `approved_by ap-reviewer`, `grant 331fb316`).

### F-qa-15: Every signature is shown twice in the record page's Decisions table
Severity: minor
Area: shell
Steps:
1. `/record/rec:purchase-request:PR-00025?as=dave#decisions` (one cog decision).
Expected: one row.
Actual: two identical rows (same signature `85865b338da25b8cb88d8517`, same payload). `page_record` concatenates `record.decision` events with `rec["decisions"]`, which are the same data. Same on PR-00029, PR-00030, REQ-2026-0010.

### F-qa-16: Uploads and check-ins accept empty files, any type and traversal filenames; the filename is echoed unescaped into `Content-Disposition`
Severity: minor
Area: DMS
Steps:
1. `curl -F as=alice -F folder=Engineering/Designs -F title="[QA] empty" -F file=@empty.bin http://127.0.0.1:8080/upload` (0 bytes) -> 303, DOC-0173, size 0.
2. Same with a 300 KB random `blob.exe` -> 303, DOC-0174.
3. Same with `-F 'file=@x;filename=../../etc/passwd'` -> 303, DOC-0176; `curl -D - '/download?as=alice&doc=rec:document:DOC-0176'` returns `Content-Disposition: attachment; filename="../../etc/passwd"` (`quote()` does not encode `/` or `.`).
4. `curl -F as=alice -F doc=rec:document:DOC-0029 -F file=@empty.bin http://127.0.0.1:8080/checkin` -> 303: a 0-byte version becomes the current version of a controlled CAD document.
Expected: empty content refused; a filename sanitized to its basename; at least an allow-list or a size cap for external intake.
Actual: as above.
Evidence: DOC-0173, DOC-0174, DOC-0176, DOC-0029 versions.

### F-qa-17: A blank principal silently acts as alice; non-members can send messages; messages to unknown recipients are accepted
Severity: minor
Area: security
Steps:
1. `curl -w '%{http_code} %{redirect_url}' http://127.0.0.1:8080/start --data-urlencode as= --data-urlencode process=purchase-request --data-urlencode title="[QA] blank principal POST" --data-urlencode 'json={"quantity":1}'` -> `303 /run/072ee5e9fa?as=alice`: a request is filed as alice. `GET /record/...?as=` also renders as alice.
2. `POST /msg as=mallory to=bob subject="[QA] from a non-member" body=hi` -> 303; the message is in bob's inbox "from mallory".
3. `POST /msg as=alice to=nobody subject="[QA] ghost"` -> 303, no error.
Expected: an empty or unknown `as` is an error (the README says `as` *is* the identity); a recipient must be a member.
Actual: `DEFAULT_USER = "alice"` fallback on both GET and POST; no sender or recipient validation.

### F-qa-18: No size limit on text inputs; a 2 MB description is accepted and rendered in full on every inbox card
Severity: minor
Area: performance
Steps:
1. `curl http://127.0.0.1:8080/start --data-urlencode as=alice --data-urlencode process=purchase-request --data-urlencode title="[QA] 2MB description" --data-urlencode description@big.txt --data-urlencode 'json={"quantity":1}'` (big.txt = 2,000,000 x "B") -> 303 in 1.4 s (run `7cb53660e2`).
2. `curl -o /dev/null -w '%{size_download}' 'http://127.0.0.1:8080/inbox?as=dave'` -> 2,216,076 bytes (was ~70 KB).
Expected: a cap on field length, and inbox payload summaries truncated (BLOB_FIELDS covers `text`/`body`/`citations` but not `description`/`reason`).
Actual: the whole value is embedded in the inbox of every assignee and in the record page.

### F-qa-19: Over-receipt is only a warning; wrong line or unknown PO fails the run instead of being refused at submit; seeded POs cannot be received
Severity: minor
Area: workflow
Steps:
1. `POST /start as=alice process=goods-receipt json='{"po":"rec:purchase-order:PO-0015","lines":[{"item":"rec:item:ITEM-00001","quantity":5}]}'` (ordered 1) -> waits for dave with warning "over-receipt received 5 of ..., ordered 1" (run `f3514d9276`).
2. `... "lines":[{"item":"rec:item:ITEM-00002","quantity":1}]` (not on PO-0015) -> run `47a924d7b3` `fail`, record GR left `failed`.
3. `... "po":"rec:purchase-order:PO-9999"` -> run `00c1549cbb` fail "po-missing".
4. `... "po":"PO-1001"` (seeded, used by purchase-to-pay matching) -> run `917e5f6869` fail "no purchase order 'PO-1001'".
Expected: 2-4 refused at submit with the reason (no dead `failed` receipt records); seeded POs either receivable or the bill matcher should not accept them either; over-receipt beyond a tolerance an error.
Actual: as above.

### F-qa-20: `hubproto tick --today` accepts an invalid date silently
Severity: minor
Area: CLI
Steps:
1. `docker compose exec -T hub hubproto tick --today notadate`.
Expected: usage error.
Actual: "tick done" (string comparison against ISO dates; `"notadate" > "2026-..."` would mark everything overdue on a hub with un-escalated items).

### F-qa-21: The optional `digest` makes the stale-payload check bypassable; the CLI has no digest at all
Severity: minor
Area: workflow
Steps:
1. `POST /decide as=bob run=<id> verdict=approve meaning=x` with no `digest` -> accepted (used throughout this report).
2. `hubproto decide <id> --as bob --verdict approve` has no `--digest` option.
Expected: the digest the approver saw is mandatory for a person's signature, since the Track records "payload_digest" as what was signed.
Actual: `decide` skips the comparison when the digest is absent.

### F-qa-22: Archive and label lowering are not reachable from the shell or the CLI
Severity: minor (missing)
Area: records
Steps:
1. Search `/record/<ref>` pages, `/admin`, `hubproto --help`, `hubproto/cli_records.py`, `hubproto/shell.py` for archive or label actions.
Expected: a way to archive a closed record and to lower a sensitivity label through a Gate decision, as `kernel.archive()` and `kernel.set_labels()` (which raises `label-lowering` without a decision) imply.
Actual: neither has a route or a command; the guard cannot be exercised by a user. Not tested further.

### F-qa-23: Cog message intake cannot be used from the shell
Severity: minor
Area: shell
Steps:
1. `POST /msg as=alice to=purchasing-assistant channel=dm subject="[QA] please raise a request" body="please file a purchase request for 5 x RC0603FR-0710KL from acme-metals"`.
Expected (README "Cogs are team members ... file records"): a purchase request, or a reply saying what is missing.
Actual: reply "I can file an attached record into a process, or report status. I approve only within the criteria in the current policy release." The cog only files from attachments, and the shell's message form has no attachment input (only the CLI `msg --attach` has), so the capability is unreachable here.

### F-qa-24: Boards: unknown type renders an empty board without an error; requirement cards show no text; default board is `calibration`
Severity: minor
Area: shell
Steps:
1. `/boards?as=dave&type=nonsense` -> 200, "Boards nonsense", columns `failed 0 rejected 0`.
2. `/boards?as=bob&type=requirement` -> every card reads `REQ-2026-00NN REQ-2026-00NN` (summary uses title/memo/description/name; requirements have `text`).
3. `/boards?as=alice` -> type `calibration` (first workflow in the bundle), not something the user works with.
Expected: 404 for an unknown type; the requirement text on the card; a sensible default (last used or the type with most items for the user).

### F-qa-25: The design is released with `requirements_accepted False` and a stale trace
Severity: question
Area: workflow
Steps:
1. DSN-2026-0006: REQ-2026-0011 accepted, REQ-2026-0010 failed (F-qa-3). `/record/rec:design:DSN-2026-0006?as=bob` after release.
Expected: either the design cannot be released while a requirement is not accepted, or the trace is refreshed at review time so the design authority sees the current state.
Actual: released with `requirements_accepted False`, `risk high`, and `requirement_trace` still saying REQ-2026-0010 is `awaiting-accept-the-requirement` (it is `failed`). The trace and risk are computed once at submission; accepting requirements afterwards changes nothing on the waiting design. The SOP prose says "Nothing reaches inventory without that signature", not "without accepted requirements", so this may be intended; then the risk warning should at least be current.

### F-qa-26: Release cutover: not tested (Frames are bind-mounted from the repo); what I would test
Severity: question
Area: policy
Steps run: `hubproto releases` (one release `b7da4af94d3c571f`), `hubproto compile` with unchanged Frames -> proposal `a2ceac194a6b`, `rules: 197 changes: 0 findings: 5` (five consistency warnings about roles that may both create and approve). Not reviewed or released.
What I would test with a Frame copy: (a) raise "The cog purchase limit" and the routing "under 500 USD" to 1,000 USD, release as bob + carol, then confirm a request started before the cutover still routes and is approved under the old digest (the run pins its release) while a new one uses the new rule; (b) remove `Design authorities may approve designs` and check that a pending design review refuses bob with the old sentence quoted; (c) a no-op edit (whitespace) yields the same digest; (d) widen a permission with only one owner's approval and confirm release is refused; (e) `hubproto review` by `alice` (not a policy owner) is refused; (f) the Releases page's "Routing in effect" table changes while a waiting run's `/run/<id>` still shows the old pinned digest.

## Test suite

`cd hub-erp-proto && . .venv/bin/activate && python -m pytest -q` (pytest 9.2.0,
Python 3.14.7; `pytest-randomly` is not installed, so no seed to report):

- Full suite, run 1: 171 passed in 8.29 s.
- Each file alone, in reverse alphabetical order (`test_workflow`, `test_shell`,
  `test_runtime`, `test_records`, `test_profile`, `test_procurement`, `test_dms`,
  `test_compile`, `test_cad`): all pass (40, 24, 15, 25, 19, 3, 26, 12, 7).
- Full suite, run 2 (`-p no:cacheprovider`): 171 passed in 7.89 s; slowest test
  `test_procurement.py::test_design_to_order_to_receipt_to_ledger` 1.09 s.

No flaky or order-dependent test was observed. Note that the suite does not cover
F-qa-1, F-qa-3, F-qa-4 (the test pre-creates an item with a unit cost), F-qa-6,
F-qa-7, F-qa-8, F-qa-9 or F-qa-10; `test_procurement` asserts the rejection and
hold paths nowhere.
