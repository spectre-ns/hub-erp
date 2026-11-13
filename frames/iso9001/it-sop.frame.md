---
type: frame [0.2]
name: it-sop
description: "IT and configuration: the hub's configuration changes only by change order; who is given which roles (cogs included); risk-based validation of configuration changes; backup and restore; incidents; what system administrators may do."
visibility: internal
version: "0.2.1"
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
use a change order. The system administrators prepare configuration changes.
The system administrator role grants no approval of its own: a change order is
approved by the roles the SOPs name for that (the quality manager signs it, the
policy owners decide its items), so a system administrator who also holds one
of those roles approves in that role, never as a system administrator, and
whoever made a direct configuration change on a change order is not its only
approver. No cog approves a configuration change.

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
change order and kept with it: a test record (a document of type test-evidence,
or a test record entered on the change order: what was run, what was expected,
what happened, pass or fail) or a test note. The change order cannot be
approved until each item has the evidence its class needs, and a failed test
stops it. The classes and what each needs are stated in the Validation section
below; a change to them is a change order like any other.

Hosted models. The cogs run on the local models the installation provides. A
hosted model (Claude, from Anthropic) is used only when a system administrator
switches it on, as an installation change recorded under this SOP (who, when,
why, and the provider, which is evaluated and approved as a supplier under the
supplier evaluation SOP), and only by the cogs bundled with it: by the senior
cogs for compilation, reading the SOPs into rules on the compile-frame
committee, and by any of them for the tasks assigned to that cog (an in-depth report,
an analysis), working for the person who filed the task. Other cogs keep their
bundled models. What may leave the hub to the provider is the SOP and policy
text being compiled, its diff against the release in force, the rules in force
it is read against, the item the senior cog is working on, and for a task the
task itself and the records and documents the cog reads for it; no other record,
document or person's data. Each use is recorded: every decision, review and
drafted text a cog produces keeps the model that produced it, the cog's persona
and its version, and the release that gave it its context; each compile
reading names the cogs that read it.

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

## Validation
Widenings are high risk.
Changes to permissions, approval criteria, segregation of duties, members, roles, groups, committees, cog profiles and personas are high risk.
Changes to record types and workflow gates are high risk.
Changes to routing, due dates, processes, thresholds, required fields, duties, folders, scopes and responsibilities are medium risk.
Changes to form layouts and wording are low risk.
High-risk changes need a test record.
Medium-risk changes need a test note.
Low-risk changes need the review of the delta.

## Thresholds
The restore test interval is 90 days.
The access review interval is 90 days.

## Segregation of duties
Whoever made a direct configuration change on a change-order is not its only approver.

## Duties
Every week the it-admin cog reviews the open change orders that touch permissions, record types or forms and comments on each what it found.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| System administrator | Prepares configuration changes as change orders, manages access, backs up and restores the hub, handles incidents, and switches hosted models on or off as a recorded installation change; approves nothing as a system administrator. |
| Management reviewer | Reviews changes to roles, limits and segregation of duties. |
| Quality manager | Approves the change orders that carry configuration changes, once each item has the validation evidence its class needs. |
| Policy owner | Decides the items of a change order, widenings by both policy owners. |
| the senior-cogs group | Compiles the SOPs and works on the tasks assigned to it, on a hosted model only when it is switched on, and sends only what this SOP allows. |
| Internal auditor | Audits system administration they did not perform. |
| the it-admin cog | Reviews open change orders that touch permissions, record types or forms; changes no configuration itself. |
| the management cog | Reviews changes to roles, limits and segregation of duties; approves nothing. |
