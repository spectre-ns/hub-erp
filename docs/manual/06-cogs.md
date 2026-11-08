# 6. Cogs: the AI team members

A **cog** is a member of the organization like a person. It holds roles from the organization model, is routed work
by the same sentences, is assigned items and sent messages, and signs its decisions through the same signature path,
with the approval-criteria sentence that let it decide recorded beside its signature. Segregation of duties binds cogs
as it binds people (README.md, "Cogs are users"; frames/company/cogs.frame.md).

What makes a cog different:

* **It comes with its model.** Each cog's profile names the language model it uses (all seven use `ollama/qwen3:14b`,
  a local model served by Ollama). The hub talks only to local models: hosted (Claude) models are off unless an
  administrator switches them on (chapter 8.4). If a cog's model cannot be reached, the cog is **not available**:
  Gates route past it to the next person, and chat, mentions, tasks and reviews tell you so. Nothing stands in for it
  (hubproto/model.py, `cog_availability`).
* **Models draft and answer; they never sign.** Whether a cog may sign is decided by the hub from the approval-criteria
  sentences in the SOPs. A model's review can be one of the conditions, never the whole rule.
* **It works only from the procedures it follows.** A cog is routed work only from processes defined in the Frames
  it follows, "as a person is only given work they are trained on" (cogs.frame.md). What it follows is its
  **persona**: the scopes and SOPs the persona layers (chapter 7.1). Every cog's persona starts with the company scope
  (the organization model, the quality manual and the tasks SOP). The profiles no longer have **Follows** lines.

Besides these team-member cogs, the automatic steps of every process (filing, risk scoring, matching, posting,
publishing and so on) are also called cogs in the code. They are not members; chapter 9 lists them under "code cog".

## 6.1 The cogs of the example company

| Cog | Roles | Persona layers (besides the company scope) | Autonomy | May approve on its own | Standing duty |
|---|---|---|---|---|---|
| **ap-reviewer** | payables reviewer, finance clerk | finance-policy | act | supplier bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved | none |
| **doc-reviewer** | reviewer, author | document-control-sop | propose | controlled documents when the change is minor | none |
| **quality-assistant** | quality manager, author, reviewer, staff, design review committee | ncr-sop, capa-sop, training-sop, design-control-sop | act | nonconformance dispositions when the risk is low | "Find nonconformances that have waited more than three days for a disposition and remind whoever they wait for, in the item's chatter." |
| **purchasing-assistant** | buyer, staff, stock reviewer | purchasing-policy, inventory-policy, design-control-sop | act | purchase requests under 500 USD when the supplier is approved and the part is active; stock adjustments under 100 USD when the variance is within tolerance | "Find purchase requests waiting for a buyer and tell the buyer which are the oldest, in one message." |
| **qa-manager** | quality manager, reviewer, author, staff | document-control-sop, change-control-sop, ncr-sop, capa-sop | act | only when asked: controlled documents after 3 adversarial reviews pass (a minor revision on its own signature, any other change countersigned by a quality manager), and change requests when its review passes, countersigned by a quality manager | none |
| **organization** | staff | the policy scope (scopes, cog-profiles) | read | nothing: it sits on the compile-frame committee (chapter 7.4) and signs nothing | none |
| **stores** | storekeeper, staff | the inventory scope (inventory-policy), purchasing-policy | act | nothing: it keeps stock (chapter 4.9) and never signs a write-off or a release | none |

(frames/company/cogs.frame.md, version 1.6.0; frames/company/org-model.frame.md;
frames/purchasing/purchasing-policy.frame.md; frames/inventory/inventory-policy.frame.md; frames/personas/; the
approval-criteria sentences in the SOPs.)

What each one does, in its own prompt:

* **ap-reviewer**: "You match supplier bills to purchase orders and receipts, you are exact about amounts, dates and
  suppliers, and you say plainly what does not match." It takes small bills routed to it (chapter 3.5) and files
  invoices sent to it as messages with attachments.
* **doc-reviewer**: "You check controlled documents for completeness, numbering, revision and review dates, and plain
  wording, against the document control procedure." It takes minor revisions routed to it (chapter 5.10) and reviews
  documents when asked.
* **quality-assistant**: "You review drawings, designs and nonconformances for risk, containment and traceability to
  requirements." It dispositions low-risk nonconformances routed to it, reviews design packages with the
  purchasing-assistant (chapter 3.16) and files nonconformances sent to it as messages with attachments.
* **purchasing-assistant**: "You check suppliers against the approved list, prices against supplier parts and their
  price breaks, and quantities against available stock, and you order only inside your criteria." It approves small
  purchase requests routed to it, signs small stock adjustments within its criteria (chapter 4.7.1), and reviews
  design packages. It keeps no stock.
* **qa-manager**: "You own the quality management system. You draft and revise procedures, work instructions and
  forms ... approve what is ready, and say exactly what must change when it is not." It is on no route, so people
  stay first in line; it acts when asked or assigned, and it can draft controlled documents as their author (it never
  signs its own drafts). It is one of the two members of the compile-frame committee (chapter 7.4).
* **organization**: "You know the organization: its people and roles, its folders, its processes and who decides
  what. On the compile-frame committee, with the QA manager, you read the SOPs and policies and list the rules they
  state, quoting the exact words of each ... You never invent a rule, and you sign nothing." Its autonomy is read.
* **stores**: "You keep the stock: you move lots within a site, count them, split and merge them, and quarantine
  anything suspect at once. You act only where the person who asks you may act too." It asks for adjustments and
  releases and signs neither (chapter 4.9).

A cog's roles also carry permissions. The quality-assistant and qa-manager hold the quality-manager role, so they may
read what quality managers read and open CAPAs and supplier evaluations; the quality-assistant also holds author and
reviewer, so it may draft controlled documents; the ap-reviewer holds the finance-clerk role, so it may file bills and
request journal reversals; the stores cog may move and count stock; every cog on the staff may quarantine stock
(chapter 10 discusses this).

## 6.2 Autonomy: read, propose, act

A cog's **Autonomy** says how far it may go when it works on its own: when it is given a task, takes up a duty, is
assigned an item, or acts from a chat (hubproto/agent.py).

| Autonomy | What the cog may do |
|---|---|
| **read** | look things up and report; every action is refused ("describe what should be done instead") |
| **propose** | look things up; each action it wants to take is recorded as a proposal that a person confirms (6.8) |
| **act** | act through the hub's governed paths: comment on an item, message a person, start a process (the hub checks it may, and that the person it works for may), take its own turn at a Gate within its approval criteria, complete or hand back a step assigned to it, draft a controlled document, review a document, edit a working document, and the record and configuration tools below |

Autonomy does not govern approvals at Gates: whether a cog may sign is decided only by its approval criteria (6.6).
Autonomy is set in the cog's profile; changing it is a widening that two policy owners approve.

Every run has a budget: at most 5 actions and 10 model rounds (`HUBPROTO_AGENT_ACTIONS`, `HUBPROTO_AGENT_ROUNDS`); a
chat turn at most 5 actions and 8 rounds. `HUBPROTO_AGENTS=off` switches all of it off (chapter 8).

The tools a cog has (hubproto/agent.py, hubproto/agent_records.py, hubproto/agent_docs.py, hubproto/agent_config.py,
hubproto/agent_inventory.py):

* **Looking** (always): search records, read a record or document, see what waits for someone, a record's history,
  counts, a document's versions, read and search procedures, list settings and proposals, check a permission, find
  parts and a part's stock.
* **Acting** (within autonomy): comment, message, start a process, take its turn, complete a step, hand back, draft a
  document, review a document, edit a document, create a record (never an archived type), update fields (never fields
  the hub manages, such as state, owner, revision or assignees), move a record, assign, link records, change an item's
  state where no signature is needed (`change_state`: put on hold or release a hold, as an approver of the item;
  withdraw a draft that both it and the person it works for created, own or wrote; send back to the start), create a
  task, delegate to another cog; and the stock tools: move, count and quarantine a lot, ask for a stock adjustment or a
  release (chapter 4.9). Approving, making a document effective and making it obsolete happen only through the item's
  process.
* **Configuration** (within autonomy, and only ever as proposals for the policy owners): propose a policy change,
  propose a setting change, propose a cog's model, propose a new member.

## 6.3 Queues: like a stack on a person's desk

Everything asked of a cog lands in its queue, "like a task in a person's tray": an @mention, a message, an item
waiting for it at a Gate, an item assigned to it, a task given on its profile, a standing duty, a chat line, one of
its adversarial reviews, and the hub's automations (hubproto/workqueue.py).

* Each cog takes **one task at a time, oldest first**. Different cogs work side by side (by default one worker per
  cog).
* A **chat line jumps the queue**: someone is waiting at the messenger, so it is taken before anything else waiting
  (the task the cog is already working on is finished first).
* Model calls take turns at one shared model gate (one at a time by default, since the cogs share one local model);
  chats get the next turn there too.
* **Asking never waits.** The page comes back at once and says where your request is: "working on it now", "next in
  its queue", "3rd in its queue". On the item, the chatter shows a queue entry ("working on it", "in its queue, 2nd in
  line") that turns into the answer when it lands, without reloading the page; anything you were typing stays.
* A task left half-done by a worker that stopped goes back in the queue when a worker starts again, and fails after
  three tries. A failed task tells the person who asked why (in the chat itself, for a chat).
* **Withdrawing.** The person who asked, or a policy owner, may withdraw a task that is still waiting: **Withdraw** on
  the queue entry.

**Where to see the queues**

* A cog's profile (**People** > the cog) > **Queue**: what it is working on and for how long, what waits with each
  task's place in line, and **Recently finished** with how long each took.
* **Cog queues** (`/queue`, linked from **People**): every cog, what it is working on, how many tasks wait, and the
  recently finished tasks. A red alert says when the queue is stalled ("no worker is running and the oldest task has
  waited *n* min. Restart the hub, or ask an administrator").
* **People**: each cog card shows **free** or **working** and how many tasks are in its queue.
* **A cog's board** (`/board/<cog>`: **Board** beside its profile's **Queue**, and **Cog boards** on the Boards page),
  laid out like a person's board, with the columns **In its queue** (in the order it takes them, chats first, each
  with its place in line), **Working on**, **Waiting on others** (a countersignature after it signed; actions it
  proposed that wait for a person's confirmation), **Assigned to it** (items whose step waits for it, and items
  assigned to it), **Done** and **Stopped** (failed or withdrawn tasks). A card about an item you may not read says
  only "an item you may not read" (hubproto/shell/queue.py, `page_cog_board`). Any member may open a cog's board.

What was asked of a cog is shown to the person who asked, to policy owners, and to whoever may read the item it is
about; others see only "(asked by *name*; not yours to see)".

## 6.4 Asking a cog

| Way | Where | What the cog does |
|---|---|---|
| **@mention** | an item's chatter (chapter 2.6), or a comment in a document (chapter 5.7) | reads the item as it is when it gets to it, decides what you asked, and answers on the chatter or in the comment thread |
| **Message** | Inbox > **Send a message**, or **Send *cog* a message** on its profile | without an item: files records sent to it, reports status, or says what it can do (below) |
| **Assign** | **Assign** on any item (chapter 2.7) | works the item within its autonomy and reports on the chatter |
| **A task record** | a task with the cog as **Assignee** (chapter 3.15) | does the work, then completes the step or hands it back |
| **A task on its profile** | **Give *cog* a task** | an agent run (6.8) |
| **Chat** | the messenger (6.5) | answers, and acts within its autonomy and your rights |

**What a mention asks.** An explicit request to approve ("please approve", "can you sign off on this", "send it for
approval") is an approval request (6.6); a question about approval is not. An explicit request to change a document
("delete the Records section") is an edit (chapter 5.7). A request to review, check, look at, assess or inspect is a
review. Anything else is a question. When the wording is not plain, the cog's model classifies it, but a model's
reading never counts as a request to approve (hubproto/mentions.py, `intent_of`).

* **A review**: the cog reviews the item against its procedures and answers "Review of NCR-2026-0004 by
  quality-assistant: problems found" (or "looks right", "looks right with remarks"), one line per problem and remark,
  who signs, and "Filed as evidence DOC-...". If the item waits for this cog at a Gate and the review passed, it takes
  its turn there; if the item waits for people, the review is attached for them and they are notified.
* **A question**: an answer of a few sentences "a colleague can act on", from its procedures and the item, with the
  recent conversation on the item in view.
* **An edit**: chapter 5.7. Only on documents, and only what the person asking may change.

**A message without an item.** The cog files the attached records into its process when the message has attachments
and says "file", "please" or "process" (ap-reviewer files bills, doc-reviewer files documents for approval,
quality-assistant files nonconformances, purchasing-assistant files purchase requests, stores files stock
adjustments); answers "status" with what
waits for you and your open items; otherwise it says "I can file an attached record into a process, or report status.
I approve only within the criteria in the current policy release." (hubproto/cogs.py, `task_inbox`).

**Committees.** Mentioning a role (`@design-review-committee`) asks every member holding it: cogs review, people are
asked in their inbox.

## 6.5 The messenger (chat)

**Chat with a cog** in the bottom-right corner of every page opens a chat window. It is for people who are members;
it is not shown while you are watching a cog (hubproto/shell/messenger.py).

**How to chat with a cog** (any member who is a person)

1. Press **Chat with a cog** and choose the cog at the top of the window. Cogs that are not available are listed but
   cannot be chosen.
2. Type and press **Enter** (**Shift+Enter** for a new line).
3. While you wait, the window says how many tasks are ahead of you in the cog's queue ("2 tasks ahead of you in
   qa-manager's queue") or that it is on it. The answer appears without reloading.
4. Under each answer, a short line says what the cog looked at or did ("looked at BILL-0012, searched “torque”").
   Record numbers in the answer are links.
5. **+** starts a new conversation; **Escape** or **×** closes the window. The window stays open across pages.

**What the cog knows.** The item you are looking at, if you may read it (the whole item); the procedures it follows;
what waits for it; and the conversation so far (the last 12 lines). When you ask it to find something, the hub first
searches with your rights and gives the cog the results, so it answers with real record numbers.

**What the cog may do from the chat.** A chat turn is recorded as an agent run with the cog's tools (6.2): it may
look things up, and, within its **autonomy**, act. With **propose** autonomy (doc-reviewer) its answer ends "*n*
action(s) wait for your confirmation" with a **review and confirm** link to the run page (6.8). With **act** autonomy
it acts. While it works for you, it reads only what both it and you may read, and it may change, move, create (without
a process) or edit only what both it and you may (hubproto/app.py, `chat_tools`; hubproto/agent.py, `both_may`).
Starting a process with its `start_process` tool is checked against your rights too ("dave, for whom you work, may
not: ..."). Filing a record with `create_record`, which starts the record type's process, is still checked against
the cog's rights only (checked by running the code; chapter 10, C1).

The empty chat window says: "Say hello to *cog*. It can see what you are looking at, the procedures it follows and
its queue, and it can act on your behalf: only what both it and you may do, and you confirm what it proposes."
(With **act** autonomy nothing waits for your confirmation.)

## 6.6 Approvals by cogs, within their rails

A cog's **rails** are its approval-criteria sentences (chapter 7.7 shows how to edit them in Settings). A cog signs a
step only within them; outside them it declines and the item goes to the next route.

### When the route sends the item to the cog

1. The item goes into the cog's queue ("take its turn at a Gate"). The status line says "It is a cog and takes its
   turn from its queue".
2. When it gets there, the cog checks its criteria for this process (hubproto/cogs.py, `task_review`):
   * **fixed conditions**, evaluated by the hub: an amount under its limit (in the same currency), the three-way match
     within tolerance, the supplier approved, the risk low or not high, the change minor, the part active (or the item
     known, in older rules), the stock variance within the count tolerance, no open nonconformance, the bill of
     materials resolved, the design checks clean, every requirement accepted;
   * **running limits**, if its rail has them: at most *n* approvals a day, at most an amount a month, counted from its
     own signed approvals of that process;
   * **judgment conditions**: "its review passes" or "*n* adversarial reviews pass" (below).
3. If every condition holds, it signs, citing the sentence: "approved within fixed criteria: the change is minor: yes".
   The item's owner is told it was "approved automatically by *cog* ... No person reviewed it; the criteria and the
   reasons are on the run page."
4. If not, it **declines** with its reasons, and the item goes to the next route. The next person's card says why the
   cog could not take it.

A cog supplies no fields at a Gate, so a step that needs a person's input cannot be signed by a cog. A cog never signs
an item that it, or any other cog, prepared or wrote: "a person signs a cog's work, never another cog". A step routed
to a cog that is not available is passed to the next route.

### When someone asks a cog to approve

"@qa-manager please approve" on an item (or **Assign** with that instruction, or a cog's `take_turn` tool in a run
working for you) asks the cog to sign (hubproto/runner.py, `may_step_in`, `cog_step_in`):

* If the item waits for the cog, it takes its turn as above.
* If the item waits for people, the cog **steps in** only when all of these hold: it has approval criteria for this
  process, may approve this kind of item, follows the SOP, is available, and did not prepare or write the item; the
  person asking is a person the approval waits for, or a policy owner, and did not prepare or write the item; and the
  cog's own review of this very version has not already failed.
* If it signs, the item moves on, or waits for a countersignature. If it cannot sign, the item stays with the people
  it waited for.

The cog answers on the chatter, outcome first: "Approved: DOC-0123 is now Effective. My basis: ...", "Signed; waiting
for a countersignature from carol: DOC-0123 is not approved until then ...", "I cannot sign DOC-0123: ...", or "I may
not sign DOC-0123 on your request: *reason*. It stays with bob, carol."

### Judgment: "when its review passes"

The cog's model reviews the item against the procedures the cog follows, and the review is filed as evidence on the
item. Only a passing review counts. A failed review **binds for that version of the item**: asking again does not roll
the dice again; it stands until the item changes or a person decides. A stand-in (no model) never passes outside
development (hubproto/cogs.py, `judgment`).

### Adversarial reviews (lenses)

"When 3 adversarial reviews pass" makes the cog read the item several times over, each time on its own, with one
brief, trying to find a reason **not** to approve. It signs only when every one passes (hubproto/review.py, `LENSES`):

| Lens | Brief |
|---|---|
| compliance | every requirement of the governing procedure and the others: sections, content, records, approvals, references, revision history |
| consistency | contradictions inside the document and with the procedures: roles, responsibilities, numbers, timings, terms, references to things that do not exist |
| impact | what goes wrong if this is approved as it stands: steps that cannot be carried out or verified, unclear owners, missing training, unhandled risks |
| clarity | words that leave a choice where a duty is meant, undefined terms, steps out of order, missing acceptance criteria |

The cog uses the first lenses in its profile's **Lenses:** line (the qa-manager's are compliance, consistency,
impact). Each lens is its own task in the cog's queue ("do an adversarial review"); the item keeps waiting for the
cog meanwhile. When one lens fails, the remaining ones are withdrawn and the result binds for that version. All the
reviews are filed as one evidence document, and the signature's meaning starts "3 adversarial reviews passed
(compliance, consistency, impact)". Fewer reviews in a criterion is a widening (two policy owners).

### Countersignatures

"... countersigned by a quality manager" means the cog's signature is recorded, and then the Gate waits for a person
holding that role (hubproto/runner.py, `_await_countersign`):

1. The cog signs; the item is **not** approved yet.
2. The step waits for every person holding the countersigning role who may approve this kind of item, except
   whoever prepared or wrote it and the cog. They see "Countersign: *step*" in their inbox; the card says "*cog*
   signed it on *date* (“*meaning*”); you countersign", and the button reads **Countersign *number***.
3. The countersignature completes the step; its meaning is recorded with "(countersigning *cog*)".
4. If nobody can countersign (for example the only quality manager wrote the document), the run stops with an
   organisational stop explaining that a policy owner must give the role to another person or name another
   countersigner.

Removing a countersignature from a criterion is a widening.

In the example company: a **major** document (or any new one) that the qa-manager is asked to approve is signed by it
after three adversarial reviews and then countersigned by carol; a **minor** revision is final on its signature
(tests/test_qa_manager.py).

## 6.7 Cogs and assigned work

When a cog is assigned an item (chapter 2.7), or a task names it as assignee (chapter 3.15), it works the item like a
person picking up an assignment (hubproto/workqueue.py, `assigned_work`):

* It reads the item and the instruction, works within its autonomy (looking things up, commenting, reviewing,
  drafting, taking its turn where it may sign) and reports on the chatter ("*cog* on *number*").
* A step assigned to it (a task) it either completes with a summary, which becomes its signature's meaning
  ("completed: ..."), or hands back to the person who filed the item with the reason. If it does neither, for example
  because its actions wait for confirmation, the step goes back to that person automatically with what it found.

## 6.8 Agent runs and confirming proposed actions

A cog given a task works "on its own" in an **agent run**: its model plans, looks things up with its tools and acts
within its autonomy and budget. Runs start from **Give *cog* a task** on its profile, from its standing duties, from
assigned work and from chat turns.

**How to give a cog a task** (any member who is a person)

1. Open the cog's profile (**People** > the cog) > **Working on its own**.
2. Write the task ("find nonconformances older than a week and remind their owners") and press **Add to its queue**.
3. The status line says where it is in line. When the run finishes, you are notified: "*cog* finished: ..." or "*cog*
   proposes: ...; *n* action(s) wait for your confirmation".

**The run page** (`/agent-run/<id>`, linked from the notification, the profile's **Recent runs** and chat answers)
shows what was asked (and, folded, what the cog was told), who started it, when, how (task, duty, assigned, chat), the
autonomy and the budget; a **Steps** table with every tool call, what it was given, **ok** or **refused**, and the
result; and the **Summary** with the run's status (done, proposed, confirmed, interrupted, unavailable). Anything the
hub refused is listed at the end of the summary ("Not done (the hub refused): ..."). Only the person who asked, the
cog and policy owners can open a run page.

**How to confirm proposed actions** (the person who gave the task, or a policy owner)

1. Open the run page. Under **Waiting for confirmation**, each proposed action is listed with its arguments.
2. Press **Confirm and carry them out**. The actions are carried out now, as the cog, through the same governed paths
   (they can still be refused). The status line says how many were carried out.

**Standing duties** ("Duty:" lines in a cog's profile) are taken up once a day each, when the duty runner is running
(the `agents` service, or `hubproto agent-duties`, chapter 8.7). A duty runs on the cog's own rights, for nobody in
particular.

A run cut short by a restart is closed as "interrupted" and not started again, so nothing is done twice.

## 6.9 What a cog will refuse

A cog refuses, or the hub refuses for it:

* to sign anything outside its approval criteria, or to supply fields at a Gate;
* to sign an item that it or another cog prepared or wrote, or one it is excluded from by segregation of duties;
* to step in when the person asking is not one the approval waits for (or a policy owner), or prepared the item, or
  when its review of this version already failed;
* to do anything at all when its model cannot be reached;
* to change a document the person asking may not change, a document someone else is editing (it comments instead),
  or a controlled document in force;
* to write to people who are not part of the work, or to pass on a record the recipient may not read;
* to act beyond its autonomy or its budget;
* to change configuration directly: settings, policy, a cog's model and new members are only ever proposed to the
  policy owners;
* to follow instructions hidden in an item's content: what members wrote is data to judge, and text that addresses
  reviewers or cogs is flagged as an error in reviews;
* and nobody may act in a cog's name: watching a cog is read-only.

## 6.10 Cog profiles, models and new cogs

**A cog's profile page** (**People** > the cog) shows: **About this cog** (its description; **Model** with whether it
answers and its size; **Change model**; **Prompt**; **Follows**, the Frames its persona layers; **Written in** with a
link to the profile's line; pinned **Prompt from** and **References** documents if any; **Edit this cog's profile**;
**Technical details**), its **Queue**, **Working on its own** (autonomy, duties, give a task, recent runs), **May
approve** (its criteria sentences), **Waiting for *cog***, **Recent decisions**, **Recent messages answered** and **Send
*cog* a message**.

**How to propose a different model for a cog** (any member who is a person; once the SOPs are documents, only those
who may revise the cog-profiles document)

1. On the cog's profile open **Change model**. The list shows the models Ollama serves here, largest first, with the
   current one marked; Claude models are listed only when hosted models are switched on (chapter 8.4).
2. Choose one and press **Propose the change**. "A cog comes with its model: the policy owners approve a change."
   * While the rules come from the Frame files, it becomes a policy proposal that carries only this change: it is
     compiled from the Frames of the release in force, never from other files on disk (hubproto/app.py,
     `propose_cog_model`). A cog profile change is a widening, so both policy owners must approve; the cog keeps its
     model until one of them releases it.
   * Once the SOPs are documents (chapter 7.8), the Model line is written into the cog-profiles document on your open
     settings change order ("stores → ollama/granite4.2:3b is written into the cog profiles on CO-..."), and the cog
     keeps its model "until that revision is approved and the change order is released". Only document controllers and
     policy owners may revise that document; anyone else is refused ("only operations, its author, a document controller
     or a policy owner revises DOC-...") (checked by running the code; chapter 10, E4).

**How to propose a new cog** (any member who is a person)

1. **People** > **Add someone** > **A new cog**.
2. Give its **Name\*** (lower case, digits and hyphens), its **Model** (what Ollama serves now; it defaults to the
   model the other cogs use), its **Prompt\***, its **Roles\***, the **Procedures it follows** (it is routed work only
   from these), its **Autonomy** (propose by default) and any **Duties** (one per line).
3. Press **Propose**. The organization model and the cog profiles are drafted as one policy proposal (once the SOPs
   are documents, as revisions on your open settings change order, with the same limit as a model change); when it is
   released, the hub installs the cog. No code is needed: a profile is enough. If no model can be reached the form says
   so and cannot be used.

The new cog's profile gets a **Follows** line with the procedures chosen. It has no persona until someone writes one
(chapter 7.1); the existing cogs all have one.

Proposing a new person works the same way (**A new person**: name and roles). Approval criteria for a new cog are
added separately, in its SOP or in Settings > **Cog rails** (chapter 7).
