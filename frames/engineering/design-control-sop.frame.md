---
type: frame [0.2]
name: design-control-sop
description: How a mechanical or electrical design is submitted, exported, reconciled against inventory, reviewed and released as a part revision.
visibility: internal
version: "1.0.0"
scope: department
maintainer: engineering
inherits: org-model
clauses: ["8.3.2", "8.3.3", "8.3.4", "8.3.5", "8.3.6"]
---
# Design control SOP

A design record carries the FreeCAD models and KiCad schematics and boards
attached to it. Submitting it exports the neutral formats, drawings, Gerbers
and reports, extracts the bill of materials, and reconciles every line
against inventory. The design authority reviews the package and releases
the part revision. Nothing reaches inventory without that signature.

## Roles
- design-engineer
- design-authority
- management-representative

## Members
- alice (person): design-engineer
- bob (person): design-authority, management-representative

## Folders
- Engineering
- Engineering/Designs
- Inventory
- Inventory/Items

## Permissions
Design engineers may create designs in Engineering/Designs.
Design engineers may read Engineering.
Design authorities may approve designs in Engineering/Designs.
Design authorities may read Engineering.
Design engineers may create items in Inventory/Items.
Quality managers may read Engineering.

## Thresholds
The design rule error limit is 0 percent.
The design review limit is 10 days.

## Routing
At Review the design in design-release go to the design authority.
Review the design in design-release is due within 10 days.
Overdue items in design-release go to the management representative.

## Segregation of duties
The designer and the approver of a design must be different people.

## Process: design-release
Records are filed in Engineering/Designs.
Closes as released.
Rejections return to Submit the design.
1. [design-engineer] Submit the design (record-filer)
2. [system] Export the design (cad-exporter)
3. [system] Reconcile the bill of materials (bom-reconciler)
4. [system] Score the risk (risk-scorer)
5. [design-authority] Review the design
6. [system] Release the part (part-releaser)
7. [system] Render the record (document-renderer)
