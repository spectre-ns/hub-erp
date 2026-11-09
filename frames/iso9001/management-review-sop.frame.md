---
type: frame [0.2]
name: management-review-sop
description: "Management review: the management representative prepares the inputs, the chair holds the review and records the outputs, and the minutes are rendered as an evidence document."
visibility: internal
version: "0.2.0"
scope: company
maintainer: bob
inherits: org-model
clauses: ["9.3"]
compile: convention
---
# Management review SOP

Top management reviews the QMS at planned intervals, at least
{{REVIEW_FREQUENCY}} (9.3.1). The management representative prepares the
review record with the date and the inputs (9.3.2): status of actions from
previous reviews, changes in context, customer satisfaction and complaints,
quality objectives, process performance and conformity, nonconformances and
corrective actions, audit results, supplier performance, adequacy of
resources, risks and opportunities, and opportunities for improvement. The
chair holds the review and records the outputs (9.3.3): improvement
decisions, changes to the QMS and resource needs, each as a task or a change
request. The record is rendered to the minutes document when it closes.
The management cog prepares the evidence and advises; it decides nothing.

## Permissions
Management representatives may create reviews in Quality/Management Review.
Management representatives may approve reviews in Quality/Management Review.
Quality managers may approve reviews in Quality/Management Review.
Management reviewers may read Quality.
Management reviewers may comment on reviews in Quality/Management Review.

## Routing
At Hold the review in management-review go to the chair.
Overdue items in management-review go to the quality manager.

## Duties
Every month the management cog summarises the nonconformances, corrective actions and audit results since the last review and comments the summary on the open management review.

## Process: management-review
Records are filed in Quality/Management Review.
The record type is management-review.
1. [management-representative] Prepare the review (record-filer)
2. [management-representative] Hold the review
3. [system] Render the record (document-renderer)

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Management representative | Prepares the review record with its inputs and holds the review as its chair. |
| Quality manager | Approves the review record and receives the overdue ones. |
| Management reviewer | Reads the quality records the review draws on and comments on the review. |
| the management cog | Summarises the evidence for the next review every month. |
