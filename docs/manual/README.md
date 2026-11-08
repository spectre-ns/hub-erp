# Hub ERP user manual

This manual describes what the Hub ERP prototype (`hubproto`) does today: every page, every process and who may do
each step. It is written for the people who use the hub (staff, approvers, quality managers, document controllers,
policy owners and administrators) and for the product owner, who will use it to review the workflows.

It describes behaviour, not plans. Where the code and a procedure (SOP) disagree, the manual says so and lists the
point in chapter 9. Where the manual could not tell what happens, it says that too.

## Contents

| Chapter | What it covers |
|---|---|
| [1. Getting started](01-getting-started.md) | Choosing who you are ("Acting as"), the top bar, app launcher and rail, light and dark, the home dashboard, the inbox, and the "Why?" page |
| [2. Records and views](02-records-and-views.md) | Record types and numbers; list, kanban and form views; the status bar; chatter, comments and @mentions; assigning; Ops buttons and hotkeys; holds, links, snapshots; history and the audit trail |
| [3. Business processes](03-business-processes.md) | How every process runs, then each one step by step: purchasing, goods receipt, items, supplier bills, journal reversals, supplier evaluation, nonconformances, CAPA, complaints, audits, calibration, management review, training, tasks, design control and requirements, change requests |
| [4. Documents](04-documents.md) | The library and folders, document classes, creating and uploading, versions, the online editor (Collabora), check-out, comments and cog replies, comparing versions, controlled-document approval, revisions and obsolete versions, review dates, templates |
| [5. Cogs](05-cogs.md) | The AI team members: what each one does, autonomy, queues, how to ask one, the messenger chat, approvals within rails, adversarial reviews, countersignatures, agent runs and confirming actions, what a cog refuses |
| [6. Policy and configuration](06-policy-and-configuration.md) | Policy owners, Frames, compiling, proposals and releases, widenings, the Settings app, SOPs as documents, change orders, the compile-frame and release automations |
| [7. Administration](07-administration.md) | Docker setup, models (Ollama) and a cog's model, backup, restore and verify, queue health, the command line |
| [8. Glossary](08-glossary.md) | Frame, cog, Op, Gate, Track, release, proposal, rail, lens, change order, staged Frame and other terms |
| [9. Workflow review notes](09-workflow-review-notes.md) | Observations for the product owner: unclear, inconsistent or surprising behaviour, per process |

## What this manual is based on

* The repository at `/home/dahubley/source/hub-erp-proto` as committed on 7 November 2026, up to commit `26962fa`
  (the change-order and automation work landed in `0e4e85b` and `ef9de69`). Chapter 6, sections 6.8 to 6.12, was
  written last, after re-reading the change-order and automation code at that commit. The working tree then also held
  uncommitted work (form layouts carried on change orders, withdrawing drafts, and smaller changes); chapter 6.12
  lists it, and the rest of the manual does not describe it.
* The SOPs and work instructions in `frames/` (the policy Frames), `config/record-types.json`, the web shell in
  `hubproto/shell/`, the engine (`hubproto/runner.py`, `hubproto/cogs.py` and others) and the tests in `tests/`.
* Pages of the running hub at `http://localhost:8080`, read only, to check wording.
* A few behaviours were checked by running the code against a throwaway copy of the hub (never the live one). Those
  are marked "checked by running the code" where they matter.

**The running hub may lag behind this manual.** The hub at `http://localhost:8080` is a Docker image built from an
earlier commit, and its policy release in force was "policy version 6" of 6 November 2026 when this manual was
written. That release has no `qa-manager` cog, no change-order process and no route for policy documents, so those
parts of chapters 3 to 6 only appear on the live hub after the image is rebuilt and the current Frames are compiled
and released (chapter 6 and chapter 7 say how). The live hub also holds test data from earlier review sessions.

## The example company

The prototype ships with the Frames of a fictional company, Example Instruments Ltd. Its members are:

| Member | Kind | Roles (from all Frames together) |
|---|---|---|
| alice | person | finance clerk, author, process owner, staff, design engineer |
| bob | person | controller, reviewer, policy owner, management representative, internal auditor, staff, design review committee, design authority |
| carol | person | quality manager, reviewer, policy owner, document controller, staff, design review committee |
| dave | person | payables reviewer, staff, buyer |
| ap-reviewer | cog | payables reviewer, finance clerk |
| doc-reviewer | cog | reviewer, author |
| quality-assistant | cog | quality manager, staff, design review committee |
| purchasing-assistant | cog | buyer, staff |
| qa-manager | cog | quality manager, reviewer, author, staff |

(`frames/company/org-model.frame.md`; `buyer` for dave and the purchasing-assistant come from
`frames/purchasing/purchasing-policy.frame.md`; `design-engineer` for alice and `design-authority` and
`management-representative` for bob are restated in `frames/engineering/design-control-sop.frame.md`.)

Examples in this manual use these names. In a real installation the roles stay the same and the names change.

## Conventions

* **Bold** words are buttons, links or field labels as the hub shows them.
* A citation in brackets names where a rule comes from: an SOP or policy Frame such as (document-control-sop), or a
  file such as (hubproto/runner.py).
* "Approver" means whoever a step waits for, whether the step is an approval, a verification, an acknowledgement or
  completing a task. The hub calls each of these a **Gate**.
* Numbered procedures start with who may do them.
