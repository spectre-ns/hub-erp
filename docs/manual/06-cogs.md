# 6. Cogs: the AI team members

A **cog** is a member of the organization like a person. It holds roles in the organization model, is routed work by
the same sentences, is assigned items and sent messages, and signs its decisions through the same signature path, with
the approval-criteria sentence that let it decide recorded beside its signature. Segregation of duties binds cogs as it
binds people (frames/company/cogs.frame.md; frames/company/org-model.frame.md).

The rule the product owner set for every question about cogs is: **when in question, treat a cog as a person with
training and a personality** (docs/design/change-control.md, principle 7). Its training is the documents whose Roles
and responsibilities tables name it or its roles; its personality is its persona. So a cog is given a role the way a
person is, by a change order to the organization model; an SOP that gives a cog work its roles do not cover produces a
*suggested* role grant on the change order, never an automatic one; and a change order shows what it does to each cog
(chapter 7.9).

What makes a cog different:

* **It comes with its model.** Each cog's profile names the model it is bundled with. Nine cogs use the local
  `ollama/qwen3:14b`; the two **senior** cogs use the hosted `claude-opus-5-5` (6.12). If a cog's model cannot be
  reached, the cog is **not available**: Gates route past it to the next person, a committee it sits on pauses, and
  chat, mentions, tasks and reviews tell you so. Nothing stands in for it (hubproto/model.py, `cog_availability`).
* **Models draft and answer; they never sign.** Whether a cog may sign is decided by the hub from the approval-criteria
  sentences in the SOPs. A model's review can be one of the conditions, never the whole rule. A cog never signs a
  change order and never decides an item of a change order's delta.
* **It has no private memory.** What it works from is its training, the knowledge base through its tools, the item and
  the conversation it is in (6.11).

Besides these team-member cogs, the automatic steps of every process (filing, risk scoring, matching, posting,
publishing and so on) are also called cogs in the code. They are not members; chapter 9 lists them under "code cog".

## 6.1 How a cog is made

Almost everything a cog is comes from the SOPs, the same documents a person's work comes from:

1. **Membership, roles and groups.** The organization model lists the cog as a member with its roles, and may put it in
   groups (chapter 7.6). Its roles and groups are all the permission it has: the `## Permissions` sentences say what
   each role, group or named cog may create, read, update, approve or delete, and in which folder. The profile page
   lists every permission that reaches it and what it comes through ("its role system administrator", "the *x* group",
   "its own name").
2. **What it follows (its context).** The company scope (the organization model, the quality manual and the tasks SOP)
   plus every document whose Roles and responsibilities table names the cog, one of its roles or one of its groups
   (chapter 7.15; hubproto/release.py, `follows_for`). A cog is routed work only from processes in documents it follows
   ("as a person is only given work they are trained on").
3. **Where it takes a turn.** The `## Routing` sentences of those SOPs, and workflow gates that name it or a committee
   it sits on (chapter 3.1).
4. **What it may sign.** The `## Approval criteria` sentences: its **rails** (6.6).
5. **Its standing duties.** `## Duties` sentences in the SOP that owns the work: "Every week the it-admin cog reviews
   the open change orders ..." (6.8).
6. **How far it acts on its own** is derived from all of the above, action by action (6.2).

What is left to the cog itself is its **identity** and its **personality**:

* **A cog profile is identity** (`## Cog: <name>` in the cog profiles): `About:` (what it does, in one paragraph),
  `Model:` (its bundled model) and `Persona:`, and optionally `Prompt from:` (a document pinned as its prompt) and
  `References:` (documents pinned into its reference material). The old `Prompt:`, `Lenses:`, `Autonomy:`, `Follows:`,
  `Reviews:` and `Duty:` lines are errors now, each saying where that lives ("lenses are in the cog's persona
  (Lenses:)", "autonomy is derived per action from the SOPs ...", "a duty is a sentence in the SOP that owns the work
  ...") (hubproto/extract.py).
* **A persona is a behaviour document**, shared by any number of cogs, for cogs only (frames/personas/):
  `Tone:` (formal or informal), `Stance:` (reviewer, executor, author or advisor), `Lenses:` (for adversarial
  reviews), `Reviews: n adversarial passes`, `Escalates:` and `Behaviour:` (its behaviour prompt). Role personas are
  gone: a role's required reading comes from the tables.

Both are documents under change control: changing a cog's model, persona or About is a change order (6.10).

### The cogs of the example company

| Cog | Roles | Group | Persona | Model | May approve on its own |
|---|---|---|---|---|---|
| **ap-reviewer** | finance clerk, payables reviewer | junior-cogs | reviewer | ollama/qwen3:14b | supplier bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved |
| **doc-reviewer** | author, reviewer | junior-cogs | reviewer | ollama/qwen3:14b | controlled documents when the change is minor |
| **quality-assistant** | design review committee, staff | junior-cogs | reviewer | ollama/qwen3:14b | nonconformance dispositions when the risk is low |
| **purchasing-assistant** | buyer, staff, stock reviewer | none | executor | ollama/qwen3:14b | purchase requests under 500 USD when the supplier is approved and the part is active; stock adjustments under 100 USD when the variance is within tolerance |
| **qa-manager** | author, reviewer, staff | junior-cogs | author | ollama/qwen3:14b | only when asked: documents after 3 adversarial reviews pass (a minor change on its own signature, any other countersigned by a quality manager); change requests when its review passes, countersigned by a quality manager |
| **organization** | policy reader | junior-cogs | advisor | ollama/qwen3:14b | nothing |
| **stores** | staff, storekeeper | none | executor | ollama/qwen3:14b | nothing: it keeps stock (chapter 4.9) |
| **it-admin** | system administrator | junior-cogs | executor | ollama/qwen3:14b | nothing ("It changes no configuration itself and approves no policy") |
| **management** | management reviewer | junior-cogs | advisor | ollama/qwen3:14b | nothing |
| **senior-organization** | policy reader | senior-cogs | advisor | claude-opus-5-5 | nothing: it sits on the compile-frame committee |
| **senior-qa-manager** | policy reader | senior-cogs | author | claude-opus-5-5 | nothing: it sits on the compile-frame committee |
| **auditor** | internal auditor | none | reviewer | claude-opus-5-5 | nothing: it runs the internal audit and records findings; the quality manager accepts them (6.14) |

(frames/company/cogs.frame.md, version 2.2.0; frames/company/org-model.frame.md; the `## Members` lines of the
purchasing policy, the inventory policy and the design-control SOP; the approval-criteria sentences in the SOPs;
checked by running the code.)

| Persona | Tone | Stance | Lenses | Passes | Cogs |
|---|---|---|---|---|---|
| reviewer | formal | reviewer | compliance, consistency, impact | 3 | ap-reviewer, auditor, doc-reviewer, quality-assistant |
| author | formal | author | compliance, consistency, impact | 3 | qa-manager, senior-qa-manager |
| executor | informal | executor | compliance | 1 | it-admin, purchasing-assistant, stores |
| advisor | formal | advisor | consistency, impact | 2 | management, organization, senior-organization |

**Duties** (SOP sentences): every day the quality-assistant (ncr-sop) and the purchasing-assistant (purchasing-policy);
every week the it-admin reviews the open change orders that touch permissions, record types or forms (it-sop); every
month the management cog summarises the nonconformances, corrective actions and audit results and comments the summary
on the open management review (management-review-sop). Every month the auditor cog audits the next process in the programme (audit-sop).

**What is new.** The **it-admin** cog works with bob, who also holds the system-administrator role, under the IT SOP
(chapter 7.16): it reviews changes that touch permissions, record types, forms, cog profiles and personas. The
**management** cog reviews changes to roles, limits and segregation of duties. Both are suggested for change orders'
review panels by the change-control SOP (chapter 7.9). No cog holds the quality-manager role any more; the
quality-assistant and the qa-manager read and act through the roles listed above.

## 6.2 Autonomy: derived per action

A cog's autonomy is not set in its profile. For each action it wants to take when it works on its own (a task, a duty,
an assigned item, a chat), the hub decides from the SOPs (hubproto/authz.py, `autonomy`; hubproto/agent.py, `decide`):

| The hub finds | The cog |
|---|---|
| a "may not" that applies | is refused |
| a permission "... when a *role* (or a person) confirms" | **proposes**: the action is recorded and waits for that person to confirm (6.8) |
| a permission for it, its roles or groups, or the step is its own | **acts**, through the hub's governed paths |
| nothing | only reads, and says what should be done: "no SOP lets X (or its roles ...) *action* *object* in *folder*" |

Its own steps (taking its turn, completing or handing back a step assigned to it) always act. In the example company
the only "when a person confirms" permissions are the doc-reviewer's: commenting on anything in Quality, and creating
change orders and documents, each when a document controller confirms. The profile's **Working on its own** says:
"Autonomy derived: for each action, X acts where the SOPs give it or its roles the permission or the step, proposes
where an SOP says a person confirms, and otherwise only reads ... Changing it is a change to those SOPs, through a change
order." **What it may do** lists each permission with "acts", "proposes: a person confirms" or "may not".

Autonomy does not govern approvals at Gates: whether a cog may sign is decided only by its approval criteria (6.6).

Every run has a budget: at most 5 actions and 10 model rounds (`HUBPROTO_AGENT_ACTIONS`, `HUBPROTO_AGENT_ROUNDS`); a
chat turn at most 5 actions and 8 rounds. `HUBPROTO_AGENTS=off` switches all of it off (chapter 8).

The tools a cog has (hubproto/agent.py, hubproto/agent_records.py, hubproto/agent_docs.py, hubproto/agent_config.py,
hubproto/agent_inventory.py):

* **Looking** (always, within its read rights, 6.11): search records, read a record or document, see what waits for
  someone, a record's history, counts, a document's versions, read and search procedures, read a section of an SOP,
  list settings and proposals, check a permission, find parts and a part's stock.
* **Acting** (where the SOPs let it): comment, message, start a process (the hub checks it may, and that the person it
  works for may), take its turn, complete a step, hand back, draft a document, review a document, edit a document,
  create a record, update fields (never fields the hub manages), move, assign, link, change an item's state where no
  signature is needed, create a task, delegate to another cog, and the stock tools (chapter 4.9). Approving, making a
  document effective and making it obsolete happen only through the item's process.
* **Configuration**: a cog's configuration tools record their change as a direct change on a change order, like
  Settings (chapter 7.7); a cog never applies configuration. (Their descriptions still say "policy proposal".)

## 6.3 Queues: like a stack on a person's desk

Everything asked of a cog lands in its queue, "like a task in a person's tray": an @mention, a message, an item
waiting for it at a Gate, an item assigned to it, a task given on its profile, a standing duty, a chat line, one of
its adversarial reviews, a change order's review (`co-review`), a committee vote, and the hub's automations
(hubproto/workqueue.py).

* Each cog takes **one task at a time, oldest first**. Different cogs work side by side (by default one worker per
  cog).
* A **chat line jumps the queue**: someone is waiting at the messenger, so it is taken before anything else waiting
  (the task the cog is already working on is finished first).
* Model calls take turns at one shared model gate (one at a time by default); chats get the next turn there too.
* **Asking never waits.** The page comes back at once and says where your request is: "working on it now", "next in
  its queue", "3rd in its queue". On the item, the chatter shows a queue entry that turns into the answer when it lands,
  without reloading the page.
* A task left half-done by a worker that stopped goes back in the queue when a worker starts again, and fails after
  three tries. A failed task tells the person who asked why.
* **Withdrawing.** The person who asked, or a policy owner, may withdraw a task that is still waiting: **Withdraw** on
  the queue entry.

**Where to see the queues**: a cog's profile > **Queue**; **Cog queues** (`/queue`, linked from **People**), with a
red alert when the queue is stalled; **People** (each cog card shows **free** or **working**); and **a cog's board**
(`/board/<cog>`), with the columns **In its queue**, **Working on**, **Waiting on others**, **Assigned to it**,
**Done** and **Stopped** (hubproto/shell/queue.py, `page_cog_board`). What was asked of a cog is shown to the person who
asked, to policy owners, and to whoever may read the item it is about.

## 6.4 Asking a cog

| Way | Where | What the cog does |
|---|---|---|
| **@mention** | an item's chatter (chapter 2.6), or a comment in a document (chapter 5.7) | reads the item as it is when it gets to it, decides what you asked, and answers on the chatter or in the comment thread |
| **Message** | Inbox > **Send a message**, or **Send *cog* a message** on its profile | without an item: files records sent to it, reports status, or says what it can do (below) |
| **Assign** | **Assign** on any item (chapter 2.7) | works the item where the SOPs let it and reports on the chatter |
| **A task record** | a task with the cog as **Assignee** (chapter 3.15) | does the work, then completes the step or hands it back |
| **A task on its profile** | **Give *cog* a task** | an agent run (6.8) |
| **Chat** | the messenger (6.5) | answers, and acts where the SOPs and your rights let it |
| **Review panel** | a change order's review round (chapter 7.9) | reviews the delta and returns findings, questions and suggestions |

**What a mention asks.** An explicit request to approve ("please approve", "can you sign off on this") is an approval
request (6.6); a question about approval is not. An explicit request to change a document is an edit (chapter 5.7). A
request to review, check, look at, assess or inspect is a review. Anything else is a question. When the wording is not
plain, the cog's model classifies it, but a model's reading never counts as a request to approve
(hubproto/mentions.py, `intent_of`).

* **A review**: the cog reviews the item against its procedures and answers "Review of NCR-2026-0004 by
  quality-assistant: problems found" (or "looks right", "looks right with remarks"), one line per problem and remark,
  who signs, and "Filed as evidence DOC-...". If the item waits for this cog at a Gate and the review passed, it takes
  its turn there; if the item waits for people, the review is attached for them and they are notified.
* **A question**: an answer of a few sentences "a colleague can act on", from its procedures and the item.
* **An edit**: chapter 5.7. Only on documents, and only what the person asking may change.

**A message without an item.** The cog files the attached records into its process when the message has attachments
and says "file", "please" or "process"; answers "status" with what waits for you and your open items; otherwise it says
what it can do (hubproto/cogs.py, `task_inbox`).

Mentioning a role (`@design-review-committee`) asks every member holding it: cogs review, people are asked in their
inbox. That is a role, not a committee of chapter 7.6.

## 6.5 The messenger (chat)

**Chat with a cog** in the bottom-right corner of every page opens a chat window. It is for people who are members; it
is not shown while you are watching a cog (hubproto/shell/messenger.py).

**How to chat with a cog** (any member who is a person)

1. Press **Chat with a cog** and choose the cog at the top of the window. Cogs that are not available are listed but
   cannot be chosen.
2. Type and press **Enter** (**Shift+Enter** for a new line).
3. While you wait, the window says how many tasks are ahead of you in the cog's queue, or that it is on it. The answer
   appears without reloading.
4. Under each answer, a short line says what the cog looked at or did. Record numbers in the answer are links.
5. **+** starts a new conversation; **Escape** or **×** closes the window.

**A chat carries its context.** The conversation is a record both sides can read. The cog is given the last 12 lines of
it as the conversation so far, and its prompt's **THE CONVERSATION** section says who it is chatting with, what they
are looking at and what waits in its queue (hubproto/workqueue.py; hubproto/cogs.py, `chat_prompt`). **+** (New
conversation) marks the earlier lines cleared: they stay on file and the cog no longer sees them (hubproto/inbox.py,
`chat_clear`). When you ask it to find something, the hub first searches with your rights and gives the cog the
results.

**What the cog may do from the chat.** A chat turn is an agent run with the cog's tools (6.2). Where the SOPs say a
person confirms, its answer ends "*n* action(s) wait for your confirmation" with a link to the run page (6.8); where
they let it act, it acts. While it works for you, it reads only what both it and you may read, and changes only what
both may (hubproto/app.py, `chat_tools`; hubproto/agent.py, `both_may`). Starting a process with `start_process` is
checked against your rights too; filing a record with `create_record`, which starts the record type's process, is still
checked against the cog's rights only (chapter 10, C1).

<!-- figure: c06-messenger -->

## 6.6 Approvals by cogs, within their rails

A cog's **rails** are its approval-criteria sentences (Settings > **Cog rails**, chapter 7.7, shows them; changing one is
a change order). A cog signs a step only within them; outside them it declines and the item goes to the next route.

### When the route sends the item to the cog

1. The item goes into the cog's queue ("take its turn at a Gate").
2. When it gets there, the cog checks its criteria for this process (hubproto/cogs.py, `task_review`):
   * **fixed conditions**, evaluated by the hub: an amount under its limit (in the same currency), the three-way match
     within tolerance, the supplier approved, the risk low or not high, the change minor, the part active, the stock
     variance within the count tolerance, no open nonconformance, the bill of materials resolved, the design checks
     clean, every requirement accepted;
   * **running limits**, if its rail has them: at most *n* approvals a day, at most an amount a month;
   * **judgment conditions**: "its review passes" or "*n* adversarial reviews pass" (below).
3. If every condition holds, it signs, citing the sentence. The item's owner is told it was "approved automatically by
   *cog* ... No person reviewed it".
4. If not, it **declines** with its reasons, and the item goes to the next route. The next person's card says why the
   cog could not take it.

"A cog's work is signed by a person" is a segregation-of-duties sentence in the organization model now (line 94), so a
cog never signs an item that it or another cog prepared or wrote; that is configuration, changed only by a change
order. "A cog never signs its own work" binds people too and stays a guard. A step routed to a cog that is not
available is passed to the next route. The runner accepts fields from a cog at a Gate, within the fields the Gate
collects, but no cog's review supplies any today, so a step that needs a person's input still waits for a person
(read the code: hubproto/runner.py, `decide`; hubproto/cogs.py, `ReviewerCog`).

### When a cog sits on a committee

A cog member of a committee (chapter 7.6) agrees only within its approval criteria; otherwise its vote pauses and the
chair intervenes. A cog never objects. At a change order's Gate a cog is always paused: "a change order's approval puts
configuration in force, so a person signs it: a cog may review it and say what it found, never approve it".

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

### Judgment: "when its review passes"

The cog's model reviews the item against the procedures it follows, and the review is filed as an evidence document
on the item. Only a passing review counts. A failed review **binds for that version of the item**: asking again does
not roll the dice again; it stands until the item changes or a person decides. A stand-in (no model) never passes
outside development (hubproto/cogs.py, `judgment`).

### Adversarial reviews (lenses)

"When 3 adversarial reviews pass" makes the cog read the item several times over, each time on its own, with one
brief, trying to find a reason **not** to approve. It signs only when every one passes (hubproto/review.py, `LENSES`):

| Lens | Brief |
|---|---|
| compliance | every requirement of the governing procedure and the others: sections, content, records, approvals, references, revision history |
| consistency | contradictions inside the document and with the procedures: roles, responsibilities, numbers, timings, terms, references to things that do not exist |
| impact | what goes wrong if this is approved as it stands: steps that cannot be carried out or verified, unclear owners, missing training, unhandled risks |
| clarity | words that leave a choice where a duty is meant, undefined terms, steps out of order, missing acceptance criteria |

The number of reviews comes from the criterion; the lenses are the first ones in the cog's **persona** (`Lenses:`). A
persona with fewer lenses than the criterion asks for makes the review fail ("*n* adversarial reviews are required but
X has fewer lenses"). The persona's `Reviews:` line changes only the prompt's wording (chapter 10.9). Each lens is its
own task in the cog's queue; when one fails, the rest are withdrawn and the result binds for that version. The reviews
are filed as evidence, and the signature's meaning starts "3 adversarial reviews passed (compliance, consistency,
impact)". Fewer reviews in a criterion is a widening.

### Countersignatures

"... countersigned by a quality manager" means the cog's signature is recorded, and then the Gate waits for a person
holding that role (hubproto/runner.py, `_await_countersign`): the cog signs; the step waits for every person holding the
countersigning role who may approve this kind of item, except whoever prepared or wrote it; they see "Countersign:
*step*" and the button **Countersign *number***; the countersignature completes the step. If nobody can countersign,
the run stops with an organisational stop. Removing a countersignature from a criterion is a widening.

In the example company a **major** document (or any new one) the qa-manager is asked to approve is signed by it after
three adversarial reviews and then countersigned by carol; a **minor** change is final on its signature
(tests/test_qa_manager.py).

## 6.7 Cogs and assigned work

When a cog is assigned an item (chapter 2.7), or a task names it as assignee (chapter 3.15), it works the item like a
person picking up an assignment (hubproto/workqueue.py, `assigned_work`): it reads the item and the instruction, works
where the SOPs let it, and reports on the chatter. A step assigned to it it either completes with a summary, which
becomes its signature's meaning, or hands back to the person who filed the item with the reason. If it does neither,
for example because its actions wait for confirmation, the step goes back to that person with what it found (when a
cog filed the item, to a person who holds the step's role). An audit's "Record the findings", routed to the auditor,
is assigned work the same way: the auditor cog completes it with its findings (6.14).

## 6.8 Agent runs and confirming proposed actions

A cog given a task works "on its own" in an **agent run**: its model plans, looks things up with its tools and acts
within its derived autonomy and budget. Runs start from **Give *cog* a task** on its profile, from its duties, from
assigned work and from chat turns.

**How to give a cog a task** (any member who is a person)

1. Open the cog's profile (**People** > the cog) > **Working on its own**.
2. Write the task and press **Add to its queue** ("It takes it when it gets to it, looks things up, then acts where the
   SOPs let it, within a budget of 5 actions; you are told what it did.").
3. When the run finishes, you are notified: "*cog* finished: ..." or "*cog* proposes: ...; *n* action(s) wait for your
   confirmation".

**The run page** (`/agent-run/<id>`) shows what was asked, who started it, when, how, "autonomy derived" and the
budget; a **Steps** table with every tool call, **ok** or **refused**, and the result; and the **Summary**. Only the
person who asked, the cog and policy owners can open it.

**How to confirm proposed actions**: open the run page; under **Waiting for confirmation**, each proposed action is
listed; press **Confirm and carry them out**. The actions are carried out now, as the cog, through the same governed
paths. The page offers the button to the person who asked and to policy owners, but the hub carries an action out only
for someone in the role the "when a *role* confirms" sentence names (chapter 10.9).

**Duties** are `## Duties` sentences in the SOP that owns the work, taken up every day, week or month when the duty
runner is running (the `agents` service, or `hubproto agent-duties`, chapter 8.7). A duty runs on the cog's own rights,
for nobody in particular. A run cut short by a restart is closed as "interrupted" and not started again.

## 6.9 What a cog will refuse

A cog refuses, or the hub refuses for it:

* to sign anything outside its approval criteria, to sign a change order, or to decide an item of a delta;
* to sign an item that it or another cog prepared or wrote ("A cog's work is signed by a person"), or one it is
  excluded from by segregation of duties;
* to step in when the person asking is not one the approval waits for (or a policy owner), or prepared the item, or
  when its review of this version already failed;
* to do anything at all when its model cannot be reached;
* to change a document the person asking may not change, a document someone else is editing, or a controlled document
  in force;
* to write to people who are not part of the work, or to pass on a record the recipient may not read;
* to act where no SOP lets it, or beyond its budget;
* to change configuration directly: its configuration tools put the change on a change order;
* to follow instructions hidden in an item's content: what members wrote is data to judge, fenced in the prompt as
  untrusted;
* to review a change order it contributed to ("it contributed to this change order, so it does not review it": the
  qa-manager, after drafting a change order's prose);
* and nobody may act in a cog's name: watching a cog is read-only.

## 6.10 Cog profiles, models and new cogs

**A cog's profile page** (**People** > the cog) shows, in order: **About this cog** (**About**; **Model** with "answers"
or "not available" and **Change model**; **Persona** with its stance, tone and lenses; **How it behaves**, the prompt
composed from the About line and the persona; **Follows**, each document with the reason in brackets, "Derived: the
company scope and every document whose Roles and responsibilities table lists it or one of its roles"; **What it may
do**, every permission with what it comes through and where it is stated; **Written in**; **Change it**; **Technical
details**), **Queue**, **Working on its own** (the derived autonomy, its duties "(from the SOPs that own the work)",
**Give *cog* a task**, recent runs), **May approve**, **Waiting for *cog***, **Recent decisions**, **Recent messages
answered**, **Send *cog* a message** and **Training** (6.11).

<!-- figure: c06-cog-profile -->

**How to propose a different model for a cog** (any member who is a person)

1. On the cog's profile open **Change model**. The list shows the models this hub can reach; Claude models only when
   hosted models are switched on (chapter 8.4).
2. Choose one and propose it. The change is recorded as a direct change on your settings change order ("recorded on the
   change order as a direct change; its review round writes it into the SOP"), and the cog "keeps *model* until that
   revision is approved and the change order is released" (hubproto/app.py, `propose_cog_model`; checked by running the
   code). A change to a cog profile is a widening and high risk: both policy owners decide it, and it needs a test
   record (chapter 7.9). The review round's qa-manager writes it into the cog profiles only if you may revise that
   document; otherwise it is approved as a divergence (chapter 10.9).

**How to propose a new cog** (any member who is a person): **People** > **Add someone** > **A new cog**: **Name\***,
**Model**, **About\***, **Persona** and **Roles\***, with the note "What it follows and what it may do come from the
SOPs that name it or its roles; its duties are sentences in the SOP that owns the work. Give it only the roles its work
needs." It is recorded on your settings change order the same way. No code is needed: a profile and its roles are
enough, once the change order is released. If no model can be reached the form says so and cannot be used. The name,
roles and persona are not checked when the change is recorded (ran the code: a cog named "Bad Name" with no such role
was recorded; chapter 10.9). Its approval criteria, duties and table rows are added in the SOPs that own its work.

Proposing a new person works the same way (**A new person**: name and roles).

## 6.11 What a cog knows

**No private memory.** A cog keeps nothing of its own between tasks. Its judgments are review records filed as evidence
on the item; a failed review is found again there (hubproto/cogs.py, `_judgment_on_item`). What a cog should remember
becomes documentation released into the system (a work instruction, a lessons-learned note) on a change order, and is
then in the context of everyone its table names.

**Training is its context.** A cog is trained on a document when that document is in its context as it works: the
company scope and every document whose Roles and responsibilities table names it, its roles or its groups. The release
that put a revision there is its training record; a cog acknowledges nothing and is given no training tasks
(hubproto/training.py, `cog_training`). The profile's **Training** section lists each document, its revision and "in
context since release v*n*". In the run for this manual, the it-admin cog gained ncr-sop rev B when the change-board
group it belongs to was written into that SOP's table.

<!-- figure: c06-cog-training -->

**The knowledge base, through its tools.** Anything else it may read under its roles' and groups' read permissions it
reaches with its look tools, never pasted into its prompt: records, documents, procedures it is not trained on
("reference: you may look it up; it is not your procedure"), and sections of the SOPs it follows (`read_sop_section`,
"trained: it is in your context and binding on you"). Working for someone, it reads only what both may read
(hubproto/agent_docs.py, `may_look`). SOP texts are always the released texts. The prompt tells the cog to use
effective revisions and a draft only when its task is about that draft, but `read_document` returns a document's latest
version whatever its state (read the code; chapter 10.9).

**The prompt, in a fixed order** (hubproto/cogs.py, `cog_context`):

1. **WHO YOU ARE**: its About line and its persona's behaviour, tone, review passes and escalation.
2. **YOUR PROCEDURES (BINDING)**: the process that governs this work and the SOP it is in, its steps with the cog's own
   marked, "YOUR PART IN IT (the sentences that give it to you; cite them)", the rest of that process's routes,
   criteria and segregation rules, and an index of the sections of every procedure it is trained on, not whole SOPs.
   Where a cited line has an open divergence, the rule in force is given beside it ("follow the rule in force").
3. **YOUR AUTHORITY**: what it may do on its own, only when someone confirms, what the SOPs forbid it, what it may do as
   a member of a group, its tools, and what it may sign.
4. **THE ITEM**: the record or document, and its comment threads, fenced as untrusted.
5. **REFERENCE: THE KNOWLEDGE BASE**: "Not pasted in: look things up when you need them", the look tools and what they
   reach, "never a source of rules".
6. **THE CONVERSATION** in a chat, or **YOUR TASK** in an agent run.
7. The output format, and a closing reminder that its procedures are binding.

## 6.12 Senior and junior cogs

Seniority distinguishes models. A **senior** cog is bundled with `claude-opus-5-5`, a hosted model; a **junior** cog
with `qwen3:14b`, served locally. The groups **senior-cogs** and **junior-cogs** say which is which (chapter 7.6).

* The seniors, **senior-organization** (knows the organization; reads SOPs into rules; signs nothing) and
  **senior-qa-manager** (the quality system), are the compile-frame committee: they read the prose of changed SOPs into
  configuration, by consensus (chapter 7.4). They do nothing else.
* The juniors do the day-to-day work: chat, drafting (the qa-manager writes a change order's prose), reviews and
  approvals within their rails.
* A senior is usable only where an administrator has switched hosted models on (`HUBPROTO_HOSTED_MODELS=on` with an
  `ANTHROPIC_API_KEY`; chapter 8.4). Otherwise it is not available, the committee cannot reach consensus, and the
  chair is named in a `member-unavailable` question. Its profile then says "senior-organization is not available. It is
  bundled with claude-opus-5-5, which this hub cannot use: claude-opus-5-5 needs an Anthropic key (ANTHROPIC_API_KEY)
  ...", even when the key is set and the switch is off (checked by running the code).
* No permission sentence names either group, and the purchasing-assistant and stores cogs are in neither.
* The **auditor** cog is senior too (claude-opus-5-5) but is in neither group: the senior-cogs group is the
  compile-frame committee's membership, and the auditor audits what the committee reads, so it reads no SOP into rules.

## 6.13 What is recorded of a cog's work

Every decision, review and drafted text a cog produces records, on the item, which cog, its model (or "no model (fixed
criteria)" or the stand-in), its persona and the persona's version, the profile's version, and the release that gave
it its context (a `cog.worked` event; hubproto/cogs.py, `provenance`). The item's history shows it as "*cog* decided at
its turn: ... · model *x*, persona *y* *v*, trained by release v*n*". A drafted revision records that its lines are the
qa-manager's, never the person who made the direct change. The prompt itself is not stored on the record.

## 6.14 The auditor cog

The auditor cog holds the **internal auditor** role and nothing else. What it may do is what the audit SOP gives that
role, so a person holding the role has the same reach: read every folder (Engineering, Finance, Inventory, Policy,
Purchasing, Quality, Tasks), create and approve audits in Quality/Audits, and create documents there (its report). It
comments nowhere, files no nonconformance and signs nothing within criteria.

**Each month** (`hubproto agent-duties`), it takes the audit SOP's duty: "Every month the auditor cog audits the next
process in the programme." The programme is every process in the release, the one audited longest ago first, never one
it owns (internal-audit is its own: its role decides a step in it). It plans the audit (`plan_audit`); the audit then waits for it at "Record the
findings", which it works as assigned work:

1. **Exact checks** (`audit_checks`, no model): items waiting past their due date; approvals by whoever prepared the
   item; decisions by someone the item was not routed to; documents in force whose table names people who were never
   assigned their training; the kernel's event chain and every run's Track and signatures; clauses no Frame cites, for
   ISO 9001:2015, ISO 13485:2016, 21 CFR 820 (QMSR) and 21 CFR Part 11 (`config/standard-clauses.json`: numbers and
   short titles only); audits whose auditor owns a process they audit. Each result cites its evidence.
2. **Samples** (`sample_records`): a few items of the process, spread over its history, with the Track entries of
   their decisions, read against the SOP text.
3. **Findings** (`record_finding`): clause, what was expected, what was found, the evidence, the grade (major, minor,
   observation or opportunity) and, where action is needed, ncr or capa. Each is checked before it is kept: the clause
   must be one of the criteria, and every evidence reference (a record number, `track:<run>#<seq>`, `event:<seq>`,
   `release:<digest>`) must exist; otherwise it is refused, and the Gate refuses it again.
4. **The report** (`draft_audit_report`): a controlled draft document on the audit with the findings and the checks; a
   person sends it through document control.
5. **Completing the step** (`submit_findings`): the findings go onto the audit with the step's completion.

An audit run has a larger, bounded budget: 30 actions and 40 model rounds by default (`HUBPROTO_AUDIT_ACTIONS`,
`HUBPROTO_AUDIT_ROUNDS`, or a caller's `--actions`; never more than 80 and 100). The checks are computed once per run and paged
from there, and each Track is read and verified once until it changes; they show only what the auditor's role reads. Without its hosted model the duty stays due and nothing stands in.
The quality manager then accepts or rejects each finding (3.11); only an accepted finding with an action becomes a
nonconformance or a CAPA. It is an internal audit against the organization's own SOPs, not a certification.
