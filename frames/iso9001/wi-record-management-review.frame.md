---
type: frame [0.2]
name: wi-record-management-review
description: "Work instruction for the management representative: prepare, hold and record a management review."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["9.3"]
compile: none
---
# WI: record a management review

For: the management representative (preparer) and the chair.

1. Before the meeting, open the **Quality/Management Review** board and
   choose *New review* (process `management-review`). Set the title, the
   date, the chair, and `inputs` with one entry per 9.3.2 input (use the
   agenda template in the profile's templates folder). Attach the audit
   reports, the complaint and nonconformance summaries and the objectives
   status.
2. Submit. The record waits for the chair at `awaiting-hold-the-review`.
3. Hold the meeting against the agenda. Record every decision as an output:
   improvement, change to the QMS, or resource need, each with an owner and
   a date.
4. The chair decides **approve** with `outputs` set and files a task or a
   change request for each action. The record closes and is rendered to the
   minutes document in the same folder.
