---
type: frame [0.2]
name: design-control-sop
description: How a mechanical or electrical design is submitted, exported, reconciled against inventory, reviewed and released as a part revision.
visibility: internal
version: "1.2.0"
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
- Engineering/Requirements
- Inventory
- Inventory/Items
- Purchasing
- Purchasing/Requests

## Permissions
Design engineers may create designs in Engineering/Designs.
Design engineers may create requirements in Engineering/Requirements.
Design authorities may approve requirements in Engineering/Requirements.
Design engineers may read Engineering.
Design authorities may approve designs in Engineering/Designs.
Design authorities may read Engineering.
Design engineers may create items in Inventory/Items.
Design engineers may create purchase requests in Purchasing/Requests.
Quality managers may read Engineering.
Quality managers may comment on anything in Engineering.
The quality-assistant cog may read Engineering.
The quality-assistant cog may comment on anything in Engineering.

## Thresholds
The design rule error limit is 0 percent.
The design review limit is 10 days.

## Routing
At Review the design in design-release go to the design authority.
Approvals in requirements-capture go to the design authority.
Accept the requirement in requirements-capture is due within 5 days.
Review the design in design-release is due within 10 days.
Overdue items in design-release go to the management representative.

## Segregation of duties
The designer and the approver of a design must be different people.
The captured-by and the approver of a requirement must be different people.

## Process: design-release
Records are filed in Engineering/Designs.
Closes as released.
Rejections return to Submit the design.
1. [design-engineer] Submit the design (record-filer)
2. [system] Trace the requirements (requirements-tracer)
3. [system] Export the design (cad-exporter)
4. [system] Reconcile the bill of materials (bom-reconciler)
5. [system] Score the risk (risk-scorer)
6. [system] Review the design package (quality-assistant, purchasing-assistant)
7. [design-authority] Review the design
8. [system] Release the part (part-releaser)
9. [system] Order the shortages (shortage-requester)
10. [system] Render the record (document-renderer)

## Process: requirements-capture
Records are filed in Engineering/Requirements.
Closes as accepted.
1. [design-engineer] Capture the requirement (record-filer)
2. [system] Score the risk (risk-scorer)
3. [design-authority] Accept the requirement
4. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Design engineer | Submits designs and captures requirements. |
| Design authority | Reviews design packages, accepts requirements and releases parts. |
| Design review committee | Reviews design packages when asked. |
| Management representative | Receives the design reviews that are overdue. |
| the quality-assistant cog | Reviews each design package for risk and traceability. |
| the purchasing-assistant cog | Reviews each design package for parts, suppliers and stock. |
