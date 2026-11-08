# 8. Glossary

**Acting as.** The name you choose in the top bar. The prototype has no password sign-in; everything you do is
recorded under that name (chapter 1.1).

**Adversarial review (lens).** One of several independent reviews a cog makes of an item before it may sign, each a
fresh read with one brief (compliance, consistency, impact or clarity), trying to find a reason not to approve. All
must pass; one failure stops it and binds for that version of the item (chapter 5.6).

**Agent run.** A cog working on its own on a task: its model plans and calls tools within the cog's autonomy and a
budget. Every step is recorded on the run page (chapter 5.8).

**Approval criteria (rail).** The sentences that say what a cog may approve on its own: the process, an amount limit,
conditions, running limits, a required review or adversarial reviews, and a countersignature. Outside them the cog
declines and the item goes to a person (chapter 5.6).

**Assignee.** Whoever a record is "with". For a Gate, the people (or cog) the step waits for; for a task, the person
or cog named in its Assignee field.

**Autonomy.** How far a cog may go when it works on its own: read (look and report), propose (a person confirms each
action) or act (it acts through governed paths). It does not govern approvals at Gates (chapter 5.2).

**Automation.** A job the hub runs on its own, with named steps recorded as they run: compile-frame and
release-change-order (chapter 6.10).

**Back to the start.** Stopping the run an item is in and returning the item to the first state of its workflow, with
a reason (chapter 3.1).

**Change order (CO, ECO).** The record that carries a change to SOPs and policies once they are documents: a draft
revision of each, the change requests it implements, and the Frame those revisions compile into (the staged Frame).
Approved by the quality manager; released by the release automation (chapter 6.9).

**Change request (CR).** A record that asks for a change and says why, approved by the quality manager and verified by
the requester (chapter 3.17).

**Chatter.** The timeline of messages, notes, notifications, decisions and history at the bottom of a record or
document page (chapter 2.6).

**Check-out.** Taking a document to work on it so that nobody else can check in a version meanwhile. An editing
session opened in the online editor is a shared check-out others may join (chapter 4.6).

**Child run.** A process run opened automatically by another run under its approval, for example a CAPA opened by a
nonconformance's disposition.

**Code cog.** An automatic step of a process done by code, not a model: record-filer, risk-scorer, invoice-extractor,
three-way-matcher, ledger-poster, journal-reverser, document-drafter, document-publisher, ncr-classifier, capa-opener,
ncr-opener, capa-drafter, effectiveness-scheduler, audit-checklist-drafter, document-renderer, notifier,
supplier-checker, po-issuer, receipt-checker, stock-booker, requirements-tracer, cad-exporter, bom-reconciler,
part-releaser, shortage-requester, frame-stager and others. Code cogs are not members.

**Cog.** An AI team member: a member of the organization with roles, a profile (model, prompt, the SOPs it follows,
autonomy, duties) and approval criteria. A cog drafts, reviews, answers and acts through governed paths; its model
never signs (chapter 5).

**Compile.** Reading the Frames (or, once converted, the SOP documents) into rules, checking them, and comparing them
with the release in force; the result is a proposal (chapter 6.4).

**Condition.** A fact an approval criterion tests, from a closed list: the three-way match is within tolerance, the
supplier is approved, the risk is low, the risk is not high, the change is minor, no open nonconformance exists, the
item is known, the bill of materials is resolved, the design checks are clean, every requirement is accepted, its
review passes, *n* adversarial reviews pass.

**Countersignature.** A person's signature required after a cog's signature before a step is complete ("countersigned
by a quality manager") (chapter 5.6).

**Decision.** A signature at a Gate: approve or reject, with its meaning, recorded with what was seen and the policy
in force (chapter 2.10).

**Document class.** controlled, template, working, evidence or external: what a document is for and whether it can
change (chapter 4.2).

**Due date (SLA).** "*Step* in *process* is due within *n* days": the date a Gate's decision is due, after which it can
be escalated.

**Duty.** A standing goal in a cog's profile ("Duty: ...") that it takes up once a day when the duty runner runs.

**Escalation (tick).** What happens to an overdue item when the escalation tick runs: people are added or informed as
the "Overdue items in ..." sentences say (chapter 3.1).

**Evidence.** Documents kept as proof and never changed: snapshots, cog reviews, CAD exports.

**Finding.** A problem a compile's checks report: an error blocks a release, a warning does not.

**Frame.** A policy document as text: a Markdown file with a header, whose rule sections the hub compiles into rules.
SOPs, policies, work instructions, the organization model and the cog profiles are Frames (chapter 6.1).

**Gate.** A step in a process where the item waits for a decision by a person, or by a cog within its criteria. The
hub calls the waiting state "Awaiting: *step*".

**Grant.** The approval a decision issues, which lets the automatic steps after it write beyond the run's own record
(post a journal, issue a purchase order, publish a document).

**Held revision.** A revision of an SOP or policy carried by a change order that has been approved through document
control and waits, in state Approved, for its change order to be released (chapter 6.9).

**Hold.** A stop put on an item by one of its approvers, with a reason; nothing can be decided until it is released.

**Lens.** See adversarial review.

**Meaning.** The words a signer writes to say what their signature confirms; stored with the signature.

**Member.** A person or a cog listed in the organization model, holding roles.

**Messenger.** The chat window in the bottom-right corner of every page (chapter 5.5).

**Model gate.** The hub's queue for model calls: the cogs share one local model, so calls take turns (chats first).

**Op.** A business process run under a policy release: its automatic steps, its Gates and its Track. "Ops" on a page
are the one-click processes and cog requests offered for that item (chapter 2.8).

**Organisational stop.** A run stopped because nobody the policy allows may take a step; only a policy change fixes it.

**Policy owner.** A member with the policy-owner role (bob, carol): reviews and releases policy changes (chapter 6.2).

**Policy release (policy version).** The compiled policy in force, identified by a digest and numbered "policy version
*n*". Releases never change; runs are pinned to the one in force when they started (chapter 6.3).

**Preparer rule.** Whoever started a run, or created its record, may never decide it on a route to a role or a cog
(chapter 3.1).

**Proposal.** The list of changes a compile (or Settings, or a change order) would make to the policy in force, each
decided by the policy owners before release (chapter 6.4).

**Queue.** A cog's list of things asked of it, worked one at a time, oldest first, chats first (chapter 5.3).

**Rail.** See approval criteria.

**Record.** Any item the hub keeps: a bill, a nonconformance, a document. Each has a type, a number, a folder, a state
and fields (chapter 2.1).

**Revision.** A new version of a controlled document approved through document control, which replaces the one in
force and makes it Obsolete (chapter 4.10).

**Routing sentence.** "Approvals in *process* ... go to *someone*": who a Gate goes to, tried in the order written.

**Run.** One pass of a process for one record, from submission to completion, with its Track.

**Seen digest.** A fingerprint of an item as a signer saw it; a decision on an item that changed since is refused.

**Segregation of duties.** "The *A* and the *B* of a *record* must be different people": who may not decide an item.
Binds cogs too.

**Settings.** The app that shows and edits the rules as data, as a per-person draft proposed to the policy owners
(chapter 6.7).

**SOP document.** A policy, SOP or work instruction kept as a controlled document from which the Frame is compiled,
once the Frames are converted (chapter 6.8).

**Staged Frame.** The Frame a change order's approved revisions compile into, staged on the change order as a proposal
before the change order is approved (chapter 6.9).

**Stand-in.** A deterministic replacement for a cog's model, allowed only in development and tests.

**Step in.** A cog taking an approval that waits for people, when one of them (or a policy owner) asks and its criteria
allow (chapter 5.6).

**Track.** The append-only, chained record of every step of a run (chapter 2.10).

**Widening.** A policy change that lets someone do more (a new permission, a new member, a new cog route or criterion,
a changed threshold or due date, a cog profile change ...). It needs two policy owners (chapter 6.4).

**WOPI.** The interface between the hub and the online editor (Collabora Online): the editor reads and saves files
through the hub.
