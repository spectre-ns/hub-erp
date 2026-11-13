---
type: frame [0.2]
name: audit-sop
description: "Internal audit: an auditor plans an audit against the processes in the policy release, records the findings, and the quality manager accepts them."
visibility: internal
version: "0.2.0"
scope: department
maintainer: bob
inherits: org-model
clauses: ["9.2"]
compile: convention
---
# Internal audit SOP

Internal audits (9.2) check that the QMS conforms to this organization's own
Frames and to ISO 9001:2015, and that it is effectively implemented. The
audit programme is set at management review. An internal auditor plans each
audit; the checklist drafter cog builds a checklist from the steps of the
processes in scope as they stand in the policy release, so the audit asks
about the process as written. The auditor records the findings within 30
days; the quality manager accepts them and states the summary. Findings that
need action are raised as nonconformances or CAPAs and linked to the audit.
Auditors do not audit processes they own.

The auditor cog holds the internal auditor's seat and nothing more: it reads
every folder, as an internal auditor does, and writes only the audits it runs
and the findings on them. Each month it takes the next process in the
programme, the one audited longest ago (never one it owns). It runs the exact
checks first (items overdue against their due dates, approvals by whoever
prepared the item, decisions by someone the item was not routed to, training
missing when a document took effect, the Track's chain, and clauses of the
standards no Frame covers), then reads a sample of the process's records
against this organization's SOPs. Each finding states the clause, what was
expected, what was found, the evidence (records and Track entries, which must
exist) and its grade: major, minor, observation or opportunity. It drafts the
audit report as a document on the audit; a person approves it. The cog signs
nothing: the quality manager accepts or rejects each finding, and only an
accepted finding that needs action is raised as a nonconformance or a CAPA.

## Permissions
Internal auditors may create audits in Quality/Audits.
Internal auditors may approve audits in Quality/Audits.
Quality managers may approve audits in Quality/Audits.
Internal auditors may read Engineering.
Internal auditors may read Finance.
Internal auditors may read Inventory.
Internal auditors may read Policy.
Internal auditors may read Purchasing.
Internal auditors may read Quality.
Internal auditors may read Tasks.
Internal auditors may create documents in Quality/Audits.

## Routing
At Record the findings in internal-audit go to the auditor.
At Accept the findings in internal-audit go to the quality manager.
Record the findings in internal-audit is due within 30 days.
Overdue items in internal-audit go to the management representative.

## Duties
Every month the auditor cog audits the next process in the programme.

## Process: internal-audit
Records are filed in Quality/Audits.
The record type is audit.
1. [internal-auditor] Plan the audit (audit-checklist-drafter)
2. [system] File the audit (record-filer)
3. [internal-auditor] Record the findings
4. [system] Notify the auditor (notifier)
5. [quality-manager] Accept the findings
6. [system] Raise the actions (audit-action-raiser)
7. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Internal auditor | Plans and runs internal audits and records the findings; never audits their own work. |
| Quality manager | Accepts or rejects each finding of each audit; an accepted finding that needs action is raised as a nonconformance or a CAPA. |
| the auditor cog | Audits the next process in the programme every month as an internal auditor, and drafts the report; signs nothing. |
| Management representative | Receives the audits that are overdue. |
