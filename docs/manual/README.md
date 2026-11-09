# Hub ERP user manual

This manual describes what the Hub ERP prototype (`hubproto`) does today: every page, every process and who may do
each step. It is written for the people who use the hub (staff, approvers, quality managers, document controllers,
policy owners and administrators) and for the product owner, who will use it to review the workflows.

It describes behaviour, not plans. Where the code and a procedure (SOP) disagree, the manual says so and lists the
point in chapter 10. Where the manual could not tell what happens, it says that too.

## Contents

| Chapter | What it covers |
|---|---|
| [1. Getting started](01-getting-started.md) | Choosing who you are ("Acting as"), the top bar, app launcher and rail, light and dark, the home dashboard, the inbox, and the "Why?" page |
| [2. Records and views](02-records-and-views.md) | Record types and numbers; list, kanban and form views; the status bar; chatter, comments and @mentions; assigning; withdrawing a draft; Ops buttons and hotkeys; holds, links, snapshots; history and the audit trail |
| [3. Business processes](03-business-processes.md) | How every process runs: processes as graphs of statuses and transitions, gates (person, cog, committee, automatic), committee tallies, what a retry does to an approval, and how items in flight follow the release in force from their next move; then each process step by step: purchasing, goods receipt into stock, part requests, supplier bills, journal reversals, supplier evaluation, nonconformances and the stock they hold, CAPA, complaints, audits, calibration, management review, training, tasks, design control and requirements, change requests |
| [4. Inventory](04-inventory.md) | Who may do what with stock; parts, manufacturer parts and supplier parts; stock locations; lots and the stock ledger; the inventory pages; free movements (transfers, counts, splits, merges, marks, quarantine); stock adjustments, releases from quarantine, dispositions and part requests; receiving into stock; the stores cog; hubs that had inventory items |
| [5. Documents](05-documents.md) | The library, its folders and adding folders, document classes, creating and uploading, versions, the online editor (Collabora), check-out, comments and cog replies, comparing versions, controlled-document approval (also from the document's page), revisions and obsolete versions, review dates, the documents that carry Frames, templates |
| [6. Cogs](06-cogs.md) | The AI team members: how a cog is made from the SOPs, identity profiles and personas, roles and groups, derived autonomy, queues and cog boards, how to ask one, the messenger and its context, approvals within rails, committees, adversarial reviews, countersignatures, agent runs, what a cog refuses, profiles and models, training as context and the knowledge base, the prompt, senior and junior cogs, what is recorded of a cog's work |
| [7. Policy and configuration](07-policy-and-configuration.md) | What the hub reads from the SOPs, releases, compiling by the senior cogs' committee, widenings, no configuration change outside a change order (and the first-start conversion), groups and committees, Settings as direct changes, the change-order workflow (stages, review rounds and the panel, questions, findings and suggestions, the delta and item decisions, freezing, approval and segregation of duties, release, divergences, related changes, state maps, the per-cog view, validation), the automations, forms, the workflow editor, record types, Roles and responsibilities and the training matrix, the IT SOP |
| [8. Administration](08-administration.md) | Docker setup and first start, settings (environment variables), models and the hosted-models switch, backup, restore and verify, queue health, the command line, development hubs (`hubproto dev`) and running the tests |
| [9. Glossary](09-glossary.md) | Frame, cog, Gate, release, change order, delta, direct change, divergence, review round, panel, state map, group, committee, chair, senior and junior cog, persona, Roles and responsibilities table, validation class, widening, part, lot and other terms |
| [10. Workflow review notes](10-workflow-review-notes.md) | Observations for the product owner: what is still open in change control v2 (10.9), and unclear, inconsistent or surprising behaviour per area, with which earlier notes are resolved |

## What this manual is based on

* Branch `change-control-v2` of the repository at `/home/dahubley/source/hub-erp-proto`, at commit `2c99c4d` (9 November
  2026): change control v2, built from the design agreed that day (docs/design/change-control.md;
  docs/design/jira-workflow-study.md for the workflow editor). The edition before this one described commit `6af5a46`
  (8 November 2026) and carried the design as a Preview chapter; that chapter is now folded into chapters 3, 6 and 7.
* The SOPs and work instructions in `frames/` (the policy Frames), `config/record-types.json`, the web shell in
  `hubproto/shell/`, the engine (`hubproto/runner.py`, `hubproto/changecontrol.py`, `hubproto/cogs.py` and others),
  DEV-NOTES.md and the tests in `tests/` (1,029 pass at `2c99c4d`).
* Behaviours checked by running the code on development hubs (`hubproto dev serve`, chapter 8.10), never the live hub,
  with the repository's Frames, the demo data, the cogs' stand-in in place of models, and hosted models off. Those are
  marked "checked by running the code" or "ran the code" where they matter; "read the code" marks what follows from the
  code cited but was not run. The change-order runs of chapter 7.9 were made at `bf3602d`, the commit before the last
  four of the branch, and the approval rule they changed was run again at `2c99c4d`.
* Screenshots: those of change control v2 were taken on 9 November 2026 from a development hub at `bf3602d` with the
  demo data and the runs of chapter 7.9; the older ones on 8 November 2026 from the live hub at `6af5a46`, by a
  headless browser that only opened pages, and they show that hub's review data. Where a page has changed since, the
  caption says so.

## What changed in this edition

Change control v2 is built, and this edition describes it as behaviour, not as a preview:

* **Change orders** carry every configuration change, prose revisions and direct changes alike, through review rounds
  with a panel of cogs and people, item-by-item decisions with accepted warnings, segregation of duties, and a release
  that records divergences and sends related changes back (chapter 7.9). No configuration changes any other way, and a
  new hub's SOPs become documents at its first release (chapter 7.5).
* **Processes are graphs** of statuses and transitions, with person, cog, committee and automatic gates; items follow
  the release in force from their next move (chapter 3.1); the **workflow editor** changes them on a change order
  (chapter 7.13).
* **Cogs** are identity profiles with shared personas; what they follow, may do and are trained on comes from the SOPs'
  Roles and responsibilities tables; senior cogs compile, junior cogs do the day-to-day work (chapter 6).
* **Groups and committees**, the **required-training matrix**, record types under change control and the **IT SOP**
  (chapter 7.6, 7.14 to 7.16); development hubs and the hosted-models switch (chapter 8).
* What is still open is in chapter 10.9.

## The example company

The prototype ships with the Frames of a fictional company, Example Instruments Ltd. Its members are:

| Member | Kind | Roles (from all Frames together) |
|---|---|---|
| alice | person | author, design engineer, finance clerk, process owner, staff |
| bob | person | controller, design authority, design review committee, internal auditor, management representative, policy owner, reviewer, staff, system administrator |
| carol | person | design review committee, document controller, policy owner, quality manager, reviewer, staff |
| dave | person | buyer, payables reviewer, staff, storekeeper |
| ap-reviewer | cog | finance clerk, payables reviewer |
| doc-reviewer | cog | author, reviewer |
| quality-assistant | cog | design review committee, staff |
| purchasing-assistant | cog | buyer, staff, stock reviewer |
| qa-manager | cog | author, reviewer, staff |
| organization | cog | policy reader |
| stores | cog | staff, storekeeper |
| it-admin | cog | system administrator |
| management | cog | management reviewer |
| senior-organization | cog (senior) | policy reader |
| senior-qa-manager | cog (senior) | policy reader |

The cogs are in the groups **junior-cogs** (all but the seniors, the purchasing-assistant and stores) and
**senior-cogs**; the compile-frame committee is defined on senior-cogs, chaired by carol (chapters 6 and 7.6).

(`frames/company/org-model.frame.md`; `buyer` for dave and the purchasing-assistant come from
`frames/purchasing/purchasing-policy.frame.md`; `storekeeper` for dave, `stock-reviewer` for the purchasing-assistant
and the stores cog's membership come from `frames/inventory/inventory-policy.frame.md`; `design-engineer` for alice and
`design-authority` and `management-representative` for bob are restated in
`frames/engineering/design-control-sop.frame.md`.)

Examples in this manual use these names. In a real installation the roles stay the same and the names change.

## Conventions

* **Bold** words are buttons, links or field labels as the hub shows them.
* A citation in brackets names where a rule comes from: an SOP or policy Frame such as (document-control-sop), or a
  file such as (hubproto/runner.py).
* "Approver" means whoever a step waits for, whether the step is an approval, a verification, an acknowledgement or
  completing a task. The hub calls each of these a **Gate**.
* Numbered procedures start with who may do them.
