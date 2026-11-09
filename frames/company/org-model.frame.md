---
type: frame [0.2]
name: org-model
description: Roles, members (people and cogs) and folders of Example Instruments Ltd.
visibility: internal
version: "1.6.0"
scope: company
maintainer: operations
---
# Organization model

This Frame is the root of the scope tree. Every other policy Frame inherits
it. Roles, members and folders named anywhere else must be declared here.
Cogs are members like anyone else: they hold roles, receive routed work and
messages, and are bound by segregation of duties. What a member may do is
what the SOPs give their roles (or them by name); a cog is given no role
wider than its work needs. A member comments on the items it may read where
a sentence below or in the SOP that owns them says so.

## Roles
- finance-clerk
- payables-reviewer
- controller
- quality-manager
- author
- reviewer
- policy-owner
- internal-auditor
- management-representative
- document-controller
- process-owner
- staff
- design-review-committee
- system-administrator
- management-reviewer
- policy-reader

## Members
- alice (person): finance-clerk, author, process-owner, staff
- bob (person): controller, reviewer, policy-owner, management-representative, internal-auditor, staff, design-review-committee, system-administrator
- carol (person): quality-manager, reviewer, policy-owner, document-controller, staff, design-review-committee
- dave (person): payables-reviewer, staff
- ap-reviewer (cog): payables-reviewer, finance-clerk
- doc-reviewer (cog): reviewer, author
- quality-assistant (cog): staff, design-review-committee
- qa-manager (cog): reviewer, author, staff
- organization (cog): policy-reader
- it-admin (cog): system-administrator
- management (cog): management-reviewer

## Folders
- Finance
- Finance/Payables
- Quality
- Quality/Controlled Documents
- Quality/Training
- Quality/Nonconformances
- Quality/CAPA
- Quality/Audits
- Quality/Suppliers
- Quality/Changes
- Quality/Management Review
- Quality/Calibration
- Quality/Complaints
- Tasks
- Policy
- Policy/Frames

## Permissions
Staff may read Policy.
Staff may create documents in Policy.
Policy owners may approve documents in Policy.
Staff may comment on anything in Policy.
Staff may comment on anything in Quality.
Staff may comment on anything in Tasks.
Payables reviewers may comment on anything in Finance.
Policy readers may read Policy.
Policy readers may read Quality.

## Segregation of duties
A cog's work is signed by a person.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Policy owner | Approves every change to the organization model and the documents filed in Policy. |
| System administrator | Keeps the members and their roles as intended: who holds which role, cogs included. |
| Staff | Knows who holds which role and where things are filed; files documents in Policy. |
