# 9. Glossary

**Acting as.** The name you choose in the top bar. The prototype has no password sign-in; everything you do is
recorded under that name (chapter 1.1).

**Adjustment (stock adjustment).** A request to set a lot to the quantity it should hold (ADJ-*n*), approved by the
controller, or by the purchasing assistant under 100 USD within the count tolerance; applied only to the quantity it
was checked on (chapter 4.7.1).

**Adversarial review (lens).** One of several independent reviews a cog makes of an item before it may sign, each a
fresh read with one brief (compliance, consistency, impact or clarity), trying to find a reason not to approve. All
must pass; one failure stops it and binds for that version of the item (chapter 6.6).

**Agent run.** A cog working on its own on a task: its model plans and calls tools within the cog's autonomy and a
budget. Every step is recorded on the run page (chapter 6.8).

**Approval criteria (rail).** The sentences that say what a cog may approve on its own: the process, an amount limit,
conditions, running limits, a required review or adversarial reviews, and a countersignature. Outside them the cog
declines and the item goes to a person (chapter 6.6).

**Assignee.** Whoever a record is "with". For a Gate, the people (or cog) the step waits for; for a task, the person
or cog named in its Assignee field.

**Automation.** A job the hub runs on its own, with named steps recorded as they run: compile-frame and
release-change-order (chapter 7.10).

**Autonomy.** How far a cog goes when it works on its own, derived for each action from the SOPs: it acts where a
permission or the step is its own, proposes where an SOP says a person confirms, and otherwise only reads. It does not
govern approvals at Gates (chapter 6.2).

**Available.** Stock that can be used: an OK lot, in stock, not expired, holding more than nothing, in a location that
counts as available (not quarantine, MRB, inspection, scrap or transit) (chapter 4.4).

**Back to the start.** Stopping the run an item is in and returning the item to the first state of its workflow, with
a reason (chapter 3.1).

**Baseline changed.** The flag an open change order gets when a related change order is released: it goes back to
revising, its decisions are stale, and it needs another review round (chapter 7.9).

**Baseline (count baseline).** The quantity a lot was received or last adjusted to, carried through splits and merges.
A free count is measured from it, so a run of small counts shares one tolerance (chapter 4.6).

**Bill of materials (of a release).** The Frames a release was compiled from, each with the hash of exactly the
text compiled and its source, the SOP document and revision (chapter 7.3).

**Chair.** The person named for a committee who intervenes when its decision pauses: decides in a paused member's
place, extends the due date, or returns the item (chapter 7.6).

**Change order (CO).** The record that carries every configuration change: prose revisions of SOPs and policies,
direct changes made in Settings and the editors, form layouts, record-type changes and workflows, and the change
requests it implements. It goes through review rounds; people decide its delta item by item; the quality manager signs
it; the release automation puts it in force (chapter 7.9).

**Change request (CR).** A record that asks for a change and says why, approved by the quality manager and verified by
the requester (chapter 3.17).

**Chatter.** The timeline of messages, notes, notifications, decisions and history at the bottom of a record or
document page (chapter 2.6).

**Check-out.** Taking a document to work on it so that nobody else can check in a version meanwhile. An editing
session opened in the online editor is a shared check-out others may join (chapter 5.6).

**Child run.** A process run opened automatically by another run under its approval, for example a CAPA opened by a
nonconformance's disposition.

**Code cog.** An automatic step of a process done by code, not a model: record-filer, risk-scorer, invoice-extractor,
three-way-matcher, ledger-poster, journal-reverser, document-drafter, document-publisher, ncr-classifier, capa-opener,
ncr-opener, capa-drafter, effectiveness-scheduler, audit-checklist-drafter, document-renderer, notifier,
supplier-checker, po-issuer, receipt-checker, stock-booker, stock-quarantiner, disposition-applier, adjustment-checker,
adjustment-applier, release-checker, stock-releaser, requirements-tracer, cad-exporter, bom-reconciler, part-releaser,
shortage-requester, frame-stager and others. Code cogs are not members.

**Cog.** An AI team member: a member of the organization with roles and groups, an identity profile (About, model,
persona), a context derived from the Roles and responsibilities tables, and approval criteria. A cog drafts, reviews,
answers and acts through governed paths; its model never signs, and it never signs a change order (chapter 6).

**Cog board.** A cog's work as a kanban board: in its queue, working on, waiting on others, assigned to it, done,
stopped (chapter 6.3).

**Committee.** A decision-making body defined on a group, with a chair, deciding by consensus: an item passes only
when every member agrees; one reasoned objection returns it; nobody stands aside. Used as a workflow gate, on a change
order's review panel, and as the compile-frame committee (chapter 7.6).

**Compile.** Reading the SOP documents into configuration: exactly by their sentence shapes, and, for changed prose,
by the compile-frame committee reading the diff; checking it; and comparing it with the release in force. On a
converted hub it happens only for a change order, as its delta (chapter 7.4).

**Compile-frame committee.** The committee of senior cogs (senior-organization and senior-qa-manager, chaired by
carol) that reads changed SOP prose into rules by consensus. It reads only where hosted models are switched on (chapter
7.4).

**Condition.** A fact an approval criterion tests, from a closed list: the three-way match is within tolerance, the
supplier is approved, the risk is low, the risk is not high, the change is minor, no open nonconformance exists, the
item is known, the part is active, the variance is within tolerance, the bill of materials is resolved, the design
checks are clean, every requirement is accepted, its review passes, *n* adversarial reviews pass.

**Configuration.** What the hub runs on, compiled from the SOPs and approved by people: members, roles, groups,
permissions, limits, routing, approval criteria, segregation of duties, duties, workflows, record types, form layouts,
cog profiles and personas. It changes only through a change order.

**Contributor.** Someone who made part of a change order: a revision's author, the maker of a direct change,
whoever accepted a suggestion, overrode an item or set the state map, its requester or creator. A contributor does not
review it or decide the items they made; a revision's author never signs it, and any other contributor signs only
alongside an independent approver (chapter 7.9).

**Count tolerance.** "The count variance tolerance is 2 percent.": how far a free count may differ from a lot's
baseline before it needs an approved adjustment (chapter 4.6).

**Countersignature.** A person's signature required after a cog's signature before a step is complete ("countersigned
by a quality manager") (chapter 6.6).

**Decision.** A signature at a Gate: approve or reject, with its meaning, recorded with what was seen and the policy
in force (chapter 2.10).

**Delta (configuration delta).** The list of configuration changes a change order would make against the release
in force, each an item saying where it came from (read from the prose, made directly, suggested by a reviewer), with
its warnings. It is what reviewers review and approvers decide (chapter 7.9).

**Development hub.** A hub started with `hubproto dev serve` from a worktree, on its own port and state, for
development and tests; never the hub at localhost:8080 (chapter 8.10).

**Direct change.** A change made in Settings, the workflow editor or another editor, recorded on a change order as a
delta item with origin "made directly". It is not written into the prose by the editor: the review round's qa-manager
cog drafts that (chapter 7.9).

**Divergence.** A line of an SOP that the configuration in force does not follow, accepted by an approver on a change
order (an override, a rejected item, a direct change that could not be written into the prose). Recorded in the
release, marked at the line, given to cogs beside the line, and answered by a change request to the SOP's owner (chapter
7.9).

**Document class.** controlled, template, working, evidence or external: what a document is for and whether it can
change (chapter 5.2).

**Due date (SLA).** "*Step* in *process* is due within *n* days": the date a Gate's decision is due, after which it can
be escalated.

**Duty.** A cog's standing goal, a `## Duties` sentence in the SOP that owns the work ("Every week the it-admin cog
..."), taken up every day, week or month when the duty runner runs (chapter 6.8).

**Escalation (tick).** What happens to an overdue item when the escalation tick runs: people are added or informed as
the "Overdue items in ..." sentences say (chapter 3.1).

**Evidence.** Documents kept as proof and never changed: snapshots, cog reviews, CAD exports.

**Finding.** A problem: a compile check's finding (an error stops the round, a warning does not), or a reviewer's
finding on a change order, blocking or advisory, which becomes a warning the approver accepts (chapter 7.9).

**Form layout.** How a kind of record looks and is filled in: sections, field rows, the start form, custom fields and
conditions; carried on a change order and released with it (chapter 7.12).

**Frame.** The text of one SOP, work instruction or policy as the hub compiles it: a header and Markdown text, compiled
from the controlled document in force once the SOPs are documents. SOPs, policies, work instructions, the organization
model, the cog profiles, the scopes and the personas are Frames (chapter 7.1).

**Freeze.** What sending a change order for approval does: the hub records hashes of everything it carries and holds
its drafts; any later change sends it back to revising and voids its decisions (chapter 7.9).

**Gate.** A step in a process where the item waits for a decision by a person, a cog within its criteria, or a
committee. The waiting status offers the transitions out of it as its buttons (chapter 3.1).

**Global transition.** A transition from any status except the done ones, such as Cancel (chapter 3.1).

**Grant.** The approval a decision issues, which lets the automatic steps after it write beyond the run's own record
(post a journal, issue a purchase order, publish a document).

**Group.** A named set of members, people and cogs, listed or defined by role, in the organization model. It may
carry permissions and be named in a Roles and responsibilities table; committees are defined on groups (chapter
7.6).

**Held revision.** A revision of an SOP or policy carried by a change order that was sent for approval: it takes no
new version until the change order is released or sent back. It is approved by the change order's signature (chapter
7.9).

**Hold.** A stop put on an item by one of its approvers, with a reason; nothing can be decided until it is released.

**Hosted models.** Models served outside the hub (Claude, from Anthropic). Off unless an administrator sets
`HUBPROTO_HOSTED_MODELS=on` with an `ANTHROPIC_API_KEY`; under the IT SOP, for the senior cogs' compiling only (chapter
8.4).

**Independent approver.** A person who contributed nothing to a change order and approved every item of its delta.
Contributors other than a revision's author may sign only alongside one (chapter 7.9).

**Item (archived).** The inventory record before parts and lots; readable, never filed or changed again (chapter 4.10).

**Junior cog.** A cog bundled with the local model qwen3:14b, in the junior-cogs group: chat, drafting, reviews and
approvals within its rails (chapter 6.12).

**Ledger (stock).** The events on a lot (received, moved, counted, adjusted, split, merged, quarantined, released,
scrapped, returned, sent for rework, status changed), each with the lot's resulting quantity, place and status, in the
chained log (chapter 4.4).

**Lens.** See adversarial review.

**Library folder.** A folder people added to the library: a subfolder with its parent's rights, or a top-level folder
a policy owner added with the rights of a policy folder (chapter 5.1).

**Lot (stock item).** A quantity of one part in one location (LOT-*n*), one per receipt line or per serial number,
with a status and a batch, serial number or expiry (chapter 4.4).

**Manufacturer part.** A manufacturer's part number for a part (MP-*n*): the approved manufacturer list (chapter 4.2).

**Meaning.** The words a signer writes to say what their signature confirms; stored with the signature.

**Member.** A person or a cog listed in the organization model, holding roles.

**Messenger.** The chat window in the bottom-right corner of every page (chapter 6.5).

**Model gate.** The hub's queue for model calls: the cogs share one local model, so calls take turns (chats first).

**Model-read rule.** A rule the compile-frame committee read from changed SOP prose, shown as "read by ..." with the
words it quotes, and decided like any other item (chapter 7.4).

**Op.** A business process run under a policy release: its automatic steps, its Gates and its Track. "Ops" on a page
are the one-click processes and cog requests offered for that item (chapter 2.8).

**Organisational stop.** A run stopped because nobody the policy allows may take a step; only a configuration change
on a change order fixes it.

**Override.** An approver changing the value of a delta item and approving it; the overrider becomes one of its
makers, and the configuration may then differ from the prose (a divergence) (chapter 7.9).

**Panel (review panel).** The reviewers of a change order's review round, people, cogs and committees, suggested by
the quality-assistant cog from the change-control SOP's review section and changed by the requester (chapter 7.9).

**Part.** One entry in the part master (PN-*n*): a purchased component or an assembly made from a released design
(chapter 4.2).

**Part category.** A tree of categories for parts (CAT-*n*); a structural category only groups others (chapter 4.2).

**Persona.** A behaviour document for cogs, shared by any number of them: tone, stance, lenses, review passes, when
it escalates, and its behaviour prompt (chapter 6.1).

**Policy owner.** A member with the policy-owner role (bob, carol): decides the items of change orders; both decide
a widening (chapter 7.2).

**Policy release (policy version).** The configuration in force, identified by a digest and numbered "policy version
*n*", with the texts it was compiled from and its divergences. Releases never change. An item follows the release in
force from its next move (chapter 7.3).

**Preparer rule.** Whoever started a run, or created its record, may never decide it on a route to a role or a cog
(chapter 3.1).

**Price break.** A supplier part's price from a given quantity up; a purchase request takes the highest break at or
under its quantity (chapter 4.2).

**Proposal.** The compiled form of a change: on a converted hub, the delta a change order stages; on a hub that kept
its Frame files, a list of changes the policy owners review and release (chapter 7.4, 7.5).

**Quarantine.** Holding stock in a quarantine or MRB location, free for any member of staff; it leaves only through a
stock release or a nonconformance's disposition (chapter 4.6).

**Question.** Something a compile reader, the round trip or a reviewer asks on a change order: ambiguous prose, a line
the committee read differently, a member unavailable, an unread rule, a removed status. Answered in the prose or marked
answered; open questions become warnings, and committee questions block approval (chapter 7.9).

**Queue.** A cog's list of things asked of it, worked one at a time, oldest first, chats first (chapter 6.3).

**Rail.** See approval criteria.

**Record.** Any item the hub keeps: a bill, a nonconformance, a document. Each has a type, a number, a folder, a state
and fields (chapter 2.1).

**Related change.** An open change order or approved change request that a released change order affects: by
definition (the same SOP, process, rule, record type, form or role; found by the hub) or by meaning (found by the
released change order's panel cogs). It is sent back to review (chapter 7.9).

**Required reading.** The documents a person must read: those whose Roles and responsibilities tables name one of
their roles or groups (chapter 7.15).

**Required training (training matrix).** A generated document in Quality/Training that lists, per role and group,
each document to read, its revision and the responsibility, and which cogs follow it; republished by releases that
change it (chapter 7.15).

**Review round.** One pass of a change order's review: compile, draft prose for direct changes, check, suggest the
panel, and the panel's reviews. Rounds repeat until the requester sends it for approval (chapter 7.9).

**Revision.** A new version of a controlled document approved through document control, which replaces the one in
force and makes it Obsolete (chapter 5.10).

**Roles and responsibilities table.** The table that ends every document (`| Role | Responsibilities |`). It defines
who must read the document: people's required reading and cogs' context (chapter 7.15).

**Round trip.** The check that the prose the qa-manager cog drafted for a direct change compiles back into the same
item; a failure is a question and a warning (chapter 7.9).

**Routing sentence.** "Approvals in *process* ... go to *someone*": who a Gate goes to, tried in the order written.

**Run.** One pass of a process for one record, from submission to completion, with its Track.

**Scope.** A set of SOP and policy Frames that applies together (the company's, a department's), layered on wider
scopes. The company scope is in every cog's context (chapter 7.1).

**Seen digest.** A fingerprint of an item as a signer saw it; a decision on an item that changed since is refused.

**Segregation of duties.** "The *A* and the *B* of a *record* must be different people": who may not decide an item.
Binds cogs too.

**Senior cog.** A cog bundled with the hosted model claude-opus-5-5, in the senior-cogs group. The two seniors are the
compile-frame committee (chapter 6.12).

**Settings.** The app that shows the rules as data and records each edit as a direct change on the person's settings
change order (chapter 7.7).

**SOP document.** A policy, SOP or work instruction kept as a controlled document from which the Frame is compiled,
once the Frames are converted (chapter 7.8).

**Stage.** Where a change order is in its loop: preparing, in review, revising, awaiting approval, approved,
released (chapter 7.9).

**Staged Frame.** The configuration a change order's compile stages on it, shown as its delta (chapter 7.9).

**Stand-in.** A deterministic replacement for a cog's model, allowed only in development and tests.

**State map.** A change order's answer to where items in a status it removes should go; applied at release, without
running rules, and recorded on each item (chapter 7.9).

**Status.** A place an item can be in a workflow graph, in the category to do, in progress or done (chapter 3.1).

**Step in.** A cog taking an approval that waits for people, when one of them (or a policy owner) asks and its criteria
allow (chapter 6.6).

**Stock location.** A place stock is held (LOC-*n*), in a tree, with a kind (store, receiving, inspection, quarantine,
MRB, scrap, production, transit); a **structural** location only groups others (chapter 4.3).

**Stock release.** The approval, by the quality manager, that returns a quarantined, ATTENTION or DAMAGED lot to OK
stock (REL-*n*, chapter 4.7.2).

**Storekeeper.** The role that keeps stock: moves, counts, splits, merges and marks lots and asks for adjustments
(dave and the stores cog) (chapter 4.1).

**Suggested role grant.** The hub's suggestion, on a change order, to give a member a role when the delta gives it
work its roles do not cover; never granted by itself (chapter 7.9).

**Suggestion (suggested change).** A reviewer's proposed extra sentence on a change order; accepted, it becomes a
delta item "suggested by a reviewer" (chapter 7.9).

**Supplier part.** What can be ordered from one supplier for a part (SP-*n*): SKU, pack quantity, price breaks, lead
time (chapter 4.2).

**Test evidence.** What the IT SOP's validation needs before a change order is approved: a test record (scripted
check, expected and actual result) for a high-risk item, a test note for a medium-risk one (chapter 7.16).

**Track.** The append-only, chained record of every step of a run (chapter 2.10).

**Training (of a cog).** The documents in a cog's context when it works; the release that put a revision there is its
training record. A cog acknowledges nothing (chapter 6.11).

**Transition.** A way from one status to another in a workflow graph, with its gate, checks, actions and fields
(chapter 3.1).

**Validation class.** High, medium or low risk, as the IT SOP's Validation section classes a change order's items,
with the evidence each needs (chapter 7.16).

**Voided approval.** An approval that no longer counts because the item changed after it; the item is approved again
on what it says now (chapter 3.1).

**Widening.** A change that lets someone do more (a new permission, member, group, committee, cog route or criterion,
a changed threshold or due date, a cog profile, persona or scope change, a lost gate, a bypassed mandatory gate ...). It
needs both policy owners who did not make it (chapter 7.4).

**Withdrawn.** The state of a draft its owner withdrew; it stays in the history, closed (chapter 2.4).

**WOPI.** The interface between the hub and the online editor (Collabora Online): the editor reads and saves files
through the hub.

**Workflow editor.** The page that changes a process's graph as a per-person draft carried on a change order
(chapter 7.13).
