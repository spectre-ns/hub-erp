# Design Engineer (design authority) panel findings

Persona: bob, design authority for `design-control-sop`; alice used only to
submit as the engineer. Release in effect: `b7da4af94d3c571f`. All records
below were created in this session unless marked as another panelist's.
Shell `http://127.0.0.1:8080`, CLI `docker compose exec -T hub hubproto ...`.

My records: DSN-2026-0002 (run 44b2dacca3, rejected), REQ-2026-0003 (run
4c0b591a8a, accepted), REQ-2026-0004 (run 85754be575, rejected),
DSN-2026-0010 (run 387447be08, released as PN-00006), REQ-2026-0016 (run
7baba72515, accepted), CR-2026-0003 (run 1d5342115f), DSN-2026-0012 (run
f26f77a473, failed), DSN-2026-0013 (run 07b1b9638c, released as PN-00008).

Severity count: blocker 2, major 13, minor 8, question 1.

---

### F-DE-1: [DE] The requirement statement is never shown to the person who signs it
Severity: blocker
Area: shell
Steps:
1. As alice: `hubproto run design-release --as alice --record-json '{"title":"[DE] Sensor node enclosure and board","kind":"electrical","user_needs":["[DE] The sensor node must run 24 hours on battery","[DE] The enclosure must survive a 1 m drop onto concrete"],"attachments":[...sensor.kicad_sch, box.FCStd as content_b64...]}'` -> run 44b2dacca3, REQ-2026-0003 and REQ-2026-0004 waiting for bob.
2. As bob open `http://127.0.0.1:8080/inbox?as=bob` and read the card "Accept the requirement · requirements-capture · REQ-2026-0003".
3. Open `http://127.0.0.1:8080/record/rec:requirement:REQ-2026-0003?as=bob`, Details tab.
4. `hubproto record rec:requirement:REQ-2026-0003`.
5. Accept it (see F-DE-6), then `hubproto doc-get rec:document:DOC-0087 --out /tmp/s.md` (the signed snapshot).
Expected: the requirement text ("The sensor node must run 24 hours on battery") on the inbox card, in Details, in the CLI, and in the signed snapshot. It is the thing being accepted.
Actual: none of the four shows the `text` field. The inbox card lists captured_by, conditions, design, kind, priority, risk, source, stakeholder, verified; Details and CLI the same; the snapshot DOC-0087 lists every field except `text`. The Activity tab (events 99 and 100) proves the text is stored. The only place a reviewer can read the statement is inside the parent design's `requirement_trace` JSON blob or the trace CSV. `hubproto/shell.py:33` (`BLOB_FIELDS = ("text", "body", ...)`) and `hubproto/cli_records.py:68` hide any field named `text`, which is the requirement's body.
Evidence: REQ-2026-0003, DOC-0087 (`## Fields` has no `text`); inbox card for run 4c0b591a8a; `hubproto record rec:requirement:REQ-2026-0003`.
Suggested fix: treat `text` as the headline for the requirement type (show it as the card title and first field; use it as the list/board label); include it in the snapshot.

### F-DE-2: [DE] Rejecting a design strands the record; "Rejections return to Submit the design" does not work and the engineer is not told
Severity: blocker
Area: workflow
Steps:
1. With DSN-2026-0002 waiting for bob (run 44b2dacca3), POST `/decide` as bob with verdict `reject`, meaning "[DE] rejected: requirements not accepted, BOM unresolved" (from the inbox card).
2. `hubproto record rec:design:DSN-2026-0002`; `hubproto runs | grep 44b2`.
3. `hubproto inbox --as alice` and `http://127.0.0.1:8080/inbox?as=alice`.
4. Open `http://127.0.0.1:8080/record/rec:design:DSN-2026-0002?as=alice` and look for a way to resubmit.
5. `hubproto decide 44b2dacca3 --as alice --verdict approve`.
Expected: per SOP line 56 the design returns to step 1 "Submit the design" assigned to alice, she is notified, revises the files and resubmits on the same record number; the review re-runs trace/export/reconcile.
Actual: record state becomes `awaiting-submit-the-design` with `assignees=-`, `due=-`; the run is terminal (`status=rejected`); alice receives no message (her inbox has only PO updates and tasks); the record page offers only attach/link/hold/snapshot, no submit; `decide` answers "run 44b2dacca3 is not waiting for a decision". The state `awaiting-submit-the-design` is not in the workflow's state list (bundle.json: states `['open','awaiting-review-the-design','released']`, transition on_reject `awaiting-submit-the-design`). The only way forward was to start a new record (DSN-2026-0010), losing the number, the decisions and the history of the first submission. `runner._rewind` returns silently because "Submit the design" is a cog step, not a checkpoint.
Evidence: DSN-2026-0002 transitions `awaiting-review-the-design -> awaiting-submit-the-design by bob run=44b2dacca3`; `hubproto runs` shows `44b2dacca3 design-release rejected`; `/hub/state/releases/b7da.../bundle.json` workflows.design-release.
Suggested fix: a rejection to a cog step should reopen the run at that step for the record owner (notify, assign, due), and re-run the automatic steps on resubmission; or the compiler should refuse `Rejections return to <cog step>`.

### F-DE-3: [DE] A design can be released with unaccepted requirements, or with no requirements at all; "risk high" is only a warning
Severity: major
Area: workflow
Steps:
1. DSN-2026-0010 (run 387447be08) submitted with REQ-2026-0003 (accepted) linked and one new user need (REQ-2026-0016, pending). Record shows `requirements_accepted False`, `risk high`, Guard finding "1 requirement(s) not yet accepted".
2. As bob approve from the inbox card without touching REQ-2026-0016 (I did accept it first, but see F-DE-4: the record still said not accepted at approval time).
3. Also: DSN-2026-0013 (run 07b1b9638c) submitted with only box.FCStd, no requirements, no user needs; Guard finding "the design traces to no requirement"; approve as bob.
Expected: a Guard blocks "Review the design" (or at least release) until every traced requirement is accepted (plan 9.6: "every requirement in a baseline has a linked verification"; clause map 8.3: "Inputs recorded before review"). A design with no requirements should not be releasable without an explicit override and reason.
Actual: both released. DSN-2026-0010 -> PN-00006 and DSN-2026-0013 -> PN-00008 with `requirements_accepted False` and `risk high` frozen on the released record and in the signed snapshot DOC-0134. Nothing asked for a second approver or a reason for releasing at high risk.
Evidence: DSN-2026-0010 fields after release; DSN-2026-0013 state=released, part PN-00008; run-show 387447be08 `trace-the-requirements pass-with-problems`.

### F-DE-4: [DE] The requirements trace and risk on a design are computed once at submission and never refreshed
Severity: major
Area: workflow
Steps:
1. DSN-2026-0010 waiting for bob with REQ-2026-0016 pending (`requirements_accepted False`, `risk high`).
2. As bob accept REQ-2026-0016 (run 7baba72515) with acceptance criteria and method `test`.
3. `hubproto record rec:design:DSN-2026-0010 | grep requirements_accepted` and reload the inbox card.
Expected: the design's trace, `requirements_accepted`, `conditions`, risk tier and Guard findings reflect the acceptance; the review package the design authority signs is current.
Actual: still `requirements_accepted False`, `risk high`, `conditions.requirements_accepted false`; the requirement_trace entry for REQ-2026-0016 still says `awaiting-accept-the-requirement` with no criteria; the trace CSV DOC-0109 is the stale snapshot. Same for DSN-2026-0002 after REQ-2026-0003 was accepted. The signed snapshot of the released design therefore states that its requirements were not accepted.
Evidence: DSN-2026-0010 record before and after accepting REQ-2026-0016; DOC-0134 fields.
Suggested fix: re-run requirements-tracer and risk-scorer when a linked requirement changes state, or at least immediately before the review Gate payload is built.

### F-DE-5: [DE] Verification of requirements is never recorded anywhere (missing)
Severity: major
Area: records
Steps:
1. `config/record-types.json` requirement type has `verified`, `verified_by`, `verification_evidence`, `verification_method`.
2. Search for anything that sets them: `grep -rn 'verified_by\|verification_evidence' hubproto/` -> only read in `cad.py:383-395` (trace CSV).
3. After release of DSN-2026-0010: `hubproto record rec:requirement:REQ-2026-0003` -> `verified False`, no `verification_evidence`; the ERC report DOC-0111 and the drop requirement have no link.
4. Try `hubproto link rec:requirement:REQ-2026-0003 evidence-for rec:document:DOC-0111 --as alice` is the only possible manual link; no process step, form, or Gate exists for "verify the requirement", and nothing checks it before part release.
Expected: a verification step or record (test, inspection, analysis, demonstration) that sets `verified`, `verified_by` and `verification_evidence`, a Guard `requirement-verified` before release or baseline (plan 9.6 Guards list), and the trace showing requirement -> design -> verification evidence.
Actual: missing. Traceability exists from requirement to design (`fulfils`) and from design to export documents, but it stops there: no requirement -> evidence, no part -> requirement, no verification of any kind, and release ignores `verified`.
Evidence: requirement fields after release; trace CSV DOC-0013/DOC-0109 column `verified` always `False`.

### F-DE-6: [DE] The acceptance form only asks for acceptance criteria; the verification method cannot be set from the shell or the CLI
Severity: major
Area: shell
Steps:
1. As bob open `/inbox?as=bob`, card "Accept the requirement · REQ-2026-0003".
2. Inspect the form: inputs are `acceptance_criteria` (required, because `accepted` requires it) and `meaning`. No `verification_method`, `priority`, `rationale`, `source`.
3. `hubproto decide --help` -> only `--verdict` and `--meaning`; no way to pass fields.
4. I could only set `verification_method=test` by hand-crafting the POST (`curl --data-urlencode verification_method=test`), which `post_decide` accepts because it copies any field in the type.
Expected: the acceptance Gate asks for acceptance criteria and verification method (the scenario says both), with the enum offered; the CLI accepts `--field k=v`.
Actual: only required-missing fields are rendered; the method is optional in the type so it is never offered; a real design authority cannot record it.
Evidence: inbox form for run 4c0b591a8a (`accepted needs: acceptance_criteria`), REQ-2026-0003 has `verification_method test` only because of the crafted POST.
Suggested fix: make `verification_method` required for `accepted`, or render the Gate's form from a per-checkpoint field list in the SOP.

### F-DE-7: [DE] Overdue requirement acceptances are never escalated; overdue design reviews escalate to the same person
Severity: major
Area: policy
Steps:
1. REQ-2026-0016 waiting for bob, due 2026-11-11 (SLA 5 days from SOP line 45 works).
2. `hubproto tick --today 2026-11-12`.
3. `hubproto inbox --as bob | tail`; `/inbox?as=bob`.
Expected: the overdue requirement is escalated somewhere (or at least flagged), as NCRs and purchase requests are.
Actual: tick escalated NCRs, a calibration and purchase requests but no requirement; REQ-2026-0016 shows no `escalated` chip. The SOP has "Review the design in design-release is due within 10 days." and "Overdue items in design-release go to the management representative." but no "Overdue items in requirements-capture ..." sentence, so the 5-day SLA on line 45 has no consequence. Also, the management representative is bob, who is the design authority: an overdue design review escalates from bob to bob. The compiler did not warn about either.
Evidence: tick output 2026-11-12 (14 escalations, none for `rec:requirement:*`); `frames/engineering/design-control-sop.frame.md` lines 43-47 and 16.
Suggested fix: add "Overdue items in requirements-capture go to the management representative." and route design-release escalation to someone who is not the design authority (the quality manager); have the compiler warn when a checkpoint has an SLA but its process has no overdue sentence, and when an escalation target equals the assignee.

### F-DE-8: [DE] Rejecting a requirement is terminal, silent, and leaves the design pointing at a rejected input
Severity: major
Area: workflow
Steps:
1. As bob POST `/decide` for run 85754be575 (REQ-2026-0004) verdict `reject`, meaning "[DE] rejected: not testable as written; state the drop surface and orientation".
2. `hubproto record rec:requirement:REQ-2026-0004`; `hubproto inbox --as alice`; `hubproto record rec:design:DSN-2026-0002 | grep fulfils`.
Expected: the engineer is told why, can revise the text and resubmit (the point of a rejection with a reason), and the design's trace shows the requirement as rejected / needs replacement.
Actual: state `rejected`, run `rejected`; no message to alice (the rejection meaning lives only on the record's decisions); requirements-capture has no "Rejections return to" sentence so there is no return path; DSN-2026-0002 still carries `fulfils REQ-2026-0004` and its trace was not updated. The engineer had to re-capture the need as a new requirement (REQ-2026-0016) with no `supersedes` link to REQ-2026-0004.
Evidence: REQ-2026-0004 decisions and transitions; alice's inbox after 14:47:00.

### F-DE-9: [DE] Engineering change after release: ECR/ECO, change classes, where-used, effectivity and stock disposition are all missing
Severity: major
Area: workflow
Steps:
1. PN-00006 rev A released from DSN-2026-0010.
2. As alice try to change C1: the only change process is `change-control` (Quality/Changes). `hubproto run change-control --as alice --record-json '{"title":"[DE] ECR: change C1 to 1uF on rec:part:PN-00006","description":"Increase decoupling","impact":"affects rec:part:PN-00006 rev A, BOM line C1, schematic DOC-0107; stock of CL10B104KB8NNNC to use as is","affected_documents":["rec:document:DOC-0107"],"change_type":"minor"}'` -> CR-2026-0003, run 1d5342115f, waiting for carol.
3. `hubproto record rec:change-request:CR-2026-0003` -> `links: none`.
4. Look for class routing: `grep -rn change_class hubproto/` -> only `cogs.py:222` (risk tier). No `engineering-change` process, no Class 1/2/3 sentences in any Frame, no `eco-reviewer` cog, no where-used cog.
5. Another panelist's DSN-2026-0009 "BRK-100 rev B" shows the only revision path: submit a new design naming `part: rec:part:PN-00002` and `revision: B` yourself; part-releaser bumps the revision on approval (`cad.py:327-331`).
Expected (plan 9.6): ECR with reason and impact, a where-used computation over BOMs/stock/orders/documents, review routed by change class ("Class 1 changes in engineering-change go to the design authority and the quality manager", "Class 3 ... go to the eco-reviewer cog"), ECO with effectivity, release of the affected revisions in one baseline, stock disposition.
Actual: a generic quality change request with free-text impact, no typed link to the part, design or documents (`affected_documents` is a list of strings, not links), routed to the quality manager regardless of class; the design authority is not in the loop. The `change_class` field on a design defaults to 2 and only shifts the risk tier; nothing routes on it. Revision B is whatever the engineer types; nothing checks that a rev B design supersedes rev A, carries an ECO, or that stock of rev A is dispositioned. No `supersedes` link is made between DSN-2026-0009 and DSN-2026-0003 / between part revisions.
Evidence: CR-2026-0003; DSN-2026-0009 fields `part rec:part:PN-00002`, `revision B`; bundle has no `engineering-change` op.
Suggested fix: say "missing" in the SOP and plan status; at minimum an `engineering-change` process in design-control-sop with class routing sentences, `affects` links to part/design/documents, and part-releaser refusing a revision bump without an approved change.

### F-DE-10: [DE] The signed snapshot is rendered before the final transition, shows the wrong state, and the signature line has no capacity
Severity: major
Area: DMS
Steps:
1. Accept REQ-2026-0003 as bob; open DOC-0087 (`hubproto doc-get rec:document:DOC-0087 --out /tmp/s.md`).
2. Approve DSN-2026-0010; open DOC-0134.
3. Shell: `/record/rec:design:DSN-2026-0010?as=bob` Decisions tab.
Expected: the rendered record says `State: accepted` / `State: released`, its title says so, and the signature manifestation states who signed, in what capacity (design authority, per SOP line 30/32), when, the meaning, and what was signed (payload digest) in a form a reader can relate to the content.
Actual: DOC-0087 is titled "REQ-2026-0003 snapshot at awaiting-accept-the-requirement" and its body says `State: **awaiting-accept-the-requirement**` although the record is accepted; DOC-0134 likewise says `awaiting-review-the-design` for a released design, and its `## Transitions` lacks the release. The document-renderer step runs before the terminal transition. The signature line is `bob (person), 2026-11-06T14:49:49+00:00: **[DE] design reviewed and released as rev A**; decision 53762c3f5cd8e38f5f25e20e; payload 221c3fca9570` — identity, time, meaning and digest are there (good), but no role/capacity, no routing sentence, and the "payload" is a digest of a JSON blob the reader cannot reconstruct from the page. The body is a JSON dump of fields (bom_lines, bom_resolved ...) rather than a review package. The `meaning` is free text with no vocabulary, so "approve" can carry any meaning.
Evidence: DOC-0087, DOC-0134 (`## Signatures`).

### F-DE-11: [DE] The compiler released a workflow with an undefined state and the release page does not show the workflow
Severity: major
Area: architecture
Steps:
1. `docker compose exec -T hub python3 -c "import json;b=json.load(open('/hub/state/releases/b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef/bundle.json'));w=b['workflows']['design-release'];print(w['states'],[(t['from'],t['to'],t.get('on_reject')) for t in w['transitions']])"`
2. Open `http://127.0.0.1:8080/releases?as=bob`.
Expected: the Consistency Guard (plan 4.1: "unreachable process states") refuses or flags a transition whose `on_reject` target (`awaiting-submit-the-design`) is not a state of the workflow; `/releases` shows per-process workflows (states, checkpoints, SLAs, rejection returns) so a policy owner can see what a sentence produced.
Actual: states are `open, awaiting-review-the-design, released`; the human transition's `on_reject` is `awaiting-submit-the-design`, which exists nowhere; the record was moved into it (F-DE-2). `/releases` is a one-row table (digest, effective, released by, frames) with no link to the rendered workflows; the only workflow view is the diagram on a record page, which draws a second automatic arrow `awaiting-review-the-design -> released : release-the-part, ...` next to the human one, suggesting release without a signature.
Evidence: bundle.json workflows.design-release; `/releases?as=bob`; Workflow tab on DSN-2026-0010.

### F-DE-12: [DE] The review package is a raw field dump with a misleading required input
Severity: major
Area: shell
Steps:
1. `/inbox?as=bob`, card "Review the design · DSN-2026-0002".
Expected: a package I can sign on: the requirements trace as a table (text, state, criteria, method), the BOM as a table with resolved/unresolved/unapproved lines and stock, the checks (ERC errors/warnings) with a link to the report, links to the schematic PDF, STEP and the source files, the risk tier and reasons, the SoD statement, and the sentence that routed it. 
Actual: every field is printed as JSON (`bom_lines [{"footprint": ...}]`, `exports [{"doc_type": "bom", "document": "rec:document:DOC-0014", ...}]`, `requirement_trace [...]`) with no links to the documents; the documents are reachable only by opening the record and the Documents tab. The card shows "released needs: part" with a required text input `part` because the `released` state requires a `part` ref that part-releaser will create after approval; the approver is asked to type a part locator. I posted `part=rec:part:PN-00001` on DSN-2026-0013 and it was (correctly) ignored, so the input is noise that looks like a bypass. Guard findings are present and useful (keep them); `risk_reasons` is in the payload but not in the record Details.
Evidence: inbox-bob card for run 44b2dacca3 (`<input name="part" ... required>`), DSN-2026-0013 released as PN-00008 not PN-00001.

### F-DE-13: [DE] The released part has no documents, no requirements and no BOM of its own
Severity: major
Area: records
Steps:
1. `hubproto record rec:part:PN-00006` after releasing DSN-2026-0010.
2. `/record/rec:part:PN-00006?as=bob` Documents and Links tabs.
Expected (plan 9.6: "a drawing defines a part revision", "parts with revisions ... engineering BOMs per part revision", baseline): the part revision links to the drawing/STEP/schematic PDF and the BOM document of that revision, to the requirements it satisfies, and carries its own BOM; a design history file is "a query over the baseline".
Actual: PN-00006 has `documents: none`, links only `defined-by DSN-2026-0010` and `realized-by ITEM-00003/ITEM-00004`; no requirement link; the BOM lives only on the design record. Revision history is a single `revision` field overwritten in place by part-releaser (`cad.py:328`), so after a rev B nothing says what rev A was. No baseline record exists.
Evidence: PN-00006 record output.

### F-DE-14: [DE] Every signature is listed twice on the record
Severity: major
Area: records
Steps:
1. Accept REQ-2026-0003 once (one POST, one 303).
2. `hubproto record rec:requirement:REQ-2026-0003` -> `decisions:` shows the same line twice (same sig c7c2a111b6bceb1f33353677).
3. `/record/rec:design:DSN-2026-0010?as=bob` Decisions tab -> two identical rows for sig 53762c3f5cd8e38f5f25e20e.
Expected: one decision, one row.
Actual: two rows for each decision (the runner writes `record.decision` and the kernel transition carries the same decision again). An auditor reading this sees two signatures for one act.
Evidence: REQ-2026-0003, REQ-2026-0016, DSN-2026-0010 decisions.

### F-DE-15: [DE] Why page answers only for the acting user and silently ignores a principal parameter
Severity: minor
Area: shell
Steps:
1. As bob: `http://127.0.0.1:8080/why?as=bob&principal=alice&action=approve&object=design&folder=Engineering/Designs`.
2. `http://127.0.0.1:8080/why?as=alice&action=approve&object=design&folder=Engineering/Designs`.
3. `http://127.0.0.1:8080/why?as=bob&action=create&object=design&folder=Engineering/Designs`.
Expected: as the policy owner I can ask "may alice approve design in Engineering/Designs" without impersonating alice; an unknown parameter is reported.
Actual: step 1 answers for bob ("allowed by design-control-sop line 32") and drops `principal` silently; only switching `as=alice` gives "denied: no rule in the release lets any of ['author','design-engineer','finance-clerk','process-owner','staff'] approve design in Engineering/Designs"; step 3 correctly says bob may not create designs. The two answers themselves are right and cite the line (good). The CLI `hubproto why --as alice ...` works.
Evidence: the three URLs above.

### F-DE-16: [DE] The Frame page has no "compiled rules" panel
Severity: minor
Area: policy
Steps:
1. `http://127.0.0.1:8080/frame/design-control-sop?as=bob`.
Expected (plan 4.4): a panel listing every rule derived from the Frame (permissions, routing, SLAs, SoD, the two workflows) and the runs that used each, so I can see the consequences of a sentence before changing it.
Actual: only the numbered source text. The SoD and routing rules can be seen only by reading `/hub/state/releases/<digest>/bundle.json` inside the container.

### F-DE-17: [DE] The SoD sentences cannot be exercised with the seeded members, and the runtime checks the run owner, not the named role
Severity: question
Area: policy
Steps:
1. `hubproto run requirements-capture --as bob --record-json '{"text":"[DE] bob self-captured requirement","kind":"system"}'` -> "bob may not start requirements-capture: no rule ... create requirement".
2. `hubproto decide 4c0b591a8a --as alice --verdict approve` -> "alice is not an assignee of this decision (assigned: bob)".
3. bundle.json `sod` contains both design-control sentences (lines 50, 51); `runner.py:428` enforces them as `by == owner of the run`.
Expected: a way to demonstrate the SoD rule (a member holding both design-engineer and design-authority) or an explicit statement that the org model makes it unreachable; the check keyed on the `captured_by`/`designer` field the sentence names, not the run owner (they differ when a cog or a colleague files on someone's behalf).
Actual: compiled, probably enforced, but unverifiable from the shell; the refusal alice gets is an assignment error, not the SoD citation, so the sentence never appears to anyone.

### F-DE-18: [DE] Policy wording: dead threshold, two routing shapes for the same thing, and no requirement-level routing by kind
Severity: minor
Area: policy
Steps:
1. Read `frames/engineering/design-control-sop.frame.md` lines 38-47.
2. `grep -rn 'design review limit' hubproto/` -> nothing reads the threshold.
Expected: as the accountable owner I would write: one SLA sentence per checkpoint (drop "The design review limit is 10 days." or make the routing sentence refer to it); the same shape for both approvals ("At Accept the requirement in requirements-capture go to the design authority." rather than the blanket "Approvals in requirements-capture go to ..."); an overdue sentence for requirements-capture; "Rejections return to Capture the requirement." for requirements-capture; routing of safety and regulatory requirements to the quality manager as well ("At Accept the requirement in requirements-capture for safety requirements go to the design authority and the quality manager." if the grammar allows a condition); and escalation to someone other than the design authority.
Actual: the threshold on line 40 duplicates line 46 and is unused; line 44 routes all approvals in the process; there is no sentence distinguishing requirement kinds (user-need, system, functional, performance, safety, regulatory, interface are captured and accepted identically; `kind` has no effect anywhere); the narrative paragraph promises "drawings, Gerbers" that the electrical export did not produce (no Gerbers/DRC for a schematic-only design is fine, but the SOP should say "where a board is attached").

### F-DE-19: [DE] A design submitted without a CAD file fails the run instead of being refused or parked
Severity: minor
Area: workflow
Steps:
1. `hubproto run design-release --as alice --record-json '{"title":"[DE] Redirect test design","kind":"mechanical"}'`.
Expected: refusal at submission ("attach at least one .FCStd/.kicad_sch/.kicad_pcb") or a waiting state for the engineer.
Actual: `run f26f77a473 status=failed`; DSN-2026-0012 left permanently in state `failed` ("step result has error-severity problems" at export-the-design) with no way to fix or close it. There are 18 failed runs on the hub from other panelists hitting similar walls.

### F-DE-20: [DE] Requirements have no label on boards, lists and inbox messages
Severity: minor
Area: shell
Steps:
1. `/boards?as=bob&type=requirement`; `hubproto records --type requirement`; bob's inbox messages "Accept the requirement: requirements-capture rec:requirement:REQ-2026-0003".
Expected: the requirement text (or a short form) as the label.
Actual: board cards show "REQ-2026-0003 REQ-2026-0003"; the CLI list has an empty title column; the message carries only the locator. Related to F-DE-1.

### F-DE-21: [DE] The shell start form cannot start a real design-release
Severity: minor
Area: shell
Steps:
1. `/?as=alice`, card "design-release".
Expected: fields for `kind` (required at review), user needs, linked requirements, and file attachments.
Actual: the generic form (title, description, amount, currency, date, po_ref, supplier, change_type, severity, training_roles) plus an "other fields (JSON object)" textarea; no upload. The scenario in `feedback/README.md` step 1 is only achievable with `--record-json` and base64 attachments. (Not reproduced through the shell; the engineer personas will cover it.)

### F-DE-22: [DE] Workflow diagram draws an automatic path around the signature
Severity: minor
Area: shell
Steps:
1. `/record/rec:requirement:REQ-2026-0003?as=bob` Workflow tab; same on any design.
Expected: one human transition `awaiting-accept-the-requirement -> accepted : Accept the requirement`, with the automatic steps after it shown on that edge.
Actual: two edges from `awaiting-accept-the-requirement` to `accepted`: `render-the-record` (automatic) and `Accept the requirement (approve)`; the table row "awaiting-review-the-design -> released automatic release-the-part, order-the-shortages, render-the-record" reads as a release without review.

### F-DE-23: [DE] The quality manual lists its maintainer as an unexpanded placeholder
Severity: minor
Area: policy
Steps:
1. `/?as=bob`, "Frames behind this release": `quality-manual v0.1.0 (company, maintained by {{MANAGEMENT_REPRESENTATIVE}})`.
Expected: `bob` (the management representative) after seeding.
Actual: the template variable was not substituted, so the top-level policy document has no accountable owner.

### F-DE-24: [DE] The design authority cannot record a design review (minutes, attendees, actions) or attach anything to the design
Severity: major
Area: policy
Steps:
1. `/record/rec:design:DSN-2026-0002?as=bob` Documents tab.
2. Try to attach review notes.
Expected (plan 9.6: "design review records with attendees, inputs reviewed, actions; open actions block the gate"): a design-review record or at least the ability for the reviewer to attach findings before deciding; a reject with actions the engineer must close.
Actual: "You cannot attach documents here: no rule in the release lets any of ['controller','design-authority',...] create document in Engineering/Designs". The only artefact of my review is the one-line `meaning` on the decision. No design-review record type, no actions, no attendees. (Permission is a one-sentence fix in the SOP; the review record is missing.)
