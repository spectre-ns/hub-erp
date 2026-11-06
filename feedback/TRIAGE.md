# Panel triage

Twelve panelists, about 330 findings (see the per-persona files). This file
records the maintainer's disposition of every blocker and major, grouped by
theme, and the handling rule for the minors. Statuses:

- **fixed** — in the core fix pass (commit "Panel fix pass"), covered by
  `tests/test_panel_fixes.py` unless noted
- **shell** — in the shell/CLI fix pass (`hubproto/shell.py`, `cli_records.py`)
- **planned** — a plan item for the production build; the prototype keeps the seam
- **policy** — a change to the seed Frames rather than code
- **no** — out of scope for the prototype, with the reason

## Blockers

| Finding | Theme | Status | Disposition |
|---|---|---|---|
| F-inventory-6, F-EE-01, F-qa-4, F-code-reviewer-1 | buyer's unit cost discarded; PO issued at 0.00; cog approves any quantity at amount 0 | fixed | decision fields overlay step payloads and the record wins over stale payloads; `po-issuer` recomputes the amount; criteria require a positive amount; the risk scorer scores by record type with release thresholds |
| F-inventory-8, F-EE-02, F-acct-8, F-qa-5 | buyer names an unapproved supplier at the Gate | fixed | `po-issuer` re-checks approval and refuses; the run fails back to the owner |
| F-DE-2, F-code-reviewer-2, F-ME-23, F-EE-12 | "Rejections return to Submit the design" is a dead end | fixed | rejection to a cog step puts the record in `returned`, assigned to the owner, who resubmits with fields and attachments (`runner.resubmit`); captured requirements are reused |
| F-DE-1, F-ME-3 | requirement text invisible to the signer | fixed + shell | the field is `statement` (the `text` name is reserved for extracted document text); the shell shows it on cards, records and snapshots |
| F-ops-1, F-code-reviewer-4, F-arch-20 | shell and CLI hand out the same number; cross-process staleness | fixed | the record cache is keyed on SQLite's `data_version`; numbers are counted in the database inside an immediate transaction and verified unique |
| F-acct-1, F-mgmt-1 | duplicate bills against a PO post freely | fixed | three-way match carries billed-to-date; over-billing is an error before any Gate |
| F-acct-2 | negative bill posts a negative journal | fixed | matcher refuses non-positive amounts; the kernel refuses negative journal lines |
| F-acct-3 | no reversal path | fixed | `journal-reversal` process (clerk requests, controller approves, mirror posting linked `supersedes`; a journal reverses once) |
| F-qa-1 | a failed run stays decidable | fixed | failed, rejected, completed and cancelled runs clear their pending decision; `decide` requires status waiting |
| F-arch-1 | kernel does not enforce grants or reaches | fixed | `ScopedKernel`: every Cog sees the kernel through its declared reaches; writes beyond the run's own record (and its attached documents) need the run's grant; reach declarations corrected |
| F-code-reviewer-3 | a Frame edit appoints its own policy owners | fixed | reviewers come from the release in force; only a bootstrap takes them from the proposal |
| F-qm-1, F-EE-03, F-ME-12 | effective controlled documents editable by check-in | fixed | effective, obsolete and in-review controlled documents refuse new versions and check-out; revision is a new document through document-control |
| F-qm-2 | "overdue items go to the quality manager" made her an approver of a personal training record | fixed + policy | new sentence form "Overdue items in X **are reported to** Y" only informs; the training SOP uses it |
| F-qm-3, F-it-1, F-arch-16 | identity is a query parameter; signature semantics | planned + partly fixed | Keycloak step-up is the plan's production design (9.1.2); the prototype's signature now binds signer, verdict, meaning, payload digest, record digest, run, step and release; `meaning_given` records whether the signer typed a meaning; the shell binds to localhost only (shell pass) |
| F-qm-4, F-qm-5 | list fields from the decision form comma-split into JSON fragments | shell | coercion by field spec (JSON for lists and objects) |
| F-ME-1 | mechanical BOM never reconciled | fixed | FreeCAD parts with a part number become BOM lines, items and purchase requests |
| F-inventory-1, F-EE-16 | nobody can create an item | fixed + policy | `item-request` process (staff requests, buyer approves with supplier and unit cost) |

## Majors, by theme

**Workflow controls (fixed):** holds block decisions (F-ops-3, F-acct-12); a failed run returns to its owner with a message and `retry`/`cancel` (F-it-4, F-inventory-20, F-acct-4/5, F-ops-2); required fields of the next state are pre-checked at every Gate, excluding system-set fields, so a signature can no longer strand a record (F-qa-3, F-ops-2, F-code-reviewer-7, F-mgmt-3); decisions may set only the type's decision fields plus what the next state requires (F-qa-8); a cog may not approve when the next state needs a human-supplied field; child runs need a live parent and an owner related to it (F-code-reviewer-13, F-arch-2); escalation records `gate.overdue` even when nobody new is added (F-mgmt-5); bills have an SLA and an overdue route (F-mgmt-4, F-acct-13); consecutive checkpoints are carried by a no-op step; duplicate step titles are a compile error (F-arch-7); `required_by_state` keys that no process reaches are warned at compile (F-arch-27); snapshots render after the closing transition so they state the final state and all signatures (F-DE-10, F-qm majors); decisions are projected once (F-DE-14, F-ME-15, F-ops minor) and the shell reads only that list (shell).

**Requirements and design (fixed):** a design cannot be approved while any linked requirement is unaccepted or it traces to nothing; acceptance refreshes the waiting design's trace, conditions and risk (F-DE-3/4, F-ME-5/6, F-mgmt-2, F-EE-14); acceptance requires acceptance criteria and a verification method (F-DE-6, F-EE-15); DNP, excluded and multi-unit parts handled, manufacturer must match (F-EE-05/06/07/08/10); stock allocated across lines (F-EE-04); documentation-only designs may be reviewed (no-CAD is a warning).

**Receipts (fixed):** outstanding quantity per line, over-receipt and negative quantities refused, a PO is received only when complete, receipts against a received PO refused, segregation of duties on receipts (F-inventory-15..19, F-code-reviewer-8).

**NCR scoring (fixed):** the risk scorer dispatches on record type, so an NCR with a quantity is an NCR (F-ops-4, F-qm-8); low severity scores low; process-raised NCRs are at least medium; the classifier no longer bumps on "lot" and writes a containment that names the item (F-ops-5 partly); the cog's status reply is scoped to the asker (F-ops-7, F-qm minor); filing from email needs a description (F-ops-6).

**Start and decision forms (fixed, after the shell pass):** `runner.submit` pre-checks the fields the first Gate's state requires (minus system fields, defaults and what a cog before the Gate `provides`), so a design without a kind is refused at Start, not after the cogs ran (F-qa-2); the Start form marks those fields required; a signer is offered only the fields still missing plus the type's decision fields, never a field already on the record, so a bill approver cannot rewrite the amount at signature.

**Operability (shell pass):** request and decision logging, upload size limit, healthz outside the lock, Content-Length validation, localhost binding, run and inbox pages trimmed per user, Start forms with file inputs and typed fields, readable review cards, retry/cancel/resubmit from the inbox, Why for another principal (F-it-2 partly, F-it-5, F-it-7, F-code-reviewer-5, F-code-reviewer-10, F-qa-7, F-ME-2/17, F-EE-17/20, F-mgmt-19, F-inventory-5/7).

**Planned (production design, kept as seams):** synchronous cog execution under one lock and non-durable runs (F-it-2, F-arch-17/18: the durability backend and remote workers in the plan, section 5); OpenFGA, Keycloak, Postgres and object storage (F-arch section 3); record-type configuration pinned in the release (F-arch-3); projection performance (F-arch-19, F-code-reviewer-12: Postgres projections); delegation and deputies (F-mgmt-6); management dashboard, cycle times and overdue views (F-mgmt-7, F-ops minors; the Dashboard type in the plan); policy change from the shell (F-mgmt-9: the compile Gate UI, plan 4.7); cog controls beyond criteria: sampling, limits, kill switch (F-mgmt-10: plan 7.5); ECR/ECO, where-used, effectivity, baselines, change-class routing (F-ME-9/10, F-DE-9, F-EE-26..29: plan 9.6, Phase 3); inventory lots, locations, counts, valuation and ledger posting of stock (F-inventory-21..25, F-acct-24: plan 9.2, Phase 3/4); chart of accounts, sub-ledgers, tax, multi-currency, period management, payment (F-acct-9/10/11/20..23: plan 9.3, Phase 4); design verification records and review minutes (F-DE-5, F-DE-24); drawing production for FreeCAD, part documents (F-ME-7/8/14); CAD worker authentication, sandboxing and limits (F-arch-35); backup and restore procedure and hot backup consistency (F-it-6: documented in the plan's operability section; SQLite backup API for the prototype is a small follow-up); threshold sentences driving the risk scorer (F-mgmt-8: done for the scorer; routing sentences still carry their own numbers by design).

**No:** authoring CAD in the platform; vendor-native CAD formats; a payroll engine; a second identity system for cogs.

## Minors

Minors are kept in the per-persona files. Those that touch the shell's
wording, forms and work-instruction mismatches (F-qm-36, F-ops WI items,
F-mgmt-18) are folded into the shell pass where cheap; the rest wait for the
next panel round.

## Policy changes made to the seed and example Frames

- training-sop: overdue acknowledgements are **reported to** the quality manager (no reassignment)
- finance-policy: bill approvals due within 5 days, overdue bills reported to the policy owner; `journal-reversal` process
- purchasing-policy: `item-request` process; segregation of duties on receipts; staff may create items
- design-control-sop: `requirements-capture` process; shortage ordering after release
