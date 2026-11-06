# Findings: General Manager / executive sponsor (acting as bob)

Persona: `bob` (controller, management representative, policy owner, design
authority). Question asked of the system: could I run the business on this,
and do I understand what I am signing?

Session: 2026-11-06 14:44-14:52 UTC, release `b7da4af94d3c571f`. Other
panelists were active on the same hub at the same time; record numbers below
are mine unless stated. Everything I created is prefixed `[MGMT]`.

What I did (steps are repeated in each finding):

1. As alice, started `design-release` "[MGMT] Sensor board" (electrical, two
   user needs, `sensor.kicad_sch` and `box.FCStd` attached): run `28bd271aef`,
   record DSN-2026-0007, requirements REQ-2026-0012 and REQ-2026-0013.
2. As bob, accepted both requirements from `/inbox`, then approved the design
   (run `28bd271aef`): part PN-00007, shortages PR-00035 and PR-00036.
3. As alice, started a 12,000 USD purchase request (PR-00015, run
   `28a4903caf`) and a 200 USD one (PR-00016, run `a248898306`); a 12,000 USD
   bill (BILL-0022, run `3c2d11d3a9`) and a 1,500 USD bill (BILL-0023, run
   `f1b3472267`), the last two against the seeded PO-1002 and PO-1001.
4. As bob, approved PR-00015 and BILL-0022 from `/inbox`.
5. Ran `hubproto tick --today 2099-01-01` in the container.
6. As bob, started and held `management-review` MR-2026-002 (run `dd3e392f28`).
7. Ran `hubproto compile` once (proposal `66adba1c6aeb`, 0 changes); I did not
   review or release it. No Frame was edited.

Note on my own inputs: for the two requirement acceptances and the small bill
my curl wrapper mangled values containing spaces, so REQ-2026-0012 carries
acceptance criteria `Reads\`, REQ-2026-0013 `Survives\`, BILL-0022/BILL-0023
description `\[MGMT\]\`. That is my error, not the hub's; F-mgmt-18 is the
only finding that leans on it, and it says so.

---

### F-mgmt-1: The ap-reviewer cog approved and posted a duplicate bill against a PO that was already fully billed
Severity: blocker
Area: workflow
Steps:
1. Confirm PO-1001 (seeded, 1,500 USD) already has a posted bill: `docker compose exec hub hubproto bills` shows BILL-0001 1500.0 USD posted (another panelist's, run `9e507b6763`).
2. As alice: `http://127.0.0.1:8080/?as=alice`, Start `purchase-to-pay` with supplier `acme-metals`, amount `1500`, currency `USD`, date `2026-11-05`, po_ref `PO-1001`.
3. Open the run (`/run/f1b3472267?as=bob`) and `hubproto ledger`.
Expected: the three-way match fails or warns ("PO-1001 already billed 1,500 of 1,500"), the bill routes to a person, nothing is posted.
Actual: the cog approved within 0.1 s under finance-policy line 27 with reasons `match_within_tolerance holds`, `supplier_approved holds`, `amount 1500.0 under 2000.0 USD`; ledger-poster posted JE-00009. PO-1001 now has four posted bills of 1,500 USD each (BILL-0001 JE-00001, BILL-0002 JE-00002, BILL-0018 JE-00005, BILL-0023 JE-00009): 6,000 USD of payables against a 1,500 USD order, all but one approved by the cog. The match only compares the bill amount with the PO amount; it does not look at what has already been billed or paid.
Evidence: `state/runs/f1b3472267/track.jsonl` seq 10-12 (`gate.cog_review`, `gate.decided`, `grant.issued`); `hubproto record BILL-0023` (`match: {"po_found": true, "received": true, ... "within_tolerance": true}`); `hubproto ledger` JE-00001/00002/00005/00009.
Suggested fix (optional): three-way-matcher adds `billed_to_date` from the PO aggregate and a condition `the order is not yet billed`; a duplicate is an error-severity Guard problem.

### F-mgmt-2: I signed a design release whose record and snapshot still say the requirements were not accepted
Severity: major
Area: workflow
Steps:
1. As alice start `design-release` with two inline user needs (CLI: `hubproto run design-release --as alice --record-file <json>`; see F-mgmt-19 for why not the shell). Run `28bd271aef`, DSN-2026-0007, REQ-2026-0012/0013.
2. As bob at `/inbox?as=bob`: the design card shows `risk high`, `risk_reasons ["requirements not all accepted"]`, Guard finding `requirements-accepted: 2 requirement(s) not yet accepted`.
3. As bob accept REQ-2026-0012 and REQ-2026-0013 from the same inbox (acceptance criteria, verification method `test`). Both records go to `accepted`.
4. Reload `/inbox?as=bob` and `/record/rec:design:DSN-2026-0007?as=bob`.
5. Approve the design; open the record and the snapshot DOC-0137.
Expected: after step 3 the design card re-evaluates (requirements accepted, risk re-scored) or at least shows that the condition is now met; what I sign matches reality.
Actual: the card, the record and the snapshot are frozen at submission time. After approval DSN-2026-0007 is `released` with `requirements_accepted False`, `risk high`, `conditions.requirements_accepted false`. DOC-0137 ("DSN-2026-0007 snapshot at awaiting-review-the-design") contradicts itself: line 25 `requirements_accepted: False`, line 23 trace states `awaiting-accept-the-requirement`, but lines 33-34 list both requirements as `state accepted`. A 7-year quality record now says the design authority released a high-risk design with unaccepted requirements.
Evidence: `hubproto record DSN-2026-0007`; `hubproto doc-get DOC-0137 --out /tmp/d.md` lines 15, 23, 25, 27, 33-34; run `28bd271aef` Track seq 5 (`requirements-accepted` warning).
Suggested fix (optional): re-run trace + risk-scorer when a linked requirement changes state (or on open of the pending item), with a new payload digest so the approver sees the item changed.

### F-mgmt-3: Inbox approval forms demand a value that the system itself creates after my signature; a browser cannot submit without typing something, and what I type is written to the record
Severity: major
Area: shell
Steps:
1. As bob open `/inbox?as=bob` with a purchase request, a bill and a design waiting.
2. Read the forms: the PR card shows `ordered needs:` and an input `po` with the HTML attribute `required`; the bill card `posted needs: journal` (required); the design card `released needs: part` (required); the management review `closed needs: outputs (JSON)`.
3. Submit the bill approval with `journal=TYPED-BY-BOB` (curl POST `/decide` with `as=bob run=3c2d11d3a9 verdict=approve meaning=... journal=TYPED-BY-BOB`).
4. Submit the PR and the design approvals with no `po`/`part` at all (curl, which ignores `required`).
Expected: the form asks only for what the approver must supply (the chair's outputs, acceptance criteria); PO, journal and part numbers are never typed by an approver; nothing typed into a system-owned field is persisted.
Actual: (a) in a browser the Approve button is blocked until the controller invents a PO number, a journal number or a part number. (b) The server does not need them: PR-00015 and DSN-2026-0007 were approved without them (po-issuer made PO-0017, part-releaser made PN-00007). (c) The typed value is persisted: BILL-0022 Activity event #1588 `record.fields_set by bob {"journal": "TYPED-BY-BOB"}` before ledger-poster overwrote it with JE-00017. The permanent history of a financial record contains a value the controller made up to get past a form.
Evidence: `/inbox?as=bob` HTML (`<input name="part" value="" size="22" required>`); `/record/rec:bill:BILL-0022?as=bob` Activity #1588; `hubproto record PR-00015` (po `rec:purchase-order:PO-0017`), `hubproto record DSN-2026-0007` (part `rec:part:PN-00007`).
Suggested fix (optional): in `page_inbox` only list `required_missing` fields when the next state is the immediate next (`runner._immediate_next`), and drop system-owned refs (`po`, `journal`, `part`, `minutes`) from the form.

### F-mgmt-4: A bill over 10,000 USD waiting for the controller has no due date, no SLA and no overdue route
Severity: major
Area: policy
Steps:
1. As alice start `purchase-to-pay`, amount `12000`, PO-1002 (BILL-0022, run `3c2d11d3a9`).
2. As bob open `/inbox?as=bob`: the card has no `due`; `/record/rec:bill:BILL-0022?as=bob` shows `due -`; `/runs` shows `bob` with no `(due ...)`.
3. `/releases?as=bob`: workflow `purchase-to-pay` checkpoint `Approve the bill [payables-reviewer] -> awaiting-approve-the-bill` has no SLA; the routing table has no `purchase-to-pay * overdue == True` row.
4. `/frame/finance-policy?as=bob` lines 20-24: no `... is due within N days.` and no `Overdue items in purchase-to-pay go to ...` sentence.
5. `hubproto tick --today 2099-01-01` escalates nothing for bills (BILL-0016, BILL-0028, BILL-0005 still waiting, not escalated).
Expected: the largest payables, the ones routed to an executive, carry the strictest SLA and an escalation path; at minimum the inbox tells me when it is due.
Actual: no due date at all for any bill approval (cog, payables reviewer or controller); a 12,000 USD bill can wait forever and nothing will ever flag it.
Evidence: `/releases` Workflows section; `frames/finance/finance-policy.frame.md` lines 20-24; tick output listed only purchase-request runs.
Suggested fix (optional): finance-policy gains `Approve the bill in purchase-to-pay is due within 5 days.` and `Overdue items in purchase-to-pay go to the quality manager.` (or whoever is not the controller; see F-mgmt-5).

### F-mgmt-5: When the executive is the one who is late, the overdue route escalates to the executive himself, so nobody is told
Severity: major
Area: policy
Steps:
1. Note the routes on `/releases?as=bob`: `purchase-request * overdue -> role:controller` (purchasing-policy line 49), `design-release * overdue -> role:management-representative` (design-control-sop line 47), `nonconformance`, `calibration`, `capa`, `change-control`, `complaint`, `internal-audit` all overdue -> management representative. bob holds controller and management-representative.
2. With PR-00004 and PR-00009 (assigned to bob, due 2026-11-09) and DSN-2026-0007 (bob, due 2026-11-16) waiting, run in the container `hubproto tick --today 2099-01-01`.
3. Read the tick output and `/inbox?as=bob`.
Expected: an item that is 72 years overdue with the controller is visible to someone else (a second executive, the quality manager, an audit trail entry), or at least flagged as overdue on the item.
Actual: tick escalated only items that sat with other people (PR-00027..32, dave's) and added bob to them. PR-00004 and PR-00009 were not escalated, carry no `escalated` chip, no Track entry, no message to anyone: `runner.tick` adds the overdue role's members only if they are not already assignees, and then stops. The same holds for every design review, NCR, CAPA, complaint and audit that lands with the management representative. The only person who would notice the executive is late is the executive.
Evidence: tick stdout (`escalated run 197dfa550e (PR-00027) to bob` ... five lines, none for PR-00004/PR-00009); `hubproto run-show 82007f4728` (`pending: Approve the purchase -> bob`, no escalation); `/inbox?as=bob` card for PR-00004 shows `due 2026-11-09` and no `escalated`.
Suggested fix (optional): when the overdue assignees are already on the item, escalate one level up (a `## Routing` sentence `Overdue items with the controller go to the quality manager.`), and always mark the item overdue and write `gate.overdue` to the Track.

### F-mgmt-6: No delegation, deputy or reassignment: an executive on leave blocks every item routed to them
Severity: major
Area: workflow
Steps:
1. As carol (quality manager, reviewer, policy owner) try to take bob's design review: POST `/decide` with `as=carol run=28bd271aef verdict=approve meaning="Covering for bob while on leave"`.
2. Look for a delegation feature: `hubproto --help` (no `delegate`, `assign`, `reassign`); the shell's actions are `/start /decide /msg /attach /upload /checkout /checkin /link /snapshot /hold`; `grep -ri "delegat\|deput\|reassign" hubproto/` finds only `delegating_rule` (the cog's sentence).
3. Check whether the policy grammar can express a deputy: `frames/reference/writing-policy-frames.frame.md` and DEV-NOTES routing vocabulary have no "when X is absent" condition.
Expected: "Delegate my approvals to carol from <date> to <date>", recorded on the Track of every decision taken under the delegation; or a policy sentence that names a deputy.
Actual: `Not done. carol is not an assignee of this decision (assigned: bob)` (HTTP 400). Missing. With bob holding controller, design authority, management representative, internal auditor and policy owner, his absence stops purchases over 10,000, all design releases, supplier approvals, management reviews and policy releases. The only workaround is the overdue route, which for most of these points back at bob (F-mgmt-5).
Evidence: shell response to step 1; `hubproto/cli.py` subcommand list; `hubproto/shell.py` `_route` POST table.

### F-mgmt-7: There is no management view: no dashboard, no overdue list, no "waiting on whom", no cycle time
Severity: major
Area: shell
Steps:
1. As bob open `/?as=bob`: the only numbers are `28 approvals waiting for me`, `5 of my runs waiting`, `7 runs I started`.
2. Open `/boards?type=purchase-request&as=bob`: one record type at a time, columns by state, cards show number, title, folder, owner, assignee, due. No amount, no risk, no "overdue" column or filter, no count of money waiting, no cross-type view.
3. Open `/runs?as=bob`: a single flat table of every run in the hub (about 110 at the time), newest first, no filter by status, process, assignee or due date, no age.
4. Open `/boards?type=bill&assignee=bob&as=bob`: works, but to see "everything waiting on bob" I have to repeat it for each of 22 record types.
5. Look for cycle time or throughput anywhere: none. `/conformance` is a clause checklist, not an operational report.
Expected: one page answering: what is open by type and state, what is overdue and by how long, who is sitting on what (per person and per cog), money awaiting approval, decisions taken by cogs this week, time from submission to decision per process.
Actual: Missing. To know whether the business is on schedule I would export `hubproto runs` and `hubproto records` and build it myself.
Evidence: `/`, `/boards`, `/runs` pages as bob; `hubproto/shell.py` RAIL (`Home, Inbox, Boards, Files, Runs, Releases, Frames, Why, Conformance, Admin`).
Suggested fix (optional): a `/dashboard` built from `runner.list()` and `kernel.query(overdue_at=today)`: counts by process and state, overdue table sorted by age, per-assignee queue, cog decision log, median cycle time per checkpoint.

### F-mgmt-8: The 10,000 USD threshold is written in four places and compiled from only one of them; changing the threshold sentence would change nothing
Severity: major
Area: policy
Steps:
1. Open `/frame/purchasing-policy?as=bob`: line 41 `The purchase approval threshold is 10,000 USD.` (section Thresholds), line 45 `Approvals in purchase-request over 10,000 USD go to the controller.` (Routing). Same pattern in finance-policy lines 19 and 22.
2. Open `/releases?as=bob`: the routing row `purchase-request approve-the-purchase amount > 10000.0 USD role:controller` cites purchasing-policy:45, not :41. The Thresholds table lists `purchase-approval-threshold 10000.0 USD` and `controller-approval-threshold 10000.0 USD` as separate, unused values.
3. Read `hubproto/cogs.py` lines 200 and 210: the risk-scorer hard-codes `amount >= 10000` for "high".
4. Compare the boundary: routing uses `over` (`> 10000`), risk uses `>= 10000`. On the board PR-00008 `[ACCT] PR-C-10000` (exactly 10,000 USD, another panelist's) is routed to dave (buyer) with `risk high`.
Expected: as policy owner I edit one sentence (`The purchase approval threshold is 12,000 USD.`), compile, two owners approve, release with an effective date, and routing, risk scoring and the inbox explanation all follow.
Actual: I would have to edit lines 41 and 45 of purchasing-policy and lines 19 and 22 of finance-policy consistently, and the risk tier would still flip at 10,000 because that number lives in Python. The "why" chain is honest about this (it cites line 45), which is exactly how I found out the threshold sentence is decorative.
Evidence: `/releases` Routing and Thresholds tables; `hubproto/cogs.py:200,210`; `hubproto/routing.py` (conditions carry their own literal amount).
Suggested fix (optional): let routing and criteria sentences reference a named threshold (`over the purchase approval threshold`) and have the risk-scorer read the release's thresholds; a Guard flags a literal amount that duplicates a named threshold.

### F-mgmt-9: A policy owner cannot change policy from the shell: no proposal page, no Gate, no release; the CLI proposal view shows nothing to approve
Severity: major
Area: shell
Steps:
1. As bob open `/releases?as=bob` and `/frames?as=bob` / `/frame/purchasing-policy?as=bob`: read-only. No "propose a change", no "proposals awaiting my approval", no release action. The shell's POST routes are `/start /decide /msg /attach /upload /checkout /checkin /link /snapshot /hold`.
2. The README path is: edit the Markdown file under `./frames` on the host filesystem, then in the container `hubproto compile`, `hubproto review <id> --as bob --approve-all`, `hubproto review <id> --as carol --approve-all`, `hubproto release <id> --as bob [--effective-from ...]`.
3. Run `hubproto compile` (no edits): `proposal 66adba1c6aeb ... rules: 197 changes: 0 findings: 5`. Run `hubproto proposal 66adba1c6aeb`: prints one line `proposal 66adba1c6aeb status=open release=None`.
Expected: the Gate I am asked to sign as policy owner is in my inbox like any other approval: each change shown as old sentence / new sentence with its frame and line, the Guard findings, the widening flag (needs two owners), the effective date, and the digest it will produce.
Actual: Missing in the shell. The CLI exists but a policy owner must have a shell on the host and edit files with a text editor; the `proposal` command gives no diff, and `review --approve-all` is the only documented way, which is the opposite of per-sentence signing. I did not release anything; proposal `66adba1c6aeb` is left open.
Evidence: `hubproto/shell.py` `_route`; CLI output above; README "Run it" section.
Suggested fix (optional): a `/proposals` page fed by `hub.compile()` results with per-change approve/reject and a release form with an effective date; an "edit this Frame" check-out in the DMS so the sentence is changed in the hub, not on the host.

### F-mgmt-10: Cog approvals have no controls beyond the criteria sentence: no sampling, no limits, no kill switch, no notice to the controller; the cog ordered a negative quantity
Severity: major
Area: workflow
Steps:
1. Review the two cog decisions on the Track: `/run/a248898306?as=bob` (PR-00016, 200 USD) and `/run/f1b3472267?as=bob` (BILL-0023, 1,500 USD). Each shows the delegating sentence, cog version 0.1.0, the reasons (`supplier_approved holds`, `item_known holds`, `amount 200.0 under 500.0 USD`), a signature and a payload digest. That part is good enough to audit after the fact.
2. Ask what else exists: `grep -rni "kill\|suspend\|disable\|pause\|sampl\|cumulative\|velocity\|rate limit" hubproto/ frames/ profiles/` returns nothing relevant. `/admin` lists cogs with version, kind, reaches and tasks, and no state (enabled/suspended) and no action.
3. Check who is told: `/inbox?as=alice` has DMs `Ordered 200 x ... (12000.0 USD)`, `Ordered -5 x RC0603FR-0710KL from acme-metals (-0.25 USD)`, `Ordered 10 x ... (0.2 USD)`; `/inbox?as=bob` has zero `Ordered` messages. The controller is never told what cogs approved.
4. Look at what the criteria let through: PR-00030 (`[QA] PR negative qty`, another panelist's) quantity `-5`, amount `-0.25 USD`, approved by the purchasing-assistant cog, PO-0018 issued; PR-00019 10 units at 0.02 USD, approved. "Under 500 USD" is true of any negative or zero amount.
Expected: before letting a cog sign for the company I would want: a cog on/off switch per cog that takes effect immediately (the Track says who flipped it); a random sample (say 1 in 20) of cog-approved items routed to a person; per-cog daily and per-supplier cumulative limits; a positive-amount and positive-quantity guard; a daily digest to the controller of what the cogs approved.
Actual: Only the criteria sentence, segregation of duties (a cog that filed cannot approve) and the grant requirement exist. Turning a cog off means editing the criteria sentence, compiling, two policy owners approving and releasing: hours, not seconds. I would not let it run unattended on payables today (see also F-mgmt-1).
Evidence: `state/runs/a248898306/track.jsonl` seq 8-10, `state/runs/f1b3472267/track.jsonl` seq 10-12; `hubproto record PR-00030`; `hubproto record PR-00019`; `/admin?as=bob` Cog catalog.

### F-mgmt-11: The management review's outputs are not turned into tasks or change requests, and the minutes are rendered from the pre-decision state
Severity: major
Area: workflow
Steps:
1. As bob on `/?as=bob` Start `management-review`: title `[MGMT] Management review 2026-11`, date `2026-11-06`, JSON `{"chair": "bob", "inputs": {... eleven 9.3.2 inputs ...}}`. Run `dd3e392f28`, MR-2026-002, waits at `awaiting-hold-the-review` for bob (chair).
2. On `/inbox?as=bob` the card asks `closed needs: outputs (JSON)`. Approve with `outputs={"improvement": "... (owner bob, due 2026-12-15)", "qms_changes": "... (owner bob, due 2026-11-30)", "resources": "... (owner carol, due 2027-01-31)"}`.
3. `hubproto record MR-2026-002`; `hubproto records --type task`; `hubproto doc-get DOC-0154 --out /tmp/mr.md`.
Expected (per management-review-sop and wi-record-management-review step 3-4): each output becomes a task or a change request with an owner and a date; `actions` lists them; the minutes document reflects the closed review.
Actual: `actions` is empty, no TASK or CR was created (tasks are still TASK-0001..0004), and the process has no step that could do it (`1. Prepare (record-filer) 2. Hold 3. Render`). The chair types outputs as a raw JSON object with no structure for owner/date, so nothing can be tracked to the next review. DOC-0154 is titled `MR-2026-002 snapshot at awaiting-hold-the-review` and says `State: **awaiting-hold-the-review**` although it is the closing minutes (it does include the outputs and my signature). Also: bob prepared and chaired alone; nothing requires top management other than the preparer to attend or sign.
Evidence: `hubproto record MR-2026-002` (state closed, `actions` absent, document DOC-0154); `/files?folder=Quality/Management Review&as=bob`; `frames/iso9001/management-review-sop.frame.md` Process section.
Suggested fix (optional): `outputs` as a list of `{kind, text, owner, due}`; a `[system] Open the actions (task-opener)` step; render after the terminal transition and title the document "minutes".

### F-mgmt-12: Purchase requests over 10,000 USD are marked risk `high` with an empty list of reasons
Severity: minor
Area: shell
Steps:
1. As alice start `purchase-request` for ITEM-00002, quantity 200, unit_cost 60, supplier acme-metals (PR-00015).
2. As bob read the card on `/inbox?as=bob`.
Expected: `risk high` with the reason (`amount at or above 10,000`), as bills get (BILL-0022 card: `risk_reasons ["amount at or above 10,000"]`).
Actual: `risk high risk_reasons []`. The approver sees a red tier with no explanation.
Evidence: `/inbox?as=bob` card for PR-00015; `hubproto/cogs.py:200` (tier from amount, no reason appended).

### F-mgmt-13: Every signature is listed twice in Decisions
Severity: minor
Area: records
Steps:
1. `hubproto record PR-00015` (or BILL-0022, DSN-2026-0007, MR-2026-002, BILL-0023); or open `/record/rec:purchase-request:PR-00015?as=bob` tab Decisions.
Expected: one row per decision.
Actual: two identical rows (same time, by, meaning, signature). The shell builds the table from `record.decision` events plus `rec["decisions"]`; the CLI does the same. An auditor counting signatures gets double.
Evidence: `hubproto record PR-00015` decisions section; `hubproto/shell.py` `page_record` Decisions.

### F-mgmt-14: The inbox card is a raw field dump, not a brief; the policy citation is not a link; cards are not ordered by due date
Severity: minor
Area: shell
Steps:
1. As bob open `/inbox?as=bob` with the design, PR and bill cards waiting.
Expected: a headline with what (amount, supplier, item, design title), why me (sentence, as a link to the line), risk and reasons, due date, Guard findings; details folded away; items sorted by due date with overdue and escalated on top.
Actual: Good parts: the routing sentence with frame and line, the due date where one exists, the Guard findings, risk and reasons (except F-mgmt-12) are on every card. Weak parts: the body is every payload field in alphabetical order (`attachments_filed`, `bom_lines`, `exports` with locators and sha256, `conditions`, `locator`, `folder`, `id`) with no summary line; the amount of a 12,000 USD bill is just one field in the list; `Routed to you by purchasing-policy line 45` is plain text here while `/releases` links the same source to `/frame/purchasing-policy#L45`; cards come in run-creation order, so after the tick the escalated items were scattered among 28 cards and listed again as 22 paragraphs under `Escalations`.
Evidence: `/inbox?as=bob` (cards for runs `28bd271aef`, `28a4903caf`, `3c2d11d3a9`; Escalations section after the 2099 tick).

### F-mgmt-15: A submission with unknown fields and no title is filed as a numbered record
Severity: minor
Area: records
Steps:
1. In the container `hubproto run design-release --as alice --record-file f.json` where f.json is `{"record": {"title": ..., "attachments": [...]}}` (the shape DEV-NOTES uses for `runner.submit`; the CLI wants the bare record).
Expected: the submission is rejected ("unknown field record; title required") and no number is consumed.
Actual: DSN-2026-0005 was created with a field named `record` holding the whole payload (including the base64 CAD file as a field value), no title, then failed at cad-exporter (`no-cad-files`). A yearly design number is used up by a typo and the failed record stays on the Engineering/Designs board.
Evidence: `hubproto record DSN-2026-0005`; run `0c12012ab8` Track seq 7-8.

### F-mgmt-16: Seed placeholders are in the released policy
Severity: minor
Area: policy
Steps:
1. As bob open `/?as=bob`, section "Frames behind this release".
2. Open `/frame/management-review-sop?as=bob`.
Expected: the compile Guard refuses or warns on `{{...}}` placeholders.
Actual: `quality-manual v0.1.0 (company, maintained by {{MANAGEMENT_REPRESENTATIVE}})`; management-review-sop body says `at planned intervals, at least {{REVIEW_FREQUENCY}} (9.3.1)`. The release digest `b7da4af94d3c571f` was signed by two policy owners with these in it.
Evidence: home page as bob; `frames/iso9001/management-review-sop.frame.md` first body paragraph; `frames/iso9001/quality-manual.frame.md` frontmatter.

### F-mgmt-17: The compile Guard claims segregation of duties is "enforced per person at run time" for management reviews, but there is no such rule and one person ran the whole review
Severity: minor
Area: policy
Steps:
1. `hubproto compile` (no edits). Finding: `[warning] consistency: role:management-representative may both create and approve review in folder:Quality/Management Review; segregation of duties is enforced per person at run time`.
2. `grep -n "different people" frames/iso9001/management-review-sop.frame.md`: nothing.
3. As bob prepare and hold MR-2026-002 (F-mgmt-11 steps 1-2).
Expected: the warning is accurate (there is a `## Segregation of duties` sentence) or it says plainly that none applies.
Actual: bob prepared, chaired and signed alone; no SoD sentence exists for reviews (nor for tasks, change requests, audits or CAPAs that the same warning covers). The warning reassures the policy owner about a control that is not there.
Evidence: compile output for proposal `66adba1c6aeb`; `hubproto record MR-2026-002` (created by bob, chair bob, decision by bob).

### F-mgmt-18: Signature meaning and acceptance criteria accept any token
Severity: minor
Area: workflow
Steps:
1. As bob accept REQ-2026-0012 with `acceptance_criteria=Reads\` and `meaning=Accepted\` (this is what my broken wrapper sent; a human can do the same by typing one character).
Expected: a minimum for the fields that make a record a quality record (an acceptance criterion of one word, or a signature meaning of one character, is rejected or warned on).
Actual: accepted; REQ-2026-0012 is `accepted` with acceptance criteria `Reads\`, and DSN-2026-0007 was released against it. Low severity on its own; listed because "every decision is evidence" is only as good as the weakest field.
Evidence: `hubproto record REQ-2026-0012`; DOC-0137 line 33.

### F-mgmt-19: The shell cannot start a design release with CAD files attached; scenario step 1 needs the CLI and base64
Severity: major
Area: shell
Steps:
1. As alice open `/?as=alice`, card `design-release`: inputs are title, description, amount, currency, date, po_ref, supplier, change_type, severity, training_roles, JSON. No file input.
2. Attaching on the record page after start is too late: `cad-exporter` runs at submission and fails the run with `no-cad-files` (run `0c12012ab8`, DSN-2026-0005).
3. Workaround used: build JSON with `content` (schematic text) and `content_b64` (FCStd) and run `hubproto run design-release --as alice --record-file` inside the container (the `examples/` folder is not mounted, so the file had to be piped in).
Expected: the Start form takes the files, as the scenario text and DEV-NOTES ("the shell's upload does the same") say.
Actual: Missing for the start form. The design engineer panelist will see the same; I record it because an executive panel scenario that starts with "use the shell" could not.
Evidence: `hubproto/shell.py` `page_home` (COMMON_INPUTS, no `enctype=multipart`), `post_start` (no `self.files`); run `0c12012ab8` Track seq 7.

### F-mgmt-20: Two "default" routes for the same NCR checkpoint; which one wins is not visible
Severity: question
Area: policy
Steps:
1. `/releases?as=bob` Routing: `nonconformance disposition-the-nonconformance default role:quality-manager (ncr-sop:34)` and `... default role:management-representative (ncr-sop:35)`.
2. Compare records: NCR-2026-0005 (reported by carol) waits for bob; NCR-2026-0001 (alice) waits for carol.
Expected: the prose intent ("the management representative when the quality manager reported it") is a condition on the sentence, or the table says the second default is the segregation-of-duties fallback.
Actual: both show as `default`; the right person is chosen at run time by SoD, but a policy owner reading the release table cannot tell. Question: is the order of sentences the rule, and is that documented anywhere?
Evidence: `/releases` Routing table; `frames/iso9001/ncr-sop.frame.md` lines 33-37.

---

## Summary

Counts: blocker 1 (F-mgmt-1); major 11 (F-mgmt-2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 19); minor 7 (F-mgmt-12, 13, 14, 15, 16, 17, 18); question 1 (F-mgmt-20).

Could I run the business on this? Not yet. The chain "sentence -> release -> routed item -> signature with the sentence on it" is real and I could follow every one of my four signatures back to a line of policy, which is more than most ERPs give me. But: a cog paid the same invoice four times (F-1); I signed a design record that still says its requirements were not accepted (F-2); a 12,000 USD bill has no due date and nothing above me is told when I am late (F-4, F-5); I cannot delegate (F-6); there is no page that tells me how the company is doing (F-7); and I could not change the 10,000 USD threshold without editing four lines in two files on a server and a number in Python (F-8, F-9).

Five most important: F-mgmt-1 (duplicate bill posted by the cog), F-mgmt-2 (stale design item signed), F-mgmt-5 with F-mgmt-4 (no escalation above the executive; no SLA on big bills), F-mgmt-3 (inbox forms demand system-generated values and persist what I type), F-mgmt-8 with F-mgmt-9 (threshold not single-sourced; no policy-change path in the shell).
