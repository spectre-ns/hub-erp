---
type: frame [0.2]
name: capa-sop
description: "Corrective and preventive action: root cause, actions, verification by the owner, an effectiveness check after a fixed interval, and closure by the quality manager."
visibility: internal
version: "0.1.1"
scope: department
maintainer: carol
inherits: org-model
clauses: ["10.2"]
compile: convention
---
# CAPA SOP

A corrective action is opened by the quality manager, or automatically from
a nonconformance whose disposition or risk requires one; in that case the
owner of the CAPA is the person who dispositioned the nonconformance and the
run is a child of the nonconformance run. The drafter cog proposes a
root-cause method, an action list and a due date. The owner verifies that
the actions are complete and states the root cause. The effectiveness check
is scheduled after the interval below; the quality manager verifies
effectiveness and records the result, which closes the record (10.2). The
record is rendered to an evidence document at closure.

## Permissions
Quality managers may create capas in Quality/CAPA.
Quality managers may approve capas in Quality/CAPA.
Management representatives may approve capas in Quality/CAPA.
Process owners may approve capas in Quality/CAPA.

## Thresholds
The effectiveness check interval is 90 days.

## Routing
At Verify the actions in capa go to the owner.
At Verify effectiveness in capa go to the quality manager.
Verify the actions in capa is due within 30 days.
Overdue items in capa go to the management representative.

## Process: capa
Records are filed in Quality/CAPA.
The record type is capa.
1. [quality-manager] Open the capa (capa-drafter)
2. [system] File the capa (record-filer)
3. [process-owner] Verify the actions
4. [system] Schedule the effectiveness check (effectiveness-scheduler)
5. [quality-manager] Verify effectiveness
6. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Quality manager | Opens corrective actions and verifies their effectiveness. |
| Process owner | Carries out and verifies the actions in their process. |
| Management representative | Approves corrective actions where the quality manager may not, and receives the overdue ones. |
| the quality-assistant cog | Reviews corrective actions for containment and traceability when asked. |
| the qa-manager cog | Reviews corrective-action plans against the procedures when asked. |
