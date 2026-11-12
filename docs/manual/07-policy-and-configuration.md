# 7. Policy and configuration

Who may do what, how items are routed, what a cog may approve and how each process runs are stated in prose, in the
SOPs, work instructions and policies. Those documents are the only source of truth. The hub never runs on prose: it
runs on **configuration** compiled from the prose and approved by people, frozen together with the text in a **policy
release**. A model reads prose only when a change is proposed, and only to propose configuration; no model is in the
path that enforces it (docs/design/change-control.md, section 1).

Every change to configuration goes through a **change order**: rules, workflows, record types and fields, form
layouts, cog profiles and personas. Every other path refuses and says to use one (7.5). This chapter explains what the
hub reads from the documents (7.1), releases (7.3), compiling (7.4), groups and committees (7.6), the Settings app
(7.7), the change-order workflow (7.9), the workflow editor (7.13), record types (7.14), the Roles and
responsibilities tables and required training (7.15), and the IT SOP that governs all of it (7.16).

## 7.1 Frames: the policy as sentences

A **Frame** is the text of one SOP, work instruction or policy as the hub compiles it: a short header (name, version,
scope, maintainer, what it inherits, the ISO clauses it covers) and Markdown text (hubproto/frames.py). Once the SOPs
are documents (7.8), which a new hub does at its first release, the Frame is compiled from the controlled document in
force, not from a file. The example company has an organization model, the cog profiles, the scopes, four personas, a
finance policy, a purchasing policy, an inventory policy, a design-control SOP, the ISO 9001 quality manual, twelve ISO
9001 SOPs (the IT SOP among them), and eight work instructions: 32 Frames in the release. A 33rd, the reference guide on
writing Frames, is read by people and is in no release (checked by running the code).

<!-- figure: c07-frame -->

The compiler reads rule sentences only inside these sections; everything else is prose, which the compile-frame
committee reads (7.4) (frames/reference/writing-policy-frames.frame.md; DEV-NOTES.md, "Sentence grammar"):

| Section | Example sentence | Becomes |
|---|---|---|
| `## Roles`, `## Members`, `## Folders` | `- carol (person): quality-manager, reviewer` | who is a member, with which roles; which folders exist |
| `## Groups` | `- senior-cogs: senior-organization and senior-qa-manager`; `- quality-managers: everyone holding quality-manager` | named sets of members, listed or by role (7.6) |
| `## Permissions` | "Finance clerks may create bills in Finance/Payables." "Quality managers may not create bills in Finance/Payables." "Comment on anything in Quality, when a document controller confirms." | who may create, read, update, approve or delete which kind of record in which folder; "may not" wins; "when a *role* confirms" makes a cog propose (chapter 6.2). A role, a group or a cog by name may be the subject |
| `## Thresholds` | "The match tolerance is 2 percent." | a named limit the automatic steps read |
| `## Routing` | "Approvals in purchase-to-pay over 10,000 USD go to the controller." "Approve the bill in purchase-to-pay is due within 5 days." "Overdue items in purchase-to-pay are reported to the policy owner." | who a Gate goes to, in order, with conditions; due dates; escalation. An amount may be written "over 10,000 USD or the same in EUR" |
| `## Approval criteria` | "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved." | a cog's rail (chapter 6.6) |
| `## Segregation of duties` | "The preparer and the approver of a bill must be different people." "A cog's work is signed by a person." | who is kept from deciding |
| `## Duties` | "Every week the it-admin cog reviews the open change orders that touch permissions, record types or forms and comments on each what it found." | a cog's standing duty, every day, week or month (chapter 6.8) |
| `## Process: <name>` | numbered steps ("1. [finance-clerk] Extract the bill (invoice-extractor)"), or statuses and transitions ("Statuses: Open (to do), ..." "From Open to Awaiting disposition: automatically, after ...") | a workflow graph (chapter 3.1, "Processes as graphs") |
| `## Cog: <name>` | `About:`, `Model:`, `Persona:` (and optionally `Prompt from:`, `References:`) | a cog's identity (chapter 6.1) |
| `## Persona: <name>` | `Tone:`, `Stance:`, `Lenses:`, `Reviews: 3 adversarial passes`, `Escalates:`, `Behaviour:` | how the cogs that share it behave (chapter 6.1) |
| `## Committee: <name>` | `Group: senior-cogs` `Chair: carol` `Purpose: ...` | a committee on a group, deciding by consensus (7.6) |
| `## Scope: <name>` | `Level: department` `Layers on: company` `Compiles: ncr-sop, capa-sop, ...` | a scope (below) |
| `## Validation` (IT SOP) | "Changes to routing, due dates, processes ... are medium risk." "High-risk changes need a test record." | the validation classes of change orders (7.16) |
| `## Roles and responsibilities` | `\| Quality managers \| Disposition nonconformances; ... \|` | who must read the document, people and cogs (7.15) |

Every rule in a release cites the sentence, Frame and line it came from; that is what the **Why?** answers, the inbox
card and the decision records quote. A sentence in a rule section that matches no shape is an error that blocks the
release, unless the compile-frame committee read it (7.4); a changed rule-like sentence that nobody read becomes a
blocking question instead (7.9).

Old profile and persona lines are errors now: a `## Cog:` profile with `Prompt:`, `Lenses:`, `Autonomy:`, `Follows:`,
`Reviews:` or `Duty:`, and a `## Persona: x (role)` or `(cog)` heading, each block the release with a note saying where
that information lives now (hubproto/extract.py; chapter 6.1).

Narrative-only Frames (`compile: none`: the quality manual and the work instructions) give the compiler no rule
sentences; their Roles and responsibilities table is still read, and cogs read them as procedures.

### Scopes

The **scopes** Frame (frames/company/scopes.frame.md) names each scope, its level (company, site, department or
process), the scopes it layers on and the Frames it compiles. The example company's scopes are company (org-model,
quality-manual, tasks-sop), policy, quality, finance, purchasing, inventory, engineering and **it** (it-sop). The
company scope is in every cog's context (chapter 6.11). Scopes no longer decide what a cog follows or what a person
must read: the Roles and responsibilities tables do (7.15). The compiler checks that each scope compiles Frames of the
release and layers only on existing, wider scopes with no cycle, and warns about a Frame in no scope
(hubproto/guards.py, `scope_guard`).

The scopes Frame says later layers "may only narrow what earlier ones allow". The hub does not check that: every rule
of every Frame in the release applies to everyone (read the code).

**Where it shows.** **Scopes** (`/scopes`, in the Policy app) lists each scope with its Frames. A Frame's page and an
SOP document's page have a card **Depends and depended on**. A change order's page shows **What it affects**: the
Frames it changes, their scopes, and the cogs and roles that read them (7.9).

## 7.2 Policy owners

Members with the role **policy-owner** (bob and carol) decide the items of a change order's delta: one policy owner, or
another approver of change orders, for most items; **both policy owners who did not make it** for a widening (7.4,
7.9). They see a **Policy** group in the rail and the **Policy** app. On a hub whose SOPs are documents, which is every
new hub, no policy owner releases anything directly: a change comes into force only when its change order is approved
and released (7.5).

## 7.3 Policy versions (releases)

A release is the compiled configuration: members, roles, groups, folders, permissions, thresholds, routing, approval
criteria, segregation of duties, duties, cog profiles, personas, scopes, committees, validation classes, every
process's workflow graph and its layout. It is identified by a digest of its content and shown as "policy version *n*".
Releases are never changed; a change is a new release (hubproto/release.py).

**Work in flight follows the release in force from its next move.** An item keeps its status when a release comes into
force. From its next move (a decision, a retry, a reassignment, an escalation, a cog's turn) it follows the release in
force at that moment; a Gate already waiting keeps its people until then (chapter 3.1, "When the configuration
changes"). A status a release removes is answered by the change order's state map (7.9).

**Its bill of materials** lists every Frame it was compiled from, with the hash of exactly the text compiled and that
text's source, the SOP document and its revision. A release made by a change order records the change order, the
**divergences** it accepted (7.9, "Divergences") and, on later releases, which of them are resolved. After the first
start the scopes page and the **Depends and depended on** cards still say "the Frame file (the SOPs are not documents
yet)" until the first change order is released, because that is the source the first release recorded (checked by
running the code; chapter 10, X5).

**Policy versions** (rail, policy owners; or `/releases`) lists every version, newest first, with when it took effect,
who or which change order released it, a **What changed** link and its digest, then the release in force in sentences:
**Routing in effect**, **Automatic approvals**, **Processes** and **Thresholds**. **A version's page** shows what
changed as sentences (**Changed**, **Added**, **Removed**, each with the Frame and line and every decision with its
meaning), its divergences ("Divergences *n*"), and the Frames in the version.

Two more pages are for policy owners: **People and roles** (`/admin`) and **Conformance** (the ISO 9001 clauses and the
Frames that claim them, and the open divergences: a count and a table of divergence, SOP line, in force, accepted by,
change request and status). **Policies** (`/frames`) says "The rules are compiled from 33 SOP and policy documents.
Change a rule by revising its SOP on a change order." and lists the Frames. A Frame's page shows its text with line
numbers, so a citation such as "line 34" matches, and a marker under any line that differs from what is in force.

## 7.4 Compiling

Compiling reads SOP text into rules, runs the checks and compares the result with the release in force. On a hub
whose SOPs are documents it happens only for a change order, as the first step of its review round (7.9), and when
another release comes into force (every open change order is compiled again). The result is staged on the change order
as its **delta** (hubproto/compile.py, hubproto/guards.py, hubproto/changecontrol.py).

**The fast path, by the grammar.** Sentences in the shapes of 7.1 are read exactly, with no model.

**Prose, by the compile-frame committee.** The rest of the prose is read by the compile-frame committee, defined in the
cog profiles on the **senior-cogs** group (the senior-organization and senior-qa-manager cogs, both bundled with
`claude-opus-5-5`), chaired by carol, deciding by consensus (7.6; hubproto/llm_extract.py, `CommitteeExtractor`):

* **Reading is diff-based.** An SOP whose text equals the one in force keeps that release's rules, with no model call.
  For a changed SOP each member gets the unified diff with line numbers and the rules in force for it (and for the
  processes it touches), and returns the rules to add, remove or change, each with its line and exact quote, and
  **questions** where the prose is ambiguous. A large diff is read in parts sized to the model's window; a new SOP is
  read whole. The SOP text is fenced as data, not instructions.
* **A reading may touch only changed lines.** Anything else is an `outside-diff` question. A reading that adds or drops
  a "not" gets a warning.
* **Consensus.** A rule goes in, or a rule in force goes out, only when every member reads it the same way; it is marked
  "read by the compile-frame committee (senior-organization, senior-qa-manager: 2 of 2 agree)". A line the members read
  differently is a blocking `disagreement` question: "Line *n*, “...”: X read: ...; Y read: ...: which is meant?". Each
  member reads from its own perspective (its About text, persona and roles; `perspective_of`).
* **A member without a model** makes a blocking `member-unavailable` question: "The compile-frame committee cannot
  read the prose of *frame*: the *cog* cog is not available (*why*), and the committee reads by consensus, so its
  chair, carol, intervenes. Make its model available here, or change the committee or the cog's model through a change
  order." Nothing is read that round, and no junior cog reads in its place.
* **A changed rule-like sentence nobody read** is a blocking `unread-rule` question, also when no model reads prose.

The seniors' model is hosted, so the committee reads only where an administrator has switched hosted models on
(`HUBPROTO_HOSTED_MODELS=on` with an `ANTHROPIC_API_KEY`, chapter 8.4; the IT SOP, 7.16). With hosted models off, which
is the default, **no model reads prose at all**: the compile step says "sentence shapes only: the compile-frame
committee cannot reach consensus without senior-organization, senior-qa-manager (not available here); its chair,
carol, intervenes", and the change order shows a box "The compile-frame committee cannot read here" with **Report it to
the system administrators**, which files an incident task (checked by running the code). Only the chair or a system
administrator may answer a committee question, never a contributor of the change order (hubproto/changecontrol.py,
`answer_question`). `HUBPROTO_EXTRACTOR=convention` switches model reading off; `HUBPROTO_EXTRACTOR_MODEL` is used only
when a release has no compile-frame committee (chapter 8.3).

**The checks ("Guards").** **grammar** (a rule-like sentence nobody could read: an error); **schema** and **grounding**
(every rule is well formed and its quoted words are at the line it cites); **consistency** (a route to a role nobody
holds; a cog gate without approval criteria; an undefined committee; a group listing a non-member; a Roles and
responsibilities table that names a role nobody holds or misses a role the SOP gives a part, 7.15; and the scope
checks); **model** (every rule a model read, shown with its words); **drift** (what changed against the release in
force). Errors stop the review round (7.9).

**Widenings.** A change needs one approver, or both policy owners who did not make it when it is a **widening**,
something that lets someone do more (hubproto/guards.py, `_widens`, `_widens_over`):

| Widening (both policy owners) | Not a widening (one approver) |
|---|---|
| a new "may" permission; removing a "may not"; any change to an existing permission | removing a "may"; adding a "may not" |
| a new member, a member gaining a role, a new group, a group gaining members or roles | a member losing a role; removing a group or a committee |
| a new committee, duty, cog profile, persona or scope; any change to one | |
| a route that now goes to someone else or informs instead of assigning, except work moving from a cog to a person or role | a new route to a role or a person; removing a route |
| a new approval criterion for a cog; a criterion losing a condition, a countersignature or a running limit, a raised limit, fewer adversarial reviews | a criterion gaining a condition or a lower limit; removing a criterion |
| a new threshold; any change to a threshold's value, up or down; any change to a due date | removing a threshold; adding or removing a due date |
| removing or changing a segregation-of-duties sentence | adding one |
| a process losing a person, cog or committee gate, or filing in another folder; in the workflow editor, a mandatory gate bypassed (7.13) | other changes to a process |
| a new, changed or removed validation class | a Roles and responsibilities row added or removed |
| the wording of a document a cog follows | the wording of other documents |

(Ran the code: lowering the purchase approval threshold from 10,000 to 8,000 USD was a widening, "it widens
authority, so both policy owners must approve it". Chapter 10.9 lists where the shape of a rule, not what it lets
people do, decides; for example, editing a workflow so that a gate goes to a cog, or removing an approval step, is not
a widening unless the editor's bypass check catches it.)

**Validation classes.** The IT SOP's `## Validation` section compiles into the release as classes: high risk
(widenings; permissions, approval criteria, segregation of duties, members, roles, groups, committees, cog profiles,
personas; record types and workflow gates), medium (routing, due dates, processes, thresholds, required fields,
duties, folders, scopes, responsibilities) and low (form layouts, wording) (hubproto/validation.py). What each class
needs before a change order may be approved is in 7.16.

## 7.5 No configuration change outside a change order

Once the SOPs are documents, every path that would change configuration outside a change order refuses with the
sentence "Configuration changes only through a change order: open one under Quality > Change orders."
(hubproto/closure.py, `CO_ROUTE`). Checked by running the code:

| Path | What it says now |
|---|---|
| `hubproto compile`, `hubproto bootstrap-release --force` | "The SOPs are documents: the Frame is compiled from them only for a change order, which stages it from the revisions it carries. Nothing was compiled." |
| `hubproto release <n>` of a proposal no change order staged | "proposal #*n* was not staged by a change order: it cannot come into force now that the SOPs are documents." |
| `hubproto release <n>` of a staged proposal | "proposal #*n* is staged by CO-...: it comes into force with its revisions when CO-... is approved and released, not on its own." |
| `hubproto review <n>` of an unstaged proposal | refused, nothing recorded |
| `hubproto seed <profile>` | "the SOPs are documents, so a seed profile's Frames would not be used" |
| Editing a Frame in the hub (`/frame-draft`) | "The ncr-sop Frame is compiled from DOC-0013: revise that document on a change order." |
| A Frame's page | "This Frame is compiled from DOC-*n*. It is not edited here: change the document through a change order, and the Frame follows when the change order is released." |
| Settings, the workflow editor, the forms editor, the record types page, a cog's **Change model**, **Add someone** | the change is recorded on a change order as a direct change (7.7, 7.12 to 7.14; chapter 6.10) |
| `hubproto record-types carry CO-...` | carries a record-type change on that change order (7.14) |
| `hubproto config propose FILE.json`, a change order's **Propose a configuration file (JSON)** | carries what an edited export changes on a change order, by the paths above; refused with every reason and nothing carried when any part cannot be (7.14) |

An edit to a `.frame.md` file on disk is not policy: `hubproto status --check` reports it ("they are not used: the
Frame is compiled from the SOP documents") and exits 1. So does a `config/record-types.json` that differs from the
record types in force, and a `links.json` or `retention.json` that differs from the link vocabulary or retention classes in
force ("it is not used"; docs/design/config-paths.md).

**The first release converts the SOPs.** However the first release is made (the container's first start, `hubproto
bootstrap-release`, or `hubproto dev serve`), the hub converts the SOPs to documents at once (hubproto/closure.py,
`after_release`; hubproto/sopdocs.py). `bootstrap-release` prints "the SOPs are documents now (33): from here on
configuration changes only through a change order: open one under Quality > Change orders". If converting is blocked,
it exits 1 with what blocks it ("the SOPs were not converted to documents, so changes are not under change control
yet: ...") and running it again finishes the job. `hubproto status --check` also exits 1 on a released hub whose SOPs
are not documents.

**Hubs that kept their Frame files.** `bootstrap-release --keep-frame-files REASON` opts out; the reason is recorded in
`state/frame-files-kept.json` and `hubproto status` says "the Frame files are the source (kept by ...)". Only on such a
hub do the older ways work: editing a Frame in the hub (**Edit this policy**, **Save and check the change**,
**Propose for release**), proposals reviewed and released by the policy owners in the web pages or with `hubproto
review` and `hubproto release` (two owners for widenings), and Settings drafts proposed to the policy owners. A
release there overwrites the Frame files with the released text and marks out of date any open proposal it leaves
behind. `hubproto sops convert --as PERSON` ends the opt-out. This manual describes converted hubs from here on.

<!-- figure: c07-proposal -->

## 7.6 Groups and committees

A **group** is a named set of members, people and cogs, in the organization model's `## Groups` section
(hubproto/groups.py). It is listed ("- change-board: dave, alice and it-admin"), defined by role ("- quality-managers:
everyone holding quality-manager", which follows role changes), or both ("dave and everyone holding reviewer").

* A group may **carry permissions**, as a role does: "The quality-board group may ...", or a plural that names a group
  ("Administrators may ..."). Routes and Roles and responsibilities rows may name a group. Permission checks consider a
  member's roles, groups and name, and **Why?** says "(through the *x* group)" (hubproto/authz.py).
* The example company has two groups: **senior-cogs** (senior-organization, senior-qa-manager) and **junior-cogs**
  (ap-reviewer, doc-reviewer, quality-assistant, qa-manager, organization, it-admin, management). No permission names
  either; senior-cogs is what the compile-frame committee is defined on, and the Roles and responsibilities rows that
  name it put the cog profiles and the IT SOP in the seniors' context. The purchasing-assistant and stores cogs are in
  neither group (chapter 10.9).

A **committee** is defined on a group, with a person as its **chair** and a purpose (`## Committee: <name>`, `Group:`,
`Chair:`, `Purpose:`). It decides by **consensus**:

* An item passes only when every member agrees on the same way forward. One objection with a reason returns it at once.
  There is no standing aside and nothing passes by default. Each vote is a signature (hubproto/runner.py,
  `_committee_gate`, `_committee_tally`).
* A cog member agrees only within its approval criteria; otherwise its vote **pauses**. A member is also paused when it
  is not available, is kept out by segregation of duties, is late, or when members chose different ways forward.
* A pause goes to the **chair**, who intervenes: decides in the paused member's place (recorded as an intervention
  with a meaning), extends the due date (late members and paused cogs are asked again), or returns the item. Every
  intervention needs a meaning ("An intervention needs its meaning: say why you intervene."). When the chair is the
  member in question or is kept out, the pause goes to whoever the process's overdue rule names; with nobody, the run
  fails ("nobody can intervene") (`intervene`).

Committees are used as a **gate** in a workflow ("the change-board committee decides", chapter 3.1), as **reviewers**
on a change order's panel (a review sentence naming "the *x* committee" puts every member on the panel, 7.9), and as
the **compile-frame committee** (7.4). The guards check that a committee's group exists and is not empty, that its
chair is a person of the organization, and that every compile-frame member is a cog (hubproto/guards.py,
`committee_guard`).

**Settings > Groups and committees** (`/settings/groups`) lists the groups (members now, the permissions each carries,
where it is stated) and the committees (group, chair, members now, where stated), with a form to add or change each.
Each edit is a direct change on your settings change order (7.7); "A group change is a widening: both policy owners
approve it." The page is in the Settings page's own list of sections, not in the Settings app's sidebar.

<!-- figure: c07-settings-groups -->

## 7.7 The Settings app

**Settings** shows every rule the hub enforces as data, and lets members who are people change them without writing
sentences (hubproto/settings.py; hubproto/shell/settings_ui.py). Cogs, and anyone watching a cog, can read the settings
but not edit them ("Only people who are members change the settings (a cog proposes changes with its tools).").

<!-- figure: c07-settings -->

| Section | What it shows and edits |
|---|---|
| **Your changes** | your edits (each in a sentence), **Check**, **Discard my draft**, and **Put on my change order** |
| **People and roles** | roles, members with their roles, folders |
| **Groups and committees** | 7.6 |
| **Permissions** | every permission as a sentence, with where it came from; add or remove one |
| **Limits and due dates** | thresholds and due dates per step |
| **Cog rails** | for each cog its profile (**Model**, **Persona**, **About** only: "What it follows, what it may do and its duties come from the SOPs that name it or its roles") and its rails (limit and currency, at most a day or a month, countersigned by, adversarial reviews, only when) |
| **Segregation of duties** | each rule; add or remove one |
| **Workflows and routing** | each process's workflow as it is in force, drawn, with **Open in the workflow editor** (7.13); routing and due dates read-only |

Forms are no longer a Settings section; they are at **Forms** (`/forms`), in the Settings app's bar (7.12).

**Settings writes no prose.** Each edit is a **direct change**: a delta item with origin "made directly" on your
settings change order, which the review round's qa-manager cog writes into the SOP it belongs to (7.9).

**How to change a setting** (any member who is a person)

1. Open **Settings** and the section. Change the value in the row's form, or use the add form, and press its button.
   The status line repeats the change as a sentence. The edit goes into your own draft; nothing changes for anyone
   else.
2. Open **Your changes** and press **Check** (the same checks as a compile: "No problem stops a release", or "These
   stop a release" with the problems).
3. Press **Put on my change order** (with a **Why**). Each edit becomes a direct change on your open settings change
   order, one you requested from Settings that is still being prepared, reviewed or revised; if you have none, one is
   created in Quality/Changes titled "Policy changes by *you*". The status line says "Recorded on CO-... as *n* direct
   change(s). Request review there: the round writes the prose into the SOPs and asks the panel." No open change order
   is needed beforehand (checked by running the code).
4. Each section then shows **Pending on change orders**: "Not in force yet: each comes into force when its change order
   is released." A later edit of the same rule replaces the earlier one; an edit that restores the rule in force is
   dropped.

If the policy in force changes while you are editing, your draft is **stale** ("the policy changed since you started
these edits; discard your draft and start again"). A change order past revising refuses new direct changes ("its
changes are fixed now").

A rail with running limits ("at most 5 approvals a day", "at most 20,000 USD a month") is enforced from the cog's own
signed approvals of that process (tests/test_settings.py).

## 7.8 SOPs and policies as documents

**Each policy, SOP and work instruction is a controlled document in the library** (a .docx, edited in Collabora like
any other), and the Frame the hub enforces is compiled from the document in force (hubproto/sopdocs.py). A new hub is
converted at its first release (7.5). In the example company that gives 33 documents: 13 SOPs and 8 work instructions
in Quality/Controlled Documents; 7 policies, 4 personas and the guide in Policy (checked by running the code). Each
heading is a heading and each line its own paragraph, so a citation such as "document-control-sop line 16" still
points at the same sentence.

* **An SOP or policy document in force** shows "Compiled into the Frame ..." with **Revise on a change order** for
  those who may revise it: its owner, its author, whoever created it, a document controller or a policy owner
  (hubproto/eco.py, `may_change_sop`). Everyone else sees why they may not.
* A line where the configuration in force differs from the prose carries a marker (7.9, "Divergences").
* `hubproto sops status` lists each SOP with its document in force and any revision under way, with its change order.

Converted documents are owned by their Frame's maintainer; the organization model, the cog profiles and the tasks SOP
by "operations", which is not a member (chapter 10, E6).

## 7.9 Change requests and change orders

The change-control SOP separates two records (change-control-sop):

* A **change request** (CR) asks for a change and says why. The quality manager approves it and its requester verifies
  it (chapter 3.17). It changes nothing by itself. A change request that a change order implements is verified only
  once that change order is released ("Verify waits until the change order implementing CR-... is released")
  (hubproto/runner.py, `_change_request_waits`).
* A **change order** (CO) carries the change and is the only way configuration changes. The object reviewed and
  approved on it is the **configuration delta**: the list of configuration changes it would make against the release
  in force, each saying where it came from.

<!-- figure: diagram-co-loop -->

This section follows a change order from start to finish. It was checked by running the code on a development hub
(chapter 8.10) with the demo data, cogs answering with the stand-in and hosted models off, at `bf3602d` and again, for
the approval rule, at `2c99c4d`: alice's demo change order lowering the ap-reviewer's limit (CO-2026-0001), dave's
Settings change order (CO-2026-0002: a lower purchase approval threshold, a tighter match tolerance, and a change-board
group and committee), and bob's workflow change (CO-2026-0003: nonconformance dispositions by the change board).

### Stages

A change order's **stage** tracks the loop; its record state follows the change-order process (hubproto/changecontrol.py,
`STAGES`; hubproto/shell/change_order_ui.py). The page opens with **Where CO-... stands**: six steps with the current
one marked, and a **Next:** line saying what to do.

| Stage | On the page | Record state |
|---|---|---|
| preparing | **Preparing**: changes carried | Open |
| in-review | **In review (round *n*)**: compile, panel, reviews | Open |
| revising | **Revising**: answer, accept, reject | Open |
| awaiting-approval | **Awaiting approval**: items decided, Gate signed | Awaiting: Approve the change order |
| approved | **Approved**: release under way | Approved |
| released | **Released**: in force | Released |

A rejected Gate sends the change order back to revising ("*name* rejected its approval: ... It is back to revising"). A
Gate nobody can take leaves it in state Failed, back at revising.

<!-- figure: c07-co-stage -->

### Prepare and make the changes

**How to prepare a change order** (any member of staff: "Staff may create change-orders in Quality/Changes")

1. **Quality** > **Change orders**. Under **New change order** give a **Title\***, say **What it changes and why**,
   and tick the approved change requests it implements. Press **Prepare the change order**. It is numbered
   CO-*year*-*nnnn* and filed in Quality/Changes; you are its requester.
2. Carry what it changes (below). You can also start from an SOP's page (**Revise on a change order**), from Settings
   (7.7), the workflow editor (7.13), the forms editor (7.12) or the record types page (7.14).

A change order may carry, mixed:

* **Prose revisions** of SOPs, policies, the organization model, the cog profiles or personas: a draft revision with
  the next revision letter, edited online. **Add an SOP or policy** is shown to the requester and document controllers
  while the change order can be changed: it lists the documents in force you may read; one you may not revise is listed
  with the reason ("only bob, its author, a document controller or a policy owner revises DOC-0009"); **Start its
  revision here** starts the draft. If you may revise none, **Ask the owner to revise** sends them a message. One
  draft per SOP: one already being revised elsewhere is refused ("is already being revised (DOC-*n*, on CO-...)").
* **Direct changes**: edits made in Settings, the workflow editor or other editors. Each is a delta item with origin
  "made directly" (who, when, what). The **Direct changes** table shows each with **Made by**, **When** and **In the
  prose**: "not in the prose yet: the next round drafts it into *sop*", then "stated in *sop* line *n* (qa-manager)" or
  the chip **does not read back**.
* **Data-model and presentation changes**: form layouts (7.12), record-type changes (7.14) and workflow layouts.

Only the requester or a document controller changes what a change order carries, and only while it is preparing, in
review or revising.

### A review round

**How to request review** (the requester or a document controller): press **Request review** (later **Request review
again**). Under **Choose the panel yourself** you may take "the reviewers the quality-assistant cog suggests from the
change-control SOP (with their reasons)" or "only the reviewers ticked below". The round runs as the **compile-frame**
automation, an Op whose steps are recorded on the change order and on the Automation page and can be retried (7.10):

1. **Collect the carried drafts and direct changes.**
2. **Compile the Frame.** The drafts are compiled as they stand against the release in force (7.4). A later round
   compiles again only when something changed ("nothing changed since the last compile: not compiled again").
3. **Draft prose for direct changes.** The **qa-manager** cog writes, into the carried revision of the SOP each direct
   change belongs to, the prose that states it, adding Roles and responsibilities rows where the change gives someone
   a part (hubproto/prose_drafting.py). The revision is started at the requester's request, must be one the requester
   may revise, and is the qa-manager's: its author and first version are the cog's, and the requester is recorded as
   having started it. The drafted text is then compiled and must give the same item back (the **round trip**). An item
   that could not be written, does not read back, or reads back with extra rules raises a `round-trip` question and
   keeps the warning "prose does not read back".
4. **Check the guards.** Errors stop the round ("round 1 stopped (the compiled delta has problems that stop it: ...):
   fix that, then request review again").
5. **Stage it on the change order.**
6. **Suggest the panel.** The **quality-assistant** cog reads the change-control SOP's **Review of changes** section
   with the delta and proposes reviewers, each with its reason. The section says: "A change that touches permissions,
   record types or forms is reviewed by the it-admin cog. A change to roles, limits or segregation of duties is
   reviewed by the management cog. A change to a quality process is reviewed by the qa-manager cog and the
   quality-assistant cog. A change to a cog profile or a persona is reviewed by the it-admin cog and the qa-manager
   cog." The stand-in applies those sentences literally. A contributor of the change order is never asked ("a
   contributor: not asked"); a name that is not a member is shown, not asked. The panel is suggested again every round.
7. **Queue the panel's reviews.** A cog reviews from its queue, a person from their inbox ("Review CO-...").
8. **Tell the people involved.**

The round also adds the hub's own **suggested role grants**: when the delta gives a member work its roles do not cover,
"X is given work at *step* in *process* that its roles do not cover: grant it the *role* role? (a suggestion: never
granted by itself)" (hubproto/changecontrol.py, `role_grant_suggestions`).

<!-- figure: c07-co-round -->

**The panel.** The requester or a document controller may **Add a reviewer** (a cog or a person, with **Why (shown to
them)**; "They are asked at once") or take one off. A reviewer who already returned results stays on the panel for
that round. A contributor is refused ("Segregation of duties: carol contributed to CO-2026-0001 and does not review
it", checked by running the code). Leaving out a reviewer the SOP suggests is a warning on the change order (code
`panel`). A committee on the panel decides by consensus (7.6): it is shown as agreed, objected, waiting or paused, its
members are due in 5 days, its chair may act in a late member's place, and a committee that has not agreed blocks
sending. The SOP's review section names no committee, so none is on a panel unless it is changed to name one.

**What reviewers return.** Each reviewer posts **findings** (blocking or advisory), **questions**, and **suggested
changes** (an SOP, a section and a sentence). A person uses **Your review of round *n*** and **Post my review**; a cog
answers through its queue. A finding stands until that reviewer's next review leaves it out. In round 2 and later, a
reviewer is given only what is new or changed since its last review, plus its earlier findings ("Since your review in
round *n*, only these items are new or changed"). With the stand-in, a cog's review is a placeholder ("3 item(s) looked
at by management's stand-in (no model): nothing to say").

### Revise

When the panel has reviewed, the change order is **Revising**, and the requester's inbox says "Answer *n* question(s)"
and "Accept or reject *n* suggested change(s)".

* **Answer questions in the prose first.** The next compile closes a compiler question that no longer arises ("no
  longer arises at line *n*"). Then **Answer it** with **Your answer\*** and **The line of the prose that says it**,
  and **Mark answered** (the requester, a document controller or a contributor; committee questions only the chair or
  a system administrator).
* **Accept or reject suggestions.** **Accept** makes a suggestion that carries a rule a delta item with origin
  "suggested by a reviewer" (suggested by the reviewer, accepted by you): "Accepted: it is a delta item now (suggested
  by dave); request review again to have it written into the prose." A suggestion in words only must be written into
  the prose yourself. **Reject it** asks **Why not**.
* **Request review again.** Rounds repeat until the requester sends the change order for approval.

Unresolved findings and questions do not stop sending; they become warnings the approver accepts.

### Send for approval: the change order is frozen

**How to send a change order for approval** (its requester, whoever created it, or a document controller; after at
least one round): press **Send for approval**. It is refused, with the reason, when it changes nothing; when no round
has run, a round is compiling or failed; when anything it carries changed since the last round ("... changed since
round *n*: request review again"); when the delta was compiled against a release no longer in force; while a baseline
flag is not dealt with (below); when the delta has errors; when a carried form, record-type change or workflow is not
valid; when a change request it implements is not approved or is flagged; when a committee on the panel has not agreed;
or when a rule would let a cog sign a change order (hubproto/changecontrol.py, `send_reasons`). Reviews still queued
for people and cogs do not stop it.

Sending **freezes** the change order: the hub records hashes of its drafts, items, forms, record-type changes,
workflows and state map, and the carried drafts are **held** (they take no new version). Any change after that sends it
back to revising and voids its item decisions: "CO-... is back to revising ... Its item decisions are void", told to
the requester and to whoever the Gate waits for.

### The delta and item decisions

The **delta** section lists every item: what it changes ("~ Changed", "+ Added", "− Removed", with "was: ..."), where
it came from ("Made directly by dave on ...; stated in purchasing-policy line 53", "read from the prose", "suggested by
a reviewer"), and side by side **The SOP says** (the line and its quote, or "nothing yet: no line states it") and **The
hub reads it as** (the rule in words). Then its **warnings**, and chips such as "widening: needs both policy owners" or
"not in the prose". Above the items, **What it affects** names the Frames, scopes, cogs and roles concerned.

A warning is any of: a reviewer's finding, an open question, a round trip that failed, an override, a divergence kept
from an earlier release, a related change (codes `finding`, `question`, `round-trip`, `override`, `divergence`,
`related`); a change order may also carry warnings of its own (a kept divergence, a panel the requester narrowed, a
dismissed related flag).

**How to decide an item** (a person who is a policy owner or may approve change orders, and did not make the item;
hubproto/changecontrol.py, `decide_item`)

1. Open the change order (its Gate card says "Decide the delta item by item ... Before it can be signed: ...").
2. For each item, under **To approve it, accept each warning**, tick **I accept: ...** for every warning, write
   **Meaning of your decision\*** and press **Approve item *n***, or **Reject item *n***. Without the ticks the hub
   refuses: "Tick I accept for each warning to approve it: ...". A rejected item does not go into force; if the prose
   states it, the configuration then differs from the prose (a divergence).
3. A **widening** needs a second policy owner who did not make it either ("needs two policy owners who did not make it
   (approved by carol)").
4. Accept any warnings on the change order itself (**I accept**, with a meaning).

<!-- figure: c07-co-delta-item -->

**Who made an item** (and so may not decide it): the authors of the revision for an item read from the prose, the
maker of a direct change, whoever accepted a suggestion, whoever overrode it, and whoever set a state-map entry. A cog
never decides an item ("people decide the items of a delta (cogs review and suggest)"). Checked by running the code:
bob was refused on his own direct change, carol without the ticks was refused, and carol with them approved it.

**Overrides.** **Override the value** lets an approver change an item's value and approve it ("Approve item *n* with
these values"). The override must keep the item's kind and slot and pass the consistency guard; earlier decisions on
it become stale, and the overrider becomes one of its makers, so someone else approves it. The form edits plain values
only (an amount, a role); a state-map entry cannot be overridden. An override may leave the configuration and the prose
disagreeing: that is a **divergence** (below).

**Signed decisions.** Every decision, acceptance, override, answer and state-map entry is a signed, chained event on
the change order (`co.item_decided`, `co.warning_accepted`, `co.override`, `co.question_answered`, `co.state_map_set`).
Before signing, the hub checks the delta against those events (`verify_delta`), and the change order's Gate signs a seal
of the decided delta (`delta_seal`). A decision becomes **stale** ("stale: decide it again") when the baseline changes or
the change order goes back to revising.

### Approval of the change order and segregation of duties

"Approvals in change-order go to the quality manager" (carol), due within 10 days; overdue change orders go to the
management representative (change-control-sop). The Gate card says "Signing approves the change order and every
revision it carries", lists **Before it can be signed**, and asks for **Meaning of your signature\***, **I have read the
item and the checks above** and **Approve CO-...**.

It can be signed only when (hubproto/changecontrol.py, `approval_reasons`): everything that sending needs still holds;
nothing changed since it was sent; the delta verifies against its signed events; every item is decided by someone
who did not make it (widenings by two policy owners) and no decision is stale; every warning, on the items and on the
change order, is accepted; no two approved items set the same rule; no blocking question is open; the state map is
valid and answers every removed status that has items; and the IT SOP's validation needs are met (7.16). Otherwise:
"The change order cannot be approved yet: ...".

**Segregation of duties on the change order** (change-control-sop; it-sop; hubproto/runner.py,
`_change_order_signers`; hubproto/changecontrol.py, `may_not_sign`):

* The author of a revision it carries never signs it.
* Any other contributor (its requester, whoever created it, the maker of a direct change, whoever accepted a
  suggestion, overrode an item or set the state map, the maker of a form or record-type change) signs only alongside an
  **independent approver**: a person who contributed nothing and approved every item of its delta. Until then they are
  still routed the Gate but refused at signing, with the reason. This is the IT SOP's "whoever made a direct
  configuration change on a change-order is not its only approver".
* A contributor does not review it, and a reviewer with results is not removed.
* A person signs, never a cog: a cog may review a change order and say what it found, never approve it, and a rule
  that would let a cog sign one stops it ("a person signs a change order").

The page lists **Contributors (who may not sign it)**.

<!-- figure: c07-co-validation -->

Checked by running the code: on dave's change order, carol and bob decided the items (both, since every item was a
widening), alice recorded the test evidence, and carol signed. On the demo change order, whose finance-policy revision
carol wrote, sending failed: "Nobody can take “Approve the change order”: quality manager: carol: segregation of
duties: they wrote a revision this change order carries, and never approve it", and the change order went to Failed,
back at revising. carol is the only quality manager, so **no change order carrying a revision she wrote can be
approved** in the example company (chapter 10.9).

**Signing also approves every revision the change order carries.** Document control does not approve them separately:
a carried draft sent through document control blocks sending ("withdraw it; the change order's approval approves it").
The release records the change order's signature on each revision ("approved with CO-...") and refuses if the signer
wrote it (hubproto/changecontrol.py, `approve_revisions`).

### Release

The approval starts the **release-change-order** automation (hubproto/automation.py), whose steps are:

| Step | What it does |
|---|---|
| Check the change order is ready | the checks of approval, again |
| Approve the carried revisions | records the signature on each revision |
| Bring the Frame into force | the approved delta becomes the release in force; divergences are recorded in it |
| Move items by the state map | items in removed statuses are moved (below) |
| Publish the revisions | each revision becomes **Effective**, the one it replaces **Obsolete** |
| Apply the form layouts | and the workflow layouts (7.12, 7.13) |
| Apply the link and retention changes | 7.14 |
| Apply the record-type changes | 7.14 |
| Assign training | acknowledgement tasks for people; republishes the required-training matrix when it changes (7.15) |
| Record divergences | raises a change request for each new one (below) |
| Link document references | the SOPs' citations of documents become links; an SOP still citing a document this release retired, or a revision it superseded, is flagged to its owner |
| Send related changes back | below |
| Compile every open change order | against the new release |
| Tell the people involved | the change order becomes **Released**; its requester is told |

Checked by running the code: dave's change order released with every step succeeding; "release ... is in force (... 3
new divergence(s))"; "change request(s) raised to bring the SOPs in line: CR-2026-0001, CR-2026-0002, CR-2026-0003";
"sent back: CO-2026-0001 (by definition)"; "compiling again: CO-2026-0001". The change order page then says "Released:
its revisions and configuration are in force (policy version *n* ...)".

### Divergences

A **divergence** is a line of an SOP that the configuration in force does not follow: an approver overrode the item, an
item read from the prose was rejected, or a direct change was approved although the qa-manager could not write it
into the prose. The release records each (`<CO>/D<n>`: the SOP, line, prose, the rule in force, the change order, who
accepted it and their meaning, a change request and its due date) and raises a change request to the SOP's owner:
"Bring *sop* line *n* in line with what is in force", due by the change-control due date, else in 30 days.

It is shown:

* on the SOP document's page, a card "In force differs from this line of *sop*" and, in **The text, line by line**, a
  marker under the line: "In force differs from this line: The match tolerance is 1 percent. (accepted by bob on
  CO-2026-0002: “agreed as the second policy owner”)";
* on the Frame's page, a draft revision's page ("The revision in force has *n* line(s) ...") and the online editor's
  bar;
* on the release's page and the **Conformance** page ("Divergences *n*", with a table);
* to cogs: a cog whose context cites that line is given "The rule in force differs from this line: ...: follow the rule
  in force (accepted by ... on CO-...)" (hubproto/cogs.py, `_cite`).

It is **not** shown on downloaded or printed copies (read the code; chapter 10.9).

<!-- figure: c07-divergence -->
<!-- figure: c07-divergence-line -->

A later change order's compile **keeps** an open divergence's rule in force while the prose still says what it said,
with a warning naming the divergence; prose that now states the rule in force, or a change that sets the configuration
back, resolves it (hubproto/changecontrol.py, `keep_divergences`). Checked by running the code: bob's change order
carrying revisions of the organization model and the cog profiles that state the change-board group and committee
resolved those two divergences; the match-tolerance one stayed open. There is no other way to close a divergence.

### Recompiling open change orders and related changes

When a change order is released, every other open change order is compiled again against the new release (the
compile-frame automation without a round: no drafting and no panel). People's direct changes and accepted suggestions
are kept; an item the release made moot becomes a `rebase` question.

**Related changes are sent back** (hubproto/changecontrol.py, `find_related`):

* **Related by definition**, found by the hub: an open change order that touches the same SOP, process, rule, record
  type, form or role. It goes back to revising with a banner "The baseline changed: CO-... was released and is related
  by definition: both change finance-policy ...", its decisions are stale, it cannot be sent until another round has run,
  and its requester is told. "Found by the hub (the same SOP, process, rule, record type, form or role): it cannot be
  dismissed. Run another review round." An approved change order not yet released is sent back the same way.
* **Related by meaning**, found by the released change order's panel cogs, each looking at open change orders and at
  approved change requests no change order carries yet: a finding with a reason. Only the requester may **Dismiss the
  flag**, with a reason, which becomes an accepted `related` warning. A change request found this way is flagged and its
  approver gets a review task; no change order implementing it can be approved until that is done. With the stand-in,
  cogs find nothing by meaning.

<!-- figure: c07-co-related -->

### State maps

When a delta removes a status that items are in, the compile counts them and asks, for example "1 task is in Under
review, which this change removes from gizmo-review: where do they go?" (tests/test_workflow_graph_change_orders.py;
hubproto/runner.py, `state_map_questions`). The change
order's **State map** section lists each removed status with **Items now**, **Move to** and **Consequences**, marked
"needs an answer" until answered; the requester or a document controller saves it. A target must be a status of the
process as the change leaves it, and only a status the delta really removes can be mapped. Each entry is a delta item
an approver decides, and every unanswered row stops approval.

At release, the signed state map is applied without running any check or action: each item's waiting decision is
withdrawn ("No longer waiting for you: ..."), the item moves, its history records "Moved from *X* to *Y* by CO-...
(state map)", and its run continues under the new release, which opens and notifies the new Gate (hubproto/runner.py,
`apply_state_map`). A state map changed after signing is refused.

### What a change does to each cog

**What this does to each cog** shows, for each cog the delta touches, what it gains and loses under Roles, Context,
Permissions, Routes, Approval criteria, Duties and Process steps, with a widening chip where it applies: "Derived from
the delta, as if every item were approved as it stands (overrides included). A cog is treated like a person in its
position: what it gains here is what an SOP gives it or its roles; a widening needs both policy owners." A cog joining a
group is not shown there (checked by running the code: the it-admin joining the change-board group showed "No cog's
roles, context, permissions, routes, approval criteria, duties or steps change").

<!-- figure: c07-co-cogs -->

### Validation and test evidence

The IT SOP's validation classes apply to each item (7.16). The **Approval** section lists what is missing, for example
"5 high-risk item(s) need test evidence (...): attach a document of type test-evidence to the change order, or record
a test record (what was run, expected and actual result, pass or fail)" and "1 medium-risk item(s) need a test note
...". A failed test stops approval. Evidence is either a document of type `test-evidence` attached to the change order,
or an entry recorded with `validation.record_evidence` (a test record: what was run, what was expected, what happened,
pass or fail; or a test note). **There is no page or command to record a test entry**: in the runs for this manual it
was recorded through the Python interface (hubproto/validation.py). Any member may record evidence, and an entry that
names no items covers all of them (chapter 10.9).

### An example, as run

1. alice's demo change order carries carol's finance-policy revision lowering the ap-reviewer's limit to 1,500 USD.
   alice requests review with the management cog and dave on the panel. The grammar reads one change ("The ap-reviewer
   cog may approve purchase-to-pay bills under 1,500 USD ..." replacing "under 2,000 USD"); the committee cannot read
   (hosted models off).
2. dave posts an advisory finding ("About a third more bills will wait for a person"), a question ("Does the 1,500 USD
   limit apply to bills in EUR too?") and a suggested routing sentence. alice answers the question with line 42,
   accepts the suggestion, and requests review again; round 2 brings the suggestion in as an item and the management
   cog sees only the new item.
3. Meanwhile dave lowers the purchase approval threshold and the match tolerance and adds the change-board group and
   committee in Settings, and puts them on his change order. The qa-manager states the threshold in
   purchasing-policy line 53; the other three cannot be written (dave may not revise the organization model or the cog
   profiles, and the finance policy is held by alice's change order), so each carries "prose does not read back".
4. alice reviews it (an advisory finding on the threshold). dave sends it. carol and bob decide every item, ticking
   **I accept** for each warning; alice records a test record; carol signs. The release makes the threshold, the
   tolerance, the group and the committee configuration, records three divergences and their change requests, and
   sends alice's change order back: "The baseline changed: CO-2026-0002 was released and is related by definition".
5. bob changes the nonconformance workflow in the editor so that the change-board committee decides the disposition,
   and carries it. Round 1 stops: "'Disposition the nonconformance' is decided by the change-board committee, which is
   not defined", because the committee is in force only as a divergence (chapter 10.9). bob adds revisions of the
   organization model and the cog profiles stating the group and committee; round 2 compiles; carol decides and signs;
   the release resolves two divergences. A nonconformance filed afterwards waits for the change board (chapter 3.1).

## 7.10 The automations: compile-frame and release

An **automation** is an Op the hub runs on its own, as a job with named steps recorded as they run. "An automation
never decides anything a person has not: it acts only on what was approved ... and it refuses when that approval is
missing or out of date" (hubproto/automation.py). Jobs run one at a time, oldest first, in the hub's own queue lane;
the same automation on the same item is not started twice while one waits, and more than ten runs in ten minutes on
one item are stopped as a loop.

**compile-frame** ("Compile the Frame"). Started when review is requested on a change order (a round), when another
release comes into force (every open change order), or by hand with **Compile the Frame again**. Its steps are in 7.9,
"A review round". Without a round (a recompile after a release) it drafts no prose and asks no panel.

**release-change-order** ("Release the change order"). Started when a change order is approved. Its twelve steps are
in 7.9, "Release".

A job ends as **succeeded**, **stopped part way** (some steps done; retry it after fixing what it says), **nothing to
do** or **failed**. When a job stops, the change order's requester is told. Steps already done are not repeated on a
retry; a compile-frame job retried after the drafts changed compiles again.

## 7.11 The Automation page

**Automation** (`/automation`; in the **Policy** app, and linked from each change order's automation line) lists the
hub's jobs, newest first, with the item each acts on, its status (and the step running now), why it ran, when it
started and how long it took. It opens with the two automations and their triggers and steps, and has filters: all,
running, waiting, stopped part way, failed, succeeded. The page updates itself while jobs run. Any member may open it;
jobs about items you may not read are left out.

A job's page shows each step with a mark (done, running, failed, skipped), its outcome in words and how long it took;
the item, why it ran, who started or retried it, when, the attempts, and the policy version it ran under.

**How to retry a stopped job** (the change order's requester, a document controller or a policy owner): fix what the
outcome says, open the job and press **Retry from the step that stopped** (or **Retry** on the change order).

## 7.12 Forms

A **form layout** says how a kind of record looks and is filled in: the titled sections of field rows on its
**Details** tab, each row's label, help text, placeholder and widget, the fields and their order on the form that
starts it, custom fields, and conditions for when a field is shown, required or read-only. The record type still says
what a record holds; the layout says how people see it (hubproto/formlayout.py; hubproto/shell/forms_ui.py).

Until a layout is released for a type, the hub's own form is in force. A layout in force follows its record type: a
field the type no longer has is dropped, and one it gained appears at the end of the last section.

**Forms** (`/forms`, in the Settings and Quality apps) lists every record type with the layout in force ("the hub's
own", or "v*n* released with CO-...") and your draft. Any member may read the forms and their history; document
controllers and policy owners change them.

**The editor** (`/forms/<type>`, for document controllers and policy owners) shows the sections as cards of fields to
drag between and reorder; **Fields not on the form**; **Properties** of a field (label, help, placeholder, widget, and
**Shown**, **Required** and **Read-only** "when ..."); the **Start form**; **Add section** and **Add the field** for a
new custom field; and a **Preview**. After each edit it checks the draft and lists errors, warnings and the changes in
words. Custom fields have names starting `x_`, are never renamed or retyped, and are retired rather than deleted;
conditions are data, not code.

**How to change a form** (a document controller or policy owner)

1. **Forms** > the record type. Change the layout; press **Save draft** (one draft per person and type), or **Discard
   my draft**.
2. Under **Carry on a change order**, choose one of your change orders being prepared, or a new one, and press **Carry
   the draft**. The change order carries a snapshot; a later edit is carried again.
3. The change order goes through its rounds as usual (7.9). When it is released, **Apply the form layouts** brings the
   layout into force as the type's next version.

A form change is classed low risk (7.16). It is not an item an approver decides one by one: it is covered by the
change order's approval (chapter 10.9). The "required" and "read-only" conditions are still not enforced (chapter 10,
X6).

## 7.13 The workflow editor

Every process is a workflow graph of statuses and transitions (chapter 3.1, "Processes as graphs"). The **workflow
editor** changes one, as a draft carried on a change order (hubproto/shell/workflow_ui.py, hubproto/workflows.py,
hubproto/workflow_model.py). Jira's workflow editor was studied for it (docs/design/jira-workflow-study.md); unlike
Jira's, nothing publishes from the editor.

**Where.** **Settings** > **Workflows and routing** draws each process as it is in force, with **Open in the workflow
editor**; **Workflows** (`/workflows`) lists the processes with the layout in force and your draft ("draft with *n*
changes"). Document controllers and policy owners edit (carol and bob); others read: "You are reading the workflow in
force. Document controllers and policy owners change it."

<!-- figure: c07-workflow-editor -->

**The page.** "*Process* workflow · policy version *n* (...) in force", then the state of your draft: "This is the
workflow in force. Your first edit starts your own draft (saved as you go); nothing changes for anyone until a change
order carrying it is released", or "Your draft, saved *when*: *n* changes from the workflow in force". Three views:
**Diagram**, **Text** and **Changes** (with a count).

* **Toolbar**: **Add status**, **Add transition**, **Undo**, **Redo**, **Tidy** (a deterministic layered layout,
  undoable), **Zoom out**, **Zoom in**, **Fit**, and a save state ("Draft saved", "Draft saved: *n* error(s) to fix
  before carrying", "Not saved").
* **Canvas**: drag a status to move it (on an 8 px grid); drag from its round handle onto another status to connect
  them; drag a transition's label; drag the background or scroll to pan, Ctrl with the wheel to zoom. Each transition
  shows its gate as a badge (a person's role, a cog, a committee, or "auto").
* **Side panel**: with nothing selected, the process ("Nonconformance: 6 statuses, 7 transitions. Stated in ncr-sop
  line 53."), **Items in each status**, **Routing and due dates** and **Open change orders on it**. A status: name,
  category (to do, in progress, done), outcome, **Due within (days)**, **Allow all statuses to move here**, connect
  from here, ways out and in, position, **Delete**. A transition: the rule as a sentence; name; from (or **From any
  status**) and to; **Who decides** ("a person in a role", "a cog, within its criteria", "a committee (consensus)",
  "automatically"), with the role and "not the item's author", the cog's fallback role, the committee, or the
  condition; "What it needs (checked before it moves ...)"; "After the move, in order"; **Save the transition**,
  **Delete the transition**. The hub's **Failed** status is read-only.
* **Keyboard**: "Tab reaches each status and transition. Arrows move a status one grid step (Shift: five); Enter opens
  it here; C connects from it; Delete deletes it; Escape deselects. Ctrl+Z undoes, Ctrl+Shift+Z redoes; + and − zoom,
  0 fits." Each edit is announced to screen readers.
* **Text view** makes every change with plain forms and needs no JavaScript; it ends with **As the SOP will say it**,
  the graph sentences the change will write.

**Checks after every edit** (`workflow_model.checks`; each selects its element). Errors: no start, or two; "no
transition leads to “X”"; "“X” is not done but no transition leaves it"; automatic transitions that overlap or leave a
gap; a role nobody holds; a cog with no approval criteria for the process; an undefined committee, or "an objection has
no way back"; "*n* items are in “X”, which this removes: say where they go". Warnings: more than 25 statuses; a weaker
gate (auto, cog, person, committee); a segregation rule or a check removed; a global transition added; and a
**mandatory gate bypassed**: "Open → Automatically → Awaiting disposition the nonconformance → Refer to the change
board → Awaiting the change board → Board disposition → Closed avoids “Disposition the nonconformance” (the quality
manager decides): a mandatory gate bypassed, a widening" (seen running). With none: "No problems: every status can be
reached and left, and every gate has someone to decide."

**Drafts.** One draft per person and process, saved at every edit, with undo and redo over its operation log. When the
workflow in force changes under it, the draft is stale and must be discarded; there is no rebasing. **Discard my
draft** removes it.

**Deleting a status items are in** asks at once: "Delete X? *n* item(s) are in X. Move it to: ...", "Asked again on the
change order with the consequences; the release moves them without running rules, and records the move on each item."
The choice becomes the draft's state map (7.9).

**The Changes view** draws the old and the new workflow as one drawing: added marked "+", removed dashed and struck
"−", changed "~", the rest faded; **Changes**, **Before** and **After** switch the view, and a linked **Changes** list
says each change in words ("~ “Disposition the nonconformance”: the change-board committee decides (consensus),
instead of the quality manager decides").

<!-- figure: c07-workflow-changes -->

**How to carry a workflow change** (a document controller or policy owner): under **Carry on a change order** choose
one of your change orders being prepared, or "a new change order..." with a title, and press **Carry the draft**. It is
refused while the draft has errors, is stale or changes nothing. The change order then carries one direct change of the
process (worded as its changes), the state map and the layout; its page shows the same drawing of old and new under
**Workflows**. The review round's qa-manager writes the process into the SOP as graph sentences, so a numbered-steps
process becomes a graph-form process once such a change is released.

Layout alone is presentation: a layout-only edit is one item, "Layout changed: no effect on how items move".

## 7.14 Record types under change control

After the conversion the record types in force are kept in the hub's state (`state/record-types/active.json`) and
change only through change orders; `config/record-types.json` is not used (hubproto/recordtypes.py). Allowed changes:
add a type, a field, choice values or a state; require fields; set retention, sensitivity or a default; archive a type;
remove a field with no data or a type with no records and no process. Refused, with the reason: renaming a type or a
field, retyping a field, removing choice values ("a field is never renamed: records keep their values under its name.
Add a new field and take this one off the form").

**Record types** (`/record-types`, linked from a change order's page as **Change a record type**) shows "The record
types in force: version *n* (...). They change only through a change order", the types in force, the changes carried
on change orders, and **Carry a record-type change** (on which change order, the change, the type, the field, why).
Only the change order's requester or a document controller carries one, while it is being prepared. On the command
line: `hubproto record-types status`, `show TYPE`, `carry CO-... --as P (--changes JSON | --changes-file PATH |
--from-file)`. The release step **Apply the record-type changes** brings them into force.

A record-type change is high risk (7.16) and covered by the change order's approval, not decided item by item. A change
order cannot carry a new record type together with a process that files it: the compile checks the process against
the record types in force (ran the code; chapter 10.9).

**The link vocabulary and the retention classes** are kept the same way (`state/config/links`, `state/config/retention`;
hubproto/configdocs.py): `config/links.json` and `config/retention.json` are not used after the conversion. A change
order carries a new relation or class, a new inverse label, a class kept for more or fewer years ("records of the
standard class are kept 1 year(s) less" is a warning shown with it), or a removal; removing a relation a link uses or
the hub's own steps use, `standard`, or a class a record type is kept under is refused. The release step **Apply the
link and retention changes** brings them into force, before the record types, so one change order may add a class and
keep a record type under it. Links are medium risk, retention classes high (7.16).

**The configuration as JSON.** `hubproto config export --out config.json` (or **the configuration in force** link on a
change order's page, `GET /api/config?as=<you>`, for anyone who may read Policy) writes everything in force as one
JSON document: the rules, members, roles, groups, folders, cog profiles and personas, the record types, the form
layouts, the link vocabulary and the retention classes. Edit it and propose it: `hubproto config propose config.json
--as carol [--co CO-...]`, or **Propose a configuration file (JSON)** under **What it carries** on a change order you
are preparing. What it changes is carried exactly as the same edit made in Settings, the forms editor or the record
types page would be (rules become direct changes whose prose the review round writes into the SOP), with the same
review, widenings and signature. A file exported before another release came into force is refused ("export again"),
and so is anything that cannot be carried, with nothing carried. The format: docs/design/config-json.md.

## 7.15 Roles and responsibilities, and required training

**Every document ends with a Roles and responsibilities table** (`| Role | Responsibilities |`). A row names a role
("Quality managers"), a group ("the senior-cogs group") or a cog ("the it-admin cog") (hubproto/extract.py,
`responsibilities_of`). **The table defines who must read the document:**

* a **person's required reading** is every document whose table names one of their roles or groups;
* a **cog's context** ("follows") is the company scope (org-model, quality-manual, tasks-sop) plus every document
  whose table names the cog, one of its roles or one of its groups (hubproto/release.py, `follows_for`).

A mention of a role elsewhere in the text no longer counts. The table is read whatever the document's compile mode.
The compile warns (hubproto/guards.py, `responsibilities_guard`) when a table names a role that is not declared, a role
nobody holds, a cog or group the organization does not have; when an SOP gives a role, cog or group a step, a
decision, a route, approval criteria, a countersignature, a duty or a create permission that its table does not list
("... but its Roles and responsibilities table does not list it"); when a document gives someone a part and has no
table; and when a cog is in no table ("it follows only the company scope and will be routed nothing"). The qa-manager's
prose drafting adds the rows a direct change needs.

**The required-training matrix.** The document **Required training** (a controlled Markdown document in
Quality/Training, type `training-matrix`, approved "by the release") summarises the tables: "Generated from the Roles
and responsibilities tables of the documents in force. It is not edited by hand." It has a section per role, then per
group, each a table of Document, Title, Revision, Link, Responsibility and Cogs that follow it, and a **Cogs** section
(cog, document, revision, in context since). It is published by the release step **Assign training** when it changes
(hubproto/training.py, `publish_matrix`). **A new hub has none**: the first release does not publish it; the first
change order released does (rev A), and each release that changes a table or a listed revision publishes the next
(checked by running the code). A person's page links it from **Required reading** ("the whole training matrix is
DOC-*n*") once it exists; the Training page does not.

<!-- figure: c07-training-matrix -->

**Training tasks.** For each revision a change order carries, the release opens one training-acknowledgement task per
person (never a cog) whose role or group the revision's table lists, titled "Read *title* rev *x*" and due within 14
days (training-sop; hubproto/cogs.py, `assign_training`). A person's **Required reading** shows each document with
"acknowledged", "due *date*", "overdue since *date*" or "not assigned". For a cog, training is the document in its
context when it works; the release that put a revision there is its training record (chapter 6.11).

## 7.16 The IT SOP and validation

The IT and configuration SOP (frames/iso9001/it-sop.frame.md, scope `it`, maintainer bob) governs the configuration
itself:

* **Configuration only by change order**, as in 7.5. "The system administrator role grants no approval of its own ...
  a system administrator who also holds one of those roles approves in that role, never as a system administrator ...
  No cog approves a configuration change." The hub enforces this by giving the system-administrator role no approve
  permission and by the change-order rules of 7.9; the one power the role has is answering the compile-frame
  committee's questions with the chair.
* **Access management** through the organization model, by change order with a reason; "a cog is given only the roles
  its work needs and never a role that approves policy"; a quarterly access check (access review interval 90 days).
* **Risk-based validation**, "in the manner of computer software assurance": the classes of 7.4. A high-risk item
  needs a **test record** (a scripted check: what was run, expected, what happened, pass or fail; or an attached
  document of type test-evidence); a medium-risk item a **test note** (an unscripted try-out and what was seen); a
  low-risk item only the review of the delta. The change order cannot be approved until each item has what its class
  needs, and a failed test stops it (hubproto/validation.py, `requirements`). With no classes in the release the hub's
  built-in defaults apply, and it says so.
* **Hosted models**: switched on only by a system administrator, as a recorded installation change, for the senior
  cogs and compilation only, with limits on what may leave the hub (chapter 8.4). The hub itself enforces none of this
  beyond the switch: the switch is an environment variable, it is not recorded, and it lets any cog bundled with a
  `claude-*` model use it (chapter 10.9).
* **Backup and restore**: daily, kept off the host; a restore test every 90 days into a separate hub; only a system
  administrator restores, on a task (chapter 8.5).
* **Incidents**: a task to the system administrators; the agents switch stops a misbehaving cog; an incident that may
  affect product is also a nonconformance.
* **Segregation of duties**: "Whoever made a direct configuration change on a change-order is not its only approver";
  "an internal auditor does not audit system administration they perform" (prose only: bob is both, chapter 10.9).
* **Rules**: system administrators and management reviewers may read Policy and Quality/Changes and comment on change
  orders; system administrators may create change orders; the it-admin and management cogs may not approve documents
  in Policy or change orders. Every week the it-admin cog reviews the open change orders that touch permissions, record
  types or forms.

**Members.** bob holds the **system-administrator** role, and so does the **it-admin** cog (the executor persona; the
change-control SOP and the IT SOP in its context; "It changes no configuration itself and approves no policy"). The
**management** cog (role management-reviewer, the advisor persona) reviews changes to roles, limits and segregation of
duties (chapter 6.1).
