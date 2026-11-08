# 1. Getting started

The hub is one web application for records (bills, nonconformances, tasks and so on), documents, the processes that
move them, and the policy that says who may do what. People and AI team members ("cogs", chapter 6) work in it side
by side. Everything you see is trimmed to what the policy in force lets you read, and every refusal says which policy
sentence caused it.

This chapter covers the parts of the screen every user meets. It refers to the live address
`http://localhost:8080`; your administrator may give you another one.

## 1.1 Choosing who you are ("Acting as")

The prototype has no password sign-in. You say who you are by choosing your name, and every signature, message and
change is recorded under the name you choose (hubproto/shell/handler.py, `page_whoami`).

**How to sign in**

1. Open the hub's address. A page **Who are you?** lists the people of the organization with their roles.
2. Click your name. The hub opens the home page as you. Your name is now in the address (`?as=alice`) and in the
   top bar.
3. To switch to someone else, choose another name in **Acting as** in the top bar and press **Go**. On most pages
   (home, inbox, documents, boards, record and document pages, runs, policy pages, people, settings) you stay on the
   same page; on others you return to the home page.

**Watching a cog.** The **Who are you?** page and the **Acting as** list also offer each cog under **See what a cog is
doing (read-only)**. Choosing a cog shows the hub as that cog sees it, with a banner "You are watching *name*, a cog.
It decides within fixed criteria on its own; nothing here can be sent in its name." Any form you send while watching
a cog is refused; choose your own name to act.

**Not a member.** If the name in the address is not a member of the organization under the policy in force, the
home page says so ("*name* is not a member of the organization ... Ask a policy owner (bob or carol) to add you to the
org model") and nothing else is available. Adding people is described in chapters 6 and 7.

If no policy release is in force at all, pages say "No policy release is in effect" and name the command an
administrator runs (chapter 8).

## 1.2 The screen

Every page has the same frame:

| Part | Where | What it holds |
|---|---|---|
| Top bar | top | the **app launcher** (grid icon), **Hub** (back to the home page), breadcrumbs ("You are here"), the light/dark switch, your avatar and name, **Acting as** and **Go** |
| App bar | under the top bar, on pages that belong to an app | the app's name, its record types (for example **Nonconformances**, **CAPAs**), its fixed pages, and up to four "start" links such as **+ Report a nonconformance** |
| Rail | left | **Home**, **Inbox**, **Documents**, **Boards**, **People**, **Why?**; for policy owners also a **Policy** group (**Runs**, **Policy versions**, **Policies**, **Conformance**, **People and roles**); on document pages a **Folders** tree |
| Status line | top of the page | after an action, one sentence saying what happened, for example "Filed NCR-2026-0004; waiting for carol at Disposition the nonconformance (approval due 12 Nov 2026)." |
| Messenger | bottom right | **Chat with a cog** (chapter 6.5) |

A **Skip to content** link is the first thing a keyboard user reaches. Refused actions re-display the form you sent
with what you typed kept and the reason beside the field. A page that cannot be shown says **Not done** with the
reason and links to **Home**, **Inbox** and **Back to the previous page**.

### The app launcher

The grid icon at the left of the top bar opens the app launcher. Each app lists up to five of its record types and
three of its pages (hubproto/shell/apps.py):

| App | Record types | Pages |
|---|---|---|
| Inbox | Tasks | Waiting for me, My work (boards), Runs |
| Documents | Documents | Files by folder, Controlled documents, Templates |
| Quality | Nonconformances, CAPAs, Audits, Calibration, Complaints, Management reviews, Training, Change requests, Change orders, Supplier evaluations, Assets | Change orders, Forms |
| Engineering | Designs, Requirements, Parts, Manufacturer parts | Archived items |
| Purchasing | Purchase requests, Purchase orders, Receipts, Supplier parts | Suppliers |
| Inventory | Stock lots, Parts, Locations, Receipts, Stock adjustments, Stock releases, Supplier parts, Manufacturer parts, Part categories | Parts and stock, Locations |
| Finance | Supplier bills, Journals, Reversals | |
| People | | Members and cogs |
| Settings | | Your changes, People and roles, Permissions, Limits and due dates, Cog rails, Segregation of duties, Workflows, Forms |
| Policy (policy owners only) | | Policy versions, Policies, Scopes and personas, Automation, Why, Conformance, People and roles |

Every member sees every app except **Policy**. The lists inside an app only show records in folders you may read, so
an app can be empty for you (carol, a quality manager, may not read the Finance folder and sees no bills). Archived
record types (inventory items) are in no app; their records stay readable (chapter 4.10).

Clicking an app's name opens its app page: one card per record type with the number of open items, **List**,
**Kanban** and a start button, and a chart of its items by stage.

### Light and dark

The moon (or sun) button in the top bar switches between a light and a dark theme. Your browser remembers the choice
for next time; it is not stored in the hub.

## 1.3 The home page (dashboard)

**Home** greets you by name and lists your roles. Below that (hubproto/shell/pages.py, `page_home`):

**Tiles.** Eight numbers, each with a two-week sparkline and a link to the list it counts:

| Tile | Counts | Opens |
|---|---|---|
| Waiting for me | items waiting for your decision | the inbox |
| Overdue | of those, the ones past their due date (red when not zero) | the overdue filter of all items |
| Returned or stopped | items returned to you for rework, and your runs that stopped | the "Returned or stopped" part of the inbox |
| My runs | process runs you started | the runs page, filtered to yours |
| Open NCRs | nonconformances that are open, waiting or returned | the nonconformance list |
| POs awaiting receipt | purchase orders not yet received | the purchase order list |
| Bills to approve | supplier bills waiting at an approval | the bill list |
| Documents checked out | documents someone has checked out | the document list |

The counts are of what you may read. **Charts:** items by stage for each app; decisions per day over the last 30 days;
cycle time by process (median days from submission to close); overdue items by assignee. Every bar is a link to the
list it counts.

**Start something.** Quick buttons for every process you may start, then one collapsible form per process. A process
is offered when the policy gives you a `create` permission for its kind of record in its folder **and** you hold the
role of its first step (hubproto/runner.py, `submit`). Processes you may not start are listed under **Not available to
you** with the reason, for example "Who may: finance clerks; you are: ...; ask: bob or carol to change the policy."
The forms themselves are described with each process in chapter 3. A **change order** is not started here: its
button opens the change-order page (chapter 7.9).

The page ends with the policy in force: "Policy in effect: policy version *n*, released by *name*."

## 1.4 The inbox

**Inbox** is your work queue. It has these parts, top to bottom (hubproto/shell/inbox.py):

| Section | Shows |
|---|---|
| **Waiting for me** | every item that waits for your decision, overdue first, then soonest due; 25 per page; a **Show only** filter by type when there are several |
| **Policy proposals waiting for your decision** | policy owners only: open proposals with changes you have not decided (chapter 7) |
| **Returned or stopped, mine** | items rejected back to you for rework (with the reason and a rework form) and your runs that stopped (with a "Fix and retry" form) |
| **Waiting on others (mine)** | your items that wait for someone else, where and until when |
| **Your items that moved** | items you own, wrote or filed that someone else moved in the last seven days |
| **Notifications** | escalations, stops, returns, rejections, automatic decisions by cogs, requests from @mentions, cog reviews, cog run reports; the ten newest, older ones folded |
| **Messages** | direct messages to you, cog replies, and other notices (for example "PO-0003 issued", "handed back") |
| **Sent by me** | the last 20 messages you sent (folded) |
| **Send a message** | a form: **To** (people first, then cogs), **Via** (direct message or email), **Subject**, **Message** |

The email channel does not send mail: the hub stores the message like a direct message and also writes it as an
`.eml` file into its outbox folder (`state/inbox/outbox`) (hubproto/inbox.py). A message sent to a cog goes into the
cog's queue and is answered there (chapter 6.4).

### Each row in "Waiting for me"

A row shows the step and the item ("Approve the bill: BILL-0012 Acme Metals ..."), its type, the key fact (an amount
with the supplier, a severity, a change class), the risk, the due date, chips for **escalated** and **on hold**, who
else it waits for ("also with bob"), and a **duplicate of ...?** chip when another waiting item has the same type and
supplier and amount, or the same title. Only one item is open at a time; clicking a row opens its card in place.

### The decision card

The open card puts the decision first:

1. **Facts**: amount, risk, whether the supplier is approved, whether the three-way match is within tolerance, the
   due date, who else holds it.
2. **Who started it** and a link to its run.
3. **Why it came to you**: the routing sentence that sent it ("Routed to you by finance-policy line 36: ..."), and,
   where a cog could have taken it, why the cog could not ("the ap-reviewer cog could not take it because the supplier
   is not approved"), the amount bands of the routing rules, and what you are asked to judge ("you are asked whether
   the bill may be posted as it stands"). An escalated item also says who it was escalated to and by which sentence.
4. **Blocked** (red) when Approve cannot work now: the item is on hold, or a design traces to requirements not yet
   accepted. Approve is disabled until that is resolved.
5. **Checks**: warnings and errors the automatic steps raised (for example "price-variance: variance 3.10% exceeds
   tolerance 2%").
6. **The approve form**: fields the next state needs ("To approve, also give: response"), other fields you may set
   under **Also set: ...**, **Meaning of your signature** (required), the confirmation **I have read the item and the
   checks above** (required) and the button. The button reads **Approve *number***, **Sign the disposition of
   *number*** for a nonconformance, **Mark *number* done** for a task and **Countersign *number*** for a
   countersignature. Under it: where approval takes the item ("Approval moves it to Posted").
7. **The item**: a readable summary of its fields and attached documents.
8. **Reject instead**: what a rejection does here ("Reject closes it as rejected; nothing more can be done with it",
   or "Reject returns it to alice for rework"), **Reason for rejecting** (required) and **Reject *number***.

**How to decide an item** (anyone the item waits for)

1. Open **Inbox** and click the item under **Waiting for me**.
2. Read the facts, the reason it came to you, the checks and the item. Open the item's page from its number if you
   need its documents or history.
3. To approve: fill in any required fields, write in **Meaning of your signature** what you are confirming (the card
   gives two examples for this kind of item), tick **I have read the item and the checks above**, and press the
   approve button.
4. To reject: write the **Reason for rejecting** and press **Reject *number***. The reason goes to whoever started
   the item.
5. The hub records your signature and moves the item on. The inbox opens the next waiting item, with a status line
   saying what happened to the one you decided.

Your signature binds what you saw. If anyone changed the item (its fields, links or documents) after your card was
drawn, the decision is refused and the inbox shows "The item changed after you opened it. Here is the current card
...", or, if someone else already decided it, "Already decided by *name* at *time*" (hubproto/runner.py, `decide`).

## 1.5 Why? (who may do what, and why an item waits)

Every refusal in the hub cites the policy sentence behind it. The **Why?** page lets you ask the same question
directly (hubproto/shell/policy.py, `page_why`).

**How to ask whether someone may do something** (any member)

1. Click **Why?** in the rail.
2. Choose **Who** (any member), **Action** (create, read, approve, update or delete), **What** (a kind of record, or
   "anything") and **Where** (a folder). Press **Ask**.
3. The answer is **allowed** or **denied** with the sentence: "Allowed by finance-policy line 21: “Finance clerks may
   create bills in Finance/Payables.”" or "Who may: finance clerks; you are: ...; ask: bob or carol to change the
   policy." A link opens the policy at that line.

Rules for the answer (hubproto/authz.py): a permission on a folder also covers its subfolders, including folders
people added to the library; a top-level library folder has the rights of the folder it was given (chapter 5.1); a
"may not" sentence wins over any "may"; reading a folder needs its own "may read" sentence. The only "update"
sentence is "Storekeepers may update stock items in Inventory/Stock." and there is none for "delete", so delete is
always denied. The inventory policy also uses the action "quarantine" ("Staff may quarantine stock items in
Inventory/Stock."), which the **Action** list does not offer.

**How to find out why an item waits for someone else** (any member who may read the item)

1. On an item that waits for someone other than you, press **Why not me?**.
2. The page says where the item waits and for whom, with the routing sentence, and then either "*you* may decide it
   now" or the reasons you may not: a missing permission, segregation of duties ("they prepared this item", "they
   are the reporter of this item"), a countersignature that needs a particular role, or simply that it waits for
   other people (hubproto/app.py, `why_item`).

## 1.6 Getting help on a page

* Every form marks required fields with **\***.
* Many sections have a **Why can I do this?** or similar disclosure that shows the permission sentence.
* The messenger (bottom right) lets you ask a cog about the page you are on (chapter 6.5).
* Keyboard shortcuts for record pages are in chapter 2.8.
