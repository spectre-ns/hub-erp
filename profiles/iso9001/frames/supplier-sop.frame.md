---
type: frame [0.2]
name: supplier-sop
description: "Control of externally provided processes, products and services: the quality manager evaluates a supplier against fixed criteria and the management representative approves it for a scope and a re-evaluation date."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["8.4"]
compile: convention
---
# Supplier evaluation SOP

External providers are evaluated before use and re-evaluated at the interval
set on the record (8.4). The quality manager files the evaluation with the
criteria scored (quality history, delivery, certification, capacity), the
total score, the scope of approval and the re-evaluation date. The
management representative approves it; the record closes as approved and the
supplier may be used within that scope. A rejected evaluation ends the run.
Purchasing information and the type and extent of control (8.4.2, 8.4.3)
follow from the scope on the approved record.

## Permissions
Quality managers may create suppliers in Quality/Suppliers.
Management representatives may approve suppliers in Quality/Suppliers.

## Routing
Approvals in supplier-evaluation go to the management representative.
Approve the supplier in supplier-evaluation is due within 10 days.

## Process: supplier-evaluation
Records are filed in Quality/Suppliers.
The record type is supplier-evaluation.
Closes as approved.
1. [quality-manager] Evaluate the supplier (record-filer)
2. [management-representative] Approve the supplier
