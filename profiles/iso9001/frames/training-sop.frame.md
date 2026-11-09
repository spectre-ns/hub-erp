---
type: frame [0.2]
name: training-sop
description: "Training and awareness: every person assigned a controlled document acknowledges it within 14 days; the quality manager may file other training records."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["7.2", "7.3"]
compile: convention
---
# Training SOP

Competence (7.2) and awareness (7.3) are evidenced by training records. When
a controlled document becomes effective, the document publisher opens one
training-acknowledgement run per person holding a role its Roles and
responsibilities table lists (the document's training roles, when set,
override the table); the record waits for that person. A cog acknowledges
nothing: it is trained on a revision from the release that puts it in its
context. The training matrix (Required training, in Quality/Training) lists
who must read what; it is generated with each release. The
acknowledgement is the person's electronic signature that they have read and
understood the revision. The quality manager and the document controller may
also file training records for courses, inductions and assessments. An
acknowledgement not given within 14 days is escalated to the quality manager.
"Approve" on a training record means signing the acknowledgement checkpoint;
it gives no authority over other people's records, because the record is
routed only to its assignee.

## Permissions
Quality managers may create training-records in Quality/Training.
Document controllers may create training-records in Quality/Training.
Staff may approve training-records in Quality/Training.
Staff may read Quality/Training.

## Routing
At Acknowledge the document in training-acknowledgement go to the assignee.
Acknowledge the document in training-acknowledgement is due within 14 days.
Overdue items in training-acknowledgement are reported to the quality manager.

## Process: training-acknowledgement
Records are filed in Quality/Training.
The record type is training.
Closes as acknowledged.
1. [system] File the training record (record-filer)
2. [staff] Acknowledge the document

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Reads and acknowledges each revision of the documents their roles must read. |
| Quality manager | Files training records for courses and assessments, and receives the overdue acknowledgements. |
| Document controller | Files training records for courses and assessments. |
