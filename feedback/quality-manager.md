# [QM] Quality Manager findings (carol)

Panelist: carol, quality manager and document controller (ISO 9001 lead; the
internal auditor reports to me). Judged as a QMS I would have to defend in a
certification audit. Release in effect during the session:
`b7da4af94d3c571f`, effective 2026-11-06T14:38:17Z, released by bob. Other
panelists were active at the same time (their records carry `[OPS]`/`[DE]`
prefixes; one of them restarted the container at about 14:46Z and ran `tick`
with a future date at 14:49Z). All record numbers below are mine unless noted.

Severity counts: blocker 5, major 19, minor 13, question 1 (38 findings).

Records produced in this session, for the maintainer's reference:
DOC-0040 (WI-17 rev A, human-approved, then edited, then obsolete),
DOC-0041 (WI-18 minor, cog-approved), DOC-0191 (WI-17 rev B), drawing run
256a844701; NCR-2026-0001 (medium, rework), NCR-2026-0002 (high, scrap),
NCR-2026-0010 and -0012 (low, cog), NCR-2026-0011 (mis-scored, rejected),
NCR-2026-0013 (from CAL-00001), NCR-2026-0014 (from CMP-2026-0001);
CAPA-2026-0002, CAPA-2026-0003; CMP-2026-0001; CAL-00001; SUP-0001;
CR-2026-0001; AUD-2026-001, AUD-2026-002; MR-2026-001; TRN-00001..00012.

---

## Part 1: running the processes

### F-qm-1: Content of an effective controlled document can be replaced without review, approval or training
Severity: blocker
Area: DMS
Steps:
1. As bob, approve DOC-0040 "WI-17 Hand soldering work instruction" (run f295d6771b). Record goes `effective`, `approved_by bob`, revision A.0.
2. As carol, open `/document/rec:document:DOC-0040?as=carol`. Press **Check out** (POST `/checkout`, `doc=rec:document:DOC-0040`).
3. Press **Check in** with a file containing `# WI-17 edited outside document control / Solder at 450 C.` (POST `/checkin`).
4. Reload the document page; run `hubproto record DOC-0040`.
5. For contrast, ask `/why?as=carol&action=update&object=document&folder=Quality/Controlled Documents`.
Expected: an effective controlled document is immutable; a change goes through document-control as a new revision (7.5.2 b-c, 7.5.3.2 c); at minimum the record must leave `effective` and the four TRN-0000x acknowledgements for rev A must be flagged.
Actual: check-in accepted (303). DOC-0040 is now revision **A.1**, state still `effective`, still `approved by bob`, content `Solder at 450 C.`; version 2 "edited" by carol. The Why page says carol may **not** `update document` in Quality/Controlled Documents ("denied: no rule in the release lets any of [...] update document"), but the shell only checks the `create` permission (`may_file`) for check-in. TRN-00005..00008 still say "Read WI-17 rev A". After I superseded it (F-qm-24) I checked in again: the **obsolete** record became A.2.
Evidence: `hubproto record DOC-0040` -> `state=effective`, `minor 1`, `content_sha 371a008b...`; document page versions 1 (alice, draft) and 2 (carol, "edited"); `/why` verdict "denied".
Suggested fix: check-in on a `controlled` document in `effective` or `obsolete` must be refused; the only way to change content is a document-control run that supersedes it. Why and the shell must use the same permission.

### F-qm-2: Escalation lets the quality manager sign another person's training acknowledgement
Severity: blocker
Area: workflow
Steps:
1. DOC-0041 went effective (F-qm-6); TRN-00004 "Read WI-18 ESD handling rev A" was opened for dave (run b841c13e0f), assignee dave.
2. `hubproto tick --today <future>` was run (by another panelist, at 14:49Z). Training-sop: "Overdue items in training-acknowledgement go to the quality manager." TRN-00004 now shows `assignees=dave,carol`.
3. As carol, from `/inbox?as=carol`, approve TRN-00004 with meaning "QM acknowledging on behalf of dave".
Expected: a training acknowledgement is a personal attestation ("the person's electronic signature that they have read and understood the revision", training-sop body line 9-10). Nobody but the assignee may sign it. Escalation should notify, not add an approver.
Actual: accepted. TRN-00004 is `acknowledged`, field `person dave`, decision "carol (person) approve: QM acknowledging on behalf of dave". The training record now asserts dave read a document he never acknowledged. The same mechanism puts the document controller onto every overdue document approval (document-control-sop: "Overdue items in document-control go to the document controller") and the management representative onto every overdue NCR, CAPA, audit and change.
Evidence: `hubproto record TRN-00004` decisions and transition `awaiting-acknowledge-the-document -> acknowledged`.
Suggested fix: `Overdue items ... go to X` should mean notify X (and show on their inbox as escalation), not add X to `pending.assignees`. For personal attestations (training) the assignee must never be widened.

### F-qm-3: Identity is a URL parameter; no authentication; the "signature" is a hash of four fields with no credential and no meaning
Severity: blocker
Area: security
Steps:
1. Open `http://127.0.0.1:8080/?as=mallory`. Then `?as=bob`, `?as=carol`.
2. Decide anything as anyone: `curl -X POST /decide -d as=bob -d run=... -d verdict=approve`; or `hubproto decide --as bob --verdict approve <run>`.
3. Read `hubproto/runner.py` line 336 (shown in the run page Track as `signature`): `signature = sha256({by, verdict, payload_digest, at})[:24]`.
Expected (ISO 9001 7.5.3.1 b, 7.5.3.2; 21 CFR 11.10(d), 11.50, 11.70, 11.200): a signature is bound to one authenticated person (two components, re-authentication at signing or step-up for the signing act), carries the printed name, date/time and meaning, and is linked to its record so that it cannot be excised, copied or altered.
Actual: anyone who can reach port 8080 can act as any member, including policy owners (release approval) and cogs. `mallory` gets a page with "Roles: none" instead of a refusal. There is no password, token, session or step-up. The signature digest excludes the **meaning** and the fields set with the decision, so the meaning text in the record could be edited without invalidating the signature; it is a truncated SHA-256 of public values that anyone can recompute, i.e. not a signature at all, only a checksum. The printed name is the login string (`carol`), not a full name.
Evidence: `/?as=mallory` returns 200 "Welcome, mallory"; `hubproto decide --help` shows `--as AS_USER`; runner.py line 336.
Suggested fix: the prototype README says "no SSO in the prototype"; say so on every signature page and in the conformance report as a Part 11 / 7.5.3 non-conformity, include the meaning and the decided fields in the signed digest, and refuse unknown principals.

### F-qm-4: List fields set from the decision form are split on commas, destroying audit findings and CAPA action lists
Severity: blocker
Area: shell
Steps:
1. As bob, on `/inbox?as=bob`, approve AUD-2026-001 "Record the findings" (run c75c76601c) with `findings` = `[{"clause":"7.5.3","statement":"DOC-0041 went effective with placeholder body...","evidence":"DOC-0041 version 1, 34 bytes","severity":"major"},{...}]`.
2. `hubproto record AUD-2026-001`.
3. Same with CAPA-2026-0003 (run 0bd6813bf5), `actions` = a JSON list of three actions.
Expected: a `list` field holding one object per finding (the audit-finding template has clause, statement, evidence, severity per entry).
Actual: `findings` is stored as 9 string fragments: `["[{\"clause\":\"7.5.3\"", "\"statement\":\"DOC-0041 went...\"", "\"evidence\":\"DOC-0041 version 1", "34 bytes\"", ...]`. The audit report DOC-0180 and the closed audit carry this garbage; there is no finding with a clause or a severity anywhere in the record. CAPA-2026-0003 `actions` is likewise fragments. The start form's JSON textarea handles lists correctly; the decide form does not (`shell.py` `coerce`: `spec == "list"` -> `value.split(",")`).
Evidence: `hubproto record AUD-2026-001` `findings` field; DOC-0180 text; `hubproto record CAPA-2026-0003` `actions`.
Suggested fix: parse a value that starts with `[` or `{` as JSON before falling back to comma splitting; give the audit and CAPA decision forms one row per finding/action.

### F-qm-5: An audit records no checklist results and accepts "findings" that are not findings
Severity: blocker
Area: workflow
Steps:
1. As bob, start `internal-audit` with processes `["document-control","nonconformance"]` (AUD-2026-001).
2. Open the record: `checklist` has 11 entries, every `result: null`. There is no control on the record page or inbox card to set a result per question.
3. Record the findings (F-qm-4), carol accepts with a `summary`.
Expected (9.2.2 c-f): the audit record shows per checklist item what was examined and the result; findings carry clause, statement, evidence, severity; findings needing action are linked to an NCR/CAPA ("Findings that need action are raised as nonconformances or CAPAs and linked to the audit", audit-sop).
Actual: results stay `null` forever, the closed audit has no linked NCR/CAPA and no field or guard asks for one; the "findings" list is unstructured text fragments. AUD-2026-001 closed and rendered as a report with 11 unanswered questions. As the QM I could not accept this audit in real life, but the system let me.
Evidence: DOC-0180 (`checklist` entries with `"result": null`); `hubproto record AUD-2026-001` links: none.

### F-qm-6: A document's first issue, with placeholder content, went effective on the author's own "minor" declaration with no human review
Severity: major
Area: workflow
Steps:
1. As alice, Home > Start document-control: title "WI-18 ESD handling", change_type `minor`, training_roles `staff`, JSON `{"doc_type":"work-instruction","review_date":"2027-11-06"}`.
2. Run e446ffd0a1 completes immediately.
Expected (7.5.2 c): review and approval for suitability and adequacy by a person; a "minor revision" presupposes an existing effective revision; a new document cannot be a minor revision of nothing.
Actual: risk-scorer sets `risk low` purely from `change_type == minor` (cogs.py RiskScorer, document branch); the doc-reviewer cog approves "within fixed criteria" ("change_minor holds"); DOC-0041 is `effective` rev A.0 with body `# WI-18 ESD handling\n\n(draft body)` (34 bytes) and four training runs were opened on it. Nothing checked that the document had content, a predecessor, or that "minor" was true: the author chooses the route.
Evidence: run e446ffd0a1 Track `gate.cog_review` reasons `["change_minor holds"]`; DOC-0041 version 1 size 34.
Suggested fix: `change is minor` must be derived (a `supersedes` link to an effective revision plus a diff size or a reviewer's classification), not an author input; refuse publication of a controlled document whose only version is the drafter's placeholder.

### F-qm-7: The document-control start form has no way to supply the document; reviewers approve a record, not a document
Severity: major
Area: shell
Steps:
1. Home as alice: the document-control form has title, description, change_type, training_roles, "other fields (JSON object)". No file upload, no body field (the JSON key `body` works but is undocumented).
2. DOC-0040 reaches carol's inbox: the card shows `content_sha`, `size 53`, `filename`, `risk_reasons` and no content or link to the version.
3. Bob approved it; effective content was `(draft body)`.
Expected: the author attaches the draft (file or text) when starting; the reviewer's inbox card links to the version being approved (wi-review-a-document step 2: "On the record page open the draft version").
Actual: as above. `document-drafter` writes `(draft body)` when no body is given. The reviewer must find the record, then the document, then the version, and nothing on the approval card says "you are approving sha 4dbe41...".
Evidence: `/inbox?as=carol` card for DOC-0040; DOC-0040 version 1 text.

### F-qm-8: Any nonconformance that states a quantity is scored "high" by the purchasing rule (supplier not approved / item not in inventory)
Severity: major
Area: workflow
Steps:
1. As alice start `nonconformance`: title "Smudged label, quantity given", description "Final inspection found a smudged serial label on one unit at the packing bench." JSON `{"quantity":1}` (NCR-2026-0011, run 3a20982c74).
2. Compare with the same description and no quantity (NCR-2026-0010, run 96aba0f6e8).
Expected: both low risk; the classifier even set `severity low` on both.
Actual: NCR-2026-0011 `risk high`, `conditions {"item_known": false, "supplier_approved": false, ...}`, routed to carol and bob, CAPA would be opened on disposition. NCR-2026-0010 `risk low`, cog-dispositioned. `RiskScorer.task_run` takes the purchasing branch whenever `rec.get("quantity")` is set. The NCR form and template ask for `quantity` (ncr.md "Quantity affected"), so every properly filled NCR is high risk. NCR-2026-0002 was "high" for the same wrong reason, not because it said "safety" and "recall".
Evidence: `hubproto record NCR-2026-0011` fields `risk high`, `severity low`, `conditions`; inbox card `risk_reasons ["supplier not approved", "item not in inventory"]` on an NCR.

### F-qm-9: NCR classification is keyword matching and sets the wrong source and severity
Severity: major
Area: workflow
Steps:
1. NCR-2026-0002 (dave): "Incoming inspection found cracked brackets ... shipped to a customer. Safety risk, potential recall." -> `source complaint`.
2. NCR-2026-0001 (alice): "3 mm scratch on the lid of 2 units from lot L-77. No functional effect." -> `severity medium`, `risk medium`, CAPA-2026-0002 opened automatically.
Expected: source = inspection (it says "incoming inspection"); a cosmetic scratch is low; a CAPA for it is noise (10.2.1 b: "evaluate the need for action").
Actual: `NCRClassifier`: the word "customer" -> source complaint; the word "lot" -> medium; "field" would match "fielded". Nothing on the record or inbox says the source/severity are a stand-in's guess; the risk tier they drive decides who signs and whether a CAPA exists.
Evidence: `hubproto record NCR-2026-0002` `source complaint`; `hubproto record CAPA-2026-0002` title "CAPA for NCR-2026-0001: Cosmetic scratch on enclosure lid".

### F-qm-10: Low-risk NCRs are "dispositioned" by the cog with a canned disposition nobody chose; I do not accept this as a disposition
Severity: major
Area: policy
Steps:
1. As alice start `nonconformance` "Label misprint on one unit" (no lot, no quantity) -> NCR-2026-0010, run 96aba0f6e8.
2. As carol (the QM herself) start "Torn carton on one unit" -> NCR-2026-0012, run f66ae3e8ad.
3. `hubproto record NCR-2026-0010`.
Expected (8.7.1, 8.7.2): a person with authority decides the action taken on the nonconforming output and records it; the cog, if used, confirms a human-proposed disposition within criteria.
Actual: both closed in the same second they were opened. `disposition rework`, `disposition_rationale "Proposed by the classifier for a low-risk nonconformance; confirmed at the Gate."`, containment "Quarantine affected items and notify the process owner." (canned), decision by quality-assistant "approved within fixed criteria". The classifier wrote `rework` for a smudged label and a torn carton (the right answers are reprint/use-as-is); "confirmed at the Gate" was the cog confirming the cog. Nobody confirmed containment, nobody inspected. The cog also dispositioned the QM's own report (NCR-2026-0012), which the SOP narrative says goes "to the management representative when the quality manager reported it". The criteria sentence `when the risk is low` is satisfiable by anything without the word "lot" and without a quantity (F-qm-8, F-qm-9).
Evidence: records NCR-2026-0010, NCR-2026-0012; DOC-0119 "snapshot at open" with the cog signature.
Suggested fix: a cog may close a low-risk NCR only when a person has set `disposition` and `disposition_rationale` and confirmed containment (make those required_by_state for the cog path); the SOP sentence should be "may approve ... when the risk is low and the disposition is use-as-is or rework and a person proposed it".

### F-qm-11: CAPA effectiveness can be verified on the day the CAPA opens; one person opens, owns, verifies and closes it
Severity: major
Area: workflow
Steps:
1. Carol dispositions NCR-2026-0002 as scrap (run e9096ab8d9) -> CAPA-2026-0003 (run 0bd6813bf5), owner carol, action "Contain and investigate" (drafter placeholder), due +30 days.
2. As carol approve "Verify the actions" at 14:51:18 with a root cause.
3. Effectiveness-scheduler sets `effectiveness_check_date 2027-02-04`; "Verify effectiveness" is immediately pending for carol.
4. As carol approve "Verify effectiveness" at 14:51:27 with `effectiveness_result "No recurrence observed."`.
Expected (10.2.1 d, capa-sop "The effectiveness check is scheduled after the interval below"): the effectiveness checkpoint cannot be signed before `effectiveness_check_date`; the owner who implements actions and the person who verifies them differ; closure by the QM is a separate signature from ownership.
Actual: CAPA-2026-0003 `closed` 85 seconds after opening, 90 days before its own check date, every signature carol's. The process step says `[process-owner] Verify the actions` but routing says "go to the owner", and the owner is whoever dispositioned the NCR.
Evidence: `hubproto record CAPA-2026-0003` decisions 14:51:18 and 14:51:27, `effectiveness_check_date 2027-02-04`, transitions to closed 14:51:28.

### F-qm-12: Evidence snapshots are rendered before the closing transition and state the wrong state
Severity: major
Area: records
Steps:
1. Close any run with a `document-renderer` step: NCR-2026-0002 -> DOC-0136, NCR-2026-0010 -> DOC-0119, MR-2026-001 -> DOC-0117, AUD-2026-001 -> DOC-0180, CAPA-2026-0003 -> DOC-0153.
2. Read the document.
Expected: the evidence document of a closed record says "closed", lists the closing transition and carries the delegating sentence for a cog decision.
Actual: DOC-0136 is titled "snapshot at awaiting-disposition-the-nonconformance", body "State: **awaiting-disposition-the-nonconformance**", transitions list stops before `closed`; DOC-0119 says "State: **open**" with no transitions at all, and its signature line for the cog says only "approved within fixed criteria" without the delegating sentence that the Track has. DOC-0117 (management review minutes) says "awaiting-hold-the-review". An auditor comparing the evidence document with the record sees a contradiction on every closed record.
Evidence: `/document/rec:document:DOC-0136?as=carol`, DOC-0119, DOC-0117, DOC-0180.
Suggested fix: render after the terminal transition (or render twice and mark the first as interim); include `delegating_rule` text in the signature line.

### F-qm-13: Training on an obsolete revision stays open, can be acknowledged after supersession, and a rejected acknowledgement leaves the person untrained with no follow-up
Severity: major
Area: workflow
Steps:
1. DOC-0040 (WI-17 rev A) effective -> TRN-00005..00008 opened, due 2026-11-20.
2. As alice start document-control with JSON `{"supersedes":"rec:document:DOC-0040","revision":"B",...}`; bob approves (run 3c2be1171a) -> DOC-0191 effective, DOC-0040 `obsolete`, TRN-00009..00012 opened.
3. `hubproto records --type training`: TRN-00005..00008 (rev A) still `awaiting-acknowledge-the-document`.
4. As alice approve TRN-00005 (run ad9543feb5) "I have read and understood WI-17 rev A": accepted, `acknowledged`, document DOC-0040 whose content by then was the A.1 edit from F-qm-1.
5. As bob reject TRN-00002 "I do not understand section 3 of WI-18".
Expected: superseding a revision cancels or re-targets open acknowledgements of the old one; an acknowledgement of an obsolete revision is refused; a rejection ("do not approve ... the quality manager will follow up", wi-acknowledge-training step 4) creates a follow-up for the QM and keeps the person listed as not current.
Actual: stale acknowledgements remain and are signable; TRN-00002 is simply `rejected` with no item in carol's inbox and nothing marking bob as untrained on WI-18. There is no "who is current on which document" view anywhere (no board, no CLI command; `grep -i current hubproto/` finds nothing): to answer the auditor's "is dave trained on WI-17 rev B?" I must read the training board by hand and know that `rejected` means "not trained".
Evidence: `hubproto records --type training`; TRN-00005 `acknowledged` with `document rec:document:DOC-0040`, `revision A`; TRN-00002 `rejected`.

### F-qm-14: A specific route silently degrades to the default route; a drawing by the only process owner is approved by a generic reviewer
Severity: major
Area: workflow
Steps:
1. As alice (author and the only `process-owner`) start document-control, JSON `{"doc_type":"drawing","review_date":"2027-11-06"}`, change_type major (run 256a844701).
2. `hubproto track 256a844701`.
Expected: document-control-sop "Drawings are approved by the process owner" / "Approvals in document-control for drawings go to the process owner." Either a second process owner is required, or the run stops with "no eligible approver" for the document controller to resolve.
Actual: Track event 8 `gate.skipped {"candidate": "role:process-owner", "reason": "no eligible person holds this route"}` then event 9 routes to `bob, carol` by line 29 "Approvals in document-control go to the reviewer." Nobody was told. The SOP's rule for drawings is not what happens, and the compile had no warning that the process-owner role has one member who is also the only author.
Evidence: Track of 256a844701 events 8-9.

### F-qm-15: The document controller cannot file documents on behalf of authors although the SOP says she may
Severity: major
Area: policy
Steps:
1. Home as carol: document-control is listed under "Not available to you: you do not hold the role 'author' that starts it".
2. document-control-sop body line 6: "Document controllers may file documents on behalf of authors and receive overdue reviews." Permissions line 21: "Document controllers may create documents in Quality/Controlled Documents."
Expected: the permission sentence is honoured, or the compile reports that the process's first step `[author]` excludes a role that holds `create documents`.
Actual: the permission compiled (it is cited on the upload form) but the process step role wins; the SOP narrative, the permission and the runtime disagree. The quality manual 5.3 ("The document controller files controlled documents") is therefore false.
Evidence: `/?as=carol` "Not available to you" table.

### F-qm-16: Management review is one person preparing, chairing and signing; inputs are not checked against 9.3.2; outputs do not become tasks
Severity: major
Area: workflow
Steps:
1. As bob start management-review with date, chair (defaults to `$owner` = bob) and an `inputs` object missing "monitoring and measurement results" and "effectiveness of actions on risks" (MR-2026-001, run 97a8d59738).
2. As bob approve "Hold the review" with `outputs` of two decisions.
Expected (9.3.1-9.3.3; management-review-sop; wi-record-management-review step 4 "files a task or a change request for each action"): top management chairs (the management representative prepares); the record lists attendees; every 9.3.2 input is present or explicitly "none"; each output decision becomes a linked task/CR; the review has a due date like every other checkpoint.
Actual: preparer = chair = signer = bob; no attendees field in the record type; inputs are a free-form object and the two missing inputs were not noticed; `actions` stays empty and no task was created (links: none); "Hold the review" has no SLA (`due -`). The minutes (DOC-0117) carry the wrong state (F-qm-12). Also, the record page's Why box says bob may **not** approve `management-review` in Quality/Management Review (permission says "approve reviews", object is "management-review"), yet the decision succeeded (F-qm-27).
Evidence: `hubproto record MR-2026-001`; DOC-0117.

### F-qm-17: Records opened by cogs are attributed to people who did not open them
Severity: major
Area: records
Steps:
1. Carol approves CAL-00001 (out-of-tolerance) -> ncr-opener creates NCR-2026-0013. Carol responds to CMP-2026-0001 -> NCR-2026-0014. Carol dispositions NCR-2026-0002 -> capa-opener creates CAPA-2026-0003. Bob approves DOC-0040 -> TRN-00005..00008.
2. `hubproto record NCR-2026-0013`, `CAPA-2026-0003`, `TRN-00005`.
Expected: "who did what when": created by `ncr-opener` under carol's grant; `reported_by` empty or `system`; the Track distinguishes the person's decision from the cog's filing.
Actual: NCR-2026-0013 "created ... by carol", `reported_by carol`, `filed_by carol`; CAPA-2026-0003 "created by carol", `filed_by carol`; TRN-00005 "created by bob" (the approver of the document, not the assignee's trainer). The NCR from the calibration also copies the calibration's description ("Annual calibration of digital multimeter DMM-004 against the reference standard") instead of the out-of-tolerance facts and does not carry the measured values or the obligation to assess previous measurements (7.1.5.2). Because `reported_by carol`, SoD then routes these NCRs to bob, so the quality manager is excluded from dispositioning nonconformances she never reported.
Evidence: the three records above; `run=4387cfdbf5` on NCR-2026-0013 is the calibration's run while its transition says run bcff13aff8.

### F-qm-18: Obsolete documents remain editable and downloadable with no obsolete marking; controlled documents carry no print-control statement
Severity: major
Area: DMS
Steps:
1. After supersession, open `/document/rec:document:DOC-0040?as=carol` (state obsolete).
2. Download version 2; check out; check in a third version.
Expected (7.5.3.2 d): obsolete documents are identified (watermark/banner in the content and on the page), retained read-only, and cannot be issued. Controlled documents say "uncontrolled when printed" and carry their number/revision/effective date in the rendered content.
Actual: the page offers **Check out** and **Upload a new version** on an obsolete document and accepts them (DOC-0040 is now A.2). The downloaded content is the bare Markdown with no header, number, revision, status or "uncontrolled when printed" line. Only evidence snapshots carry "Printed copies are uncontrolled." (dms.py line 220). The folder listing shows `obsolete` only as a state word.
Evidence: `hubproto record DOC-0040` `state=obsolete`, `minor 2`.

### F-qm-19: Supplier approval never expires; the re-evaluation date is decorative
Severity: major
Area: workflow
Steps:
1. As carol start supplier-evaluation for acme-metals with `reevaluation_date 2027-11-06`, scope "Machined aluminium brackets and enclosures" (SUP-0001); bob approves.
2. Read `hubproto/procurement.py` `supplier_approved`: any supplier-evaluation in state `approved` with that supplier name makes the supplier approved.
3. `hubproto tick` only escalates pending gates (runner.py `tick`): nothing reads `reevaluation_date`.
Expected (8.4.1, supplier-sop "re-evaluated at the interval set on the record"): the approval lapses or is flagged at the re-evaluation date; the purchasing cog's "supplier is approved" condition respects the date and the scope; a reminder run is opened for the QM.
Actual: missing. SUP-0001 also has no `approved` field set, no link to the supplier record or to any PO, and no evidence document is rendered (the process has no renderer step), so the purchasing evidence for "approved by supplier evaluation SUP-0001" lives only in the purchasing record's note.
Evidence: `hubproto record SUP-0001` (`approved` absent, links none, documents none).

### F-qm-20: Rejecting a nonconformance makes the nonconformity disappear
Severity: major
Area: workflow
Steps:
1. As carol reject NCR-2026-0011 (mis-scored, run 3a20982c74) with "Risk tier is wrong; needs re-scoring".
2. `hubproto record NCR-2026-0011`.
Expected: an NCR cannot be "rejected" out of existence; either rejection returns to "Open the nonconformance" (the grammar has "Rejections return to <step>" but ncr-sop does not use it) or a reject must be a disposition with a rationale.
Actual: state `rejected`, no disposition, no CAPA, no snapshot, no re-open route; the smudged-label unit is still at the packing bench with no record that says what to do with it. Same for complaint and calibration: a reject ends the run with no customer response / no NCR.
Evidence: `hubproto record NCR-2026-0011` transitions.

### F-qm-21: The conformance map counts a frontmatter claim as coverage; it is not honest about what is uncovered or what "covered" means
Severity: major
Area: docs
Steps:
1. `hubproto conformance`; `/conformance?as=carol`.
2. Compare with `profiles/iso9001/frames/quality-manual.frame.md` and `profile.py conformance()`.
Expected: a clause map an auditor can use: for each clause, the requirement, the Frame sentence(s) that implement it, and the records that evidence it; "covered" only where a rule or a record exists.
Actual:
- Coverage = `clauses:` listed in a Frame's frontmatter, with parent claims expanded to all children (`clause.startswith(c + ".")`). 7.1.3 Infrastructure is "covered" by a quality manual whose text is `{{INFRASTRUCTURE}}`; 7.1.4, 7.1.6, 5.2.1, 5.2.2, 6.1, 6.2, 4.x are "covered" by placeholder paragraphs that the compiler extracts nothing from (`compile: none`). 7.5.1/7.5.2/7.5.3 are "covered" because document-control-sop claims "7.5". 5.3 is "covered" by a work instruction on cog escalations.
- Uncovered: 8.2.2-8.2.4, 8.3 and 8.3.1 (while 8.3.2-8.3.6 are covered, so the parent is uncovered and the children covered), 8.5.1-8.5.5, 8.6, 9.1, 9.1.1, 9.1.3. For a company that "designs, makes and supports" products, 8.5.1 (production control), 8.5.2 (identification and traceability: lots are free text on NCRs), 8.6 (release of product: nothing records who released lot L-78) and 9.1.1/9.1.3 (what is monitored, how analysed) are not optional; an auditor would raise them as majors on day one. The profile description "a quality management system skeleton that compiles to a policy release with no error findings" is true and irrelevant to conformance.
- The shell page is the raw JSON dict in a two-column table; it is not usable in an audit.
Evidence: `hubproto conformance` output "50 of 66 clauses covered".
Suggested fix: three columns per clause (claimed by, rules extracted, records existing); count placeholders as "not covered"; never expand a parent claim to children.

### F-qm-22: Every decision appears twice in the Decisions table and in `hubproto record`
Severity: major
Area: records
Steps: `hubproto record NCR-2026-0002` (or any decided record); open the record page, Decisions.
Expected: one row per signature.
Actual: each signature is listed twice with the same sig (e.g. NCR-2026-0002 carol 14:50:01 x2; CAPA-2026-0003 four rows for two decisions; MR-2026-001, SUP-0001, DOC-0040, TRN-00004 all doubled). An auditor counting signatures gets the wrong number; I cannot tell whether the event store has the decision twice or the view duplicates it.
Evidence: as above.

### F-qm-23: Cog "status" reply discloses every waiting item in the hub to whoever asks
Severity: major
Area: security
Steps: as alice (staff) DM quality-assistant "status?" (another panelist did; the reply is in `/inbox?as=alice`).
Expected (7.5.3.1 b, confidentiality): a member sees only items in folders they may read.
Actual: the cog replies with all 41 waiting items including Finance bills (BILL-0005, BILL-0017), designs and purchase requests that alice has no read rule for. `ReviewerCog.task_inbox` lists `ctx.runner.list()` unfiltered.
Evidence: `/inbox?as=alice` message "41 item(s) are waiting at a Gate: ...".

### F-qm-24: Change requests close without an evidence document, a due date on verification, or links to the documents they change
Severity: minor
Area: workflow
Steps: dave raises CR-2026-0001 (affected_documents `["WI-17"]`); carol approves; dave verifies.
Expected (8.5.6, 6.3): the closed CR links to the revised document(s) and the NCR that prompted it, verification has an SLA, and the record is rendered like the others.
Actual: `affected_documents` is free text, links none, documents none, "Verify the change" has `due -`. The change-control process has no `document-renderer` step; the template change-request.md ("Verified by / Date") has no rendered counterpart.
Evidence: `hubproto record CR-2026-0001`.

### F-qm-25: Internal auditor independence is not enforced
Severity: minor
Area: policy
Steps: as bob (maintainer of audit-sop and management-review-sop) start internal-audit with processes `["management-review","internal-audit"]` (AUD-2026-002, run ba4431abd9).
Expected (9.2.2 c; audit-sop "Auditors do not audit processes they own"): refused or warned.
Actual: accepted, assigned to bob.
Evidence: `hubproto records --type audit`.

### F-qm-26: Retention is a label; nothing computes a retention date, checks the evidence list at closure, or holds disposal
Severity: minor
Area: records
Steps: `config/retention.json` says class `quality` must have `documents` and `guard_findings` evidence; CR-2026-0001 (class quality) closed with no document; SUP-0001 likewise.
Expected (7.5.3.2 d): a closed record shows "retain until <date>", and the class's evidence list is verified at closure.
Actual: records show `retention: quality` and nothing else; no retain-until date; no check. There is no way to put a legal hold other than the per-record "Put on hold" button, which is a workflow hold, not a retention hold.
Evidence: `hubproto record CR-2026-0001` labels, documents none.

### F-qm-27: The "Why" on a record page contradicts the engine for processes whose permission noun differs from the record object
Severity: minor
Area: shell
Steps: as bob open `/record/rec:management-review:MR-2026-001` and `/record/rec:supplier-evaluation:SUP-0001` while they wait for him.
Expected: Why says allowed, citing "Management representatives may approve reviews / suppliers in ...".
Actual: Why says "denied: no rule in the release lets any of [...] approve management-review in Quality/Management Review" (and `approve supplier-evaluation`), yet the decision succeeds (runner checks `manifest.object`). The home page "Not available to you" likewise says "create review". Two nouns for one thing in the SOP ("reviews" in Permissions, "The record type is management-review") and the compile raised no finding.
Evidence: record pages above; decisions succeeded at 14:48:30Z.

### F-qm-28: The link between an NCR and its CAPA is stated backwards
Severity: minor
Area: records
Steps: `hubproto record NCR-2026-0002` links; `hubproto record CAPA-2026-0003` links.
Expected: NCR `caused` CAPA (CAPA `caused-by` NCR).
Actual: CAPA `caused` NCR (out); NCR shows `caused-by rec:capa:CAPA-2026-0003`, i.e. the corrective action caused the nonconformance. The calibration/complaint NCRs use `concerns` instead because `caused-by` is not a relation name in `config/links.json` (the code tests for it).
Evidence: as above.

### F-qm-29: Escalation evidence can be produced with any date; the clock is an input
Severity: minor
Area: security
Steps: `/inbox?as=carol` Escalations: "Overdue: Acknowledge the document on rec:training:TRN-00004 at 2026-11-06T14:49:00+00:00 Due 2026-11-20".
Expected (Part 11 11.10(e); 7.5.3): time stamps from a trusted clock; an item cannot be "overdue" 14 days before it is due.
Actual: `hubproto tick --today <any date>` is a documented option and produced this record. `record-filer`, `effectiveness-scheduler` and `document-publisher` also use the host's `date.today()`.
Evidence: inbox escalation text above; `hubproto tick --help`.

### F-qm-30: Rule citations use body line numbers, not file line numbers
Severity: minor
Area: docs
Steps: Home as carol: "allowed by ncr-sop line 16: Staff may create nonconformances..."; open `frames/iso9001/ncr-sop.frame.md` at line 16.
Expected: the cited line is the sentence.
Actual: file line 16 is inside the narrative paragraph; the sentence is file line 28 (12 lines of frontmatter are not counted). The shell's `/frame/` view renumbers from the body so it agrees with itself, but an auditor given the file path and "line 16" finds the wrong sentence. Say "body line" or cite file lines.
Evidence: `frames/iso9001/ncr-sop.frame.md` lines 16 and 28.

### F-qm-31: The quality manual is released with its placeholders, including the maintainer's name
Severity: minor
Area: policy
Steps: Home as carol, "Frames behind this release": `quality-manual v0.1.0 (company, maintained by {{MANAGEMENT_REPRESENTATIVE}})`; management-review-sop body: "at least {{REVIEW_FREQUENCY}}"; calibration-sop "traceable to {{NATIONAL_STANDARDS_BODY}}".
Expected: the compile refuses (or at least warns on) a release whose Frames still contain `{{...}}` tokens; the quality manual says "Replace the tokens before the first management review" but MR-2026-001 was held and closed with them in place.
Actual: released without a finding; an auditor reading the manual reads "{{QUALITY_POLICY}}".
Evidence: home page list; `hubproto conformance` counts the manual as covering 14 clauses.

### F-qm-32: Training records never get an `acknowledged` date or `assessed_by`; there is no read-and-understood assessment
Severity: minor
Area: records
Steps: `hubproto record TRN-00001` (acknowledged by alice).
Expected (7.2 c-d, template training-record.md "Acknowledged / Assessed by / Competence evidence"): the acknowledged date is on the record, and for documents that need it a short assessment (questions, or a trainer's sign-off in `assessed_by`) before the record counts as competence evidence.
Actual: `acknowledged` and `assessed_by` are defined in `config/record-types.json` but never set; the date exists only in the decision row. No assessment mechanism exists: a click with any text (or none, F-qm-34) is the whole evidence of competence.
Evidence: TRN-00001 fields.

### F-qm-33: A signature with no meaning is accepted and the system invents one
Severity: minor
Area: workflow
Steps: as carol approve TRN-00003 (run 2c95dfe101) with the meaning field empty.
Expected (Part 11 11.50(a)(3); wi-acknowledge-training step 3 "in the meaning field write that you have read and understood"): refused, or recorded as blank.
Actual: accepted; the Decisions row says meaning "approved acknowledge the document", text the signer never wrote. The attestation "read and understood" was never made but the record reads as if it were.
Evidence: `hubproto record TRN-00003` decisions.

### F-qm-34: Decisions cannot be made from the record page; the work instructions say they can
Severity: minor
Area: docs
Steps: open `/record/rec:ncr:NCR-2026-0002?as=carol` while it waits for carol; look for Approve/Reject.
Expected: wi-disposition-stock step 3 "Decide approve with the fields ..." after "Open the record page"; wi-acknowledge-training step 3 "Return to the record page and decide approve"; wi-raise-a-change-request step 4 "Open the record and decide approve"; wi-run-an-audit step 4.
Actual: the record page has attach/link/hold/snapshot forms only; the decide form exists on `/inbox` alone. See F-qm-36 for the other wrong sentences.
Evidence: record page form actions: `/attach`, `/hold`, `/link`, `/snapshot`.

### F-qm-35: The convention grammar is writable by a document controller only for the shapes shown; half the shapes the SOPs use are undocumented in the reference Frame
Severity: question
Area: docs
Steps: compare `frames/reference/writing-policy-frames.frame.md` (the Frame a document controller would be given) with the sentences the SOPs actually rely on.
Expected: everything the seed SOPs use is in the reference.
Actual: the reference does not mention `At <checkpoint> in <process> go to <assignee>`, `Overdue items in <process> go to ...`, `... is due within N days`, `for drawings`, `with a minor change`, field assignees (`the assignee | owner | author | requester | auditor | chair | evaluator | reporter`), `The record type is X.`, `Closes as X.`, `Rejections return to <step>.`, `interval` as a threshold word, or the conditions `the risk is not high` / `the document is a minor revision` / `no open nonconformance exists`; those are only in `DEV-NOTES.md`, a developer file. Could my document controller write these SOPs? The sentence shapes themselves, yes (they read like procedure prose). What she cannot do is know which conditions are evaluable (closed vocabulary in `rules.py`), that a cog route needs a matching criteria sentence, that the object noun comes from the first step's title and must match the permission noun (F-qm-27), or that an unparsed sentence is reported only at compile time by someone with the CLI. She also cannot express what the ncr-sop narrative promises: "for the management representative when the quality manager reported it" has no sentence shape; it only happens as a side effect of the segregation sentence.
Evidence: writing-policy-frames.frame.md lines 18-46 vs DEV-NOTES.md lines 59-98.

### F-qm-36: Work instructions that do not match the shell
Severity: minor
Area: docs
Steps: read each WI against the shell as it is.
Expected: accurate.
Actual (wrong sentences):
- wi-file-a-nonconformance step 1 "Open the Quality/Nonconformances board and choose *New nonconformance*": boards have no New button; processes start from Home. Same in wi-raise-a-change-request step 1, wi-run-an-audit step 1, wi-record-management-review step 1.
- wi-file-a-nonconformance step 1 "Words such as 'safety' or 'recall' raise the severity to high": true, but "lot" raises it to medium and any `quantity` makes the risk high (F-qm-8), which the WI does not say; step 4 "The quality manager (or the quality-assistant cog for a low-risk item) dispositions the record": the cog does not disposition, it confirms the classifier's canned "rework" (F-qm-10).
- wi-disposition-stock step 3: see F-qm-34; step 5 "If you reported the nonconformance yourself, it is routed to the management representative": a low-risk one goes to the cog.
- wi-review-a-document step 2 "the review date is set": the reviewer cannot set it and it is never acted on (F-qm-37); step 5 "the document controller ... may approve instead" is accurate and is the problem (F-qm-2).
- wi-acknowledge-training step 3 "in the meaning field write that you have read and understood": not enforced (F-qm-33); step 4 "decide reject with the reason; the quality manager will follow up": no follow-up exists (F-qm-13).
- wi-run-an-audit step 3 "Perform the audit with the checklist on the record page": there is no way to record results (F-qm-5); step 5 "Raise a nonconformance for each major finding and link it": nothing asks for or checks this.
- wi-record-management-review step 1 "Attach the audit reports...": the start form has no attachment; step 4 "files a task or a change request for each action": nothing does (F-qm-16).
- wi-respond-to-a-cog-escalation step 1 "Your inbox item names the routing sentence; the record's Track shows the gate.cog_review event": correct. Step 2 "reasons ... say which condition failed": for a document a cog never escalates (minor = low = approve); for an NCR the reasons include purchasing conditions (F-qm-8).
Evidence: the WI files under `frames/iso9001/wi-*.frame.md` and the pages cited.

### F-qm-37: Periodic review dates are collected and never used
Severity: minor
Area: workflow
Steps: DOC-0040/0041/0191 have `review_date 2027-11-06` (required_by_state effective); `grep review_date hubproto/runner.py` finds nothing; `tick` only looks at pending gates.
Expected (7.5.2 c "review and approval"; 7.5.3.2 b): a review-due run or task for the owner before the date; a report of documents past review.
Actual: missing. If no date is given the publisher invents "today + 1 year" (kernel.py line 444), so the field always looks filled.
Evidence: as above.

### F-qm-38: Missing QMS elements, stated as missing
Severity: major
Area: policy
Steps: searched Frames, record types, boards and CLI for each item.
Expected vs actual:
- **Risk register (6.1)**: the quality manual points to `{{RISK_REGISTER_LOCATION}}`; no record type, board or template exists. MR-2026-001 input `risks: "risk register not yet established"` was accepted.
- **Printed-copy control (7.5.3.2 d)**: no "uncontrolled when printed" or copy-number mechanism on controlled documents (only on snapshots); download of obsolete revisions allowed (F-qm-18).
- **Obsolete-document watermarking**: none (F-qm-18).
- **Periodic review**: F-qm-37.
- **Supplier re-evaluation reminders**: F-qm-19.
- **Management review input completeness**: F-qm-16; the agenda template lists 12 inputs, the record type's `inputs` is a free object.
- **Read-and-understood assessment**: F-qm-32.
- **Training-current view / training matrix**: F-qm-13.
- **Quality objectives (6.2)**: "tracked as a task in the Tasks folder": a task has title/description/due/priority, no measure, target, baseline or owner-of-objective distinction; nothing aggregates them for 9.3.2 c.
- **Calibration asset register**: `asset` record type exists but no process creates or updates it; CAL-00001's `asset DMM-004` is a string, `next_due` on the asset is never updated and nothing is due-dated.
- **Product release (8.6) and lot traceability (8.5.2)**: no record of who released lot L-78 to Northwind; `lot` is free text on the NCR.
Evidence: `config/record-types.json`, `hubproto records --type asset` (empty), boards.

---

## Part 2: audit-readiness, two records

### NCR-2026-0002 (high risk, scrap, CAPA)
Can I produce what an auditor asks for?
- Who/what/when: yes, from `hubproto record NCR-2026-0002` plus `hubproto track e9096ab8d9`: reported by dave 14:44:30Z, classified by ncr-classifier 0.1.0, scored by risk-scorer 0.1.0, routed to carol by "At Disposition the nonconformance in nonconformance go to the quality manager." (ncr-sop body line 22), dispositioned by carol 14:50:01Z with meaning and fields, CAPA-2026-0003 opened by capa-opener under grant 189a0bcc.., rendered to DOC-0136. The policy sentence behind each route is there and the release digest is pinned. That is better than most paper QMSs.
- What I cannot defend: the risk tier reason ("supplier not approved", F-qm-8); `source complaint` on an incoming-inspection finding (F-qm-9); the evidence document that says the record is still awaiting disposition (F-qm-12); the duplicate signature rows (F-qm-22); the CAPA with carol as creator (F-qm-17), closed in 85 seconds with garbage actions (F-qm-4, F-qm-11); `retention: quality` with no retain-until date (F-qm-26); and a "signature" that is `sha256(by, verdict, payload, at)` by a `carol` who logged in by typing `?as=carol` (F-qm-3).

### DOC-0040 (WI-17 rev A)
- Who/what/when: created by alice 14:44:23Z, routed by document-control-sop body line 29, approved by bob 14:52:28Z (meaning recorded), effective 2026-11-06, review date 2027-11-06, training TRN-00005..00008 linked `trains-on`, superseded by DOC-0191 at 14:55:10Z with the transition recorded.
- What I cannot defend: the approved content was `(draft body)` (F-qm-7); the content was replaced after approval while `effective` and again while `obsolete` (F-qm-1, F-qm-18); TRN-00005 was signed against rev A after rev A was obsolete and after its content had changed (F-qm-13); no snapshot/signature page exists for a controlled document (the README claims "snapshots with signature pages"; only evidence renders of records exist, and the document itself has none); no "uncontrolled when printed" or revision block in the file.

---

## Part 3: electronic signatures (summary of F-qm-3, F-qm-33, F-qm-29, F-qm-2)
Meaning: present when the signer types it, fabricated when they do not (F-qm-33), and not part of the signed digest (F-qm-3). Identity: a URL/CLI parameter; no authentication, no step-up, unknown names accepted (F-qm-3); a role can be added to a personal attestation by escalation (F-qm-2). Time: server UTC for decisions, but escalation and scheduling dates come from an arbitrary `--today` (F-qm-29). Linkage: the decision carries `payload_digest` and `run_id` and the Track pins the release; good. Under ISO 9001 7.5.3 I would not accept the training acknowledgements (F-qm-2, F-qm-33) or any cog "signature" that reads as a disposition (F-qm-10) as evidence; under 21 CFR Part 11 nothing here is a signature (11.10(d), 11.50, 11.70, 11.100, 11.200 all fail). The prototype should say this plainly on the signature page rather than call the digest a signature.

## Part 4: SOP prose (summary of F-qm-35, F-qm-36, F-qm-15, F-qm-27, F-qm-14)
The sentences read like procedure prose and my document controller could write them, but she cannot predict what they do: a permission noun that differs from the process object compiles silently and breaks Why (F-qm-27); a specific route to a role whose only member is excluded by SoD degrades silently (F-qm-14); a permission sentence can be overridden by a step's role (F-qm-15); the evaluable conditions and half the sentence shapes are only in DEV-NOTES (F-qm-35). The work instructions describe a shell that does not exist in several places (F-qm-34, F-qm-36).
