# 3. Business processes

Each business process is written in an SOP or policy Frame as numbered steps, each with the role that performs it.
The hub compiles those steps into a workflow (chapter 7). A step with a cog name in brackets is done automatically; a
step without one is a **Gate**: the item waits there for a person (or, where the policy allows, a cog) to decide.

Section 3.1 explains what every process has in common. Sections 3.2 to 3.17 take each process in turn. Document
control is in chapter 5.10, change orders in chapter 7.9, and the inventory processes (stock adjustments, releases
from quarantine and part requests) in chapter 4.7.

## 3.1 How every process runs

### Who may start a process

You may start a process when the policy gives you a `create` permission for its kind of record in the folder it files
into, and you hold the role of its first step (a first step marked `[system]`, or naming a field of the record such as
`[author]`, needs no role) (hubproto/runner.py, `submit`). The home page lists what you may start and, under **Not available to you**, what you may not and why.

**How to start a process with a new record** (anyone allowed, see above)

1. On **Home**, under **Start something**, press the process's button (for example **Report a nonconformance**), or
   use the **+** link in the app bar.
2. Fill in the form. Required fields are marked **\***. Pickers list suppliers, parts, supplier parts, lots, stock
   locations, purchase orders, documents and other records you may read. **Files to attach** files each file as a
   document attached to the new record. Fields the form does not show can be given under **Advanced: other fields** as
   JSON (the hint lists them).
3. Press the button. The hub refuses a form with a missing or malformed field before it creates anything and shows
   the reason beside the field.
4. You land on the run's page with a status line: "Filed *number*; waiting for *people* at *step* (approval due
   *date*)", or "it completed", or "it stopped at *step*: *reason*".

**How to start a process on an existing record**: use its button in the record's **Ops** row (chapter 2.8), for
example **Send for approval** on a document. The existing record is the submission: its fields fill the steps. Only
its owner, author, requester or filer, someone it is assigned to, or (for documents) a document controller may do
this, and only when it is not finished and not already in a run (hubproto/runner.py, `_may_submit_existing`). Writing
"@*someone* please approve" on an item with nothing in flight does the same (chapter 2.6).

### Automatic steps

After you submit, the automatic steps run one after another: filing the record, extracting, matching, scoring risk,
drafting and so on. Each step's result, with any problems it raised, is on the run's page. A **warning** goes to the
approver's card under **Checks**. An **error** stops the run (see "Stops" below).

The risk scorer (step "Score the risk" in many processes) sets a risk tier of low, medium or high, and the facts the
cogs' approval criteria test (for example "the supplier is approved"). How it scores each kind of record is given in
each section below (hubproto/cogs.py, `RiskScorer`).

### The Gate: who the item waits for

When the item reaches a Gate, the hub reads the process's routing sentences in the order they are written and gives
the item to the **first route that someone can take** (hubproto/runner.py, `_human_gate`, `_eligible`):

* A route **to a cog** is taken when the cog may approve that kind of item, follows the SOP that defines the process,
  is available (its model answers), and did not prepare the item; and no cog prepared or wrote the item ("a person
  signs a cog's work, never another cog"). The cog then decides within its approval criteria (chapter 6.6). If its
  criteria do not hold it **declines**, and the item goes on to the next route.
* A route **to a role** gives the item to every *person* holding the role who may approve this kind of item in its
  folder and is not excluded by segregation of duties. Cogs holding the role are not included.
* A route **to a named field** ("go to the requester", "go to the assignee", "go to the owner", "go to the chair",
  "go to the auditor") gives the item to the member named in that field of the record. Only a task's assignee may be a
  cog.
* A route with a condition (an amount band, a risk tier, a document type) is only tried when the condition holds.
  Amount bands compare only amounts in the currency the sentence names (USD in all the Frames today).

If no route can be taken, the run stops with "Nobody can take “*step*”" and the reasons (see "Stops").

**Segregation of duties.** Whoever prepared the item (the person who started the run, and whoever created the
record) may never decide it on a route to a role or a cog. A segregation sentence such as "The reporter and the
approver of a nonconformance must be different people" also keeps out the person named in that field of the record.
On a route to a named field (the requester verifies their own change) the named person decides, unless a segregation
sentence for that kind of record says otherwise (hubproto/runner.py, `excluded`). Segregation binds cogs too.

**Due dates.** A sentence "*Step* in *process* is due within *n* days" gives the Gate a due date when it is routed to
people. Steps without such a sentence have no due date.

### Deciding

The people the item waits for find it under **Waiting for me** in their inbox and decide it on its card (chapter 1.4).
Any one of them may decide; the first decision counts. Approving may require fields the next state needs (for example
a disposition); the card asks for them. A cog that decides does so through the same signature path, citing the
approval-criteria sentence that let it decide, and the item's owner is told ("approved automatically by ap-reviewer
... No person reviewed it").

When someone else approves your item, you get a notice saying where it is now.

### Escalation

Overdue items are escalated when an administrator runs the escalation tick (`hubproto tick`, chapter 8.7); nothing in
the hub runs it on a schedule by default. For each waiting item past its due date, once (hubproto/runner.py, `tick`):

* "Overdue items in *process* go to *role*" **adds** to the item those holders of the role who could themselves have
  been routed this step (they may approve this kind of item, are not excluded by segregation of duties, and for a
  countersignature hold the countersigning role). Anyone it names who could not take the step is only informed.
* "Overdue items in *process* are reported to *role*" only **informs**.
* Everyone involved gets an "Overdue" notification. The card then shows "escalated to *people* on *date* by
  *sentence*; originally routed by *sentence*".

### Rejection

A rejection needs a reason. What follows depends on the process:

* In most processes the record ends as **Rejected**, the run ends, and the person who started it is told. Nothing more
  can be done with that record; start a new one, or for a document, a CAPA, a design or a change request use **Back to
  the start** instead of rejecting (the card says so).
* Where the process says "Rejections return to *step*" (today only design release), the item is **Returned** to its
  owner for rework (see below).

### Rework and resubmit (returned items)

**How to rework a returned item** (the item's owner)

1. Open **Inbox** > **Returned or stopped, mine**, or the item's page (button **Rework and resubmit**).
2. Read the reason. Change what needs changing in the form (fields left as they are stay as they are), add files,
   and say **What changed** (it goes to the reviewer).
3. Press **Resubmit**. The run starts again from the step the process names.

### Stops (failed runs) and Fix and retry

A run stops when an automatic step raises an error, when the item is on hold as a step starts, or when nobody may take
a Gate. The record goes to state **Failed**, assigned to the person who started it, who is told "*number* stopped at
*step*" (hubproto/runner.py, `_fail`). The record page shows **Stopped at *step*** with what the step said.

* **A data problem** (a missing purchase order, a closed accounting period, an unapproved supplier): the form shows
  the record's fields with the ones the step complained about marked in red.

  **How to fix and retry** (the person who started it, or anyone who may approve that kind of item)

  1. Open the stopped item from **Inbox** > **Returned or stopped, mine**, or its page (**Fix and retry**).
  2. Correct the marked fields, add files if needed, and say **What you fixed**.
  3. Press **Fix and retry**. Where the run starts again depends on what you changed (hubproto/runner.py, `retry`):
     * **A field changed after an approval.** A signature covers the item as it was. Every approval already given is
       void (the history says "dave's approval at *step* no longer matches: alice changed quantity, unit_cost after
       it, so it is approved again"), and the run starts again from the first signed Gate: the item is approved again
       on what it says now. The automatic steps before that Gate do not run again, so a value they worked out (a
       purchase request's amount) is not worked out again (chapter 10, G1).
     * **Otherwise.** If you only added files, or changed fields before anything was signed, the automatic steps run
       again on what the item says now, from the step after the last signed Gate (from the first step when nothing was
       signed); with no change at all, the run starts again from the step that stopped. Signatures already given are
       kept.
     * **An earlier check no longer holds.** When the step that stopped says so (a stock adjustment whose lot changed
       since its check, chapter 4.7.1), the run starts again from that check, and approvals given after it are void.

* **An organisational stop** (nobody the policy allows may take the step): the card says "No field change fixes this.
  Nobody the policy allows may take this item now", names the policy owners, and offers **Retry now** once they have
  changed who may take it (chapter 7).

### Cancelling

**How to cancel a run** (the person who started it, or anyone who may approve that kind of item): under **Cancel this
*type* instead**, give a **Reason for cancelling**, tick **I want to cancel this *type*** and press the button. The
record stays on file as **Cancelled** and cannot be reopened.

### Back to the start

**Back to the start** stops the run an item is in and returns the item to the first state of its workflow (**Draft**
for controlled documents and templates, **Open** for everything else) with nobody assigned. Versions and history stay.
Its owner and whoever it waited for are told (hubproto/runner.py, `restart`).

**How to send an item back to the start** (its owner, anyone who may approve that kind of item, or for a document a
document controller): on its page open **Back to the start**, write **Why** and press **Send it back to the start**.
Not available for an effective, obsolete or superseded item, or one already at its start.

### Processes opened by other processes

Some steps open another process automatically, as a "child" run admitted by the parent's approval: a CAPA from a
nonconformance, a nonconformance from a complaint or a calibration, training sign-offs from a document that became
effective, requirements and purchase requests from a design. The child's run page links its parent.

### When a process completes

The record moves to the process's finishing state. Most quality and engineering processes then file an evidence
snapshot of the record (chapter 2.9) with every signature on it.

### The processes at a glance

| Process | Starts it (first step role) | Gates and who decides | SOP |
|---|---|---|---|
| purchase-request | staff | Approve the purchase: purchasing-assistant cog, buyer or controller by amount | purchasing-policy |
| goods-receipt | staff | Confirm the receipt: buyer | purchasing-policy |
| purchase-to-pay | finance clerk | Approve the bill: ap-reviewer cog, payables reviewer or controller by amount | finance-policy |
| journal-reversal | finance clerk | Approve the reversal: controller | finance-policy |
| supplier-evaluation | quality manager | Approve the supplier: management representative | supplier-sop |
| nonconformance | staff | Disposition: quality-assistant cog (low risk), quality manager, else management representative | ncr-sop |
| capa | quality manager | Verify the actions: the CAPA's owner; Verify effectiveness: quality manager | capa-sop |
| complaint | staff | Respond to the complaint: quality manager | complaint-sop |
| internal-audit | internal auditor | Record the findings: the auditor; Accept the findings: quality manager | audit-sop |
| calibration | staff | Review the calibration: quality manager | calibration-sop |
| management-review | management representative | Hold the review: the chair | management-review-sop |
| training-acknowledgement | (opened automatically; quality managers and document controllers may file one) | Acknowledge the document: the assignee | training-sop |
| task | staff | Complete the task: the assignee | tasks-sop |
| design-release | design engineer | Review the design: design authority | design-control-sop |
| requirements-capture | design engineer | Accept the requirement: design authority | design-control-sop |
| change-control | staff | Approve the change: quality manager; Verify the change: the requester | change-control-sop |
| document-control | author | Approve the document: by document type and risk (chapter 5.10) | document-control-sop |
| change-order | staff | Approve the change order: quality manager (chapter 7.9) | change-control-sop |
| part-request | staff | Approve the part: buyer (chapter 4.7.4) | inventory-policy |
| stock-adjustment | storekeeper | Approve the stock adjustment: purchasing-assistant cog or controller by value (chapter 4.7.1) | inventory-policy |
| stock-release | staff | Approve the stock release: quality manager (chapter 4.7.2) | inventory-policy |

In the example company, several roles are held by one person only (carol is the only person who is a quality
manager, dave the only buyer and payables reviewer, bob the only controller). When that person starts an item that
would be routed to their own role, nobody else can take it and the run stops (checked by running the code: a purchase
request, part request or goods receipt started by dave; a calibration recorded by carol; a release from quarantine
carol asked for). See chapter 10.

---

## 3.2 Purchase requests and purchase orders

A purchase request names a part, a quantity and a supplier, usually through a supplier part with its price breaks
(chapter 4.2). Approving it issues the purchase order the supplier's bill will later be matched against
(purchasing-policy).

**Who starts it:** staff. Form fields: **Title\***, **Part\*** (picker), **Supplier part** (picker), **Quantity\***,
**Unit cost**, **Supplier\*** (picker; unapproved suppliers are marked "(not approved)"), **Needed by**, **Reason**.
Currency defaults to USD; another currency, a manufacturer part number and a manufacturer can be given under
**Advanced**. A part's page also has a **Raise a purchase request** form (chapter 4.5).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Raise the purchase request | staff | PR-*n* filed in Purchasing/Requests |
| 2 | Check the supplier | automatic | takes the supplier part named, or the part's primary active supplier part (from the named supplier, if one is named); is the supplier approved (on the seeded approved list, or by an approved supplier evaluation, 3.7)? Is the part active? Unit cost = the request's, else the supplier part's price break for this quantity; amount = quantity × unit cost; currency from the request, else the supplier part, else USD. Warnings for no known part, a part that is not active, an unapproved supplier, no unit cost, and a quantity that is not a whole number of packs; errors for a quantity that is not positive and a supplier part of another part |
| 3 | Score the risk | automatic | high: amount 10,000 or more, supplier not approved, or no price; medium: 500 or more, or the part is not known and active; low otherwise |
| 4 | Approve the purchase | Gate (table below) | the approver may also set the supplier, supplier part, unit cost, needed-by date and reason |
| 5 | Issue the purchase order | automatic | re-checks the supplier and a positive quantity and price, and works the amount out again; creates PO-*n* (Purchasing/Orders, state Issued) with one line (part, supplier part, SKU, MPN, quantity, unit cost) and the order the bill matching reads; links it to the request and the part; tells the requester "PO-*n* issued for PR-*n*". A cog may not have approved more than the purchase approval threshold (10,000) |
| 6 | Render the record | automatic | evidence snapshot; the request closes as **Ordered** |

**Routing** (purchasing-policy, in this order):

| Condition | Goes to |
|---|---|
| over 10,000 USD | the controller (bob) |
| under 500 USD | the purchasing-assistant cog |
| any other request | the buyer (dave) |

A request in another currency matches neither amount band and goes to the buyer.

**Approval criteria:** "The purchasing-assistant cog may approve purchase-request purchase requests under 500 USD
when the supplier is approved and the part is active." When that does not hold, the cog declines and the request
goes to the buyer.

**Segregation of duties:** the requester and the approver must be different people.
**Due date and escalation:** approval is due within 3 days; overdue requests go to the controller.
**On reject:** the request ends as Rejected.

**How to approve a purchase request** (the buyer or controller it is routed to): open it in the inbox; check the
supplier, price and quantity on the card ("you are asked whether this purchase may go ahead at this price and
supplier"); set the unit cost if it is missing (otherwise issuing the order stops: "no purchase order: the request has
no positive quantity and price"); sign.

**Purchase orders** have no process of their own. A PO record shows its supplier, lines, amount and currency, the
requests it fulfils, who issued it (the po-issuer cog) and who approved it, and a **Receiving** card from which the
goods are received (chapter 4.8). It moves to **Received** when a goods receipt completes it (3.3). The dashboard tile
**POs awaiting receipt** counts the ones not yet received.

## 3.3 Goods receipt

Recording what arrived against a purchase order books the goods into a stock location as lots and marks the order
received, so its bill can be matched (purchasing-policy; inventory-policy).

**Who starts it:** staff, from the **Receiving** card on the purchase order (chapter 4.8, the usual way) or **Home** >
**Record a goods receipt**. The Home form's fields: **Purchase order\*** (picker), **Location** (picker of every
location), **Lines received\*** (one per line: "part number, quantity, batch if it has one", for example
`PN-00001, 4, B24`), **Packing slip**, **Notes**.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Record the receipt | staff | GR-*n* filed in Inventory/Receipts; received-by is you, received today |
| 2 | Check the order | automatic | the location must hold stock (not a structural one; without one, the first receiving location); each line must name a part on the order, be positive, carry the serial numbers or batch its part's tracking needs, and not be more than is outstanding plus the over-receipt tolerance (5%); an order already fully received, or one whose lines name items, is refused; the receipt is linked to the order |
| 3 | Confirm the receipt | buyer (dave) | may also set notes, lines and location |
| 4 | Book the stock | automatic | receives each line into the location as a lot (one per serial number for a serial-tracked part), at the order line's unit cost; if nothing is outstanding the order is marked received (so the bill can match "goods received"), otherwise a partial receipt is noted. Closes as **Received** |

Each line is compared with what is outstanding on its own, so two lines for the same part can together receive more
than was ordered, and lines or a location the buyer changes when confirming are booked without being checked again
(checked by running the code; chapter 10, I4).

**Segregation of duties:** "The received-by and the approver of a receipt must be different people."
**Due date:** none; no escalation. **On reject:** Rejected.

## 3.4 Part requests

The item request is gone: inventory items are archived (chapter 4.10). A new part is requested through the part-request
process and approved by the buyer (chapter 4.7.4). Parts also arrive from the design process: bill-of-materials lines
that match no manufacturer part become new parts when a design is released (3.16).

## 3.5 Supplier bills (purchase-to-pay)

A supplier bill is matched to its purchase order and receipt, scored, approved and posted to the ledger. Small, clean
bills may be approved by the ap-reviewer cog (finance-policy).

**Who starts it:** finance clerks (alice; the ap-reviewer cog also holds this role). Quality managers are explicitly
refused ("Quality managers may not create bills in Finance/Payables."). Form fields: **Supplier\***, **Amount\***,
**Currency\*** (USD, EUR, GBP, CAD), **Invoice date\***, **Purchase order** (picker of open orders, with supplier,
amount and "not yet received"), **Description**.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Extract the bill | finance clerk (automatic step) | takes the fields; a missing supplier, amount, currency or date is an error; no purchase order is a warning |
| 2 | File the bill | automatic | BILL-*n* filed in Finance/Payables |
| 3 | Match the bill to purchase orders | automatic | the three-way match: the order must exist, be for the same supplier and in the same currency (errors otherwise); the variance from the order amount must be within the match tolerance of 2 percent (a warning otherwise); the goods should be received (a warning otherwise); bills against one order may not exceed it (error); the accounting period of the invoice date must be open (error); the supplier should be on the approved list (warning) |
| 4 | Score the risk | automatic | high: the match failed (outside tolerance or goods not received), or other problems on a bill of 10,000 or more; medium: other problems (supplier not approved, 10,000 or more) or 500 or more; low otherwise |
| 5 | Approve the bill | Gate (table below) | the card shows the match result, variance, PO, receipt and supplier status |
| 6 | Post the journal | automatic | posts JE-*n*: debit 5000 Purchases, credit 2000 Accounts payable, dated the invoice date, linked as settling the bill. Refused if the preparer and approver are the same person or the period is closed. The bill closes as **Posted** |

**Routing** (finance-policy, in this order):

| Condition | Goes to |
|---|---|
| over 10,000 USD | the controller (bob) |
| at or under 2,000 USD | the ap-reviewer cog |
| any other bill | the payables reviewer (dave) |

**Approval criteria:** "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way
match is within tolerance and the supplier is approved." "Within tolerance" also requires the goods to be received.
When this does not hold, the cog declines and the bill goes to the payables reviewer. A bill of exactly 2,000 USD is
routed to the cog, which declines it ("amount 2000.0 is not under 2000.0 USD") (checked by running the code).

**Segregation of duties:** "The preparer and the approver of a bill must be different people."
**Due date and escalation:** approval is due within 5 days; overdue bills "are reported to the policy owner" (bob and
carol are informed; nobody is added).
**On reject:** Rejected.

**How to file a bill** (a finance clerk)

1. **Home** > **File a supplier bill** (or **Finance** > **+ File a supplier bill**).
2. Choose the supplier and the purchase order, give the amount, currency and invoice date, and attach the invoice
   file if you have it.
3. Press **File a supplier bill**. A small, matched bill from an approved supplier is approved and posted at once by
   the ap-reviewer cog; anything else waits for a person, and the status line says who.

## 3.6 Journal reversals

Posted journals are never edited; a correction is a reversal (finance-policy).

**Who starts it:** finance clerks. Form fields: **Journal\*** (picker), **Reason\***.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Request the journal reversal | finance clerk | JR-*n* filed in Finance |
| 2 | Approve the reversal | controller (bob) | |
| 3 | Post the reversal | automatic | posts the mirror image of the journal (debits and credits swapped) dated today, linked as superseding the original; refused if the journal was already reversed. Closes as **Posted** |

**Segregation of duties:** "The requester and the approver of a journal-reversal must be different people."
**Due date:** none. **On reject:** Rejected.

## 3.7 Supplier evaluation

A supplier is evaluated against fixed criteria and approved for a scope and a re-evaluation date (supplier-sop).

**Who starts it:** quality managers. Form fields: **Supplier\***, **Scope**. Criteria (a JSON object), score and
re-evaluation date can be given under **Advanced** or at approval.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Evaluate the supplier | quality manager | SUP-*n* filed in Quality/Suppliers; you are the evaluator |
| 2 | Approve the supplier | management representative (bob) | must give the **Score** and **Re-evaluation date**; may set approved and criteria. Closes as **Approved** |

Once approved, purchase requests treat the supplier as approved (3.2). Bill matching and the **Suppliers** page still
read only the finance system's own approved list (see chapter 10).

**Due date:** approval within 10 days; no escalation sentence. **On reject:** Rejected ("A rejected evaluation ends
the run.").

**The Suppliers page** (Purchasing app) lists the suppliers known to the finance system (seeded: Acme Metals,
approved; NewCo Tools, not approved) with their bills, purchase orders and evaluations. There is no form to add a
supplier.

## 3.8 Nonconformances

A nonconforming output is identified, contained, classified and dispositioned (rework, scrap, use-as-is or return).
A low-risk one may be dispositioned by the quality-assistant cog; a corrective action is opened when the risk or the
disposition demands one. Stock the nonconformance names is quarantined when it is filed, and the disposition is
applied to it (ncr-sop; wi-file-a-nonconformance; wi-disposition-stock; chapter 4.7.3).

**Who starts it:** staff. Form fields: **Title\***, **Description\*** (what was found, where, how many, whether anyone
could be hurt), **Severity** (low, medium, high), **Source** (inspection, complaint, audit, calibration, supplier,
internal), **Stock affected** (one line per lot: "lot number, quantity affected", or the lot number alone for all of
it), **Containment**. You are recorded as the reporter.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Open the nonconformance | staff (automatic step: ncr-classifier) | proposes the source ("complaint" if the description mentions a customer, else "inspection"); if you gave no severity, sets it high when the description mentions safety, recall, field, injury, fire or shock, otherwise low; a low severity from a calibration, complaint, audit or supplier source becomes medium; writes containment wording if you gave none ("Quarantine the affected items ... pending disposition, and notify the process owner."); for a low severity, proposes the disposition **rework** with a rationale |
| 2 | File the nonconformance | automatic | NCR-*year*-*n* in Quality/Nonconformances |
| 3 | Quarantine the stock | automatic | every lot named under **Stock affected** (or the quantity named, split off) goes into the site's quarantine location at once, without a Gate, if the reporter may quarantine stock; a lot that cannot be held is a warning |
| 4 | Score the risk | automatic | high: severity high, or source complaint or audit; medium: severity medium, or source calibration or supplier; low otherwise. Also records whether another nonconformance is open |
| 5 | Disposition the nonconformance | Gate (table below) | must give **Disposition** and **Reason for the disposition** (pre-filled for low severity; may be changed); may change containment. The card lists the stock held. The button reads **Sign the disposition of NCR-...** |
| 6 | Apply the disposition | automatic | scraps, releases (use as is), sends for rework or returns the quarantined lots (chapter 4.7.3) |
| 7 | Open corrective action | automatic | when the risk is medium or high, or the disposition is scrap or return, opens a CAPA (3.9) owned by whoever signed the disposition, linked to the nonconformance |
| 8 | Render the record | automatic | evidence snapshot; closes as **Closed** |

The severity you choose wins over the words in the description: the keyword check only applies when you leave
**Severity** empty (checked by running the code; see chapter 10).

**Routing** (ncr-sop, in this order):

| Condition | Goes to |
|---|---|
| risk low | the quality-assistant cog |
| any | the quality manager (carol) |
| any | the management representative (bob) |

The management representative is reached only when no quality manager may take it, for example because the quality
manager reported it.

**Approval criteria:** "The quality-assistant cog may approve nonconformance nonconformances when the risk is low."
This is a fixed criterion: the cog signs the classifier's proposed disposition (rework) without a model review, and
the stock held is then sent for rework (checked by running the code: chapter 10, Q1).

**Segregation of duties:** "The reporter and the approver of a nonconformance must be different people."
**Due date and escalation:** disposition within 5 days; overdue items go to the management representative.
**On reject:** Rejected.

**How to report a nonconformance** (staff)

1. **Quality** > **+ Report a nonconformance** (or **Home** > **Report a nonconformance**).
2. Give a short title and a description that says what was found, where, how many and whether anyone could be hurt.
   Choose a severity only if you are sure; left empty, the classifier sets it. Under **Stock affected**, name the lots
   (for example `LOT-000012, 20`).
3. Press **Report a nonconformance**. The status line gives its number and who holds it; the lots named are now in
   quarantine.
4. Quarantine anything else affected as the containment text says (wi-file-a-nonconformance): stock not named on the
   nonconformance can be held from its lot's page (chapter 4.6).

**How to disposition a nonconformance** (the quality manager, or the management representative when it is routed to
them)

1. Open it from the inbox. The card shows the classifier's source, severity and containment, the risk and the
   reporter.
2. Confirm containment was done; inspect the items.
3. Choose **Disposition** and write **Reason for the disposition**. Use-as-is needs the customer's concession where
   their requirement is affected (wi-disposition-stock).
4. Write the meaning, tick the confirmation and press **Sign the disposition of NCR-...**. The held stock is scrapped,
   released, sent for rework or returned at once. Scrap or return, or medium or high risk, opens a CAPA owned by you.

## 3.9 Corrective and preventive action (CAPA)

Root cause, actions, verification by the owner, an effectiveness check after a fixed interval, and closure by the
quality manager (capa-sop).

**Who starts it:** quality managers, or automatically from a nonconformance (3.8). Form fields: **Title\***,
**Nonconformances** (picker), **Root cause method** (5-whys, fishbone, fault-tree, other). Actions, owner, due date
and effectiveness criteria can be given under **Advanced**. The owner defaults to you; for a CAPA opened from a
nonconformance it is whoever signed the disposition.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Open the capa | quality manager (automatic step: capa-drafter) | proposes the root-cause method (5-whys), an action list ("Contain and investigate", the owner, open) and a due date 30 days out; warns that the root cause is not yet stated |
| 2 | File the capa | automatic | CAPA-*year*-*n* in Quality/CAPA |
| 3 | Verify the actions | the CAPA's owner | must give the **Root cause**; may set the method, actions (one per line: action; owner; due date; status), due date and effectiveness result |
| 4 | Schedule the effectiveness check | automatic | sets the effectiveness check date 90 days out ("The effectiveness check interval is 90 days.") and the criterion "No recurrence of the nonconformance in the interval." |
| 5 | Verify effectiveness | quality manager | must give the **Effectiveness result** |
| 6 | Render the record | automatic | evidence snapshot; closes as **Closed** |

The SOP gives step 3 to the process owner role, but the routing sentence sends it to the CAPA's owner field.
Quality managers, management representatives and process owners may approve CAPAs.

**Due dates and escalation:** "Verify the actions" is due within 30 days; overdue items go to the management
representative. "Verify effectiveness" has no due date, and nothing makes it wait for the check date.
**Segregation of duties:** no sentence. For a CAPA opened from a nonconformance, the person who reported the
nonconformance counts as having prepared the CAPA and may not take its role-routed step, "Verify effectiveness". If
the only person quality manager reported the nonconformance, nobody can verify effectiveness and the CAPA stops
(checked by running the code; chapter 10).
**On reject:** Rejected.

## 3.10 Complaints

Customer complaints are logged, risk-scored and answered by the quality manager; a medium or high one opens a
nonconformance (complaint-sop).

**Who starts it:** staff. Form fields: **Title\***, **Customer**, **Description\***, **Product**, **Severity** (low,
medium, high).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Log the complaint | staff | CMP-*year*-*n* in Quality/Complaints |
| 2 | Score the risk | automatic | from the severity: high, medium or low |
| 3 | Respond to the complaint | quality manager (carol) | must give the **Response** |
| 4 | Open the nonconformance | automatic | when the severity or risk is medium or high, opens a nonconformance (source complaint) linked to the complaint, reported by whoever responded. Closes as **Closed** |

**Due date and escalation:** response within 10 days; overdue items go to the management representative.
**On reject:** Rejected (no response is recorded).

Because the responder is recorded as the nonconformance's reporter, a nonconformance opened from a complaint the
quality manager answered goes to the management representative (3.8).

## 3.11 Internal audits

An auditor plans an audit against the processes as written in the policy release, records the findings, and the
quality manager accepts them (audit-sop; wi-run-an-audit).

**Who starts it:** internal auditors (bob). Form fields: **Title\***, **Clauses in scope**, **Processes**
(comma-separated process names such as `document-control`; empty means every process), **Planned date**. You are the
auditor unless you name another under **Advanced**.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Plan the audit | internal auditor (automatic step: audit-checklist-drafter) | one checklist question per step of each process in scope, from the release in force: "Is '*step*' performed by a *role* as the SOP states, with records?" |
| 2 | File the audit | automatic | AUD-*year*-*n* in Quality/Audits |
| 3 | Record the findings | the auditor | must give **Findings** (a list; an empty list is a valid finding set when nothing was found) |
| 4 | Notify the auditor | automatic | tells the person who planned the audit |
| 5 | Accept the findings | quality manager | must give the **Summary** |
| 6 | Render the record | automatic | the audit report as evidence; closes as **Closed** |

**Due date and escalation:** findings within 30 days; overdue items go to the management representative.
"Accept the findings" has no due date.
**On reject:** Rejected.

Raising nonconformances for findings is done by hand: "Raise a nonconformance for each major finding and link it to
the audit" (wi-run-an-audit). The SOP's "Auditors do not audit processes they own" is not checked by the hub.

## 3.12 Calibration

Calibration results are recorded, reviewed by the quality manager, and an out-of-tolerance result opens a
nonconformance (calibration-sop).

**Who starts it:** staff. Form fields: **Asset\*** (picker of asset records), **Result\*** (in-tolerance,
out-of-tolerance), **Standard**, **Procedure** (picker of documents). Measured values (a JSON object) and the next due
date can be given under **Advanced**. You are recorded as the performer, today as the date.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Record the calibration | staff | CAL-*n* in Quality/Calibration |
| 2 | Review the calibration | quality manager | must give the **Next due** date; may set the measured values |
| 3 | Open the nonconformance | automatic | when the result is out-of-tolerance, opens a nonconformance (source calibration) linked to the calibration. Closes as **Closed** |

**Due date and escalation:** review within 5 days; "Overdue items in calibration go to the management
representative", but management representatives may not approve calibrations, so the escalation only informs them.
**On reject:** Rejected.

**Note:** the asset picker lists asset records, and no page, form or process creates assets today, so a calibration
cannot be recorded from the web pages until assets exist (chapter 10). The SOP's "{{NATIONAL_STANDARDS_BODY}}"
placeholder is not filled in.

## 3.13 Management review

The management representative prepares the review with its inputs; the chair holds it and records the outputs; the
minutes are filed as evidence (management-review-sop; wi-record-management-review).

**Who starts it:** management representatives (bob). Form fields: **Title\***, **Date\***, **Inputs\***. Inputs is a
JSON object, one entry per input of ISO 9001 9.3.2, for example `{"audit results": "AUD-2026-001: 2 minor findings",
"customer feedback": "3 complaints"}`. The chair defaults to you; name another under **Advanced** (`"chair": "carol"`).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Prepare the review | management representative | MR-*year*-*n* in Quality/Management Review |
| 2 | Hold the review | the chair | must give **Outputs** (a JSON object: improvement decisions, changes to the QMS, resource needs); may set actions |
| 3 | Render the record | automatic | the minutes as evidence; closes as **Closed** |

**Due date and escalation:** no due date. The sentence "Overdue items in management-review go to the quality manager"
can therefore never apply.
**On reject:** Rejected.

Filing a task or change request for each action is done by hand after the review (wi-record-management-review).

## 3.14 Training acknowledgement

Every person assigned a controlled document acknowledges it within 14 days (training-sop; wi-acknowledge-training).

**How it starts.** When a controlled document becomes effective, the hub opens one training sign-off for every
*person* (not cog) holding one of the document's **Roles that must read it**. The record TRN-*n* ("Read *title* rev
*revision*") is filed in Quality/Training, linked to the document, and waits for that person alone. Its run belongs to
whoever approved the document (chapter 5.10).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | File the training record | automatic | TRN-*n* |
| 2 | Acknowledge the document | the assignee (the person who must read it) | approving is the signature that you have read and understood the revision; may set "assessed by". Closes as **Acknowledged** |

**How to acknowledge a document** (the person it waits for)

1. Open **Inbox** > **Waiting for me** > "Acknowledge the document: TRN-...".
2. Open the document from the card and read the revision named on the record.
3. Return to the card, write in the meaning that you have read and understood it, tick the confirmation and press
   **Approve TRN-...**.
4. If you do not understand it, do not approve: message the document's author from the record page, or reject with
   the reason.

**Due date and escalation:** within 14 days; overdue items "are reported to the quality manager" (informed only).
**On reject:** Rejected; the run's owner (the document's approver) is told.

Quality managers and document controllers may also start a training sign-off from **Home** (**Assign a training
sign-off**), for courses and inductions. The form shows only a title and a description, and the person and document
go in the JSON box; the field the step is routed by (the assignee) cannot be set there, so such a record stops at once
with "Nobody can take “Acknowledge the document”" (checked by running the code; chapter 10).

## 3.15 Tasks

A task is the general-purpose record for work that is not one of the quality processes: an action from management
review, an objective milestone, an improvement suggestion, a reminder (tasks-sop).

**Who starts it:** staff. Form fields: **Title\***, **Description**, **Assignee** (people and cogs; defaults to you),
**Due**, **Priority** (low, medium, high).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | File the task | staff | TASK-*n* in Tasks (everyone may read Tasks) |
| 2 | Complete the task | the assignee | the button reads **Mark TASK-*n* done**; may set description, priority, due. Closes as **Done** |

The task's own **Due** date shows on the record, on boards and in the **Overdue** filter, but the step itself has no
due date, so tasks are never escalated. **On reject:** Rejected.

**A task assigned to a cog.** The cog takes it from its queue and does the work within its autonomy. It completes the
step with a summary of what it did, or hands it back to the person who filed it with the reason. If it does neither,
the step goes back to that person automatically (hubproto/workqueue.py, `assigned_work`; chapter 6.6).

## 3.16 Design control and requirements

A mechanical or electrical design is submitted with its CAD files, exported, reconciled against the part master,
reviewed by cogs and the design authority, and released as a part revision; shortages become purchase requests
(design-control-sop).

### Design release

**Who starts it:** design engineers (alice). Form fields: **Title\***, **Description**, **Kind\*** (mechanical,
electrical, mixed), **Change class** (1, 2, 3; default 2), **User needs** (one per line), and **Files to attach**
(FreeCAD `.FCStd`, KiCad `.kicad_sch` and `.kicad_pcb`). Existing requirement numbers can be listed under
**Advanced** (`"requirements": ["REQ-2026-0001"]`).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Submit the design | design engineer | DSN-*year*-*n* in Engineering/Designs; attached files filed as documents |
| 2 | Trace the requirements | automatic | links the requirements you named; each user need becomes a requirement (a requirements-capture run, routed to the design authority); files the trace matrix as evidence; reports whether every requirement is accepted |
| 3 | Export the design | automatic | reads parts lists and bills of materials; through the CAD worker exports STEP and STL models, PDF schematics, netlists and Gerbers and runs the electrical and design rule checks (ERC, DRC); files every export as evidence. Check errors above "The design rule error limit is 0 percent." stop the run |
| 4 | Reconcile the bill of materials | automatic | matches each BOM line to a part through its manufacturer part (MPN and manufacturer; chapter 4.2) and notes what is available; proposes new parts for unknown lines; flags manufacturer parts not approved. Creates nothing yet |
| 5 | Score the risk | automatic | high: change class 1, or requirements not all accepted; medium: class 2, or design checks not clean or not run; low otherwise |
| 6 | Review the design package | quality-assistant and purchasing-assistant cogs | each cog reviews the design against the SOPs it follows; findings become warnings on the approver's card and one evidence document; the verdict shows on the record |
| 7 | Review the design | design authority (bob) | **Approve is blocked** until every traced requirement is accepted (or, with no trace, until requirements are linked or user needs given). May set change class and description |
| 8 | Release the part | automatic | creates part PN-*n* at revision A (an assembly, serial-tracked, not purchaseable, filed with the design), or the next revision of the part the design names; links the design and the BOM's parts |
| 9 | Order the shortages | automatic | creates the proposed parts, each with its manufacturer part, in state Open in Inventory/Parts, and raises a purchase request (needed in 14 days, through the part's primary active supplier part when it has one) for every line short of available stock |
| 10 | Render the record | automatic | evidence snapshot; closes as **Released** |

**Segregation of duties:** "The designer and the approver of a design must be different people."
**Due date and escalation:** review within 10 days; overdue items go to the management representative.
**On reject:** the design is **Returned** to the designer, who reworks and resubmits it from "Submit the design"
(the only process where a rejection returns the item).

### Requirements capture

**Who starts it:** design engineers, or automatically from a design's user needs. Form fields: **Statement\***,
**Kind\*** (user-need, system, functional, performance, safety, regulatory, interface), **Source**, **Priority** (must,
should, could).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Capture the requirement | design engineer | REQ-*year*-*n* in Engineering/Requirements |
| 2 | Score the risk | automatic | high for safety and regulatory requirements, low otherwise |
| 3 | Accept the requirement | design authority (bob) | must give **Acceptance criteria** and **Verification method** (test, inspection, analysis, demonstration) if missing; may set priority and rationale |
| 4 | Render the record | automatic | evidence snapshot; closes as **Accepted** |

**Segregation of duties:** "The captured-by and the approver of a requirement must be different people."
**Due date:** acceptance within 5 days; no escalation sentence. **On reject:** Rejected.

## 3.17 Change requests

Any member of staff raises a change request with its impact; the quality manager approves it; the requester carries
out the change and verifies it (change-control-sop; wi-raise-a-change-request). A change request asks for a change;
the change itself, when it revises SOPs or policies, travels on a change order (chapter 7.9).

**Who starts it:** staff. Form fields: **Title\***, **Description\***, **Impact\*** (which documents, processes,
products and people are affected and what could go wrong), **Affected documents** (comma-separated).

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Raise the change request | staff | CR-*year*-*n* in Quality/Changes; you are the requester |
| 2 | Score the risk | automatic | always medium (a change request cannot be declared minor) |
| 3 | Approve the change | quality manager (carol) | may set the plan and the verification |
| 4 | Notify the requester | automatic | tells the requester it was approved |
| 5 | Verify the change | the requester | must give **Verification** (what was done and how it was checked). Closes as **Closed** |

**Approval criteria:** "The qa-manager cog may approve change-control change-requests when its review passes,
countersigned by a quality manager." The qa-manager is not on any route: it acts only when asked (chapter 6.6).
**Permissions:** staff may approve change requests (so that the requester can sign the verification); the approval
step is routed to the quality manager only.
**Segregation of duties:** no sentence; the requester may not approve their own request (preparer rule) but verifies
it by design.
**Due date and escalation:** approval within 10 days; overdue items go to the management representative, who is
added as an approver (as staff he may approve change requests). "Verify the change" has no due date.
**On reject:** Rejected; raise a new request with the concerns addressed.

**How to raise and close a change request** (staff)

1. **Quality** > **+ Raise a change request**. Give the title, the description and the impact, and list the affected
   documents.
2. Press **Raise a change request**. It waits for the quality manager at "Approve the change", due in 10 days.
3. When you are told it was approved, carry out the change: revise documents through document control or a change
   order, update the processes, train the people.
4. Open the request from your inbox, write the **Verification** and approve. That closes the request.
