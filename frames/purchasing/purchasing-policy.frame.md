---
type: frame [0.2]
name: purchasing-policy
description: How items are ordered from approved suppliers, who approves a purchase, when the purchasing cog may order on its own, and how goods are received into stock.
visibility: internal
version: "1.0.0"
scope: department
maintainer: dave
inherits: org-model
clauses: ["8.4.1", "8.4.2", "8.4.3"]
---
# Purchasing policy

A purchase request names an item, a quantity and a supplier. The supplier
must be on the approved list (a seeded approval or an approved supplier
evaluation). Small requests from approved suppliers may be approved by the
purchasing assistant cog within fixed criteria; everything else is approved
by the buyer, and large purchases by the controller. Approval issues the
purchase order. Receiving the goods books the stock and marks the order
received so the supplier's bill can be matched and paid.

## Roles
- buyer
- design-engineer

## Members
- dave (person): buyer
- purchasing-assistant (cog): buyer, staff

## Folders
- Purchasing
- Purchasing/Requests
- Purchasing/Orders
- Inventory
- Inventory/Items
- Inventory/Receipts

## Permissions
Staff may create purchase requests in Purchasing/Requests.
Staff may read Purchasing.
Buyers may approve purchase requests in Purchasing/Requests.
Buyers may read Purchasing.
Buyers may read Inventory.
Controllers may approve purchase requests in Purchasing/Requests.
Controllers may read Purchasing.
Staff may create receipts in Inventory/Receipts.
Staff may read Inventory.
Buyers may approve receipts in Inventory/Receipts.
Design engineers may create items in Inventory/Items.

## Thresholds
The purchase approval threshold is 10,000 USD.
The cog purchase limit is 500 USD.

## Routing
Approvals in purchase-request over 10,000 USD go to the controller.
Approvals in purchase-request under 500 USD go to the purchasing-assistant cog.
Approvals in purchase-request go to the buyer.
Approve the purchase in purchase-request is due within 3 days.
Overdue items in purchase-request go to the controller.
At Confirm the receipt in goods-receipt go to the buyer.

## Approval criteria
The purchasing-assistant cog may approve purchase-request purchase requests under 500 USD when the supplier is approved and the item is known.

## Segregation of duties
The requester and the approver of a purchase-request must be different people.

## Process: purchase-request
Records are filed in Purchasing/Requests.
Closes as ordered.
1. [staff] Raise the purchase request (record-filer)
2. [system] Check the supplier (supplier-checker)
3. [system] Score the risk (risk-scorer)
4. [buyer] Approve the purchase
5. [system] Issue the purchase order (po-issuer)
6. [system] Render the record (document-renderer)

## Process: goods-receipt
Records are filed in Inventory/Receipts.
Closes as received.
1. [staff] Record the receipt (record-filer)
2. [system] Check the order (receipt-checker)
3. [buyer] Confirm the receipt
4. [system] Book the stock (stock-booker)
