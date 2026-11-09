---
type: frame [0.2]
name: calibration-sop
description: "Monitoring and measuring resources: calibration results are recorded by staff, reviewed by the quality manager, and an out-of-tolerance result opens a nonconformance automatically."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["7.1.5"]
compile: convention
---
# Calibration SOP

Measuring equipment is calibrated against standards traceable to
{{NATIONAL_STANDARDS_BODY}} at the interval set on the asset (7.1.5). The
person performing the calibration records the asset, the procedure, the
standard used, the measured values, the result and the next due date. The
quality manager reviews the record within 5 days. When the result is out of
tolerance, a nonconformance is opened automatically and linked to the
calibration so that the validity of previous measurements is assessed and
affected product is dispositioned.

## Permissions
Staff may create calibrations in Quality/Calibration.
Quality managers may approve calibrations in Quality/Calibration.

## Routing
At Review the calibration in calibration go to the quality manager.
Review the calibration in calibration is due within 5 days.
Overdue items in calibration go to the management representative.

## Process: calibration
Records are filed in Quality/Calibration.
The record type is calibration.
1. [staff] Record the calibration (record-filer)
2. [quality-manager] Review the calibration
3. [system] Open the nonconformance (ncr-opener)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Records the calibration of the equipment they use. |
| Quality manager | Reviews each calibration; a failed one opens a nonconformance. |
| Management representative | Receives the calibrations that are overdue. |
