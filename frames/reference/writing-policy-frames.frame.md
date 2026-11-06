---
type: frame [0.2]
name: writing-policy-frames
description: How to write a policy Frame so the compiler can read it. Sentence shapes for permissions, thresholds, routing, approval criteria, segregation of duties and processes.
visibility: public
version: "0.1.0"
scope: reference
maintainer: operations
---
# Writing policy Frames

The compiler reads rule sentences only inside these sections: Roles,
Members, Folders, Permissions, Thresholds, Routing, Approval criteria,
Segregation of duties, and any section titled "Process: name". Narrative
can go anywhere else. A sentence in a rule section that matches no shape is
reported to the author as unparsed.

## Permission sentences
A plural role, "may" or "may not", an action, an object, "in", a folder.
Example: Finance clerks may create bills in Finance/Payables.
Reading a folder: Controllers may read Finance.

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
