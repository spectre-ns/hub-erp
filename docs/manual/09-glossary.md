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

**Autonomy.** How far a cog may go when it works on its own: read (look and report), propose (a person confirms each
action) or act (it acts through governed paths). It does not govern approvals at Gates (chapter 6.2).

**Available.** Stock that can be used: an OK lot, in stock, not expired, holding more than nothing, in a location that
counts as available (not quarantine, MRB, inspection, scrap or transit) (chapter 4.4).

**Back to the start.** Stopping the run an item is in and returning the item to the first state of its workflow, with
a reason (chapter 3.1).

**Baseline (count baseline).** The quantity a lot was received or last adjusted to, carried through splits and merges.
A free count is measured from it, so a run of small counts shares one tolerance (chapter 4.6).

**Bill of materials (of a release).** The Frames a release was compiled from, each with the hash of exactly the text
compiled and its source: the Frame file, or the SOP document and revision (chapter 7.3).

**Change order (CO, ECO).** The record that carries a change to SOPs and policies once they are documents: a draft
revision of each, the change requests it implements, and the Frame those revisions compile into (the staged Frame).
Approved by the quality manager; released by the release automation (chapter 7.9).

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

**Cog.** An AI team member: a member of the organization with roles, a profile (model, prompt, autonomy, duties), a
persona (what it follows) and approval criteria. A cog drafts, reviews, answers and acts through governed paths; its
model never signs (chapter 6).

**Cog board.** A cog's work as a kanban board: in its queue, working on, waiting on others, assigned to it, done,
stopped (chapter 6.3).

**Committee.** Cogs that do one piece of work together, each on its own. The compile-frame committee (qa-manager and
organization) reads SOP prose into rules; a rule counts only when 2 of 2 read it the same way (chapter 7.4).

**Compile.** Reading the Frames (or, once converted, the SOP documents) into rules, by their sentence shapes and, in
background work, by the compile-frame committee's reading of the prose; checking them; and comparing them with the
release in force. The result is a proposal (chapter 7.4).

**Condition.** A fact an approval criterion tests, from a closed list: the three-way match is within tolerance, the
supplier is approved, the risk is low, the risk is not high, the change is minor, no open nonconformance exists, the
item is known, the part is active, the variance is within tolerance, the bill of materials is resolved, the design
checks are clean, every requirement is accepted, its review passes, *n* adversarial reviews pass.

**Count tolerance.** "The count variance tolerance is 2 percent.": how far a free count may differ from a lot's
baseline before it needs an approved adjustment (chapter 4.6).

**Countersignature.** A person's signature required after a cog's signature before a step is complete ("countersigned
by a quality manager") (chapter 6.6).

**Decision.** A signature at a Gate: approve or reject, with its meaning, recorded with what was seen and the policy
in force (chapter 2.10).

**Document class.** controlled, template, working, evidence or external: what a document is for and whether it can
change (chapter 5.2).

**Due date (SLA).** "*Step* in *process* is due within *n* days": the date a Gate's decision is due, after which it can
be escalated.

**Duty.** A standing goal in a cog's profile ("Duty: ...") that it takes up once a day when the duty runner runs.

**Escalation (tick).** What happens to an overdue item when the escalation tick runs: people are added or informed as
the "Overdue items in ..." sentences say (chapter 3.1).

**Evidence.** Documents kept as proof and never changed: snapshots, cog reviews, CAD exports.

**Finding.** A problem a compile's checks report: an error blocks a release, a warning does not.

**Form layout.** How a kind of record looks and is filled in: sections, field rows, the start form, custom fields and
conditions; carried on a change order and released with it (chapter 7.12).

**Frame.** A policy document as text: a Markdown file with a header, whose rule sections the hub compiles into rules.
SOPs, policies, work instructions, the organization model, the cog profiles, the scopes and the personas are Frames
(chapter 7.1).

**Gate.** A step in a process where the item waits for a decision by a person, or by a cog within its criteria. The
hub calls the waiting state "Awaiting: *step*".

**Grant.** The approval a decision issues, which lets the automatic steps after it write beyond the run's own record
(post a journal, issue a purchase order, publish a document).

**Held revision.** A revision of an SOP or policy carried by a change order that has been approved through document
control and waits, in state Approved, for its change order to be released (chapter 7.9).

**Hold.** A stop put on an item by one of its approvers, with a reason; nothing can be decided until it is released.

**Item (archived).** The inventory record before parts and lots; readable, never filed or changed again (chapter 4.10).

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

**Model-read rule.** A rule the compile-frame committee read from SOP prose, shown to the reviewers as "read by ..."
with the words it quotes, and approved like any other change (chapter 7.4).

**Op.** A business process run under a policy release: its automatic steps, its Gates and its Track. "Ops" on a page
are the one-click processes and cog requests offered for that item (chapter 2.8).

**Organisational stop.** A run stopped because nobody the policy allows may take a step; only a policy change fixes it.

**Part.** One entry in the part master (PN-*n*): a purchased component or an assembly made from a released design
(chapter 4.2).

**Part category.** A tree of categories for parts (CAT-*n*); a structural category only groups others (chapter 4.2).

**Persona.** A Frame for a cog or a job role that layers scopes and Frames: what the cog follows, or the role's
required reading (chapter 7.1).

**Policy owner.** A member with the policy-owner role (bob, carol): reviews and releases policy changes (chapter 7.2).

**Policy release (policy version).** The compiled policy in force, identified by a digest and numbered "policy version
*n*". Releases never change; runs are pinned to the one in force when they started (chapter 7.3).

**Preparer rule.** Whoever started a run, or created its record, may never decide it on a route to a role or a cog
(chapter 3.1).

**Price break.** A supplier part's price from a given quantity up; a purchase request takes the highest break at or
under its quantity (chapter 4.2).

**Proposal.** The list of changes a compile (or Settings, or a change order) would make to the policy in force, each
decided by the policy owners before release (chapter 7.4).

**Quarantine.** Holding stock in a quarantine or MRB location, free for any member of staff; it leaves only through a
stock release or a nonconformance's disposition (chapter 4.6).

**Queue.** A cog's list of things asked of it, worked one at a time, oldest first, chats first (chapter 6.3).

**Rail.** See approval criteria.

**Record.** Any item the hub keeps: a bill, a nonconformance, a document. Each has a type, a number, a folder, a state
and fields (chapter 2.1).

**Required reading.** The Frames a person must read, from the personas of the roles they hold (chapter 7.1).

**Revision.** A new version of a controlled document approved through document control, which replaces the one in
force and makes it Obsolete (chapter 5.10).

**Routing sentence.** "Approvals in *process* ... go to *someone*": who a Gate goes to, tried in the order written.

**Run.** One pass of a process for one record, from submission to completion, with its Track.

**Scope.** A set of SOP and policy Frames that applies together (the company's, a department's), layered on wider
scopes; personas layer scopes (chapter 7.1).

**Seen digest.** A fingerprint of an item as a signer saw it; a decision on an item that changed since is refused.

**Segregation of duties.** "The *A* and the *B* of a *record* must be different people": who may not decide an item.
Binds cogs too.

**Settings.** The app that shows and edits the rules as data, as a per-person draft proposed to the policy owners
(chapter 7.7).

**SOP document.** A policy, SOP or work instruction kept as a controlled document from which the Frame is compiled,
once the Frames are converted (chapter 7.8).

**Staged Frame.** The Frame a change order's approved revisions compile into, staged on the change order as a proposal
before the change order is approved (chapter 7.9).

**Stale proposal (out of date).** A proposal a release left behind that can no longer be released: a compile of the
files, a draft of a Frame the release changed, or one on an older release. Its author redoes or withdraws it
(chapter 7.6).

**Stand-in.** A deterministic replacement for a cog's model, allowed only in development and tests.

**Step in.** A cog taking an approval that waits for people, when one of them (or a policy owner) asks and its criteria
allow (chapter 6.6).

**Stock location.** A place stock is held (LOC-*n*), in a tree, with a kind (store, receiving, inspection, quarantine,
MRB, scrap, production, transit); a **structural** location only groups others (chapter 4.3).

**Stock release.** The approval, by the quality manager, that returns a quarantined, ATTENTION or DAMAGED lot to OK
stock (REL-*n*, chapter 4.7.2).

**Storekeeper.** The role that keeps stock: moves, counts, splits, merges and marks lots and asks for adjustments
(dave and the stores cog) (chapter 4.1).

**Supplier part.** What can be ordered from one supplier for a part (SP-*n*): SKU, pack quantity, price breaks, lead
time (chapter 4.2).

**Track.** The append-only, chained record of every step of a run (chapter 2.10).

**Voided approval.** An approval that no longer counts because the item changed after it; the item is approved again
on what it says now (chapter 3.1).

**Widening.** A policy change that lets someone do more (a new permission, a new member, a new cog route or criterion,
a changed threshold or due date, a cog profile, persona, scope or committee change ...). It needs two policy owners
(chapter 7.4).

**Withdrawn.** The state of a draft its owner withdrew; it stays in the history, closed (chapter 2.4).

**WOPI.** The interface between the hub and the online editor (Collabora Online): the editor reads and saves files
through the hub.
