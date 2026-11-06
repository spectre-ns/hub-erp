---
type: frame [0.2]
name: grammar-sample
description: Test-only Frame exercising every convention-mode sentence form; read by the extractor tests, never installed in a hub.
visibility: internal
version: "1.0.0"
scope: department
maintainer: tests
inherits: org-model
---
# Grammar sample (test fixture)

## Roles
- sample-reviewer

## Members
- sam (person): sample-reviewer

## Folders
- Samples

## Permissions
Sample reviewers may approve samples in Samples.

## Thresholds
The sample review interval is 14 days.
The sample tolerance is 2.5 percent.

## Routing
At Review the sample in sample-review go to the assignee.
Approvals in sample-review go to the sample reviewer.
Overdue items in sample-review go to the sample reviewer.
At Review the sample in sample-review for drawings with a minor change go to the sample reviewer.
Approvals in sample-review over 10,000 USD go to the sample reviewer.
At Review the sample in sample-review with high risk go to the sample reviewer.
Review the sample in sample-review is due within 3 days.
Approvals in sample-review are due within 5 days.

## Process: sample-review
Records are filed in Samples.
The record type is task.
Closes as done.
Rejections return to Review the sample.
1. [sample-reviewer] File the sample (record-filer)
2. [sample-reviewer] Review the sample
3. [system] Link the sample (record-linker)
4. [sample-reviewer] Approve the sample
