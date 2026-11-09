---
type: frame [0.2]
name: writing-policy-frames
description: How to write a policy Frame so the compiler can read it. Sentence shapes for permissions, thresholds, routing, approval criteria, segregation of duties and processes.
visibility: public
version: "0.2.0"
scope: reference
maintainer: operations
---
# Writing policy Frames

The compiler reads rule sentences only inside these sections: Roles,
Members, Folders, Permissions, Thresholds, Routing, Approval criteria,
Segregation of duties, Duties, and any section titled "Process: name". Narrative
can go anywhere else. A sentence in a rule section that matches no shape is
reported to the author as unparsed.

## Permission sentences
A plural role, "may" or "may not", an action, an object, "in", a folder.
Example: Finance clerks may create bills in Finance/Payables.
Reading a folder: Controllers may read Finance.
Commenting: Staff may comment on anything in Quality.
One cog by name: The it-admin cog may not approve documents in Policy.
A cog's action a person confirms first: The stores cog may move stock
items in Inventory/Stock when a storekeeper confirms. ("when a person
confirms": whoever gave the cog the task.) Such a sentence narrows a wider
permission: the cog proposes, and the person in that role confirms.

## Duties
"Every day|week|month the <cog> cog <does the task>." in a section
"## Duties" of the SOP that owns the work: a standing goal the cog takes up.

## Cog profiles and personas
A cog profile is "## Cog: name" with About: (what it does), Model: and
Persona:. A persona is "## Persona: name" with Tone:, Stance: (reviewer,
executor, author or advisor), Lenses:, Reviews: (n adversarial passes),
Escalates: and Behaviour:. What a cog follows and may do is not written in
its profile: it comes from the SOPs that name it or its roles.

## Roles and responsibilities
Every document ends with a section "## Roles and responsibilities": a table
with the header row "| Role | Responsibilities |", the rule "|---|---|" and
one row per role (or "the <name> cog") with what it does in the document.
The table says who must read the document and what each cog has in its
context. List every role and cog the document gives a step, a route,
approval criteria, a duty or a create, approve or update permission.

## Threshold sentences
"The <name> threshold|tolerance|limit is <number> <unit>." Units: USD, EUR,
GBP, percent, days.

## Routing sentences
"Approvals in <process> [over|under|at or under|at or over <amount> <CUR> | with low|medium|high risk] go to <the role | the <cog> cog | person>."
Specific routes are tried before the default route. A route to a cog is
valid only if the cog has approval criteria for that process.

## Approval criteria sentences
"The <cog> cog may approve <process> <objects> [under <amount> <CUR>] [when <condition> and <condition>]."
Conditions the runtime can evaluate: the three-way match is within
tolerance; the supplier is approved; the risk is low; the change is minor.
Anything else is refused by the consistency Guard.

## Segregation of duties sentences
"The <role A> and the <role B> of a <object> must be different people."
This binds people and cogs alike.

## Process sections
A section "## Process: <name>", one line "Records are filed in <Folder>.",
then numbered steps "n. [role] Title (cog-id)". A step without a cog is a
human checkpoint and becomes a Gate on the step before it, routed by the
Routing sentences for that process.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Process owner | Writes the SOPs of their processes in these shapes. |
| Document controller | Checks that a new SOP reads as rules before it is sent for approval. |
