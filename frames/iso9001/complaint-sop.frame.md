---
type: frame [0.2]
name: complaint-sop
description: "Customer communication and satisfaction: complaints are logged by staff, risk-scored, answered by the quality manager within 10 days, and a medium or high severity complaint opens a nonconformance."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["8.2.1", "9.1.2"]
compile: convention
---
# Complaint SOP

Customer complaints are part of customer communication (8.2.1) and the main
measure of customer perception (9.1.2). Any member of staff logs a complaint
with the customer, the product and the severity. The risk scorer tiers it.
The quality manager responds within 10 days and records the response, which
closes the record. A complaint of medium or high severity opens a
nonconformance automatically, linked to the complaint, so the cause is
handled in the nonconformance and CAPA processes. Complaint trends are an
input to management review.

## Permissions
Staff may create complaints in Quality/Complaints.
Quality managers may approve complaints in Quality/Complaints.
Management representatives may approve complaints in Quality/Complaints.

## Routing
At Respond to the complaint in complaint go to the quality manager.
Respond to the complaint in complaint is due within 10 days.
Overdue items in complaint go to the management representative.

## Process: complaint
Records are filed in Quality/Complaints.
The record type is complaint.
1. [staff] Log the complaint (record-filer)
2. [system] Score the risk (risk-scorer)
3. [quality-manager] Respond to the complaint
4. [system] Open the nonconformance (ncr-opener)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Logs the complaints customers make. |
| Quality manager | Responds to each complaint. |
| Management representative | Approves responses where the quality manager may not, and receives the overdue ones. |
