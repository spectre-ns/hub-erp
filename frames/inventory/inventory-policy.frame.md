---
type: frame [0.2]
name: inventory-policy
description: "Parts, stock locations and lots: what moves freely, what needs an approval, and how stock is held, counted and released."
visibility: internal
version: "1.2.0"
scope: department
maintainer: dave
inherits: org-model
clauses: ["7.1.5", "8.5.2", "8.5.4", "8.7"]
---
# Inventory policy

Every part has one record in the part master: purchased components and the
assemblies made from released designs alike. A manufacturer part is the
manufacturer's part number on the approved manufacturer list; a supplier part
is what can be ordered from an approved supplier, with its pack size and
price breaks. Stock is held as lots, or one lot per serial number for
serial-tracked parts, in stock locations; a structural location only groups
others and holds nothing.

Every movement of stock is written to the ledger and cannot be undone or
switched off. Receiving into a location, moving stock within a site, splitting
a lot, merging lots of one batch, a count within the count variance tolerance
and moving stock into quarantine are free: containment never waits. A count is
measured from the quantity the lot was received or last adjusted to, carried
through splits and merges, so a run of small counts shares one tolerance
however the lot is split or merged. A count outside the tolerance is an
adjustment. Whoever keeps the stock does not approve its write-off, so the
controller approves adjustments; a small one within the tolerance may be
signed by the purchasing assistant, which holds the stock reviewer role for
that and for nothing else. The stores cog keeps stock as a storekeeper does,
moving and counting it only where the person who asks may too; it signs no
adjustment and no release. Whoever asked for an adjustment, and whoever
approved the order the stock came from, never signs it. An adjustment is
applied only to the quantity it was checked and approved on: if the lot
changed in between, it is checked and approved again. Stock leaves quarantine
only through a stock release the quality manager approves, or through the
disposition of the nonconformance that holds it: scrap, use as is, rework or
return to the supplier.

## Roles
- storekeeper
- stock-reviewer

## Members
- dave (person): storekeeper
- purchasing-assistant (cog): stock-reviewer
- stores (cog): storekeeper, staff

## Folders
- Inventory/Parts
- Inventory/Stock
- Inventory/Locations
- Inventory/Adjustments
- Inventory/Releases

## Permissions
Staff may read Inventory.
Staff may create parts in Inventory/Parts.
Buyers may approve parts in Inventory/Parts.
Buyers may create manufacturer parts in Inventory/Parts.
Buyers may create supplier parts in Inventory/Parts.
Design engineers may create parts in Inventory/Parts.
Design engineers may create manufacturer parts in Inventory/Parts.
Storekeepers may create part categories in Inventory/Parts.
Storekeepers may create stock locations in Inventory/Locations.
Storekeepers may create stock items in Inventory/Stock.
Storekeepers may update stock items in Inventory/Stock.
Staff may quarantine stock items in Inventory/Stock.
Storekeepers may create stock adjustments in Inventory/Adjustments.
Controllers may approve stock adjustments in Inventory/Adjustments.
Stock reviewers may approve stock adjustments in Inventory/Adjustments.
Staff may create stock releases in Inventory/Releases.
Quality managers may approve stock releases in Inventory/Releases.
Staff may comment on anything in Inventory.

## Thresholds
The count variance tolerance is 2 percent.
The adjustment approval threshold is 500 USD.
The over receipt tolerance is 5 percent.

## Routing
Approvals in stock-adjustment over 500 USD go to the controller.
Approvals in stock-adjustment under 100 USD go to the purchasing-assistant cog.
Approvals in stock-adjustment go to the controller.
Approvals in stock-adjustment are due within 3 days.
Approvals in stock-release go to the quality manager.
Approvals in stock-release are due within 5 days.
Approvals in part-request go to the buyer.

## Approval criteria
The purchasing-assistant cog may approve stock-adjustment stock adjustments under 100 USD when the variance is within tolerance.

## Segregation of duties
The requester and the approver of a stock-adjustment must be different people.
The ordered-by and the approver of a stock-adjustment must be different people.
The requester and the approver of a stock-release must be different people.

## Process: stock-adjustment
Records are filed in Inventory/Adjustments.
The record type is stock-adjustment.
Closes as applied.
1. [storekeeper] Request the stock adjustment (record-filer)
2. [system] Check the adjustment (adjustment-checker)
3. [system] Score the risk (risk-scorer)
4. [controller] Approve the stock adjustment
5. [system] Apply the adjustment (adjustment-applier)

## Process: stock-release
Records are filed in Inventory/Releases.
The record type is stock-release.
Closes as released.
1. [staff] Request the stock release (record-filer)
2. [system] Check the lot (release-checker)
3. [system] Score the risk (risk-scorer)
4. [quality-manager] Approve the stock release
5. [system] Release the stock (stock-releaser)

## Process: part-request
Records are filed in Inventory/Parts.
The record type is part.
Closes as active.
1. [staff] Request the part (record-filer)
2. [buyer] Approve the part

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Storekeeper | Keeps the stock: receives, moves, counts and asks for adjustments. |
| Stock reviewer | Signs small stock adjustments within the tolerance. |
| Controller | Approves stock adjustments. |
| Quality manager | Approves releases from quarantine. |
| Buyer | Approves parts and keeps manufacturer and supplier parts. |
| Design engineer | Creates parts and manufacturer parts for their designs. |
| Staff | Asks for parts, quarantines suspect stock and asks for its release. |
| the purchasing-assistant cog | Signs small stock adjustments within its criteria. |
| the stores cog | Keeps stock as a storekeeper does, where the person who asks may too. |
