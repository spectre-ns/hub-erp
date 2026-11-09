---
type: frame [0.2]
name: wi-respond-to-a-cog-escalation
description: "Work instruction for approvers: what to do when a cog escalates an item to you instead of deciding it."
visibility: internal
version: "0.2.0"
scope: department
maintainer: carol
inherits: org-model
clauses: ["5.3"]
compile: none
---
# WI: respond to a cog escalation

For: anyone holding the `staff` role who receives an item a cog could not decide.

1. A reviewer cog (doc-reviewer, quality-assistant) approves only within the
   approval criteria written in the SOP. When the item is outside them, the
   cog escalates and the item is routed to the next rule, usually to you.
   Your **inbox** item names the routing sentence; the record's Track shows
   the `gate.cog_review` event with the cog's reasons.
2. Read the reasons first: they say which condition failed (risk not low,
   change not minor, open nonconformance exists). They are facts the risk
   scorer recorded, not opinions.
3. Review the item as you would any other and decide **approve** or
   **reject** with your meaning. You are the signature; the cog's review is
   evidence, not a decision.
4. If cogs escalate the same kind of item repeatedly, propose a change to
   the approval criteria sentence in the SOP through a change request. Any
   widening of what a cog may approve needs two policy owners at the Gate.
5. Never message the cog to ask it to approve: it cannot act outside the
   release.
