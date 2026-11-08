# 2. Records and views

Everything the hub keeps is a **record**: a bill, a nonconformance, a task, a document. One record system spans every
module (hubproto/kernel.py). This chapter explains what a record is and the pages you use to find, read and work on
records. The processes that move records are in chapter 3; documents have extra features, covered in chapter 5.

## 2.1 Record types and numbers

Each record has a type, a number, a folder, a state and fields. Numbers are given in order and never reused. Types
marked "yearly" restart each year and carry the year (`NCR-2026-0004`). Journals and purchase orders are numbered
without gaps (config/record-types.json).

| Type (as the hub names it) | Number | Created by | Filed in | App |
|---|---|---|---|---|
| Task | TASK-0001 | task process | Tasks | Inbox |
| Document | DOC-0001 | uploads, **New document**, document-control process, cogs | any folder | Documents |
| Supplier bill | BILL-0001 | purchase-to-pay | Finance/Payables | Finance |
| Journal | JE-00001 (gapless) | posting a bill or a reversal | with the bill (Finance/Payables) | Finance |
| Journal reversal | JR-00001 | journal-reversal | Finance | Finance |
| Purchase request | PR-00001 | purchase-request | Purchasing/Requests | Purchasing |
| Purchase order | PO-0001 (gapless) | issued automatically when a purchase request is approved | Purchasing/Orders | Purchasing |
| Goods receipt | GR-00001 | goods-receipt | Inventory/Receipts | Purchasing, Inventory |
| Item (archived) | ITEM-00001 | nothing: items are archived and read-only (chapter 4.10) | Inventory/Items | none (**Engineering** > **Archived items**) |
| Nonconformance | NCR-2026-0001 (yearly) | nonconformance | Quality/Nonconformances | Quality |
| Corrective action (CAPA) | CAPA-2026-0001 (yearly) | capa | Quality/CAPA | Quality |
| Complaint | CMP-2026-0001 (yearly) | complaint | Quality/Complaints | Quality |
| Internal audit | AUD-2026-001 (yearly) | internal-audit | Quality/Audits | Quality |
| Calibration | CAL-00001 | calibration | Quality/Calibration | Quality |
| Asset | AST-0001 | nothing in the web pages or the policy creates assets today (see 3.12 and chapter 10) | | Quality |
| Management review | MR-2026-001 (yearly) | management-review | Quality/Management Review | Quality |
| Training sign-off | TRN-00001 | training-acknowledgement | Quality/Training | Quality |
| Supplier evaluation | SUP-0001 | supplier-evaluation | Quality/Suppliers | Quality |
| Change request | CR-2026-0001 (yearly) | change-control | Quality/Changes | Quality |
| Change order | CO-2026-0001 (yearly) | change-order (chapter 7.9) | Quality/Changes | Quality |
| Design | DSN-2026-0001 (yearly) | design-release | Engineering/Designs | Engineering |
| Requirement | REQ-2026-0001 (yearly) | requirements-capture | Engineering/Requirements | Engineering |
| Part | PN-00001 | part-request; the design process (its assembly, and new parts for unknown BOM lines) | Inventory/Parts (an assembly: Engineering/Designs) | Engineering, Inventory |
| Manufacturer part | MP-00001 | with a part made by the design process; a cog's `create_record` tool | Inventory/Parts | Engineering, Inventory |
| Supplier part | SP-00001 | a cog's `create_record` tool (no page creates one) | Inventory/Parts | Purchasing, Inventory |
| Part category | CAT-0001 | the sample data; a cog's `create_record` tool | Inventory/Parts | Inventory |
| Stock location | LOC-0001 | the sample data; a cog's `create_record` tool | Inventory/Locations | Inventory |
| Stock lot | LOT-000001 | booking a goods receipt; splitting a lot (also by a partial move or quarantine) | Inventory/Stock | Inventory |
| Stock adjustment | ADJ-00001 | stock-adjustment (chapter 4.7.1) | Inventory/Adjustments | Inventory |
| Stock release | REL-00001 | stock-release (chapter 4.7.2) | Inventory/Releases | Inventory |

**States.** A record created by a process starts **Open**. While it waits at a step it is **Awaiting: *step*** (for
example "Awaiting: Approve the bill"). It ends in its process's finishing state (Closed, Posted, Effective, Ordered,
Received, Active, Accepted, Released, Acknowledged, Done, Applied or Approved). Four side exits exist for every
process: **Returned** (rejected back to its owner for rework), **Failed** (a step stopped; the owner fixes and
retries), **Cancelled** and **Rejected**. Documents also use Draft, Effective, Obsolete, Filed (evidence) and Received
(external files) (chapter 5); stock lots use In stock, Depleted, Scrapped and Returned (chapter 4.4); a draft that is
withdrawn is **Withdrawn** (2.4).

**Fields.** Each type has typed fields (text, number, money, date, list, a reference to another record, a member, a
choice). Some fields are set only by the system (a bill's match result, a record's risk); the hub refuses them in
forms ("'risk' is set by the system, not at submission"). Some fields are required before a record may enter a state:
for example a nonconformance cannot close without a disposition and a reason for it.

**Who may see a record.** You see a record when the policy lets you read its folder. A folder permission covers its
subfolders. Anywhere a record you may not read would appear, the hub shows at most its number, without its title or a
link.

## 2.2 List view

Open a type from the app bar, the app launcher or an app page. The list (`/items/<type>`) shows every record of that
type you may read, newest first, with its count in the heading (hubproto/shell/views.py).

| Control | What it does |
|---|---|
| Search box | finds a number or words in the title |
| **Group by** and **Group** | groups rows by State, Assignee, Supplier, Type or Folder |
| Filter chips | **My items** (you own, created or hold it), **Open** (open, waiting or returned), **Waiting for me**, **Overdue**, **This week** (changed in the last seven days); click again to clear. Filters set from a dashboard chart (state, stage, assignee, folder, "decided on") show as chips you can remove |
| Column headers | click to sort by Number, Title, State, Key fact, Owner, Due or Updated; click again to reverse |
| **List / Kanban / Form** | switches view; **Form** opens the first record |
| Pages | 25 rows a page |

Columns: Number, Title (with an **on hold** chip), State, Key fact (an amount and supplier, a severity, a quantity, a
revision, a change class, a priority; for a lot its quantity, status and batch or serial number; for a part its
revision, IPN and minimum stock; for a location its kind; for a supplier part its SKU and pack), Owner, With (who it
waits for), Due, Updated.

**How to put several items on hold** (approvers of those items)

1. In the list, tick the items (or tick the box in the header to select all on the page).
2. Under the table, write **Reason for the hold** and press **Put selected on hold**.
3. The status line lists what was held and what was not, with the reason: "you are not its approver", "already on
   hold" (hubproto/shell/views.py, `post_bulk_hold`).

The **Items** list (`/items/all`) shows records of every type except documents (documents are included when you search).

## 2.3 Kanban view and boards

**Kanban** shows one column per state of the type's workflow, in order, plus any side exit that has records. Each card
shows the type icon, the number and title, the key fact, the folder and owner, who it waits for, and chips for risk,
a cogs' review verdict, the due date, **on hold** and **escalated**. Its foot counts attached documents and links. A
card's left edge is coloured by risk.

**Boards** (in the rail) also links each cog's board (chapter 6.3). It opens **My work**: your records (those you own,
created or wait on) in five columns named by what happens next: **Waiting for me**, **Waiting on others**, **Returned or
stopped**, **Done**, **Cancelled or rejected**. Choose a **Type** to see every record of that type by state instead.
Filters: State, Folder, **Assigned to**, Search. **Link to this filter** gives an address you can bookmark; **Open as
the *type* kanban** switches to the type's kanban view.

## 2.4 Form view (the record page)

Clicking a number opens the record page (`/record/<number>`). Documents open their own page instead (chapter 5). The
page has, from top to bottom:

1. **Action buttons** (left) and the **status bar** (right).
2. The **Ops** row (2.8).
3. **Smart buttons**: counts of Documents, Links, Runs, Decisions and, when cogs reviewed the item, Reviews. Each jumps
   to its tab.
4. **The sheet**: number, type, state (and **on hold**), the title or statement in bold, and one line with the
   folder, owner, who it is with, its due date, the approval due date, the process, its run and labels. A last line
   says what you may do here ("You may approve here", "hold or release this item", "cancel its run").
5. A card when the item is on hold, stopped, returned or waiting (2.5).
6. **Tabs**: Details, Documents, Links, Reviews (when there are any), History, Workflow and decisions.
7. The **chatter** (2.6).

**Action buttons**, shown only when they apply to you:

| Button | Shown when | See |
|---|---|---|
| **Approve** (key a), **Reject** (key x) | the item waits for your decision | 1.4 |
| **Rework and resubmit** | the item was returned to you | 3.1 |
| **Fix and retry** | the item's run stopped | 3.1 |
| **Put on hold** / **Release the hold** | you may approve this kind of item | 2.9 |
| **Attach**, **New document** | you may file documents in the item's folder | 2.9, 5.3 |
| **Start a process** | you may start any process | 1.3 |
| **Why not me?** | the item waits for someone else | 1.5 |
| **Assign** | you own it, created it, it is with you, or you may approve it (for a document, also a document controller) | 2.7 |
| **Back to the start** | you own it or may approve it, or (documents) you are a document controller; not for an effective, obsolete or superseded item, nor one already at its start | 3.1 |
| **Withdraw this draft** (on the **Details** tab; not on documents) | the item is still at its start (Draft, Open or New) and no run holds it (a stopped or returned run still does), and you created, own, wrote or requested it | below |
| **Render snapshot** | always | 2.9 |
| **Print** | always | |

### The status bar

The status bar across the top right lists every state of the item's workflow in order. States already passed are
marked done, the current one is highlighted and announced as the current step, and side exits (Returned, Failed,
Cancelled, Rejected) are marked as exits. On a narrow screen the bar scrolls to keep the current state in view.

### The tabs

* **Details**: the fields in the sections of the record type's form layout (chapter 7.12), main ones first (title,
  description, supplier, amount ...). Fields the layout leaves off are folded under **Not on the form**, and fields the
  system set (risk, match results, conditions and the like) under **Set by the system**. The tab ends with the hold
  form and, for a draft you may withdraw, **Withdraw this draft**: give **Why it is withdrawn** and press **Withdraw**.
  The item is closed as Withdrawn and stays in the history; nothing else changes (hubproto/shell/actions.py,
  `post_withdraw_record`).
* **Documents**: attached documents (number, title, class, type, revision, state, size, and **Edit** or **View**), a
  **New document** form and an **Attach** form (2.9).
* **Links**: links to other records with their relation and direction, and an **Add link** form (2.9).
* **Reviews**: one row per cog review (cog, verdict, summary, model, procedures, number of findings, evidence
  document). "A model drafted the words, the full text is filed as evidence, and nobody signed."
* **History**: every change to the record as a sentence ("bob approved it: “...”; now Posted"), newest last, each with
  its raw details folded. **Run steps and checks** folds out the run's own record of every step (2.10).
* **Workflow and decisions**: a diagram of the workflow with the current state outlined (the diagram needs the browser
  to load a script from the internet; without it the diagram is hidden), the list of states, and a table of
  transitions: from, to, how (automatic, or "approval: *step* (due within *n* days)"), what reject does, and, for the
  current step, who it is assigned to and who holds the role. Then the item's runs and the **Decisions** table: when,
  who (person or cog), verdict, meaning, the step, and for a cog the approval-criteria sentence it decided under.
  **signature** folds out the signature and the digest of what was signed.

## 2.5 Cards on the record page

* **On hold**: "On hold: *reason*. Nothing can be decided until an approver releases the hold."
* **Stopped at *step***: what stopped it and why, with a form to fix and retry, or a note that no field change can fix
  it (chapter 3.1).
* **Returned to you for rework**: the reason and a resubmit form (chapter 3.1).
* **Waiting for you**: the full decision card (1.4).
* **Waiting for *people* at *step***: for everyone else, with the approval due date.

## 2.6 The chatter: messages, notes and @mentions

The chatter at the bottom of a record or document page is one timeline, newest first, of everything said and done
about the item: messages and replies, notes, notifications sent by the hub, decisions, cog reviews and what was
declined or escalated, stops and retries. While a cog is working on a request about the item, a **queue** entry at
the top says so ("working on it", "next in its queue") and is replaced by the answer when it lands, without reloading
the page (hubproto/shell/queue.py).

**How to send a message about an item** (any member who may read it)

1. In **Chatter**, choose **To** (people, then cogs). It defaults to the item's owner.
2. Keep or change **Subject** ("About NCR-2026-0004").
3. Write the **Message**. Use **Who to mention** to pick a name, then write it as `@name` in the message.
4. Press **Send message**. The message appears on the item and in the recipient's inbox.

**How to log a note** (any member who may read it): open **Log a note**, write it and press **Log note**. It stays on
the item and is sent to nobody else.

**What an @mention does** (hubproto/mentions.py):

| You write | What happens |
|---|---|
| `@bob ...` (a person) | bob gets a request in his inbox Notifications, linked to the item ("alice asks you to review DOC-0123") |
| `@quality-assistant ...` (a cog) | the request goes into that cog's queue; the cog reads the item as it is when it gets there and answers on the chatter (chapter 6.4) |
| `@design-review-committee ...` (a role) | every member holding the role is asked: people in their inbox, cogs in their queues |
| `@nobody` (not a member or role) | you get a notice "No member or role is called @nobody" |
| "... please approve" (or "sign off", "release", "send it for approval") with nothing in flight | the hub starts the item's own process, if you may start it, and the reply says whom it now waits for |

What a cog does with a mention depends on what you ask: an explicit request to approve, a request to change a
document, a request to review, or a question. Chapter 6.4 describes each. Sending a message **To** a cog about an item
counts as mentioning it.

A cog's own words never set off another cog.

## 2.7 Assigning an item

**Assign** hands an item to a person or a cog with an instruction. You may assign an item you own, created, or that
is with you, or one you may approve; a document controller may also assign documents (hubproto/shell/actions.py,
`post_assign`).

**How to assign an item**

1. On the item's page, press **Assign**.
2. Choose **To** (cogs are listed first, then people).
3. Write **What to do**. It is required for a cog ("e.g. review the containment and draft the disposition").
4. Press **Assign**.

* **To a person**: they are added to the item's "with" list and told in their inbox ("Assigned NCR-2026-0004 to
  you").
* **To a cog**: the cog must be available (its model must answer, chapter 6.1). It is added to the "with" list, the
  instruction is posted on the chatter, and a task goes into its queue. The status line says where it is in line ("it
  is next", "it is 3rd in line"). The cog works the item within its autonomy (chapter 6.2) and reports on the chatter.
  If the item waits at an approval the cog could sign under its own approval criteria, it may take that turn, but only
  when the person who assigned it is one the approval waits for (or a policy owner) and did not prepare the item
  (chapter 6.6).

Assigning does not change who a waiting step is routed to: a person you assign can read and discuss the item, but only
the people the step waits for can decide it.

## 2.8 Ops buttons and keyboard shortcuts

The **Ops** row on a record or document page offers one-click actions on this item (hubproto/shell/pages.py,
`_ops_row`):

* **Processes that apply**, numbered 1 to 9, when the item is not in a running process and not finished: for example
  **Send for approval** on a document, **Request the purchase** on a purchase request, **Open the nonconformance** on a
  nonconformance. The process starts on this item, not on a copy. Only processes you may start are offered. On a
  document (other than evidence) you must also choose **minor or major?** before the button works.
* **Ask *cog* to review**, lettered q, w, e and on: one for each cog that works from the SOP defining this kind of
  item's process (its persona, chapter 7.1). It sends "@*cog* please review this *type*" on the chatter. Every cog
  works from the company scope, which includes the tasks SOP, so a task offers all seven cogs.

**How to use the keyboard shortcuts**

1. On a record or document page, press **o**. The page shows the keys next to the buttons.
2. Press the key: a digit for a process, a letter for a cog, **a** for Approve, **x** for Reject.
3. Approve and Reject jump to their form and put the cursor in its first field. Other buttons are highlighted with
   "Press Enter to *action*, or Escape." Press **Enter** to carry it out.

Shortcuts do nothing while you are typing in a field, and the **o** key times out after two seconds. **Keyboard
shortcuts: on/off** in the Ops row turns them off in your browser.

## 2.9 Holds, attachments, links and snapshots

**How to put an item on hold** (anyone who may approve that kind of item in its folder)

1. On the **Details** tab, write **Reason for the hold**.
2. Press **Put on hold**. Nothing can be decided on the item until the hold is released, and a run that reaches its
   next step while the item is on hold stops with "*number* is on hold; lift the hold and retry the run".
3. To release it, write a reason and press **Release the hold**. Both reasons stay in the history.

**How to attach a file or document** (anyone who may file documents in the item's folder)

1. On the **Documents** tab (or **Attach** at the top), choose **Upload and attach a file** and optionally a title,
   or choose an existing document from the same folder under **Or attach an existing document**.
2. Press **Attach**. An uploaded file is filed as an *external* document, kept exactly as received (chapter 5.2).

"May file documents" means the policy lets you create documents in that folder, or create any kind of record there
(hubproto/shell/pages.py, `may_file`).

**How to link two records** (anyone who may file documents in the item's folder)

1. On the **Links** tab choose **Link as** (concerns, caused, fulfils, settles, supersedes, evidence for, part of,
   defines, realized by, affects, trains on, references, produces, carries, implements and others) and type the other
   record's number in **To item**.
2. Press **Add link**. The other record shows the link under its inverse name (for example "caused-by").

**How to file a snapshot** (anyone who may read the item): press **Render snapshot**. The hub renders the record
(fields, links, transitions and every signature) to a Markdown file and files it as an *evidence* document attached to
the item. It ends "Printed copies are uncontrolled." Many processes file one automatically when they close.

## 2.10 History and the audit trail

The hub keeps three layers of evidence:

1. **The record's history**: every event (created, fields set, moved, assigned, linked, held, decided, version
   added) with who and when. Events are chained to each other, so changing or removing one afterwards is detectable
   (hubproto/kernel.py, `verify_chain`).
2. **The run's Track**: every step of a process run (step started, its result and problems, the Gate routed, a cog's
   review, a decline, an escalation, a decision, the grant that let later steps write, a stop, a retry, a
   cancellation). Each entry is chained to the one before it, and each decision is signed with the hub's secret key
   (hubproto/track.py).
3. **Signatures on the record**: each decision records who decided (person or cog), the verdict, the meaning they
   wrote, when, the digest of what they saw, the policy version in force, the step and, for a cog, the
   approval-criteria sentence that let it decide.

Every run is pinned to the policy version in force when it started, and finishes under it even if a new version is
released meanwhile (hubproto/runner.py).

**Runs page.** **Runs** (in the Inbox app, or the rail for policy owners) lists runs whose item you may read, plus your
own and those waiting for you, newest first: the item, process, who started it, status, when, and who it waits for
(or why it stopped). Filter by search text or **mine**.

**Run page.** A run's page shows its status, process, who started it, its item, its policy version and parent run, a
card if it waits (with the routing sentence) or stopped, the **Steps** table (each step, which cog and version did
it, its result and problems, and each approval with who decided and under which rule), and the **History** (the
Track, each entry with its details folded).

An administrator checks the whole chain with `hubproto verify` (chapter 8.6).
