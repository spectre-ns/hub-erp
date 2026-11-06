# Mechanical engineer (alice, design engineer; bob as design authority)

Session: 2026-11-06, release `b7da4af94d3c571f`. My records: DSN-2026-0003
(BRK-100 rev A, released, part PN-00002), REQ-2026-0005..0007, DSN-2026-0009
(BRK-100 rev B), CR-2026-0002 (ECO), PR-00012 (manual hardware request),
DOC-0023 (model, 2 versions). The model is my own `bracket.FCStd` built in the
CAD worker: a `Part::MultiFuse` bracket with custom properties PartNumber
`BRK-100`, Revision `A`, Material `AISI 304`, plus a bolt (`DIN933-M6X16-A2`,
Quantity 4, A2-70) and a nut (`DIN934-M6-A2`, Quantity 4). The hub container
was removed mid-session (around 14:48 UTC) while I was approving rev B; the
state survived and I finished after it came back.

Verdict for my day: the submit-export-review-release loop runs and the Track is
excellent evidence, but as a mechanical engineer I cannot get a BOM, a drawing,
an item, a purchase request, a part revision, an ECO tied to the part, or a
readable requirement out of it. Today it is a KiCad demo with a FreeCAD
attachment.

### F-ME-1: [ME] Mechanical BOM is never reconciled; FreeCAD parts never become items or purchase requests
Severity: blocker
Area: workflow
Steps:
1. As alice, POST `/start` process `design-release`, title `BRK-100 sensor bracket`, JSON `{"kind":"mechanical","user_needs":[...3],"attachments":[bracket.FCStd (base64), BRK-100-spec.md]}`. Run `fc3d18fc7d`, record DSN-2026-0003.
2. Open `/record/rec:design:DSN-2026-0003?as=alice`, Details tab, fields `parts`, `bom_lines`, `bom_unresolved`.
3. As bob approve `Review the design` (run `fc3d18fc7d`). Then `/boards?as=alice&type=item` and `/boards?as=alice&type=purchase-request`.
Expected: the five FreeCAD objects with PartNumber/Quantity become BOM lines (1 x BRK-100, 4 x DIN933-M6X16-A2, 4 x DIN934-M6-A2); the reconciler says the two hardware lines match no item and proposes items; after release an item is created for each and purchase requests appear for the shortages, as the SOP prose promises ("extracts the bill of materials, and reconciles every line against inventory").
Actual: `parts` holds the five objects with part numbers, materials and quantities correctly extracted, but `bom_lines` is `[]`, `bom_resolved/unresolved/unapproved` are `[]`, the reconciler Gate is `pass` with `bom_resolved: true`, `shortage-requester` passes and creates nothing. Boards show only ITEM-00001 `100n` and ITEM-00002 `10k` (KiCad path) and no PR for my design. Only the KiCad schematic path produces a BOM.
Evidence: DSN-2026-0003 activity 205 (`bom_lines: []`) and 206 (`bom_resolved: []`, `conditions.bom_resolved: true`); run `fc3d18fc7d` steps 8-9; `/boards?type=item`.
Suggested fix: feed `parts` entries with a `part_number` into `bom_lines` (mpn = part number, manufacturer = standard), skip entries consumed by a boolean/fuse, and let the reconciler report an empty BOM as a warning rather than `bom_resolved: true`.

### F-ME-2: [ME] Start form has no file upload, no `kind`, no user-need fields; attachments must be hand-typed as base64 JSON
Severity: major
Area: shell
Steps:
1. `GET /?as=alice`, card `design-release`.
2. Try to attach `bracket.FCStd` and type two user needs.
Expected: a file input (multipart) for the models, a `kind` select (the record type defines `enum:mechanical,electrical,mixed`), and repeatable user-need text fields; the README scenario says to start the process "with the two example files attached".
Actual: the card shows the same eleven generic inputs for every process (`amount`, `po_ref`, `supplier`, `training_roles`...), none relevant to a design, and an "other fields (JSON object)" textarea. The only way to attach a model from the shell is `{"attachments":[{"name":"bracket.FCStd","content_b64":"<10 KB of base64>"}]}` in that textarea; `kind` and `user_needs` also go in the JSON. The form posts as `application/x-www-form-urlencoded` (`<form method="post" action="/start">`, no `enctype`), so a file input would not work either. The record page later offers "Upload and attach" with a real file input, but by then the export step has already run (F-ME-7).
Evidence: `/?as=alice` HTML: `COMMON_INPUTS` rendered identically on all 11 cards; my submission `start.json` (10,304 bytes of JSON pasted into a 2-row textarea).
Suggested fix: render the Start form from the record type's `fields` (enum selects, lists as repeatable rows) and make it multipart with a file input that the record-filer turns into attachments.

### F-ME-3: [ME] Requirement text is invisible on the record, the inbox card, the board, and the signed snapshot
Severity: blocker
Area: records
Steps:
1. `GET /record/rec:requirement:REQ-2026-0005?as=alice` (also as bob), Details tab.
2. `GET /inbox?as=bob`, card "Accept the requirement REQ-2026-0005".
3. `GET /boards?as=alice&type=requirement`.
4. After bob accepts, download the snapshot `DOC-0042` (`/download?as=alice&doc=rec:document:DOC-0042`).
5. CLI: `hubproto record REQ-2026-0005`.
Expected: the sentence being accepted ("The bracket shall hold a 5 kg sensor with a 2 g shock load without yielding.") is the first thing shown everywhere, and the signed snapshot states it.
Actual: `text` is stored (activity 173 `record.fields_set ... "text": "The bracket shall hold a 5 kg sen...`) but is not listed on the Details tab, the inbox card (shows `captured_by, conditions, design, ... verified` only), the board card (title is the number, `REQ-2026-0005 REQ-2026-0005`), the CLI output, or the snapshot DOC-0042 `## Fields`. bob electronically signed "Accepted as design input" on a page that never shows what the requirement says; the only place the text appears is the design's trace CSV and the design snapshot.
Evidence: DOC-0042 content (fields list has no `text`); `/inbox?as=bob` card for run `796f661ef2`; `hubproto record REQ-2026-0005`.
Suggested fix: treat `text` as the requirement's title/summary (`summary_of`) and never drop it from field rendering.

### F-ME-4: [ME] Design authority signs with the inbox form demanding a `part` number that it then ignores
Severity: major
Area: shell
Steps:
1. `GET /inbox?as=bob`, card "Review the design DSN-2026-0003".
2. Note `released needs: part <input required>`.
3. POST `/decide` as bob with `verdict=approve` and no `part` value.
Expected: either the system creates the part (it does, PN-00002 by `part-releaser`) and the form does not ask for one, or the form explains what to type.
Actual: the form marks `part` as required with an empty text box (a reference to a record that does not exist yet); submitting without it succeeds and the part-releaser fills it. A browser user is blocked by the `required` attribute and has no idea what to enter.
Evidence: `/inbox?as=bob` HTML `released needs: part{input name="part" ... required}`; decision on run `fc3d18fc7d` at 14:45:08 succeeded without it.

### F-ME-5: [ME] Acceptance of requirements does not reach the waiting design; bob released a package that says requirements are not accepted
Severity: major
Area: workflow
Steps:
1. Submit DSN-2026-0003 (requirements captured inline). Design waits for bob with `requirements_accepted: false`, risk `high`.
2. As bob accept REQ-2026-0005, -0006, -0007 (14:44:47).
3. Reload `/record/rec:design:DSN-2026-0003?as=bob` and bob's inbox card.
Expected: the trace, `requirements_accepted`, `conditions` and `risk` re-evaluate once the requirements are accepted (or the design waits for the acceptances before routing to review), so bob reviews a truthful package.
Actual: the design still shows `requirements_accepted: False`, `requirement_trace[*].state: awaiting-accept-the-requirement`, risk `high`, Guard finding "3 requirement(s) not yet accepted"; it was released that way and the released record and PN-00002 carry the stale values. Only a fresh submission (DSN-2026-0009) shows `requirements_accepted: true`.
Evidence: DSN-2026-0003 Details after release: `conditions {"requirements_accepted": false ...}`, `risk high`; REQ-2026-0005 transition to `accepted` at 14:44:47, design release at 14:45:08.

### F-ME-6: [ME] A mechanical design can never be "checks clean" or leave risk `high`
Severity: major
Area: workflow
Steps:
1. Submit DSN-2026-0009 (rev B) linking the three accepted requirements: `{"requirements":["rec:requirement:REQ-2026-0005",...]}`.
2. Look at `conditions` and `risk`.
Expected: with no design rule checks defined for FreeCAD, `design_checks_clean` is not-applicable (or a geometry check runs), and risk reflects the accepted requirements.
Actual: `checks: {}`, `design_checks_clean: false`, `risk_reasons: ["design checks not clean"]`, risk `high` even with `requirements_accepted: true`. Every mechanical design is "high risk" by construction, so a routing sentence like "with low risk go to ..." can never apply to my work.
Evidence: DSN-2026-0009 `conditions {"bom_resolved": true, "design_checks_clean": false, "requirements_accepted": true, "risk_low": false, "risk_not_high": false}`; DSN-2026-0003 `risk_reasons ["requirements not all accepted", "design checks not clean"]`.

### F-ME-7: [ME] No drawing is produced for a FreeCAD model, and nothing in the record is a drawing
Severity: major
Area: workflow
Steps:
1. Submit a design with `bracket.FCStd`.
2. Documents tab of DSN-2026-0003.
Expected: per the SOP ("Submitting it exports the neutral formats, drawings, ...") a drawing PDF bound to the part revision, or at least a way to attach my own drawing with `doc_type: drawing` from the Start form.
Actual: exports are `model.step`, `model.stl`, `props.json` only. The exporter asks the worker for `["step","pdf"]` and no PDF comes back, with no warning recorded ("Export the design: passed"). My Markdown spec went in as a generic controlled document with no `doc_type` visible. Nothing distinguishes a drawing from a model from a spec on the Documents tab.
Evidence: DSN-2026-0003 `exports` (3 entries, doc types `model-step`, `model-stl`, `model-properties`); run `fc3d18fc7d` step 7 problems `[]`.

### F-ME-8: [ME] Parts list counts construction solids and the extracted material/revision go nowhere
Severity: major
Area: records
Steps:
1. Submit `bracket.FCStd` (Base and Leg boxes fused into `Bracket`; bolt and nut cylinders).
2. Read `parts` on DSN-2026-0003 and the released part PN-00002.
Expected: a parts list of three (bracket, bolt x4, nut x4); the released part carries part number `BRK-100`, material `AISI 304`, revision `A` and mass from `props.json` volume (45,005 mm3 x 7.9 g/cm3 = 356 g); the part type even has `material` and `mass_g` fields.
Actual: `parts` lists five entries including `Base` and `Leg` (consumed by the MultiFuse, not real parts). PN-00002 has only `design, released_by, revision A, title`; `material`, `mass_g`, `classification` are empty; the CAD part number `BRK-100` is lost (the hub number PN-00002 is the only identity). The document DOC-0023 got `cad_properties` (Id `BRK-100`, Company, CreatedBy) but nothing reads them.
Evidence: DSN-2026-0003 `parts`; `hubproto record PN-00002`; DOC-0023 activity 192.

### F-ME-9: [ME] Part revision increments only if the engineer hand-types the part locator into the JSON; no supersedes, no where-used, no effectivity, no revision history on the part
Severity: major
Area: workflow
Steps:
1. With PN-00002 rev A released from DSN-2026-0003, submit DSN-2026-0009 with JSON `{"part":"rec:part:PN-00002","revision":"B","requirements":[the three accepted],"attachments":[bracket-revB.FCStd]}`. As bob approve run `10cb4a0f34`.
2. Submit DSN-2026-0014 the same way but with `"revision":"A"` (a typo going backwards) and the old rev A file. As bob approve run `9afd242946`.
3. `hubproto record PN-00002`; Links tabs of DSN-2026-0003, -0009, -0014; `hubproto records --type design | grep BRK`.
Expected: the design picks the part it revises from a list; the part goes A -> B with the rev A design marked superseded and a `supersedes` link between the designs; a nonsensical revision is rejected or at least shown to bob; the part page lists its revisions with their designs, documents, signatures, where-used and effectivity.
Actual: it works only because I knew to type `"part":"rec:part:PN-00002"` into the "other fields" textarea (no picker, no validation that the locator exists or that I may revise it; another panelist's resubmission got a brand-new PN-00006 instead). The part-releaser then ignores the typed revision and increments on its own: PN-00002 went A -> B -> C, and my "A" typo became rev C with bob's signature on it. All three designs stay `released` with no link between them; PN-00002 carries three bare `defined-by` links that do not say which revision each design produced; the revision history exists only as `record.fields_set` lines in the Activity tab (633, 1872, 1966). No where-used, no effectivity, no baseline anywhere in the shell or CLI. Missing.
Evidence: PN-00002 activity 1872 (`revision B`, run `10cb4a0f34`) and 1966 (`revision C`, run `9afd242946`); DSN-2026-0014 Details `revision C` (overwritten from my `A`).

### F-ME-10: [ME] Engineering change (ECO) path is missing: change-control is a QMS change request routed to the quality manager with no part, effectivity or baseline
Severity: major
Area: workflow
Steps:
1. As alice, start `change-control`, title `ECO: BRK-100 rev A to rev B (5 mm, 316L)`, `change_type=major`, JSON `{"impact": ..., "affected_documents": ["rec:document:DOC-0023","rec:document:DOC-0024"], "affected_processes": ["design-release"]}`. Record CR-2026-0002, run `676e25522d`.
2. Look at its Links, Workflow, assignees.
Expected (the plan promises PLM: ECR/ECO, baselines, where-used, drawings bound to part revisions): an ECO that names the part and revisions (from/to), is reviewed by the design authority, lists where-used, sets an effectivity, and gates the release of the new design revision.
Actual: CR-2026-0002 routes to carol (quality manager) by `change-control-sop` line 19; the design authority is not involved; `affected_documents` is a bare list of locators with no links created (Links: "No links."); there is no field for part, from/to revision, effectivity or disposition of stock; no relation to DSN-2026-0009 unless I add a generic link by hand. There is no ECR, no baseline, no where-used anywhere in the shell or CLI. Missing.
Evidence: CR-2026-0002 Details and Links; record-types `change-request` fields.

### F-ME-11: [ME] Cannot order hardware that is not yet an item, and cannot create the item
Severity: major
Area: workflow
Steps:
1. As alice, start `purchase-request`, title `4 x DIN933 M6x16 A2 for PN-00002`, amount 12 USD, supplier `acme-metals`, JSON `{"mpn":"DIN933-M6X16-A2","manufacturer":"Bossard","quantity":4,"unit_cost":3,"design":"rec:design:DSN-2026-0003"}`. Run `3499667f8c`, PR-00012.
2. `GET /why?as=alice&action=create&object=item&folder=Inventory%2FItems` (permission "Design engineers may create items in Inventory/Items").
Expected: the request routes to dave with the "no known item" warning (as the KiCad-path requests do via a proposed item), or the engineer can create the item first.
Actual: supplier-checker warns `the request names no known inventory item`, the cog escalates, the Gate routes to dave, and then the run fails: `required-fields: PR-00012 cannot enter 'awaiting-approve-the-purchase' without item`. The record ends in state `failed` while the run page header says both "Run 3499667f8c failed" and "Waiting at Approve the purchase for dave until 2026-11-09". No process creates an item (the Start page lists none), so the permission sentence is dead. Items only ever come from the KiCad BOM reconciler.
Evidence: PR-00012 activity 879; run `3499667f8c` Track entries 9 (gate.pending) then 10 (run.failed); `/?as=alice` "Processes you can start" has no item process.

### F-ME-12: [ME] A controlled model under a released design can be checked in and changed with no control; design documents never leave `draft`
Severity: major
Area: DMS
Steps:
1. After DSN-2026-0003 is released (PN-00002 rev A), as alice POST `/checkout` doc `rec:document:DOC-0023`, then POST `/checkin` (multipart) with `bracket-revB.FCStd`, note `rev B: 5 mm plate, 316L`.
2. Open `/document/rec:document:DOC-0023?as=alice` and the design's `sources`.
Expected: the model behind a released part revision is frozen (or a new version requires an ECO / starts a new revision); documents of a released design show as released/effective; the design or part shows that its source now differs from what was released.
Actual: check-in succeeds; DOC-0023 becomes `A.1`, state still `draft`, filename now `bracket-revB.FCStd`; the design's `sources` still says version 1 sha `8ef501a7...` and nothing flags the mismatch; PN-00002 still points to DSN-2026-0003 whose model file has silently changed. All six documents of the released design remain `draft` (controlled) or `filed` (evidence). The check-out itself works (`checked out by alice`, versions table, download per version), which is good.
Evidence: DOC-0023 versions 1 and 2; DSN-2026-0003 `sources[0].version: 1`.

### F-ME-13: [ME] Design authority cannot attach anything to a design (review markups, redlines)
Severity: major
Area: policy
Steps:
1. As bob, POST `/checkin` on DOC-0023 (or "Upload and attach" on DSN-2026-0003).
Expected: a design authority can file review comments or a marked-up drawing against the design under review; a check-in by someone else is refused because alice holds the check-out, with that reason.
Actual: 403 "You cannot change documents in Engineering/Designs: no rule in the release lets any of ['controller', 'design-authority', ...] create document in Engineering/Designs". The refusal is a permission refusal, not the check-out lock, so I could not test the lock between two engineers (alice is the only design engineer who can file there). The design authority has approve and read but not create.
Evidence: `/checkin` as bob at 14:46:18, HTTP 403.

### F-ME-14: [ME] The part record is an empty stub: no documents, no workflow, filed under Designs
Severity: major
Area: records
Steps:
1. `GET /record/rec:part:PN-00002?as=alice`.
Expected: the part is the thing a mechanical engineer looks up: model, STEP, STL, drawing, spec and requirements trace of the releasing revision, who signed, revision history, where-used, material and mass.
Actual: "Documents: No documents attached", "No workflow in the release covers this record type", "Decisions: No signatures yet" (bob's signature is on the design, not the part), one link `defined-by DSN-2026-0003`, folder `Engineering/Designs`, owner `part-releaser`. To find the STEP for PN-00002 I must follow the link to the design and then scan a flat Documents table where `model.step from bracket.FCStd` sits next to the spec and the requirements trace with no doc types shown.
Workaround tried: as alice I could hand-attach DOC-0023 to PN-00002 (POST `/attach`, `doc=rec:document:DOC-0023`) and render a part snapshot (DOC-0139); both work, but the binding is to the document, not to a version, and the model is now at A.1 while the part says rev C.
Evidence: PN-00002 page; `hubproto record PN-00002` ("documents: none, decisions: none" before my manual attach).

### F-ME-15: [ME] Decisions table and CLI show every signature twice
Severity: minor
Area: records
Steps:
1. `GET /record/rec:design:DSN-2026-0003?as=alice`, Decisions tab; `hubproto record REQ-2026-0005`.
Expected: one row per signature.
Actual: bob's single approve (signature `88dd489b533e111d79d4ccf8`) is listed twice on DSN-2026-0003, DSN-2026-0001 and every REQ; the CLI prints `decisions:` with the same line twice. On evidence this reads as two signatures.
Evidence: DSN-2026-0003 Decisions tab; `hubproto record REQ-2026-0005` decisions block.

### F-ME-16: [ME] Snapshot "at awaiting-..." is rendered after approval but states the pre-approval state
Severity: minor
Area: DMS
Steps:
1. Download DOC-0065 (`DSN-2026-0003 snapshot at awaiting-review-the-design`) and DOC-0042 (REQ-2026-0005 snapshot).
Expected: the snapshot taken as the last process step says the record is `released`/`accepted` and lists the transition it just took.
Actual: `State: **awaiting-review-the-design**` and Transitions omit the final transition, although the Signatures section already carries bob's approval at 14:45:08 and the file is rendered 14:45:08. The title says "at awaiting-review-the-design". A reader sees an approved-but-not-released record.
Evidence: DOC-0065, DOC-0042 content.

### F-ME-17: [ME] Inbox cards dump the raw record (`parts`, `exports`, `requirement_trace` JSON) into one unreadable line
Severity: major
Area: shell
Steps:
1. `GET /inbox?as=bob`, card "Review the design DSN-2026-0003".
Expected: a reviewer sees title, kind, designer, the requirements with their texts and states, the parts list as a table, the exports as download links, the Guard findings, and the approve form.
Actual: the card is one run-together string of every field value (`attachments_filed[...]bom_lines[]...parts[{"label": ...}]requirement_trace[{...}]...`) with no labels separated from values, JSON for lists, and no links to the documents. The one thing that matters (the model/STEP to open) is not clickable from the inbox.
Evidence: `/inbox?as=bob` HTML for run `fc3d18fc7d`.

### F-ME-18: [ME] Inbox accept form asks only for acceptance criteria; verification method is not offered
Severity: minor
Area: shell
Steps:
1. `GET /inbox?as=bob`, card "Accept the requirement REQ-2026-0005".
Expected: fields for `acceptance_criteria` and `verification_method` (enum test/inspection/analysis/demonstration), as the README scenario step 2 expects bob to supply both.
Actual: only `acceptance_criteria` is shown (because only it is required for `accepted`). I could set `verification_method=analysis` by adding the parameter to the POST, and it was stored, so the shell accepts it but never shows it.
Evidence: `/inbox?as=bob` card for run `796f661ef2`; REQ-2026-0005 activity 357.

### F-ME-19: [ME] Record page tabs are anchors on one long page; "Attach existing" lists every document in the hub
Severity: minor
Area: shell
Steps:
1. `GET /record/rec:part:PN-00002?as=alice`, click Documents, look at the "or attach existing" select.
Expected: tabs that switch content; a document picker scoped to the folder or to the record's design, searchable.
Actual: the tabs scroll to anchors; the Track is repeated in full on the record page and on the run page; the select lists all 75+ documents (`DOC-0001 sensor.kicad_sch ... DOC-0075 props.json from box.FCStd`) from every panelist's design, including evidence exports that should not be re-attached by hand.
Evidence: PN-00002 page `<select name="doc">` contents.

### F-ME-20: [ME] Workflow diagram and "States" line misrepresent the process
Severity: minor
Area: shell
Steps:
1. Workflow tab on DSN-2026-0003, REQ-2026-0005, PR-00012, CR-2026-0002.
Expected: one path per outcome; terminal states shown as branches; the current state highlighted even when it is `failed`.
Actual: two parallel arrows `s1 --> s2` ("release-the-part, order-the-shortages, render-the-record" and "Review the design (approve)") for one transition; the "States:" line reads as a linear chain `open -> awaiting-review-the-design -> released` and for REQ `... -> accepted (current) -> rejected`, as if rejected followed accepted; on PR-00012 the current state `failed` is highlighted in the diagram (`class s4 current`) but `failed` is absent from the States line and the transitions table, so the page never says how the record got there. The diagram needs Mermaid from a CDN (`cdnjs.cloudflare.com/.../mermaid.min.js`); offline it shows as raw `stateDiagram-v2` text.
Evidence: Workflow sections of the four records.

### F-ME-21: [ME] "Why" on a record always asks the one question I do not have
Severity: minor
Area: shell
Steps:
1. Open any of my records as alice.
Expected: Why explains the decisions that touched this record: why it was routed to bob, why it is high risk, why the BOM is empty, which sentence let me submit.
Actual: the header shows `Why: may alice approve design in Engineering/Designs? denied no rule ...` on every record I own, i.e. the one permission I never asked for. The routing sentence is shown separately in the waiting card (good) and the `/why` page only answers create/read/approve/update/delete for a folder. The `/frame/design-control-sop` page is a line-numbered dump, so "line 43" links land on the right line but the page gives no context of what was compiled from it.
Evidence: `/record/rec:design:DSN-2026-0003?as=alice` header; `/why?as=alice`.

### F-ME-22: [ME] Boards work for designs but a requirement board shows only numbers, and no board for parts or documents per design
Severity: minor
Area: shell
Steps:
1. `/boards?as=alice&type=design` (fine: columns by state, assignees, due).
2. `/boards?as=alice&type=requirement`.
3. `/boards?as=alice&type=part`.
Expected: requirement cards carry the requirement text and the design they belong to; parts board carries part number, revision and design.
Actual: requirement cards read `REQ-2026-0005 REQ-2026-0005` and only the folder and owner; no indication which design they belong to. The parts board (`type=part`) has a single `released` column of seven cards showing title and owner `part-releaser`, no revision, no CAD part number, no design.
Evidence: `/boards?as=alice&type=requirement`.

### F-ME-23: [ME] A rejected design has no resubmit path in the shell
Severity: major
Area: workflow
Steps:
1. Open `/record/rec:design:DSN-2026-0002?as=alice` (another panelist's design that bob rejected; state `awaiting-submit-the-design`, run `44b2dacca3` rejected).
2. Look for a way to fix the package and resubmit, as the SOP says "Rejections return to Submit the design".
Expected: a "Submit the design" action on the record (attach the corrected model, resubmit the same DSN number, keeping the requirement links and history), and the run continuing on the same record.
Actual: the record page has only attach/link/snapshot/hold forms; no resubmit; the run is `rejected` and the record sits in `awaiting-submit-the-design` with no assignee and no due date, so it also never escalates. The other panelist had to start a new design (PN-00006 "[DE] ... (resubmission of DSN-2026-0002)"), which got a new DSN and a new part number. Rejection comments from bob are not shown on the record either (only in Decisions as "meaning").
Evidence: DSN-2026-0002 page forms: `/attach`, `/hold`, `/link`, `/snapshot` only; `/boards?type=design` column `awaiting-submit-the-design 1`.

### F-ME-24: [ME] The designer is not told when the design is released or rejected
Severity: minor
Area: shell
Steps:
1. After bob releases DSN-2026-0003, -0009, -0014, open `/inbox?as=alice`.
Expected: a message "DSN-2026-0009 released as PN-00002 rev B by bob" (the hub does send "PO-0007 issued for PR-00013" DMs for purchase orders).
Actual: no message for any design or requirement decision; alice learns of the release only by reopening the record or the Runs list. The "runs I started" counter on Home says `0` for alice although `/runs?as=alice` lists dozens.
Evidence: `/inbox?as=alice` Messages section (PO updates only); `/?as=alice` counts card.

### F-ME-25: [ME] Run page header says both "failed" and "Waiting at ... for dave"
Severity: minor
Area: shell
Steps:
1. `GET /run/3499667f8c?as=alice` (PR-00012).
Expected: one status.
Actual: "Run 3499667f8c failed" followed by the warn card "Waiting at Approve the purchase for dave until 2026-11-09"; the Runs list also shows status `failed` with "Waiting for dave (due 2026-11-09)". The Steps table shows `Score the risk: passed` with no failed step although the Track's last entry is `run.failed`.
Evidence: run `3499667f8c` page and `/runs?as=alice` row.

## Would this fit my day?

What works: submitting a model and getting STEP, STL and model properties back within a second; the Track and the pinned release digest; check-out/check-in with per-version SHA and download; the design snapshot with requirements, acceptance criteria and signature; the design board.

What stops me: no mechanical BOM, items or purchase requests (F-ME-1, F-ME-11); the Start form (F-ME-2); requirements whose text nobody can see (F-ME-3); a part that is an empty stub with no bound documents, drawings, revision history or where-used (F-ME-8, F-ME-9, F-ME-14); no ECO path tied to the part (F-ME-10); released models that can be changed in place (F-ME-12); risk that is always high (F-ME-6); and no resubmit after rejection (F-ME-23).
