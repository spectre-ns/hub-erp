---
type: frame [0.2]
name: document-control-sop
description: Lifecycle of controlled documents, who approves them, and when the document reviewer cog may.
visibility: internal
version: "2.0.0"
scope: department
maintainer: carol
inherits: org-model
---
# Document control SOP

Controlled documents are drafted, filed, risk-scored, approved and published.
A minor revision is low risk and may be approved by the document reviewer
cog. A major revision is approved by a human reviewer who is not the author.

## Permissions
Authors may create documents in Quality/Controlled Documents.
Authors may read Quality.
Reviewers may approve documents in Quality/Controlled Documents.
Reviewers may read Quality.
Quality managers may read Quality.

## Routing
Approvals in document-control with low risk go to the doc-reviewer cog.
Approvals in document-control go to the reviewer.

## Approval criteria
The doc-reviewer cog may approve document-control documents when the change is minor.

## Segregation of duties
The author and the approver of a document must be different people.

## Process: document-control
Records are filed in Quality/Controlled Documents.
Closes as effective.
1. [author] Draft the document (document-drafter)
2. [system] File the document (record-filer)
3. [system] Score the risk (risk-scorer)
4. [reviewer] Approve the document
5. [system] Publish the document (document-publisher)
