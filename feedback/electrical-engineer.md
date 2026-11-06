# Electrical engineer (PCB design in KiCad) — acceptance findings

Panelist: alice, design engineer. Everything below was reproduced on the live
Docker hub (release `b7da4af94d3c`, KiCad 9.0.2 in the CAD worker) on
2026-11-06 between 14:45 and 15:00 UTC, with curl against
`http://127.0.0.1:8080` and `docker compose exec -T hub hubproto ...`. Other
panelists were active at the same time, so record numbers in between mine are
theirs. My records carry `[EE]` in the title.

Test files are in `feedback/ee-files/` (generators `gen_sch.py`, `gen_pcb.py`,
`gen_simple.py` and the generated `.kicad_sch` / `.kicad_pcb`). The small
ones are pasted inline where a finding depends on them.

How I started a run with files attached (the only way the shell offers, see
F-EE-20): the `json` textarea of the Start form, with
`{"kind":"electrical","user_needs":[...],"attachments":[{"name":"x.kicad_sch","content_b64":"...","doc_class":"controlled","doc_type":"schematic"}]}`,
posted as

```
curl -F as=alice -F process=design-release -F 'title=...' -F "json=<start.json" http://127.0.0.1:8080/start
```

## Scenario trace (for reference, not findings)

1. `design-release` DSN-2026-0008, run `6a76cb2c6d`, with `examples/sensor.kicad_sch`
   and `examples/box.FCStd`: REQ-2026-0014 and REQ-2026-0015 waiting for bob,
   trace DOC-0068, BOM DOC-0069, ERC DOC-0070, schematic PDF DOC-0071 (25 KB,
   real), netlist DOC-0072, STEP DOC-0073, STL DOC-0074; design waiting for bob,
   risk `high`. Correct as specified.
2. bob accepted both requirements (runs `6d9b7f93bd`, `e4d8d7a4c8`), approved the
   design: part PN-00003 released, PR-00013 and PR-00014 raised, both waiting
   for dave (neither could go to the cog, see F-EE-13).
3. dave approved PR-00014 naming `acme-metals` / 0.02: PO-0006 issued with amount
   0.00 (F-EE-01). The cog path only worked for a hand-made request PR-00019
   (`acme-metals`, 10 x 0.02): approved by purchasing-assistant under
   purchasing-policy line 53, PO-0011. PR-00017 naming `newco-tools` was
   refused by the cog ("condition supplier_approved not met") and waits for
   dave. A 12,000 USD request (run `ac8f4208a1`) went to bob. Correct.
4. `goods-receipt` GR-00006 for PO-0006 and GR-00014 for PO-0011, dave confirmed,
   stock rose, both POs `received`. Correct.
5. `purchase-to-pay` against PO-0011: BILL-0036 matched, approved by ap-reviewer,
   JE-00019 posted. Against PO-0006 (amount 0): price variance 100 %, waits for
   dave (F-EE-01).
6. Second design DSN-2026-0016 (my attack schematic + board): run failed at ERC
   (F-EE-10). Third DSN-2026-0018 (BOM edge cases + unrouted board): released
   by bob, PN-00009, ITEM-00005..7 created, PR-00039..44 raised. Fourth
   DSN-2026-0020 (quantity above stock): released, PN-00010, no purchase
   request (F-EE-04).

## Findings

### F-EE-01: Unit cost entered by the buyer at the Gate is dropped; the PO is issued with amount 0 and the bill can never match
Severity: blocker
Area: workflow
Steps:
1. Release a design whose BOM lines resolve to items without a unit cost (any
   item created by the system has none). DSN-2026-0008 produced PR-00014
   "2 x RC0603FR-0710KL", item ITEM-00002, unit_cost 0.0, amount 0.0, waiting
   for dave with Guard finding "no unit cost on the request or the item;
   amount is 0 until a buyer sets it".
2. As dave: `curl -F as=dave -F run=84903a0445 -F verdict=approve -F 'meaning=ordered from Acme' -F supplier=acme-metals -F unit_cost=0.02 http://127.0.0.1:8080/decide`
3. `hubproto record PR-00014`; `hubproto record PO-0006`.
4. As alice: start `purchase-to-pay` with amount 0.04, po_ref PO-0006,
   supplier acme-metals (run `ace2829d11`).
Expected: PR-00014 unit_cost 0.02, amount 0.04; PO-0006 amount 0.04; the bill
matches within tolerance and the cog approves it.
Actual: PR-00014 `unit_cost 0.0`, `amount 0.0`, `supplier acme-metals`; PO-0006
`amount 0.0`, line `unit_cost 0.0`. The supplier from the Gate was kept, the
unit cost was not. The bill run stops at "price-variance variance 100.00%
exceeds tolerance 2.0%" and waits for dave. The Guard text promised "until a
buyer sets it", but the buyer cannot set it.
Evidence: PR-00014, PO-0006, run `84903a0445`, bill run `ace2829d11` (BILL
against PO-0006). Compare PR-00018, where I put `unit_cost` in the request
JSON myself: amount 0.1 was computed.
Suggested fix: po-issuer must take unit cost and supplier from the record as
it is after the decision, and the supplier check and amount must be recomputed
after the Gate (see F-EE-02).

### F-EE-02: The buyer can route a PO to an unapproved supplier by naming it at the Gate; the supplier check never re-runs
Severity: blocker
Area: workflow
Steps:
1. As alice: `curl -F as=alice -F process=purchase-request -F 'title=[EE] 5 x RC0603FR-0710KL no supplier' -F 'json={"item":"rec:item:ITEM-00002","quantity":5,"unit_cost":0.02}' http://127.0.0.1:8080/start` → PR-00018, run `66c7bcd273`, waits for dave ("no supplier named").
2. As dave: `curl -F as=dave -F run=66c7bcd273 -F verdict=approve -F 'meaning=order from NewCo' -F supplier=newco-tools -F unit_cost=0.02 http://127.0.0.1:8080/decide`
3. `hubproto record PR-00018`.
Expected: the purchasing policy says "The supplier must be on the approved
list"; naming `newco-tools` (registered, not approved) should be refused or
sent back through "Check the supplier".
Actual: PR-00018 is `ordered`, `supplier newco-tools`, `supplier_approved
False`, `supplier_note "no supplier named"` (stale), PO-0012 issued to
newco-tools. Nothing in the Track says a supplier was checked after the
decision. The cog correctly refuses newco-tools (PR-00017), but a person at
the Gate bypasses the list entirely.
Evidence: PR-00018, PO-0012, run `66c7bcd273`.

### F-EE-03: The controlled source of a released design can be replaced by check-in after release, with no gate, no re-export and no new revision
Severity: blocker
Area: DMS
Steps:
1. DSN-2026-0018 is `released` (PN-00009). Its schematic is DOC-0192
   (`ee_bom_edge.kicad_sch`, sha 5ede3aae...).
2. As alice: `curl -F as=alice -F doc=rec:document:DOC-0192 http://127.0.0.1:8080/checkout`
3. `curl -F as=alice -F doc=rec:document:DOC-0192 -F file=@ee_clean.kicad_sch -F 'note=ERC-clean rev' http://127.0.0.1:8080/checkin`
4. `hubproto doc-versions DOC-0192`; `hubproto record DSN-2026-0018`.
Expected: a released design's sources are frozen; a change needs a change
order / new design revision and a new review (ISO 9001 8.3.6, which the frame
cites).
Actual: DOC-0192 is now version 2, file `ee_clean.kicad_sch`, 27301 bytes, sha
ff42de09..., revision A.1, still state `draft`. DSN-2026-0018 stays
`released`, its `sources` still cite version 1 sha 5ede3aae..., no run started,
no notification. The released part now points at a design whose controlled
document is not what bob reviewed.
Evidence: DOC-0192 versions 1 and 2, DSN-2026-0018 `sources`.
Suggested fix: lock documents attached to a released design (or any record
past its human checkpoint); a check-in must raise a design change.

### F-EE-04: Shortage is computed per BOM line, not per item, and nothing allocates stock: 268 needed against 139 in stock raises no purchase request
Severity: major
Area: workflow
Steps:
1. ITEM-00002 (RC0603FR-0710KL) had stock 139.0.
2. Submit `feedback/ee-files/ee_qty2.kicad_sch`: 134 resistors with Value `10k`
   and 134 with Value `10K`, all MPN RC0603FR-0710KL (Manufacturer Panasonic,
   which makes both lines resolve to ITEM-00002, see F-EE-06). Run
   `0a3dd1f9c4`, DSN-2026-0020.
3. Record page `bom_resolved`: two lines, each `quantity 134, stock 139.0, short 0`.
4. bob approves: `hubproto record DSN-2026-0020`.
Expected: one line of 268 (value case should not split a part) and a shortage
of 129; or at least the two lines summed per item.
Actual: DSN-2026-0020 released, PN-00010, no `purchase_requests`, no
`items_created`. Stock is also never reserved or consumed by a release, so
every later design sees the same 139 pieces.
Evidence: DSN-2026-0020, BOM DOC-0213.

### F-EE-05: DNP parts are counted in the BOM and ordered
Severity: major
Area: workflow
Steps:
1. Submit `ee_bom_edge.kicad_sch` (pasted below): C2 has `(dnp yes)`, same
   value/footprint/MPN as C1. DSN-2026-0018, BOM DOC-0195.
2. bob approves; `hubproto records --type purchase-request`.
Expected: BOM line `C1,1,100n,...` (C2 listed as DNP with quantity 0, or
omitted); a request for 1.
Actual: BOM CSV `C1 C2,2,100n,Capacitor_SMD:C_0603_1608Metric,CL10B104KB8NNNC,Samsung,`;
PR-00039 "2 x CL10B104KB8NNNC for DSN-2026-0018".
Evidence: DOC-0195, PR-00039.

### F-EE-06: Symbols excluded from the BOM (`in_bom no`) are counted; a mounting hole becomes an inventory item and a purchase request
Severity: major
Area: workflow
Steps: same design as F-EE-05. R5 is `(in_bom no)`; H1 is
`Mechanical:MountingHole` with `(in_bom no)`.
Expected: R5 and H1 absent from the BOM; nothing created for them.
Actual: BOM CSV `R1 R5 R6,3,10k,...` (R5 counted) and
`H1,1,MountingHole,MountingHole:MountingHole_2.2mm_M2,,,`. After approval
ITEM-00007 "MountingHole" (no MPN, no manufacturer) was created and PR-00044
"1 x MountingHole for DSN-2026-0018" raised to dave. The `in_bom no` flag is
written on the symbol exactly as KiCad 8/9 writes it (`(in_bom no)`), so the
extractor's exclusion does not work on real files.
Evidence: DOC-0195, ITEM-00007, PR-00044.

### F-EE-07: Multi-unit symbols are counted once per unit: one LM358 becomes three
Severity: major
Area: workflow
Steps: same design. U1 is placed three times (units 1, 2, 3 of a dual op-amp,
same Reference `U1`), as KiCad stores every multi-unit part.
Expected: BOM line `U1,1,LM358,...,LM358DR,Texas Instruments`.
Actual: `U1 U1 U1,3,LM358,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,LM358DR,Texas Instruments,`;
ITEM-00006 created, PR-00043 "3 x LM358DR". Any op-amp, logic gate or
multi-gang connector will be over-ordered.
Evidence: DOC-0195, PR-00043.

### F-EE-08: A manufacturer mismatch is silently resolved to another manufacturer's item
Severity: major
Area: workflow
Steps: same design. R4 has MPN `RC0603FR-0710KL` (a Yageo number) with
Manufacturer `Panasonic`.
Expected: either unresolved with a proposal, or resolved with a warning such
as the bom-reconciler's own "manufacturer not on the approved list"; at
minimum the reviewer sees the discrepancy.
Actual: `bom_resolved` line R4: `manufacturer Panasonic, item ITEM-00002`
(whose manufacturer is Yageo), `short 0`, no problem on the Gate, nothing in
`bom_unapproved`. The Track shows "Reconcile the bill of materials: passed".
Evidence: DSN-2026-0018 `bom_resolved`, run `827db20622`.

### F-EE-09: MPN case differences split the BOM and the purchase requests
Severity: minor
Area: workflow
Steps: same design. R2 has MPN `rc0603fr-0710kl` (lower case), otherwise
identical to R1.
Expected: one line R1 R2 R6 (and R5 if it were in the BOM) for the one part.
Actual: separate BOM line `R2,1,10k,...,rc0603fr-0710kl,Yageo,` and a separate
request PR-00041 "1 x rc0603fr-0710kl for DSN-2026-0018" next to PR-00040
"3 x RC0603FR-0710KL". Reconciliation matches case-insensitively but
grouping and shortage do not.
Evidence: DOC-0195, PR-00040, PR-00041.

### F-EE-10: A BOM line without an MPN becomes an inventory item with an empty MPN and a purchase request for "4k7"
Severity: major
Area: workflow
Steps: same design. R3 is `4k7` with no MPN and no Manufacturer.
Expected: the line is flagged for the engineer before release (the design
authority cannot buy "4k7"); no item is created until someone assigns a
part number.
Actual: ITEM-00005 created with fields `name 4k7, footprint ..., stock 0` and
no `mpn`/`manufacturer` at all; PR-00042 "1 x 4k7 for DSN-2026-0018" waits for
dave with `mpn` and `manufacturer` empty. The reconciler's warning ("1 BOM
line(s) match no inventory item") does not say which, and the design was
approvable anyway.
Evidence: ITEM-00005, PR-00042.

### F-EE-11: Items are created from stale proposals at release time, producing duplicate items for the same MPN; later designs then resolve to the wrong (zero-stock) duplicate
Severity: major
Area: records
Steps:
1. Run `f21db81ca8` (DSN-2026-0004, another panelist) was submitted at 14:43:58,
   when no items existed; its `bom_unresolved` proposed both parts.
2. ITEM-00001/ITEM-00002 were created at 14:44:02 by another release.
3. `f21db81ca8` was approved at 14:47:26: ITEM-00003 (CL10B104KB8NNNC, Samsung)
   and ITEM-00004 (RC0603FR-0710KL, Yageo) were created, duplicates of 00001/2.
4. My DSN-2026-0018 and DSN-2026-0019 resolved their Yageo lines to ITEM-00004
   (stock 0) while the stocked ITEM-00002 had 139; the Panasonic line (F-EE-08)
   got ITEM-00002.
Expected: at release the shortage-requester re-matches proposals against the
items that exist now; the reconciler never has two candidates for one
MPN+manufacturer.
Actual: four items for two parts; `hubproto records --type item` shows
`ITEM-00001 100n, ITEM-00002 10k, ITEM-00003 100n, ITEM-00004 10k`; which one
a design hits depends on query order.
Evidence: ITEM-00003, ITEM-00004 (`created ... by bob; run=f21db81ca8`), DSN-2026-0019 `bom_resolved`.

### F-EE-12: An ERC error kills the whole run instead of returning the design to the engineer; the requirement it spawned is orphaned and nobody is told
Severity: major
Area: workflow
Steps:
1. Submit `feedback/ee-files/ee_attack.kicad_sch` (real KiCad 8/9 structure with
   `lib_symbols`; U1 unit 2 left unconnected) plus `ee_attack.kicad_pcb`:
   run `0ef1e94202`, DSN-2026-0016.
2. `/runs?as=alice`, `/inbox?as=alice`, `hubproto record DSN-2026-0016`,
   `hubproto records --type requirement`.
3. Try to fix it: `curl -F as=alice -F record=rec:design:DSN-2026-0016 -F file=@ee_clean.kicad_sch http://127.0.0.1:8080/attach`.
Expected: the frame says "Rejections return to Submit the design"; an ERC
failure should land the record in `awaiting-submit-the-design` with the ERC
report in front of alice, and REQ-2026-0019 should follow the design.
Actual: Track: "ERC reports 7 error(s); the limit is 0.0" → `run.failed`,
record `failed`, assignees none. No inbox message to alice (only the Runs page
shows `failed`). REQ-2026-0019 stays `awaiting-accept-the-requirement` for
bob, pointing at a dead design. Attaching the fixed schematic just files
DOC-0179 as an `external` document titled "fixed schematic"; nothing re-runs.
A new submission gets a new DSN number, so the history splits. The ERC report
DOC-0164 and even the DRC/Gerbers/STEP (DOC-0167..0169) were produced and
attached, so the data was there to review.
Evidence: run `0ef1e94202`, DSN-2026-0016, REQ-2026-0019, DOC-0164, DOC-0179.

### F-EE-13: DRC "unconnected items" are not counted: an unrouted board passes as design_checks_clean
Severity: major
Area: workflow
Steps:
1. Submit `feedback/ee-files/ee_unrouted.kicad_pcb` (R1, R2, C1 on a 17 x 13 mm
   outline; VDIV routed, GND between R2.2 and C1.2 deliberately unrouted, no
   clearance violation) with `ee_bom_edge.kicad_sch`: DSN-2026-0018.
2. Download DOC-0199 (DRC report): `violations` = 3 x lib_footprint_mismatch
   (warning) + 1 x track_dangling (warning); `unconnected_items` = 1 x
   `unconnected_items` severity **error** ("Missing connection between items").
3. `hubproto record DSN-2026-0018`: `checks {"drc": {"errors": 0, "ran": true, "warnings": 4}}`,
   `conditions.design_checks_clean true`.
Expected: `errors 1`, `design_checks_clean false`, "design checks not clean"
on the risk. KiCad's own DRC exit code and GUI treat unconnected items as
errors; a board with missing connections must not be released.
Actual: released by bob as PN-00009 with clean checks; Gerbers DOC-0200
(22 files incl. drill and job) and STEP DOC-0201 were exported for an
incomplete board.
Evidence: DOC-0199, DSN-2026-0018.

### F-EE-14: Accepting requirements does not update the design: it is reviewed and released with "requirements not all accepted" and risk high
Severity: major
Area: workflow
Steps:
1. DSN-2026-0008 submitted with two user needs (REQ-2026-0014/0015); design
   `requirements_accepted False`, `risk high`, trace DOC-0068 says both
   `awaiting-accept-the-requirement`.
2. bob accepts both (runs `6d9b7f93bd`, `e4d8d7a4c8`); `hubproto record DSN-2026-0008`.
3. bob approves the design.
Expected: the design's `requirements_accepted`, risk and trace are re-evaluated
when its requirements are accepted (or at least when bob opens the review),
so the review is of a design with accepted requirements and risk low/medium.
Actual: after step 2 the design still shows `requirements_accepted False`,
`risk high`, `risk_reasons ["requirements not all accepted"]`, and the trace
CSV DOC-0068 still says awaiting. Released that way (PN-00003). Only the
snapshot's "## Requirements" section (DOC-0085) shows the live state, next to
stale fields. The test suite works around this by submitting a second design.
Evidence: DSN-2026-0008, DOC-0068, DOC-0085.

### F-EE-15: Verification method is neither required nor offered when accepting a requirement
Severity: major
Area: records
Steps:
1. `/inbox?as=bob`: the "Accept the requirement" card for REQ-2026-0015 offers
   only `acceptance_criteria` ("accepted needs: acceptance_criteria") plus the
   meaning field.
2. Approve with acceptance criteria only (my REQ-2026-0015); approve with
   `-F verification_method=test` added by hand in curl (REQ-2026-0014).
Expected: an accepted requirement names how it will be verified (test,
inspection, analysis, demonstration) and by what evidence; the form should
offer the enum and `accepted` should require it. The scenario text itself
says bob supplies "acceptance criteria and a verification method".
Actual: REQ-2026-0015 is `accepted` with no `verification_method` (trace and
snapshot print "verification: n/a"). REQ-2026-0014 has `test` only because
curl can send fields the form does not show. `verified`, `verified_by` and
`verification_evidence` exist on the type but no process or form ever sets
them, so a requirement can never be closed as verified.
Evidence: REQ-2026-0014, REQ-2026-0015, DOC-0085.

### F-EE-16: No way to create or maintain an inventory item (supplier, unit cost, approved manufacturer); the cog path for the "known item" is unreachable from a design release
Severity: major
Area: shell
Steps:
1. `/?as=alice`: processes alice can start are calibration, change-control,
   complaint, design-release, document-control, goods-receipt,
   nonconformance, purchase-request, purchase-to-pay, requirements-capture,
   task. None creates or edits an `item`, although the frame grants "Design
   engineers may create items in Inventory/Items".
2. `hubproto --help`: no command creates a record either.
3. Therefore every item comes from the shortage-requester with `stock 0` and no
   `supplier`, `unit_cost`, `lead_time_days` (ITEM-00001..00007), and every
   design-driven request is "no supplier named" → dave (PR-00013, PR-00014,
   PR-00039..44). Dave's naming a supplier on the request does not write it
   back to the item, so the next design asks again.
Expected: an item record a design engineer or buyer can create and edit
(MPN, manufacturer, approved flag, preferred supplier, unit cost, stock), so
the purchasing-assistant can order within criteria as the scenario expects.
Actual: not reachable from the shell or CLI. Could not set up: an item whose
manufacturer is not approved (`approved_manufacturer false`) — there is no
form, process or CLI that sets that field, so the reconciler's
"approved-manufacturer" warning could not be exercised at all.
Evidence: home page process list; ITEM-00001..00007 fields.

### F-EE-17: Reviewer sees the BOM, trace, checks and exports as raw JSON; the BOM CSV carries no item numbers and the ERC/DRC are only counts
Severity: major
Area: shell
Steps:
1. `/inbox?as=bob`, card "Review the design · DSN-2026-0018": `bom_lines`,
   `bom_resolved`, `bom_unresolved`, `exports`, `requirement_trace` are printed
   as one-line JSON blobs; `checks` is `{"drc": {"errors": 0, ...}}`.
2. `/record/rec:design:DSN-2026-0018?as=bob` Details tab: same.
3. DOC-0195 BOM CSV: the `item` column is empty on every line (the CSV is
   written before reconciliation).
4. The ERC/DRC JSON reports are attached but nothing lists the violations, and
   the four DRC warnings (lib_footprint_mismatch, track_dangling) are
   invisible unless you download the JSON.
Expected: a BOM table (refs, qty, value, footprint, MPN, manufacturer, item,
stock, short, status), a requirements table, a list of exports with download
links, and the ERC/DRC violations with severity. This is what a design
authority signs.
Actual: a design review in this shell is a JSON reading exercise; bob approved
a 3 x LM358 / mounting-hole BOM without any table telling him.
Evidence: inbox and record pages for DSN-2026-0018, DOC-0195.

### F-EE-18: The example schematic has no symbol definitions, so "ERC clean" and the netlist are vacuous
Severity: minor
Area: docs
Steps:
1. DSN-2026-0008 used `examples/sensor.kicad_sch`: ERC DOC-0070 `violations: []`;
   netlist DOC-0072 has 0 `(comp` and 0 `(net` entries (753 bytes of header).
2. Same for my minimal-format `ee_bom_edge.kicad_sch` and `ee_qty2.kicad_sch`
   (DOC-0196, DOC-0214): 0 violations, no components.
Expected: the example used to prove "ERC report filed" should be a schematic
KiCad can actually check; or the exporter should flag "0 components in
netlist" as checks not run rather than clean.
Actual: any schematic without `lib_symbols` (or with symbols KiCad cannot
resolve) passes ERC with `design_checks_clean true`.
Evidence: DOC-0070, DOC-0072, DOC-0196, DOC-0198.

### F-EE-19: Alternate part numbers are dropped from the BOM (missing)
Severity: minor
Area: workflow
Steps: R6 in `ee_bom_edge.kicad_sch` carries `MPN2 ERJ-3EKF1002V` /
`Manufacturer2 Panasonic` (the usual way to carry an alternate in KiCad
fields).
Expected: the BOM line lists the alternate so purchasing can buy it when the
primary is short; reconciliation accepts an item matching either.
Actual: only MPN/Manufacturer are read; the alternate is nowhere in DOC-0195
or the record. Missing: an alternates model (approved alternates per item or
per BOM line).
Evidence: DOC-0195 line `R1 R5 R6,...`.

### F-EE-20: The Start form cannot attach files; attachments must be hand-encoded as base64 JSON
Severity: major
Area: shell
Steps:
1. `/?as=alice`, card "design-release": inputs are title, description, amount,
   currency, date, po_ref, supplier, change_type, severity, training_roles and
   a JSON textarea. No file input, no `kind` select, no `user_needs` input.
2. The only way to run the documented scenario ("two example files attached")
   is to base64-encode each file and paste `{"attachments":[{"name":...,"content_b64":...}]}`
   plus `"kind":"electrical"` and `"user_needs":[...]` into the textarea. The
   key names are not documented anywhere in the shell.
3. Attaching after start is too late: the exporter runs synchronously at submit.
Expected: file inputs (multiple) on the Start form, a `kind` select, a
user-needs list; or a two-step start (file, attach, submit).
Actual: a design engineer cannot start the design process from the UI with
their files. The CLI `hubproto run --record-file` has the same limitation
(JSON with base64).
Evidence: home page form for design-release.

### F-EE-21: Approval forms ask the approver to type fields the system fills afterwards (`part` for a design, `po` for a request), and hide the ones they must supply
Severity: minor
Area: shell
Steps: `/inbox?as=bob` "Review the design": "released needs: part" with a text
box. `/inbox?as=dave` "Approve the purchase": "ordered needs: po" (required
text box), while `supplier` and `unit_cost`, which dave is expected to supply
per the scenario, are not offered.
Expected: do not ask for fields a later system step creates; do ask for
supplier and unit cost on a request that has none.
Actual: as described; I could only supply supplier/unit cost with curl.
Evidence: inbox pages for bob and dave.

### F-EE-22: A requirement linked to a design after submission does not enter the design's trace; there is no UI to link requirements at all
Severity: minor
Area: workflow
Steps:
1. As alice: start `requirements-capture` with
   `{"text":"[EE] The board shall withstand 2 kV HBM ESD on all external pins.","kind":"safety","design":"rec:design:DSN-2026-0019","verification_method":"test","acceptance_criteria":"..."}`
   → REQ-2026-0027.
2. `hubproto record DSN-2026-0019`.
Expected: the design lists REQ-2026-0027 in `requirements`/trace (or at least
a `fulfils` link appears) and the review shows it.
Actual: DSN-2026-0019 still traces only REQ-2026-0023; the `design` field on
the requirement is a one-way text ref. The Start form has no field for
`requirements` on a design; only the JSON key works.
Evidence: REQ-2026-0027, DSN-2026-0019.

### F-EE-23: Requirement records have no title and a risk reason that makes no sense for a requirement
Severity: minor
Area: records
Steps: `/boards?as=alice&type=requirement`, `hubproto records --type requirement`,
`hubproto record REQ-2026-0014`.
Expected: the requirement text (or a short id + text) as the title in lists;
a risk tier derived from kind/priority (safety, regulatory = high).
Actual: boards and lists show "REQ-2026-0008 REQ-2026-0008" with no text.
Every requirement is `risk medium` with `risk_reasons ["source or severity 'customer'"]`
and `conditions {"no_open_ncr": false, ...}`: the scorer treats the
requirement's `source` as an NCR severity.
Evidence: REQ-2026-0014, REQ-2026-0015, requirement board.

### F-EE-24: The design-rule threshold is written in percent but applied as a count
Severity: minor
Area: policy
Steps: frame line "The design rule error limit is 0 percent."; run
`0ef1e94202` Track: "ERC reports 7 error(s); the limit is 0.0".
Expected: a count ("The design rule error limit is 0 errors.") or a percent
of something defined.
Actual: the number is compared with the error count; "percent" is decorative.
Evidence: frames/engineering/design-control-sop.frame.md, run `0ef1e94202`.

### F-EE-25: Controlled CAD sources attached to a released design stay in state `draft`
Severity: minor
Area: DMS
Steps: `hubproto record DSN-2026-0018` (released): DOC-0192 `controlled rev
A.1 state=draft`, DOC-0193 `controlled rev A.0 state=draft`.
Expected: on release the sources become the controlled, effective revision of
the part's design package (or are linked into document-control).
Actual: they remain draft, editable (F-EE-03), with no effective date or
revision tied to PN-00009 rev A.
Evidence: DOC-0192, DOC-0193.

### F-EE-26: Missing: engineering change orders on parts and designs
Severity: major
Area: architecture
Steps: `frames/iso9001/change-control-sop.frame.md`: `change-control` is a QMS
change request (title, description, impact) approved by the quality manager;
no field or link to a design or part, no revision bump, no re-export, no
re-review. Resubmitting a design creates a new DSN; PartReleaser bumps the
part revision only if the new submission names the old `part` in JSON (undocumented in the shell).
Expected: an ECO record that references the part/design, carries the change
class (the design type has `change_class` but nothing reads it), triggers a
new design revision run, re-validates BOM/ERC/DRC, and supersedes the old
revision with `supersedes` links and where-used impact.
Actual: missing. Together with F-EE-03 there is no controlled way to change a
released electrical design.

### F-EE-27: Missing: approved manufacturer list
Severity: major
Area: architecture
Steps: `config/record-types.json` item: `approved_manufacturer: bool` default
`true`; the reconciler says it "flags manufacturers not on the approved
list"; `hubproto records` has no AML record type; the supplier list is a
seeded aggregate (`supplier:acme-metals`).
Expected: an AML (manufacturer, MPN, approval status, evidence) consulted per
BOM line, independent of the purchasing supplier (distributor) list.
Actual: missing. Every system-created item is implicitly approved; the flag
cannot be set from any UI (see F-EE-16), so "manufacturer not approved" can
never fire in practice.

### F-EE-28: Missing: part libraries (symbol/footprint library governance)
Severity: minor
Area: architecture
Steps: every real ERC report (DOC-0164) carries 15 warnings "The current
configuration does not include the symbol library 'Device'/'power'": the
worker checks the file in isolation with no project or library table.
Expected: a controlled company library (symbols, footprints, 3D models) with
MPN/manufacturer fields, from which BOM lines inherit item numbers; the
worker runs with that library so library mismatches are real findings.
Actual: missing. Part identity lives only in free-text symbol fields
(MPN/Manufacturer), which is where F-EE-08/09/10 come from.

### F-EE-29: Missing: where-used
Severity: minor
Area: architecture
Steps: `hubproto record ITEM-00002` links show `concerned-by PO-0001, ...`
only; PN-00003 shows `realized-by ITEM-00001/00002`. `/boards` has no
"linked to" filter; the item page has no "used in parts" view; a design or
part page does not list the items it consumes with quantities.
Expected: from an item: which parts/designs/revisions use it and in what
quantity (needed for F-EE-26 impact and for obsolescence).
Actual: missing; the `realized-by` link exists but carries no quantity and is
only visible from the part side.

### F-EE-30: Designer gets no notification when a run fails
Severity: minor
Area: shell
Steps: after run `0ef1e94202` failed, `/inbox?as=alice` has no message about
it; `/runs?as=alice` shows `failed` in a row.
Expected: an inbox item ("Export the design failed: ERC reports 7 errors",
link to the ERC report).
Evidence: run `0ef1e94202`.

### F-EE-31: Risk of the design is not recomputed at review and a high-risk design with unaccepted requirements can be released
Severity: question
Area: policy
Steps: DSN-2026-0008 was approvable while `risk high` and
`requirements_accepted False` (F-EE-14); DSN-2026-0018 while
`bom_resolved false` with three unresolved lines.
Question: is it intended that the design authority may release a design whose
Guard conditions are false? If so the conditions are advisory only and the
shell should say so; if not, the Gate should refuse or require a documented
override.

### F-EE-32: Stock quantities are floats for discrete units
Severity: minor
Area: records
Steps: `hubproto record ITEM-00002` → `stock 139.0`, unit `ea`; receipts of
`2` produce `3.0`.
Expected: integer quantities for `ea`.
Evidence: ITEM-00002.

## What I could not set up, and why

- An item whose manufacturer is not approved: no form, process or CLI sets
  `approved_manufacturer` (F-EE-16, F-EE-27).
- A "known item from an approved supplier" for the design flow: items cannot be
  created or edited by a person; only a hand-made purchase request that names
  the supplier and unit cost reaches the cog (PR-00019).
- A fully ERC-clean real schematic in the worker: my hand-written `power:+3V3`
  / `power:GND` library symbols are not recognised as drivers by kicad-cli
  (2 x `power_pin_not_driven`), so the ERC-clean BOM run used the minimal
  format (F-EE-18). The deliberate-error run (`ee_attack.kicad_sch`) is a
  real KiCad structure and the 7 errors there are genuine.

## Files used

`feedback/ee-files/ee_bom_edge.kicad_sch` (BOM edge cases; the one bob released as DSN-2026-0018):

```
(kicad_sch (version 20231120) (generator "eeschema")
  (symbol (lib_id "Device:R") (at 10 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R1" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 20 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R2" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "rc0603fr-0710kl" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 30 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R3" (at 0 0 0))
    (property "Value" "4k7" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 40 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R4" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Panasonic" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 50 10 0) (unit 1) (in_bom no) (on_board yes) (dnp no)
    (property "Reference" "R5" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0))
  )
  (symbol (lib_id "Device:R") (at 60 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R6" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0))
    (property "MPN2" "ERJ-3EKF1002V" (at 0 0 0))
    (property "Manufacturer2" "Panasonic" (at 0 0 0))
  )
  (symbol (lib_id "Device:C") (at 70 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "C1" (at 0 0 0))
    (property "Value" "100n" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0))
    (property "MPN" "CL10B104KB8NNNC" (at 0 0 0))
    (property "Manufacturer" "Samsung" (at 0 0 0))
  )
  (symbol (lib_id "Device:C") (at 80 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp yes)
    (property "Reference" "C2" (at 0 0 0))
    (property "Value" "100n" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0))
    (property "MPN" "CL10B104KB8NNNC" (at 0 0 0))
    (property "Manufacturer" "Samsung" (at 0 0 0))
  )
  (symbol (lib_id "EE:DUALOPAMP") (at 90 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "U1" (at 0 0 0))
    (property "Value" "LM358" (at 0 0 0))
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0))
    (property "MPN" "LM358DR" (at 0 0 0))
    (property "Manufacturer" "Texas Instruments" (at 0 0 0))
  )
  (symbol (lib_id "EE:DUALOPAMP") (at 100 10 0) (unit 2) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "U1" (at 0 0 0))
    (property "Value" "LM358" (at 0 0 0))
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0))
    (property "MPN" "LM358DR" (at 0 0 0))
    (property "Manufacturer" "Texas Instruments" (at 0 0 0))
  )
  (symbol (lib_id "EE:DUALOPAMP") (at 110 10 0) (unit 3) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "U1" (at 0 0 0))
    (property "Value" "LM358" (at 0 0 0))
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0))
    (property "MPN" "LM358DR" (at 0 0 0))
    (property "Manufacturer" "Texas Instruments" (at 0 0 0))
  )
  (symbol (lib_id "power:GND") (at 120 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "#PWR01" (at 0 0 0))
    (property "Value" "GND" (at 0 0 0))
  )
  (symbol (lib_id "power:+3V3") (at 130 10 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "#PWR02" (at 0 0 0))
    (property "Value" "+3V3" (at 0 0 0))
  )
  (symbol (lib_id "Mechanical:MountingHole") (at 140 10 0) (unit 1) (in_bom no) (on_board yes) (dnp no)
    (property "Reference" "H1" (at 0 0 0))
    (property "Value" "MountingHole" (at 0 0 0))
    (property "Footprint" "MountingHole:MountingHole_2.2mm_M2" (at 0 0 0))
  )
)
```

The BOM the hub produced from it (DOC-0195); the correct BOM has 5 lines
(C1 x1, R1 R2 R6 x3, R4 x1 flagged, R3 x1 flagged, U1 x1):

```
references,quantity,value,footprint,mpn,manufacturer,item
C1 C2,2,100n,Capacitor_SMD:C_0603_1608Metric,CL10B104KB8NNNC,Samsung,
R1 R5 R6,3,10k,Resistor_SMD:R_0603_1608Metric,RC0603FR-0710KL,Yageo,
R4,1,10k,Resistor_SMD:R_0603_1608Metric,RC0603FR-0710KL,Panasonic,
R2,1,10k,Resistor_SMD:R_0603_1608Metric,rc0603fr-0710kl,Yageo,
R3,1,4k7,Resistor_SMD:R_0603_1608Metric,,,
U1 U1 U1,3,LM358,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,LM358DR,Texas Instruments,
H1,1,MountingHole,MountingHole:MountingHole_2.2mm_M2,,,
```

`feedback/ee-files/ee_attack.kicad_sch` (25 KB, full KiCad 8/9 structure with
`lib_symbols`, generated by `gen_sch.py`): divider +3V3 → R1 → VDIV → R2 →
GND with C1, C2 (DNP), R3..R6 across VDIV/GND and a dual op-amp U1; unit 2
deliberately unconnected. kicad-cli 9.0.2: 7 errors (3 pin_not_connected,
2 pin_not_driven, 2 power_pin_not_driven), 15 warnings.

`feedback/ee-files/ee_attack.kicad_pcb` (8.9 KB, `gen_pcb.py`): R1, R2, C1 0603
on a 17 x 13 mm outline, VDIV routed, a +3V3 trace 0.10 mm from VDIV
(clearance 0.20) and GND unrouted. kicad-cli: 3 clearance errors, 4
warnings, 1 unconnected item. `ee_unrouted.kicad_pcb` is the same without
the clearance violation (0 errors, 4 warnings, 1 unconnected item).

`feedback/ee-files/ee_qty2.kicad_sch` (268 resistors, `gen_simple.py` with
`qty2_parts.json`).
