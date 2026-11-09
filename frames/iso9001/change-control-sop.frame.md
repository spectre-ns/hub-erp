---
type: frame [0.2]
name: change-control-sop
description: "Planning and control of changes: any member of staff raises a change request with its impact, the quality manager approves it, and the requester verifies the change was carried out as planned."
visibility: internal
version: "0.4.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["6.3", "8.5.6"]
compile: convention
---
# Change control SOP

A change request asks for a change and says why. A change order carries the
change: draft revisions of the SOPs, policies, cog profiles and personas it
changes, direct changes made in Settings or the forms editor, form layouts,
and the change requests it implements. The configuration delta (what the
change order would change against the release in force, each item with where
it came from) is what is reviewed and approved.

A change order is prepared, then reviewed in rounds. The requester (or a
document controller) requests review; each round compiles the delta from the
drafts as they stand, drafts the prose for direct changes, suggests the panel
(see Review of changes) and queues the panel's reviews. The author answers the
questions in the prose, accepts or rejects the suggestions and requests review
again, until the requester sends the change order for approval. The approvers
then decide each item of the delta, accepting every warning it carries; a
widening needs both policy owners. The quality manager approves the change
order, and that approval also approves every revision it carries. The release
automation brings the configuration and the revisions into force together,
records any divergence between them, compiles every other open change order
again and sends related changes back to their requesters.

A contributor of a change order is anyone who wrote a revision it carries or
made a direct change, a form change or a record-type change on it.

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

## Segregation of duties
The contributor and the approver of a change-order must be different people.

## Review of changes
This section guides the quality-assistant cog when it suggests the panel for a
review round; it is not a rule the hub enforces. The cog reads it with the
delta and proposes each reviewer with its reason, and the requester may add or
remove reviewers before the panel reviews.

A change that touches permissions, record types or forms is reviewed by the it-admin cog.
A change to roles, limits or segregation of duties is reviewed by the management cog.
A change to a quality process is reviewed by the qa-manager cog and the quality-assistant cog.
A change to a cog profile or a persona is reviewed by the it-admin cog and the qa-manager cog.

## Process: change-order
Records are filed in Quality/Changes.
The record type is change-order.
Closes as approved.
1. [staff] Prepare the change order (record-filer)
2. [system] Check the change order is ready (frame-stager)
3. [quality-manager] Approve the change order

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Raises change requests, prepares change orders and verifies the changes they asked for. |
| Quality manager | Approves change requests and change orders, and countersigns what the qa-manager cog approves. |
| Management representative | Receives the change requests and change orders that are overdue. |
| the qa-manager cog | Approves change requests within its criteria and drafts the prose for direct changes. |
| the quality-assistant cog | Suggests the review panel for each change order. |
| the it-admin cog | Reviews changes that touch permissions, record types or forms. |
| the management cog | Reviews changes to roles, limits and segregation of duties. |
