---
type: frame [0.2]
name: wi-run-an-audit
description: "Work instruction for internal auditors: plan an audit, use the generated checklist, record findings."
visibility: internal
version: "0.2.0"
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
   `nonconformance`) and the clauses covered: never a process you own (one of
   your roles decides a step in it, or a routing sentence sends one to you).
   You are the auditor unless you name another.
2. Submit. The checklist drafter builds one question per step of each process
   in scope from the current policy release. The record waits for you at
   `awaiting-record-the-findings`, due in 30 days.
3. Perform the audit with the checklist on the record page: for each step,
   ask for the record that shows the step was done by the role the SOP
   names, and look at the run's Track.
4. Decide **approve** with the `findings` field set: one entry per finding,
   numbered F-1, F-2 ..., with the clause, what was expected, what was found,
   the evidence (record numbers and Track entries) and the grade (major,
   minor, observation, opportunity), and ncr or capa where action is needed.
   A finding with no number, or whose evidence does not exist, is refused.
5. The quality manager receives "Accept the findings": on the audit's page
   they accept or reject each finding (a rejection says why), set the
   `summary` and sign once. Each accepted finding that needs action is raised
   as a nonconformance or a CAPA and linked to the audit; the record closes
   and is rendered to the audit report.
6. The auditor cog does the same each month for the next process in the
   programme, and drafts the report on the audit; a person sends the report
   through document control.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Internal auditor | Runs audits as this instruction says. |
| Quality manager | Accepts audit findings as this instruction says. |
