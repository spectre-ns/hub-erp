---
type: frame [0.2]
name: wi-review-a-document
description: "Work instruction for reviewers and process owners: how to review and approve a controlled document."
visibility: internal
version: "0.1.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["7.5"]
compile: none
---
# WI: review a document

For: reviewers, process owners and the document controller.

1. An item "Approve the document" in your **inbox** names the document,
   the author and the SOP sentence that routed it to you. The record is in
   Quality/Controlled Documents, state `awaiting-approve-the-document`,
   due in 10 days.
2. On the record page open the draft version. Check: the title and
   revision are right, the change type (minor or major) matches the content,
   the training roles list who must read it, and the review date is set.
3. Decide **approve** to publish: the revision becomes effective, the
   previous revision is withdrawn, and training runs are opened for every
   person holding a training role. Decide **reject** with the reason to end
   the run; the author starts again from a corrected draft.
4. Minor revisions are approved by the doc-reviewer cog within its criteria
   and never reach you; drawings go to the process owner. The author of a
   document can never approve it. **Why** on the record shows which rule
   applied.
5. If an item is still waiting after its due date, the document controller
   is added to the assignees and may approve instead.
