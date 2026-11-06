---
type: frame [0.2]
name: wi-run-an-audit
description: "Work instruction for internal auditors: plan an audit, use the generated checklist, record findings."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["9.2"]
compile: none
---
# WI: run an internal audit

For: internal auditors and the quality manager.

1. Open the **Quality/Audits** board and choose *New audit* (process
   `internal-audit`). Give the title, the planned date, the processes in
   scope (their names as in the policy release, for example
   `document-control`) and the clauses covered. You are the auditor unless
   you name another.
2. Submit. The checklist drafter builds one question per step of each process
   in scope from the current policy release. The record waits for you at
   `awaiting-record-the-findings`, due in 30 days.
3. Perform the audit with the checklist on the record page: for each step,
   ask for the record that shows the step was done by the role the SOP
   names, and look at the run's Track.
4. Decide **approve** with the `findings` field set: one entry per finding
   with the clause, the statement, the evidence and the severity (observation,
   minor, major). An audit cannot move on without findings (an empty list is
   a valid finding set when nothing was found).
5. The quality manager receives "Accept the findings", sets the `summary`
   and approves; the record closes and is rendered to the audit report. Raise
   a nonconformance for each major finding and link it to the audit.
