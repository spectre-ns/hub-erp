---
type: frame [0.2]
name: wi-disposition-stock
description: "Work instruction for the quality manager: disposition a nonconformance and the stock it concerns."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["8.7"]
compile: none
---
# WI: disposition nonconforming stock

For: the quality manager and the management representative.

1. An item "Disposition the nonconformance" in your **inbox** is due in 5
   days. Open the record page: the classifier's source, severity and
   containment are on it, with the risk tier and the reporter.
2. Confirm the containment was done. Inspect the items.
3. Decide **approve** with the fields `disposition` (rework, scrap,
   use-as-is or return) and `disposition_rationale`. For a low-risk item the
   classifier already proposed rework; change it if you disagree. Use-as-is
   needs the customer's concession where their requirement is affected.
4. Scrap and return, or medium and high risk, open a CAPA automatically
   with you as its owner; the NCR shows the link. The record is rendered to an
   evidence document and closes.
5. If you reported the nonconformance yourself, it is routed to the
   management representative instead: the reporter and the approver must be
   different people.
