---
type: frame [0.2]
name: org-model
description: Roles, members (people and cogs) and folders of Example Instruments Ltd.
visibility: internal
version: "1.5.0"
scope: company
maintainer: operations
---
# Organization model

This Frame is the root of the scope tree. Every other policy Frame inherits
it. Roles, members and folders named anywhere else must be declared here.
Cogs are members like anyone else: they hold roles, receive routed work and
messages, and are bound by segregation of duties.

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

## Members
- alice (person): finance-clerk, author, process-owner, staff
- bob (person): controller, reviewer, policy-owner, management-representative, internal-auditor, staff, design-review-committee
- carol (person): quality-manager, reviewer, policy-owner, document-controller, staff, design-review-committee
- dave (person): payables-reviewer, staff
- ap-reviewer (cog): payables-reviewer, finance-clerk
- doc-reviewer (cog): reviewer, author
- quality-assistant (cog): quality-manager, author, reviewer, staff, design-review-committee
- qa-manager (cog): quality-manager, reviewer, author, staff
- organization (cog): staff

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
