# 6. Policy and configuration

Who may do what, how items are routed, what a cog may approve and how each process runs are not settings buried in
code: they are sentences in policy documents called **Frames**. The hub compiles the Frames into an immutable **policy
release**; policy owners review every change before it takes effect. This chapter explains that cycle, the Settings
app that edits the same rules as data, and how SOPs, change orders and the automations bring a change into force.

## 6.1 Frames: the policy as sentences

A Frame is a Markdown file with a short header (name, version, scope, maintainer, what it inherits, the ISO clauses it
covers) in `frames/` (hubproto/frames.py). The example company has an organization model, the cog profiles, a finance
policy, a purchasing policy, a design-control SOP, the ISO 9001 quality manual, eleven ISO 9001 SOPs and eight work
instructions (chapter 3 cites them by name).

The compiler reads rule sentences only inside these sections; everything else is narrative
(frames/reference/writing-policy-frames.frame.md; DEV-NOTES.md):

| Section | Example sentence | Becomes |
|---|---|---|
| `## Roles`, `## Members`, `## Folders` | `- carol (person): quality-manager, reviewer` | who is a member, with which roles; which folders exist |
| `## Permissions` | "Finance clerks may create bills in Finance/Payables." "Quality managers may not create bills in Finance/Payables." "Controllers may read Finance." | who may create, read or approve which kind of record in which folder (and its subfolders); "may not" wins |
| `## Thresholds` | "The match tolerance is 2 percent." | a named limit the automatic steps read |
| `## Routing` | "Approvals in purchase-to-pay over 10,000 USD go to the controller." "At Verify the change in change-control go to the requester." "Approve the bill in purchase-to-pay is due within 5 days." "Overdue items in purchase-to-pay are reported to the policy owner." | who a Gate goes to, in order, with conditions; due dates; escalation |
| `## Approval criteria` | "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved." | a cog's rail (chapter 5.6) |
| `## Segregation of duties` | "The preparer and the approver of a bill must be different people." | who is kept from deciding |
| `## Process: <name>` | "Records are filed in Finance/Payables." "Closes as posted." "1. [finance-clerk] Extract the bill (invoice-extractor)" | a workflow: its folder, record type, steps, Gates, finishing state, where a rejection returns |
| `## Cog: <name>` | `Model:`, `Prompt:`, `Follows:`, `Lenses:`, `Autonomy:`, `Duty:` | a cog's profile |

Every rule in a release cites the sentence, Frame and line it came from; that is what the **Why?** answers, the inbox
card and the decision records quote. A sentence in a rule section that matches no shape is reported as an error and
blocks the release ("compile refuses to release a heading or a rule-like sentence it could not read").

Narrative-only Frames (`compile: none`: the quality manual and the work instructions) produce no rules, but cogs read
them as procedures.

## 6.2 Policy owners

Members with the role **policy-owner** (bob and carol) review and release policy changes. The reviewers of a proposal
are the policy owners **of the release in force**: a proposal cannot appoint its own reviewers. Only a policy owner may
release (hubproto/compile.py). Policy owners see a **Policy** group in the rail and the **Policy** app, and get a
notification and an inbox section when a proposal waits for them.

## 6.3 Policy versions (releases)

A release is the compiled policy: members, roles, folders, permissions, thresholds, routing, approval criteria,
segregation of duties, cog profiles and every process's workflow. It is identified by a digest of its content (the
same rules give the same digest) and shown as "policy version *n*". Releases are never changed; a change is a new
release. Every run is pinned to the release in force when it started (hubproto/release.py).

**Policy versions** (rail, policy owners; or `/releases`) lists every version, newest first, with when it took effect,
who released it, what it was based on, a **What changed** link and its digest; a link to **Policy proposals** with the
number open; then the release in force in sentences: **Routing in effect**, **Automatic approvals** (each cog rail
with its limit), **Processes** (each workflow's states and approvals, who decides and the due dates) and
**Thresholds**.

**A version's page** shows who released it, when, what it was based on, the proposal and its reviewers, and **What
changed**: tables of sentences **Changed** (before and after), **Added** and **Removed**, each with the Frame and line,
the kind of rule, and every owner's decision with its meaning and a **widening** chip. Then the Frames in the version,
with those that changed marked.

A release can be scheduled: `hubproto release <n> --as bob --effective-from 2026-12-01` (chapter 7.8).

Two more pages are for policy owners: **People and roles** (`/admin`: each member and their roles as written in the
org model, each cog with what it may approve and its limit, and developer details) and **Conformance** (the ISO 9001
clauses and which Frames claim them: "*n* of *m* clauses are claimed by a policy").

**Policies** (`/frames`) lists the Frames on disk with version, scope, maintainer and description; a Frame's page
shows its text with the file's own line numbers, so a citation such as "line 34" matches.

## 6.4 Compiling and proposals

Compiling reads the Frames into rules, runs the checks, and compares the result with the release in force. The result
is a **proposal**: the list of changes, each to be decided by the policy owners (hubproto/compile.py,
hubproto/guards.py).

The checks ("Guards"):

* **grammar**: a rule-like sentence the compiler could not read (an error);
* **schema** and **grounding**: every rule is well formed and its cited sentence really is in the Frame;
* **consistency**: for example a route to a role nobody holds ("fill the org model": an error), a route to a cog
  without approval criteria, a condition the runtime cannot evaluate, a step with no routing sentence (a warning; it
  routes to the step's role), a cog whose model this hub cannot reach (a warning);
* **drift**: what changed against the release in force.

Each change is one of: a rule **added**, **removed** or **changed** (a removed and an added rule about the same thing
read as one change, "was ..."), or the **wording** of a Frame changed (a wording change is reviewed even when no rule
moved, because the SOP is what people follow and cogs judge against).

**One or two owners.** A change needs one policy owner's approval, or two when it is a **widening**, something that
lets someone do more (hubproto/guards.py, `_widens`):

| Widening (two owners) | Not a widening (one owner) |
|---|---|
| a new "may" permission; removing a "may not"; any change to an existing permission | removing a "may"; adding a "may not" |
| a new member, or a member gaining a role | a member losing a role |
| any change to a cog's profile (model, prompt, SOPs, autonomy, duties) | |
| a new route to a cog; a route that now goes to someone else, or that informs instead of assigning | a new route to a role or a person; removing a route |
| any new approval criterion for a cog; a criterion losing a condition, a countersignature or a running limit, a raised limit, fewer adversarial reviews, a changed currency | a criterion gaining a condition or getting a lower limit; removing a criterion |
| a new threshold; any change to a threshold's value, up or down | removing a threshold |
| any change to a due date, longer or shorter | adding or removing a due date |
| removing or changing a segregation-of-duties sentence | adding one |
| a process losing a checkpoint, or filing in another folder | other changes to a process |
| the wording of a Frame a cog follows | the wording of other Frames |

(A changed rule is compared with the rule it replaces; for thresholds, due dates, permissions and segregation
sentences any difference counts as a widening, read the code: hubproto/guards.py, `_widens_over`. Ran the code: a
due date shortened from 5 to 3 days was flagged "needs both policy owners".)

Error findings block a release. A proposal compiled against an older release than the one in force is refused ("the
proposal is based on release ... compile again so the change is reviewed against the current policy").

**Policy proposals** (`/proposals`, linked from **Policy versions**) lists proposals by number in four groups:
**Open**, **Out of date: redo or withdraw**, **Released**, **Replaced or withdrawn**, with when each was made, by whom
(or "staged by CO-..." for a change order, 6.9), the number of changes and of widenings, and a **your decision** chip
for the ones waiting for you.

## 6.5 Editing a Frame in the hub

Until the SOPs are converted to documents (6.8), a Frame is changed by editing its text, either on the host followed
by `hubproto compile` (chapter 7.9), or in the web pages through the Frame's document (hubproto/shell/policy_docs.py;
hubproto/app.py, `frame_draft`).

Every Frame has a controlled document in **Policy/Frames** carrying its text ("*name* (policy Frame)"), created when
first needed and given a new version on every release.

**How to propose a change to a Frame** (any member who may read the Policy folder; all staff in the example company)

1. Open **Policies** > the Frame > **Open as a document**, and press **Edit this policy**. The hub makes your draft: a
   working Markdown document in Policy/Frames with the Frame's current text, one per person and Frame.
2. Change the sentences in the text editor. Leave the version line alone; the hub raises it. **Save**.
3. Press **Save and check the change** (or **Check the change** on the draft's page). The check page says whether the
   draft compiles ("The draft cannot be released yet" with the errors and lines, or "The draft compiles"), lists **What
   the policy would change** with "one policy owner" or "two policy owners" for each change, warnings about the change,
   lines the compiler did not read as rules, and the text compared with the policy in force.
4. Press **Propose for release**. The draft becomes a proposal and you land on its page. It appears in the policy
   owners' inbox under **Policy proposals waiting for your decision**.
5. To give up, press **Withdraw the draft** (you or a policy owner): it leaves any open proposal and is no longer
   compiled; the Frame stays as it is in force.

Only the draft's author or a policy owner may change, propose or withdraw a draft. Proposing the same draft again
replaces its earlier proposal and keeps the decisions already given on unchanged changes. When another proposal is
released first, the other open proposals are compiled again against the new release; a proposal whose draft changes
a Frame the release just changed becomes **out of date**, and its author is told to redo the draft on the new text.

Changing a cog's model (chapter 5.10) and proposing a new person or cog work the same way: the hub drafts the change to
the cog profiles and the organization model and proposes it.

## 6.6 Reviewing and releasing a proposal

**How to review a proposal** (a policy owner)

1. Open it from **Inbox** > **Policy proposals waiting for your decision**, from the notification, or from **Policy
   proposals**.
2. Read the header (made from Settings or compiled from drafts, by whom, why), each draft's text compared with the
   Frame in force (with who wrote and changed the draft), any **Errors** (they block the release) and **Warnings**.
3. In the **Changes** table, each row is one change in sentences, with "was: ..." for a changed rule, a **widening:
   two owners** chip where it applies, its state and the decisions so far. For each change you have not decided,
   choose **Approve**, **Reject** or **Not now**.
4. Write **What you are confirming\*** (kept with each decision) and press **Record my decisions**, or **Approve all
   *n* remaining**.

A rejected change is left out when the rest is released; it can be proposed again. A change where one rule replaces
another is decided as one.

**How to release a proposal** (a policy owner, once every change has the decisions it needs)

1. Open the proposal. When nothing blocks it, a green card says "Every change has the decisions it needs".
   Otherwise **Before it can be released** lists what is missing ("needs 2 approvals (has 1)", errors, "the policy in
   force changed after this proposal was made").
2. Press **Release**. The approved changes become the policy in force; the drafts are written into their Frame files
   and their Frame documents get a new version; other open proposals are rebased.

The same can be done on the command line with `hubproto review` and `hubproto release` (chapter 7.9).

## 6.7 The Settings app

**Settings** shows every rule the hub enforces as data, and lets members who are people change them without writing
sentences (hubproto/settings.py; hubproto/shell/settings_ui.py). Cogs, and anyone watching a cog, can read the settings
but not edit them ("a cog proposes changes with its tools").

| Section | What it shows and edits |
|---|---|
| **Your changes** | your draft: the edits you made (each in a sentence), **Check**, **Discard my draft**, and **Propose to the policy owners** with a **Why** |
| **People and roles** | roles (add, remove), members with their roles (change roles, remove, add a person), folders (add, remove). A new cog also needs a profile: add it from the People page |
| **Permissions** | every permission as a sentence, with where it came from; add one (role, may or may not, do, what, in folder) or remove one |
| **Limits and due dates** | thresholds (edit value and unit, add, remove) and due dates per step (edit the days, add, remove) |
| **Cog rails** | for each cog: its profile (model, autonomy, prompt, the SOPs it follows, duties) and its rails. A rail says what the cog may approve, **Under (amount)** and currency, **At most a day** (approvals), **At most a month** (amount), **Countersigned by** (a role or nobody), **Adversarial reviews** (1 to 6, or none) and **Only when** (conditions such as "the risk is low") |
| **Segregation of duties** | each rule ("The author and the approver of a document must be different people."); add one (the ..., and the ..., of a ...) or remove one |
| **Workflows and routing** | each process and its routes, read-only ("edited in the workflow builder (coming next)") |

Each row says where the rule came from: a Frame and line, or "settings, by *name* on *date*", and **in your draft** when
you changed it.

**How to change a setting** (any member who is a person)

1. Open **Settings** and the section. Change the value in the row's form, or use the add form at the end, and press
   its button. The status line repeats the change as a sentence ("The ap-reviewer cog may approve purchase-to-pay bills
   under 1,500 USD when ..."). The edit goes into **your own draft**, based on the policy in force; nothing changes for
   anyone else yet.
2. Open **Your changes** and press **Check**. The check lists each change (added, changed with before and after,
   removed) with a **widening** chip where it applies, and either "No problem stops a release" or "These stop a
   release" with the problems. It runs the same checks as a compile.
3. Write **Why** and press **Propose to the policy owners**. What happens next depends on where the policy comes from
   (below).
4. **Discard my draft** throws your edits away.

If the policy in force changes while you are editing, your draft is **stale**: "The policy changed since you started";
discard it and start again from the policy in force.

**Where the change goes.**

* **While the rules come from Frames** (the starting point): the draft becomes a proposal "Made from Settings",
  reviewed and released like any other (6.6), with two owners for widenings. When it is released, **Settings becomes
  the source of truth**: from then on, compiling the Frames is refused ("the rules are edited in Settings since the
  release in force came from there; the Frames are the SOPs people and cogs read"). Settings says so beforehand:
  "Proposing a change from Settings makes Settings the source of truth from then on".
* **Once the SOPs are documents** (6.8): Propose writes each change into the SOP it belongs to, as a draft revision on
  your open settings change order (6.9).

Cogs can do the same through their tools: `list_settings` reads the rules as data with their sentences, and
`propose_setting_change` proposes one change to the policy owners; a cog never applies a setting.

A rail with running limits ("at most 5 approvals a day", "at most 20,000 USD a month") is enforced from the cog's own
signed approvals of that process: once the limit is reached the cog declines, and the item goes to a person
(tests/test_settings.py).

## 6.8 SOPs and policies as documents

*Sections 6.8 to 6.12 describe the code as committed at `26962fa` (the change-order and automation work landed in
`0e4e85b` and `ef9de69`), re-read just before they were written. Work that was still uncommitted at that moment is
listed in 6.12.*

Up to now the rules were compiled from the `.frame.md` files. The hub can switch to a second mode in which **each
policy, SOP and work instruction is a controlled document in the library** (a .docx, edited in Collabora like any
other) and the Frame the hub enforces is **compiled from those documents** (hubproto/sopdocs.py; hubproto/app.py,
`sop_source`, `compile`).

**How to convert the Frames into documents** (an administrator, as a policy owner or document controller)

1. Run `hubproto sops convert --as carol` (in Docker: `docker compose exec hub hubproto sops convert --as carol`).
2. Each Frame of the release in force becomes a document, revision A, state **Effective**, owned by the Frame's
   maintainer: SOPs and work instructions (document types `sop` and `wi`) in Quality/Controlled Documents; policies
   (finance policy, purchasing policy, quality manual, organization model, cog profiles; type `policy`) and the
   reference guide (type `guide`) in Policy. Each heading becomes a heading and each line its own paragraph, so a
   citation such as "document-control-sop line 16" still points at the same sentence. The Frame's header is kept on
   the document.
3. The command then compiles from the documents and reports how many rules that changes. It should be none ("the
   documents read back exactly as the release in force"); otherwise it exits 1 and asks you to check them.
4. `hubproto sops status` lists each SOP with its document in force and any revision under way, with its change order.

Converting is done once; running it again does nothing. There is no button for it in the web pages.

**What changes once the SOPs are documents**

* **Compiling reads the documents in force**, not the files on disk. An edit to a `.frame.md` file is not policy any
  more ("an edit to the Frame file is not policy: the documents are the source", tests/test_eco.py).
* **A Frame is not edited directly.** A Frame's page says "This Frame is compiled from DOC-*n*. It is not edited here:
  change the document through a change order, and the Frame follows when the change order is released." **Edit this
  policy** is gone.
* **An SOP or policy document in force** shows "Compiled into the Frame. The rules the hub enforces are read from this
  document; change it through a change order, which carries the revision and stages the Frame it makes", with
  **Revise on a change order** for those who may revise it: its owner, its author, whoever created it, a document
  controller or a policy owner (hubproto/eco.py, `may_change_sop`). Everyone else sees why they may not.
* **Settings** (6.7): **Propose** writes your changes into the SOPs on a change order (6.9).
* When a change order is released, the hub also rewrites the `.frame.md` files on disk with the new text, so the files
  stay in step with the documents (hubproto/app.py, `release`).
* Proposing a new person or cog, or a cog's model, from the People and profile pages is refused with "the org-model
  Frame is compiled from DOC-*n*: change that document on a change order" (ran the code; chapter 9).

## 6.9 Change requests and change orders

The change-control SOP now separates two records (change-control-sop):

* A **change request** (CR) asks for a change and says why. It is approved on its own by the quality manager and
  verified by the requester (chapter 3.17). It changes nothing by itself.
* A **change order** (CO, "ECO") carries the change: a draft revision of every SOP and policy it changes, each approved
  one by one through document control, and the change requests it implements. "The hub compiles the Frame when a
  revision is approved (the compile-frame automation), so the rule changes show next to the text; the change order
  goes for approval only when that compiled Frame is ready, and its approval releases it: the release automation brings
  the Frame into force and the approved revisions into effect together."

Change orders live in Quality/Changes. **Quality** > **Change orders** lists them with their title, state, what they
carry, which change requests they implement and their staged Frame ("3 change(s)", or "1 error(s)"). **Prepare a
change order** on the home page and on the Quality app page opens this page; a change order has no start form.

### The life of a change order

| # | Stage | Who | What happens |
|---|---|---|---|
| 1 | Prepare | any member of staff | CO-*year*-*n* is created, state Open, you are its requester |
| 2 | Carry revisions | its requester or a document controller, for SOPs they may revise | each SOP or policy added gets a draft revision with the next revision letter (B, C, ...), revising the one in force |
| 3 | Write the revisions | the revision's author (whoever added it) and anyone who may change it | edited online like any document |
| 4 | Approve each revision | through document control (chapter 4.10) | on approval the revision is **held**: state Approved, waiting for the change order, not yet effective |
| 5 | Compile the Frame | the **compile-frame** automation, started by each revision's approval | compiles the Frame from the approved revisions against the release in force and stages it on the change order |
| 6 | Widenings | both policy owners, on the staged proposal | every rule change that is a widening needs both policy owners' approval (6.4) |
| 7 | Send for approval | its requester or a document controller, once nothing blocks it | starts the change-order process |
| 8 | Check the compiled Frame | automatic (frame-stager) | stops the run if the Frame is not ready |
| 9 | Approve the change order | the quality manager (carol) | due within 10 days; overdue items go to the management representative |
| 10 | Release | the **release-change-order** automation, started by the approval | the Frame comes into force, the revisions become effective, training is assigned; the change order becomes **Released** |

**How to prepare a change order** (any member of staff)

1. **Quality** > **Change orders**. Under **New change order** give a **Title\***, say **What it changes and why**,
   and tick the approved change requests it implements (you can add them later). Press **Prepare the change order**.
   The description is required before the change order can wait for approval.
2. On the change order's page, under **Carries**, choose an SOP or policy in **Add an SOP or policy** and press
   **Start its revision here**. The list shows only documents in force that you may revise and that the change order
   does not carry yet. One draft per SOP; an SOP already being revised on another change order is refused ("is already
   being revised (DOC-*n*, on CO-...)").
   You can also start from the SOP's own page: **Revise on a change order**, choosing one of your open change orders
   or **a new change order** (it is titled "Revise *title*").
3. Edit each draft (**Edit** in the Carries table opens it online). **compare with the version in force** shows what
   you changed. **Remove** takes an SOP off the change order and withdraws its draft.
4. Send each draft through document control: on the draft's page choose **minor or major?** and press **Send for
   approval** (chapter 4.10). A policy goes to a policy owner; a minor SOP revision goes to the doc-reviewer cog; others
   to the reviewers. When it is approved the draft shows "approved, held for this change order".
5. Watch **Staged Frame** (below). If it reports widenings, the policy owners approve them on the staged proposal.
6. When **Before it can be sent for approval** is gone, press **Send for approval**. It waits for the quality manager.

**The change order's page** shows, in a section of its own above the tabs:

* **Carries**: each SOP or policy, its draft revision ("DOC-0027 rev B, revises DOC-0011") and where it stands:
  "being written", "waiting for approval by ...", "approved, held for this change order" or "in force".
* **Implements**: the change requests, with their state, and a form to add an approved one.
* **Staged Frame**: what the policy becomes when the change order is released, as sentences ("Changed: ... was:
  ..."), with chips "widening: needs both policy owners" or "both policy owners approved", and a link to the staged
  proposal. Before anything is approved it shows a **preview** compiled from the drafts as they stand ("Preview from the
  drafts as they stand. The Frame that counts is compiled when each revision is approved"), and errors in the drafts
  ("The drafts do not compile as they stand"). The latest automation job is shown live, with **Retry** when it
  stopped, and **Compile the Frame again** when the staged Frame is out of date.
* **Before it can be sent for approval**: the blockers, in words (hubproto/eco.py, `check_approval`):
  * "DOC-*n* (*sop*) is ...: it must be approved through document control first";
  * "the Frame has not been compiled yet", "the Frame is being compiled (see the automation job)";
  * "the compiled Frame is not made of the approved revisions: compile it again";
  * "the Frame was compiled against a release no longer in force: compile it again";
  * the compile errors;
  * "widening: ... needs both policy owners (approved by bob)", or "rejected by a policy owner: ...";
  * "CR-... is not approved yet", "there is no change request CR-...";
  * "it carries no SOP or policy revision".
* **Send for approval** (its requester or a document controller), or "Sent for approval: it is in the change-order
  process", or "Released: its revisions and Frame are in force".

**Who approves what in a change order**

| Part | Approved by | Rule |
|---|---|---|
| each SOP or policy revision (its text, and its wording changes) | document control: policy owner for policies; doc-reviewer cog for a minor SOP revision; the reviewers otherwise | document-control-sop |
| each rule change that is a widening | both policy owners, on the staged proposal | hubproto/eco.py, `status` |
| rule changes that are not widenings | covered by the change order's approval | hubproto/eco.py, `activate_frame` |
| the change order | the quality manager, only when nothing blocks it | change-control-sop; hubproto/runner.py, `decide` |

When the Frame comes into force, the release is recorded as made by "change-order:CO-..." and each change that was
not a widening is recorded as "approved with CO-... (the revision was approved through document control)". The hub
refuses to approve a change order while anything blocks it ("the change order cannot be approved yet: ...").

**Segregation of duties.** The change-order process has no sentence of its own; the preparer rule applies, so the
requester may not approve their own change order. Because carol is the only person quality manager, a change order
she prepares stops at "Approve the change order" (ran the code; chapter 9).

**Settings changes as change orders.** Once the SOPs are documents, **Propose** in Settings does not make a proposal.
The hub writes each change into the SOP it belongs to: a changed rule replaces its sentence, a removed rule deletes it,
and a new rule is added at the end of the right section ("## Routing", "## Approval criteria" ...) of the SOP that
defines its process, or of the organization model for people, roles and folders (hubproto/sop_settings.py). The edits
go into draft revisions on your open settings change order ("Settings changes by *name*"; one is created if you have
none). The status line says "Written into the SOPs on CO-...: *n* sentence(s) changed; *m* could not be placed, see the
change order. Get each revision approved, then send the change order for approval." You must be allowed to revise each
SOP touched (6.8).

## 6.10 The automations: compile-frame and release

An **automation** is an Op the hub runs on its own, as a job with named steps that are recorded as they run. "An
automation never decides anything a person has not: it acts only on what was approved ... and it refuses when that
approval is missing or out of date" (hubproto/automation.py). Jobs run one at a time, oldest first, in the hub's own
queue lane; a job of the same automation on the same item is not started twice while one waits, and more than ten runs
in ten minutes on one item are stopped as a loop.

**compile-frame** ("Compile the Frame"). Started when a revision carried by a change order is approved, when another
release comes into force (for every other open change order with a staged Frame), or by hand with **Compile the Frame
again**.

| Step | What it does |
|---|---|
| Collect the approved revisions | lists the revisions approved and held for this change order, and those still to approve. With none approved it ends as "nothing to do" |
| Compile the Frame | compiles the Frame from those revisions against the release in force and stages it on the change order as its proposal, marked "staged by CO-..." (replacing any earlier one) |
| Check the guards | stops if the compiled Frame has errors; reports how many widenings need both policy owners |
| Stage it on the change order | records the release it was compiled against |
| Tell the people involved | tells the requester "The Frame for CO-... is compiled"; when there are widenings, tells every policy owner "CO-... widens authority: your approval is needed" |

**release-change-order** ("Release the change order"). Started when a change order is approved.

| Step | What it does |
|---|---|
| Check the change order is ready | the same checks as before approval; stops if anything blocks it |
| Bring the Frame into force | releases the staged proposal: the new policy version is in force |
| Publish the revisions | each carried revision becomes **Effective**; the revision it replaces becomes **Obsolete** |
| Assign training | training sign-offs for each revision's training roles (chapter 3.14) |
| Restage the other open change orders | starts compile-frame for each other open change order with a staged Frame, since the release they were compiled against is no longer in force |
| Tell the people involved | the change order becomes **Released**; its requester is told "CO-... is released" |

A job ends as **succeeded**, **stopped part way** (some steps done; retry it after fixing what it says), **nothing to
do** or **failed**. When a job stops, the change order's requester is told "*automation* stopped on CO-...". Steps
already done are not repeated on a retry.

## 6.11 The Automation page

**Automation** (`/automation`; in the **Policy** app, and linked from each change order's automation line) lists the
hub's jobs, newest first, with the item each acts on, its status (and the step running now), why it ran, when it
started and how long it took. It opens with the two automations and their triggers and steps, and has filters: all,
running, waiting, stopped part way, failed, succeeded. The page updates itself while jobs run. Any member may open it;
jobs about items you may not read are left out.

A job's page shows each step with a mark (done, running, failed, skipped), its outcome in words and how long it took;
the item, why it ran ("DOC-0027 was approved"), what it acts on ("approved by carol in run ..."), who started or
retried it, when, the attempts, and the policy version it ran under.

**How to retry a stopped job** (the change order's requester, a document controller or a policy owner): fix what the
outcome says, open the job and press **Retry from the step that stopped** (or **Retry** on the change order).

**How to compile a change order's Frame again** (the same people): when the change order says the compiled Frame is
out of date, press **Compile the Frame again** on its page.

## 6.12 Work in progress at the time of writing

When this chapter was written, the working tree held uncommitted changes that extend change orders. They are listed
here so the product owner knows they exist; they are not described elsewhere in this manual and may change:

* **Forms** (`/forms`, in the Quality and Settings apps): each record type's form layout (sections, field order,
  labels, help, conditions for when a field is shown, required or read-only, the start form, custom fields), edited by
  document controllers and policy owners as a per-person draft and **carried on a change order**. The release
  automation gains a step "Apply the form layouts", and a change order may carry form changes alone, without any SOP
  revision (hubproto/formlayout.py, hubproto/shell/forms_ui.py; not yet committed).
* **Withdrawing a draft** record ("withdrawn" state) from its page, and a cog tool `change_state` to hold, release a
  hold, withdraw a draft or send an item back to the start, checked against the person the cog works for.
* The quality-assistant cog gaining the author and reviewer roles in the organization model (org-model 1.5.0).
* Releasing a change into a Frame file that has other edits on disk merges the change instead of replacing the file.
