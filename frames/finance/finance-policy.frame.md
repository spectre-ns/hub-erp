---
type: frame [0.2]
name: finance-policy
description: Who may file and approve supplier bills, how they are routed, and how purchase-to-pay runs.
visibility: internal
version: "1.2.1"
scope: department
maintainer: bob
inherits: org-model
---
# Finance policy

Supplier bills are filed in the payables folder, matched to a purchase order
and a receipt, risk-scored, approved, and posted to the ledger. Small, clean
bills may be approved by the payables reviewer cog within fixed criteria.
Everything else is approved by a person.

## Permissions
Finance clerks may create journal reversals in Finance.
Controllers may approve journal reversals in Finance.
Finance clerks may create bills in Finance/Payables.
Finance clerks may read Finance.
Payables reviewers may approve bills in Finance/Payables.
Payables reviewers may read Finance.
Controllers may approve bills in Finance/Payables.
Controllers may read Finance.
Quality managers may not create bills in Finance/Payables.

## Thresholds
The match tolerance is 2 percent.
The controller approval threshold is 10,000 USD.

## Routing
Approvals in purchase-to-pay over 10,000 USD go to the controller.
Approvals in purchase-to-pay at or under 2,000 USD go to the ap-reviewer cog.
Approvals in purchase-to-pay go to the payables reviewer.
Approvals in journal-reversal go to the controller.
Approve the bill in purchase-to-pay is due within 5 days.
Overdue items in purchase-to-pay are reported to the policy owner.

## Approval criteria
The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved.


## Segregation of duties
The preparer and the approver of a bill must be different people.
The requester and the approver of a journal-reversal must be different people.

## Process: purchase-to-pay
Records are filed in Finance/Payables.
Closes as posted.
1. [finance-clerk] Extract the bill (invoice-extractor)
2. [system] File the bill (record-filer)
3. [system] Match the bill to purchase orders (three-way-matcher)
4. [system] Score the risk (risk-scorer)
5. [payables-reviewer] Approve the bill
6. [system] Post the journal (ledger-poster)

## Process: journal-reversal
Records are filed in Finance.
Closes as posted.
1. [finance-clerk] Request the journal reversal (record-filer)
2. [controller] Approve the reversal
3. [system] Post the reversal (journal-reverser)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Finance clerk | Files supplier bills and journal reversals. |
| Payables reviewer | Reviews and approves bills within their limits. |
| Controller | Approves large bills and journal reversals. |
| Policy owner | Is told of the overdue approvals. |
| the ap-reviewer cog | Approves small matched bills within its criteria. |
