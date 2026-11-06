---
type: frame [0.2]
name: org-model
description: Roles, members (people and cogs) and folders of Example Instruments Ltd.
visibility: internal
version: "1.0.0"
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

## Members
- alice (person): finance-clerk, author
- bob (person): controller, reviewer, policy-owner
- carol (person): quality-manager, reviewer, policy-owner
- dave (person): payables-reviewer
- ap-reviewer (cog): payables-reviewer, finance-clerk
- doc-reviewer (cog): reviewer, author

## Folders
- Finance
- Finance/Payables
- Quality
- Quality/Controlled Documents
