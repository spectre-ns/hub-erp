---
type: frame [0.2]
name: wi-file-a-nonconformance
description: "Work instruction for staff: how to open a nonconformance from the shell and what happens next."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["8.7"]
compile: none
---
# WI: file a nonconformance

For: anyone holding the `staff` role.

1. Open the **Quality/Nonconformances** board and choose *New nonconformance*
   (process `nonconformance`). Give a short title and a description that says
   what was found, where, how many, and whether anyone could be hurt. Words
   such as "safety" or "recall" raise the severity to high.
2. Submit. The classifier proposes the source, the severity and containment
   wording; the risk scorer sets the risk. You will see the record page with
   its number (NCR-YYYY-NNNN), its state `awaiting-disposition-the-nonconformance`
   and who holds it.
3. Quarantine the affected items as the containment text says. Do not use or
   ship them.
4. The quality manager (or the quality-assistant cog for a low-risk item)
   dispositions the record within 5 days. You cannot approve your own record.
   Watch the record page: when it closes, the disposition and its rationale
   are on it, and a CAPA is linked when one was opened.
5. Press **Why** on any routing or decision to see the SOP sentence behind it.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Files nonconformances as this instruction says. |
