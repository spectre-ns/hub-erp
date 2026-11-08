---
type: frame [0.2]
name: scopes
description: "The scoping Frames: which SOP and policy Frames each scope compiles, at which level, and what it layers on. Personas layer these."
visibility: internal
version: "1.0.0"
scope: company
maintainer: carol
inherits: org-model
---
# Scopes

A scope is a set of SOPs and policies that applies together: the company's,
a department's, a process's. A scope layers on a wider one, so a department
scope brings the company's with it. A persona (a cog or a job role) layers the
scopes and SOPs it works within: those are what it reads and what binds it.
Later layers add rules and context and may only narrow what earlier ones allow.

## Scope: company
Level: company
Compiles: org-model, quality-manual, tasks-sop

## Scope: policy
Level: company
Layers on: company
Compiles: scopes, cog-profiles

## Scope: quality
Level: department
Layers on: company
Compiles: document-control-sop, change-control-sop, ncr-sop, capa-sop, complaint-sop, audit-sop, calibration-sop, supplier-sop, training-sop, management-review-sop, wi-review-a-document, wi-raise-a-change-request, wi-file-a-nonconformance, wi-disposition-stock, wi-acknowledge-training, wi-run-an-audit, wi-record-management-review, wi-respond-to-a-cog-escalation

## Scope: finance
Level: department
Layers on: company
Compiles: finance-policy

## Scope: purchasing
Level: department
Layers on: company
Compiles: purchasing-policy, supplier-sop

## Scope: engineering
Level: department
Layers on: company
Compiles: design-control-sop
