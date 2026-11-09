---
type: frame [0.2]
name: org-model
description: "Template organization model for the ISO 9001 seed profile: the roles and folders the profile routes to, with no members. Fill it before the first release."
visibility: internal
version: "0.1.0"
scope: company
maintainer: "{{QUALITY_MANAGER}}"
---
# Organization model (template)

This Frame is the root of the scope tree; every policy Frame of the ISO 9001
profile inherits it. It is installed only when the hub has no Frame named
`org-model`. It declares the roles and folders the profile's routing sentences
name and **no members**, so the compile reports "fill the org model" for every
routed role until people and cogs are added below. That is deliberate: a
release cannot go out with placeholders in it.

## How to fill this Frame

Add one line per person and per cog under Members, in the shape
`- name (person): role, role` or `- name (cog): role, role`. The profile needs:

* at least two people holding `policy-owner` (widening changes need two approvers);
* one `quality-manager` and one `management-representative` (different people, or
  nonconformances raised by the quality manager cannot be dispositioned);
* at least one `internal-auditor`, `document-controller` and `process-owner`;
* every person holding `staff` (training is assigned by that role);
* the reviewer cogs `doc-reviewer (cog): reviewer, author` and
  `quality-assistant (cog): quality-manager, staff`.

## Roles
- quality-manager
- management-representative
- internal-auditor
- document-controller
- process-owner
- author
- reviewer
- policy-owner
- staff

## Members

## Folders
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

## Segregation of duties
A cog's work is signed by a person.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Policy owner | Approves every change to the organization model and the documents filed in Policy. |
| System administrator | Keeps the members and their roles as intended: who holds which role, cogs included. |
| Staff | Knows who holds which role and where things are filed; files documents in Policy. |
