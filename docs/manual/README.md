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
| [3. Business processes](03-business-processes.md) | How every process runs (including what a retry does to an approval), then each one step by step: purchasing, goods receipt into stock, part requests, supplier bills, journal reversals, supplier evaluation, nonconformances and the stock they hold, CAPA, complaints, audits, calibration, management review, training, tasks, design control and requirements, change requests |
| [4. Inventory](04-inventory.md) | Who may do what with stock; parts, manufacturer parts and supplier parts; stock locations; lots and the stock ledger; the inventory pages; free movements (transfers, counts, splits, merges, marks, quarantine); stock adjustments, releases from quarantine, dispositions and part requests; receiving into stock; the stores cog; hubs that had inventory items |
| [5. Documents](05-documents.md) | The library, its folders and adding folders, document classes, creating and uploading, versions, the online editor (Collabora), check-out, comments and cog replies, comparing versions, controlled-document approval (also from the document's page), revisions and obsolete versions, review dates, the documents that carry Frames, templates |
| [6. Cogs](06-cogs.md) | The AI team members: what each one does and works from (its persona), local models, autonomy, queues and cog boards, how to ask one, the messenger chat, approvals within rails, adversarial reviews, countersignatures, agent runs and confirming actions, what a cog refuses, changing a cog's model |
| [7. Policy and configuration](07-policy-and-configuration.md) | Frames, scopes and personas, policy owners, releases and what they record, compiling with the compile-frame committee, proposals, widenings and stale proposals, the Settings app, SOPs as documents, change orders, the compile-frame and release automations, form layouts |
| [8. Administration](08-administration.md) | Docker setup and first start, settings (environment variables), local models (Ollama) and a cog's model, backup, restore and verify, queue health, the command line |
| [9. Glossary](09-glossary.md) | Frame, cog, Op, Gate, Track, release, proposal, rail, lens, change order, scope, persona, committee, part, supplier part, lot, stock location, stale proposal and other terms |
| [10. Workflow review notes](10-workflow-review-notes.md) | Observations for the product owner: unclear, inconsistent or surprising behaviour, per area, with which earlier notes are resolved |

## What this manual is based on

* The repository at `/home/dahubley/source/hub-erp-proto` at commit `6af5a46`, its HEAD on 8 November 2026. The manual
  was first written from commit `26962fa` (7 November 2026) and has been brought up to date with the 21 commits since:
  inventory after InvenTree, the stores and organization cogs, scopes and personas, the compile-frame committee, local
  models only, releases that record the compiled texts, SOPs filed as quality documents, library folders, form
  layouts, and the fixes made after the first review.
* The SOPs and work instructions in `frames/` (the policy Frames), `config/record-types.json`, the web shell in
  `hubproto/shell/`, the engine (`hubproto/runner.py`, `hubproto/cogs.py`, `hubproto/inventory.py` and others) and the
  tests in `tests/` (731 pass at `6af5a46`).
* Pages of the running hub at `http://localhost:8080`, read only, to check wording. That hub runs `6af5a46`; its
  release in force is policy version 9 (digest `2dcbfc780b93`, released 8 November 2026), which carries everything this
  manual describes, and its SOPs have been converted to documents (chapter 7.8). It also holds test data from earlier
  review sessions.
* Behaviours checked by running the code against a throwaway copy of the hub (never the live one), with the
  repository's Frames and the tests' stand-in in place of the models. Those are marked "checked by running the code"
  where they matter; "read the code" marks what follows from the code cited but was not run.

## The example company

The prototype ships with the Frames of a fictional company, Example Instruments Ltd. Its members are:

| Member | Kind | Roles (from all Frames together) |
|---|---|---|
| alice | person | finance clerk, author, process owner, staff, design engineer |
| bob | person | controller, reviewer, policy owner, management representative, internal auditor, staff, design review committee, design authority |
| carol | person | quality manager, reviewer, policy owner, document controller, staff, design review committee |
| dave | person | payables reviewer, staff, buyer, storekeeper |
| ap-reviewer | cog | payables reviewer, finance clerk |
| doc-reviewer | cog | reviewer, author |
| quality-assistant | cog | quality manager, author, reviewer, staff, design review committee |
| purchasing-assistant | cog | buyer, staff, stock reviewer |
| qa-manager | cog | quality manager, reviewer, author, staff |
| organization | cog | staff |
| stores | cog | storekeeper, staff |

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
