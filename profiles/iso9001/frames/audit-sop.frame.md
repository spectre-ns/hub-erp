---
type: frame [0.2]
name: audit-sop
description: "Internal audit: an auditor plans an audit against the processes in the policy release, records the findings, and the quality manager accepts them."
visibility: internal
version: "0.1.0"
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

## Permissions
Internal auditors may create audits in Quality/Audits.
Internal auditors may approve audits in Quality/Audits.
Quality managers may approve audits in Quality/Audits.

## Routing
At Record the findings in internal-audit go to the auditor.
At Accept the findings in internal-audit go to the quality manager.
Record the findings in internal-audit is due within 30 days.
Overdue items in internal-audit go to the management representative.

## Process: internal-audit
Records are filed in Quality/Audits.
The record type is audit.
1. [internal-auditor] Plan the audit (audit-checklist-drafter)
2. [system] File the audit (record-filer)
3. [internal-auditor] Record the findings
4. [system] Notify the auditor (notifier)
5. [quality-manager] Accept the findings
6. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Internal auditor | Plans and runs internal audits and records the findings; never audits their own work. |
| Quality manager | Accepts the findings of each audit. |
| Management representative | Receives the audits that are overdue. |
