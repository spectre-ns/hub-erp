---
type: frame [0.2]
name: wi-raise-a-change-request
description: "Work instruction for staff: raise a change request, get it approved, verify it."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["6.3", "8.5.6"]
compile: none
---
# WI: raise a change request

For: anyone holding the `staff` role.

1. Open the **Quality/Changes** board and choose *New change request*
   (process `change-control`). Give the title, a description of the change,
   and the `impact`: which documents, processes, products and people are
   affected and what could go wrong. List the affected documents.
2. Submit. The risk scorer tiers the request and it waits for the quality
   manager at `awaiting-approve-the-change`, due in 10 days.
3. When the quality manager approves, you are notified. Carry out the change:
   revise the documents through document control, update the processes,
   train the people.
4. Open the record and decide **approve** with `verification` set to what
   was done and how it was checked. That closes the request. A rejected
   request ends the run; raise a new one with the concerns addressed.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Raises change requests as this instruction says. |
