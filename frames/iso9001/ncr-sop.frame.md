---
type: frame [0.2]
name: ncr-sop
description: "Control of nonconforming outputs: any member of staff opens a nonconformance, it is classified and risk-scored, the quality manager dispositions it within 5 days, and a corrective action is opened when the risk or the disposition demands one."
visibility: internal
version: "0.3.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["8.7", "10.2"]
compile: convention
---
# Nonconformance SOP

A nonconforming output is identified, contained, classified and dispositioned
(rework, scrap, use-as-is or return) so that it is not delivered or used
unintentionally (8.7). Any member of staff opens the record. The classifier
cog proposes the source, the severity and containment wording; the risk
scorer sets the risk tier. A low-risk nonconformance may be dispositioned by
the quality-assistant cog within its criteria; everything else waits for the
quality manager, and for the management representative when the quality
manager reported it (the reporter may not approve their own record). When the
disposition is scrap or return, or the risk is medium or high, a CAPA is
opened automatically and linked to the record (10.2). The record is rendered
to an evidence document when it closes. The quality-assistant cog keeps an
eye on dispositions that are waiting too long.

## Permissions
Staff may create nonconformances in Quality/Nonconformances.
Quality managers may approve nonconformances in Quality/Nonconformances.
Management representatives may approve nonconformances in Quality/Nonconformances.
The quality-assistant cog may approve nonconformances in Quality/Nonconformances.

## Routing
At Disposition the nonconformance in nonconformance with low risk go to the quality-assistant cog.
At Disposition the nonconformance in nonconformance go to the quality manager.
At Disposition the nonconformance in nonconformance go to the management representative.
Disposition the nonconformance in nonconformance is due within 5 days.
Overdue items in nonconformance go to the management representative.

## Approval criteria
The quality-assistant cog may approve nonconformance nonconformances when the risk is low.

## Segregation of duties
The reporter and the approver of a nonconformance must be different people.

## Duties
Every day the quality-assistant cog finds nonconformances that have waited more than three days for a disposition and reminds whoever they wait for, in the item's chatter.

## Process: nonconformance
Records are filed in Quality/Nonconformances.
The record type is ncr.
1. [staff] Open the nonconformance (ncr-classifier)
2. [system] File the nonconformance (record-filer)
3. [system] Quarantine the stock (stock-quarantiner)
4. [system] Score the risk (risk-scorer)
5. [quality-manager] Disposition the nonconformance
6. [system] Apply the disposition (disposition-applier)
7. [system] Open corrective action (capa-opener)
8. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Opens a nonconformance for anything that does not conform, and contains it. |
| Quality manager | Dispositions nonconformances. |
| Management representative | Dispositions the nonconformances the quality manager reported, and receives the overdue ones. |
| the quality-assistant cog | Dispositions low-risk nonconformances within its criteria and reminds those a disposition waits for. |
