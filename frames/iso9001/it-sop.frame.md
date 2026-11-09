---
type: frame [0.2]
name: it-sop
description: "IT and configuration: the hub's configuration changes only by change order; who is given which roles (cogs included); risk-based validation of configuration changes; backup and restore; incidents; what system administrators may do."
visibility: internal
version: "0.1.0"
scope: department
maintainer: bob
inherits: org-model
clauses: ["7.1.3", "7.5.3", "8.5.1"]
compile: convention
---
# IT and configuration SOP

The hub runs on approved configuration and never on prose: the rules (members
and roles, folders, permissions, limits, routing, approval criteria, segregation
of duties, duties, processes), the record types and their fields, the form
layouts, the cog profiles and the personas. This SOP says how that configuration
changes, who is given access, how a change is validated, and how the hub is kept
safe. The installation itself (the model server, ports, secrets, the queue, the
backups, the agents switch) is the environment: it changes under this SOP and
outside the release, and every release records what it ran against.

Configuration changes only through a change order. A change made in Settings,
the forms editor or the workflow editor is recorded on a change order as a
direct change, and the prose that states it is drafted into the SOP it belongs
to; nothing reaches the release in force until the change order is approved and
released. Any other path that would change configuration refuses and says to
use a change order. The system administrators prepare configuration changes;
they do not approve them, and neither does a cog.

Access is managed through the organization model. A person or a cog is given a
role by a change order to the organization model that says why; a cog is given
only the roles its work needs and never a role that approves policy. The system
administrators check every quarter that the members and their roles in force
are the ones intended (people who have left, roles no longer needed, cogs
holding more than their work needs) and raise a change order for any
difference.

A configuration change is validated in proportion to its risk, in the manner of
computer software assurance. Each item of the change order's configuration
delta is classed: high risk when it changes who may approve, sign or see what
(permissions, approval criteria, segregation of duties, members and roles, a
cog's model or persona), medium when it changes how work flows (routing, due
dates, processes, record types and required fields), low when it changes
presentation only (form layouts, wording). A high-risk item is tested with a
recorded scripted check before approval; a medium-risk item is tried out in an
unscripted test whose outcome is noted on the change order; a low-risk item is
validated by the review of the delta itself. The evidence is attached to the
change order and kept with it.

The hub is backed up every day (the kernel, the releases, the proposals, the
documents and the messages) and the backup is kept off the host. A restore is
tested at least every restore test interval, into a separate hub, and the test
is recorded. Only a system administrator restores, and only on a task that says
why.

An incident (the hub or a model unavailable, a wrong result, a suspected misuse
of access, a cog acting outside what it should) is reported as a task to the
system administrators at once. A cog that misbehaves is stopped with the agents
switch and its runs are kept for the investigation. An incident that may have
affected product or records is also a nonconformance.

Segregation of duties: whoever made a direct configuration change on a change
order is not its only approver. An internal auditor does not audit system
administration they perform.

For: system administrators and the management reviewers.

## Permissions
System administrators may read Policy.
System administrators may read Quality/Changes.
System administrators may create change orders in Quality/Changes.
System administrators may comment on change orders in Quality/Changes.
Management reviewers may read Policy.
Management reviewers may read Quality/Changes.
Management reviewers may comment on change orders in Quality/Changes.
The it-admin cog may not approve documents in Policy.
The it-admin cog may not approve change orders in Quality/Changes.
The management cog may not approve documents in Policy.
The management cog may not approve change orders in Quality/Changes.

## Thresholds
The restore test interval is 90 days.
The access review interval is 90 days.

## Segregation of duties
Whoever made a direct configuration change on a change-order is not its only approver.

## Duties
Every week the it-admin cog reviews the open change orders that touch permissions, record types or forms and comments on each what it found.
