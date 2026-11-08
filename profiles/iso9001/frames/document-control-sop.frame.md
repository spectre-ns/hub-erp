---
type: frame [0.2]
name: document-control-sop
description: "Lifecycle of controlled documents, who approves them, when the document reviewer cog may, how long a review may take, and the training that follows a release."
visibility: internal
version: "2.3.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["7.5"]
compile: convention
---
# Document control SOP

Controlled documents are drafted, filed, risk-scored, approved and published.
A minor revision is low risk and may be approved by the document reviewer
cog. A major revision is approved by a human reviewer who is not the author. When asked,
the QA manager cog may approve on its own review: a minor revision on its
signature, a major one only with a quality manager's countersignature.
Drawings are approved by the process owner. Document controllers may file
documents on behalf of authors and receive overdue reviews. When a document
becomes effective, every person holding one of its training roles receives a
training-acknowledgement run (see the training SOP). The superseded revision
is withdrawn automatically. This Frame satisfies clause 7.5 (creating,
updating and controlling documented information): identification, format,
review and approval, availability, and protection are all properties of the
record and its versions.

## Permissions
Authors may create documents in Quality/Controlled Documents.
Authors may read Quality.
Reviewers may approve documents in Quality/Controlled Documents.
Policy owners may approve documents in Policy.
Reviewers may read Quality.
Quality managers may read Quality.
Document controllers may create documents in Quality/Controlled Documents.
Document controllers may read Quality.
Process owners may approve documents in Quality/Controlled Documents.
Staff may read Quality.

## Routing
Approvals in document-control for policies go to the policy owner.
Approvals in document-control with low risk go to the doc-reviewer cog.
Approvals in document-control for drawings go to the process owner.
Approvals in document-control go to the reviewer.
Approve the document in document-control is due within 10 days.
Overdue items in document-control go to the document controller.

## Approval criteria
The doc-reviewer cog may approve document-control documents when the change is minor.
The qa-manager cog may approve document-control documents when 3 adversarial reviews pass and the change is minor.
The qa-manager cog may approve document-control documents when 3 adversarial reviews pass, countersigned by a quality manager.

## Segregation of duties
The author and the approver of a document must be different people.

## Process: document-control
Records are filed in Quality/Controlled Documents.
Policies are filed in Policy.
Closes as effective.
1. [author] Draft the document (document-drafter)
2. [system] File the document (record-filer)
3. [system] Score the risk (risk-scorer)
4. [reviewer] Approve the document
5. [system] Publish the document (document-publisher)
