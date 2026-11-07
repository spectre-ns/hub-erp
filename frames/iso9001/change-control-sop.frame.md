---
type: frame [0.2]
name: change-control-sop
description: "Planning and control of changes: any member of staff raises a change request with its impact, the quality manager approves it, and the requester verifies the change was carried out as planned."
visibility: internal
version: "0.3.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["6.3", "8.5.6"]
compile: convention
---
# Change control SOP

A change request asks for a change and says why. A change order carries the
change: a draft revision of every SOP and policy it changes, approved one by
one through document control, and the change requests it implements. The hub
compiles the Frame when a revision is approved (the compile-frame automation),
so the rule changes show next to the text; the change order goes for approval
only when that compiled Frame is ready, and its approval releases it: the
release automation brings the Frame into force and the approved revisions into
effect together.

Changes to the QMS, to processes and to production or service provision are
planned (6.3) and controlled (8.5.6). Any member of staff raises a change
request stating the title, the description and the impact on documents and
processes; the risk scorer tiers it. The quality manager approves the change
within 10 days. After approval the requester is notified, carries out or
coordinates the change, and verifies it by recording what was done; that
verification closes the record. "Approve" for staff means signing the
verification checkpoint of their own request; the approval checkpoint is
routed to the quality manager only.

## Permissions
Staff may create change-requests in Quality/Changes.
Staff may create change-orders in Quality/Changes.
Quality managers may approve change-orders in Quality/Changes.
Staff may approve change-requests in Quality/Changes.
Quality managers may approve change-requests in Quality/Changes.

## Routing
Approvals in change-control go to the quality manager.
At Verify the change in change-control go to the requester.
Approve the change in change-control is due within 10 days.
Overdue items in change-control go to the management representative.
Approvals in change-order go to the quality manager.
Approve the change order in change-order is due within 10 days.
Overdue items in change-order go to the management representative.

## Approval criteria
The qa-manager cog may approve change-control change-requests when its review passes, countersigned by a quality manager.

## Process: change-control
Records are filed in Quality/Changes.
The record type is change-request.
1. [staff] Raise the change request (record-filer)
2. [system] Score the risk (risk-scorer)
3. [quality-manager] Approve the change
4. [system] Notify the requester (notifier)
5. [staff] Verify the change

## Process: change-order
Records are filed in Quality/Changes.
The record type is change-order.
Closes as approved.
1. [staff] Prepare the change order (record-filer)
2. [system] Check the compiled Frame (frame-stager)
3. [quality-manager] Approve the change order
