# 4. Inventory

Inventory follows the model of InvenTree, an open-source inventory system: one **part master**, the **manufacturer
parts** and **supplier parts** that say who makes a part and where it can be bought, **stock locations** in a tree,
and stock held as **lots** in those locations. Every movement of stock is an event on the lot in the hub's chained
log, so the ledger is the only source of a lot's quantity, place and status (hubproto/inventory.py).

The rules come from the inventory policy (inventory-policy, version 1.1.0) and the purchasing policy. Some movements
are free; others need an approval, which runs as a process like any other (chapter 3). This replaced the earlier
inventory items outright: item records are archived (4.10).

## 4.1 Who may do what

| Who (example company) | May | From |
|---|---|---|
| staff (everyone) | read Inventory; put stock in quarantine; ask for a part (part request); ask for a release from quarantine; record a goods receipt | inventory-policy, purchasing-policy |
| storekeepers (dave, the stores cog) | move, count, split, merge and mark stock; ask for a stock adjustment; create stock locations and part categories | inventory-policy |
| buyers (dave, the purchasing-assistant cog) | approve part requests; confirm goods receipts; create manufacturer parts and supplier parts; read Inventory | inventory-policy, purchasing-policy |
| design engineers (alice) | create parts and manufacturer parts | inventory-policy |
| the controller (bob) | approve stock adjustments | inventory-policy |
| stock reviewers (the purchasing-assistant cog) | approve stock adjustments, within its approval criteria only | inventory-policy |
| quality managers (carol) | approve releases from quarantine | inventory-policy |

The policy says "Whoever keeps the stock does not approve its write-off": dave is a storekeeper and may not approve
adjustments, and the purchasing assistant holds the stock-reviewer role "for that and for nothing else", so it keeps
no stock (inventory-policy). The quality manager approves releases; nobody may approve a lot itself, so a lot cannot
be put on hold.

There is no page for creating manufacturer parts, supplier parts, part categories or stock locations. Stock
locations and part categories come with the sample data (4.3) and manufacturer parts with the parts a released design
makes (chapter 3.16). Otherwise these records are made only by a cog's `create_record` tool, which checks that both
the cog and the person it works for may create the record there (chapter 10, I6).

## 4.2 Parts, manufacturer parts and supplier parts

| Record | Number | What it is | Main fields |
|---|---|---|---|
| **Part** | PN-00001 | one entry in the part master, for a purchased component or an assembly made from a released design | title, internal part number (IPN), revision, category, units (default `ea`), **Tracked by** (none, lot or serial), purchaseable, assembly, component, active, default location, minimum stock, description, footprint, parameters |
| **Manufacturer part** | MP-00001 | the manufacturer's part number for a part: the approved manufacturer list | part, manufacturer, MPN, approved |
| **Supplier part** | SP-00001 | what can be ordered from one supplier | part, supplier, manufacturer part, supplier SKU, pack quantity, price breaks, currency, lead time, primary, active |
| **Part category** | CAT-0001 | a tree of categories; a structural category only groups others | name, parent, structural, default location |

(config/record-types.json.)

**A part is active** when its record is Active (or Released, for an assembly), its **Active** field is not off and it
is purchaseable. Only an active part meets the purchasing assistant's criterion "the part is active" (chapter 3.2)
(hubproto/inventory.py, `part_active`).

**Price breaks.** A supplier part lists prices by quantity, for example "1+: 0.50 USD; 1000+: 0.40 USD". A purchase
request takes the price of the highest break at or under its quantity, and warns when the quantity is not a whole
number of the supplier part's packs (hubproto/procurement.py, `SupplierChecker`).

**Where parts come from.**

* A **part request** (4.7.4) files a new part, which the buyer approves. It has no manufacturer part and no supplier
  part.
* A **released design** (chapter 3.16) makes its assembly a part (revision A, serial-tracked, not purchaseable),
  filed with the design in Engineering/Designs. Bill-of-materials lines that match no manufacturer part become new
  parts in Inventory/Parts, each with its manufacturer part, in state Open. An Open part is not active, so the
  purchasing assistant declines the purchase requests raised for it and a person approves them.

## 4.3 Stock locations

Stock locations (LOC-0001 ...) form a tree. Each has a **kind**, and a **structural** location only groups the ones
under it and holds no stock. The **site** is the nearest site name up the tree.

| Kind | Stock here counts as available? | Notes |
|---|---|---|
| store | yes | the default kind |
| receiving | yes | goods receipts default to the first receiving location |
| production | yes | a **rework** disposition sends stock here (4.7.3) |
| inspection | no | |
| quarantine, mrb | no | hold locations: only quarantined stock is kept here (4.6) |
| scrap | no | stock goes here only through a **scrap** disposition |
| transit | no | stock moves between sites through a transit location |

**The sample site.** Every hub has a structural location **Main site** (site MAIN) with Stores, Receiving, Inspection,
Quarantine, Scrap, Production and Transit under it, and the part categories Electronics (structural, with Resistors,
Capacitors and Connectors), Mechanical and Assemblies. A hub created before inventory existed gets them on its next
start (hubproto/inventory.py, `seed`; chapter 8.2).

## 4.4 Lots and the stock ledger

A **lot** (the record type is "stock item", numbered LOT-000001) is a quantity of one part in one location: one lot per
line of a goods receipt, or one lot per serial number for a serial-tracked part. It has a batch, a serial number, an
expiry date, its unit cost and currency (from the order line), and the order, receipt and supplier part it came
from.

| Status | Meaning |
|---|---|
| OK | usable |
| ATTENTION, DAMAGED | out of use where it is (marked by a storekeeper, or ATTENTION after a rework disposition) |
| QUARANTINED | held in a quarantine or MRB location |
| DESTROYED | scrapped |
| RETURNED | returned to the supplier |
| REJECTED, LOST | listed, but nothing in the hub sets them today |

A lot is also in one of the states **In stock**, **Depleted** (counted, adjusted or merged down to nothing),
**Scrapped** or **Returned**. A lot is **available** when it is OK, in stock, holds more than nothing, has not expired
and sits in a location that counts as available (4.3). "Available" is what the parts list, the purchase-request form and
the design's shortage check count; "on hand" counts every lot in stock.

**The ledger.** Every movement writes one event on the lot: received, moved, counted, adjusted, split, merged,
quarantined, released, scrapped, returned to the supplier, sent for rework, status changed. The event carries the
quantity and the lot's resulting quantity, location, status and state, so the lot's fields are what its last event
left. The events are part of the record system's chain, which `hubproto verify` checks (chapter 8.6). Nothing
undoes a movement; a correction is another movement.

**Genealogy.** A lot split off another is linked "split from" it; merged lots are linked "merged into" the one they
joined; a received lot is linked "received in" its goods receipt; a lot held for a nonconformance is linked
"quarantined by" it.

## 4.5 The inventory pages

The **Inventory** app lists stock lots, parts, locations, receipts, stock adjustments, stock releases, supplier parts,
manufacturer parts and part categories, with two pages of its own (hubproto/shell/inventory_ui.py):

* **Parts and stock** (`/inventory`): tiles for parts, lots in stock, quarantined lots and parts below their minimum
  stock; a filter by words (number, title, IPN or MPN), category and "below minimum only"; and one row per part with
  its title, category, IPN, **Available**, **On hand**, minimum and state. A part below its minimum shows **low**.
  **Request a part** and **Raise a purchase request** are offered when you may start them.
* **Locations** (`/inventory/locations`): the location tree with the number of lots in and under each location; the
  chosen location's kind, site, whether it holds stock and whether it counts as available; the locations in it; and
  the stock here (or, for a structural location, the stock under it).

Record pages show what inventory knows about them, in a card above the tabs:

| Page | Shows |
|---|---|
| a part | **Available**, **On hand** and **Minimum stock**; stock by location; every lot; its supplier parts with SKU, pack, price breaks and lead time; its manufacturer parts; and a **Raise a purchase request** form (active parts only) |
| a lot | its part, quantity, status, location, batch, serial number, expiry, whether it is available (and why not), unit cost, order, receipt, the nonconformance that holds it; the **Actions** you may take (4.6, 4.7); the **Ledger**; the **Genealogy** |
| a location | its path and the stock held here |
| a purchase order | **Receiving**: each line ordered, received and outstanding, and the receive form (4.8) |
| a goods receipt | **Lots booked** |
| a nonconformance | the stock it names, the lots it quarantined, and what its disposition did to them |

The inbox card of a stock adjustment shows the lot, its quantity now, the change and its value, whether it is
within the count tolerance, the change since the lot was last set (4.6) and who approved the order the stock came
from. The card of a stock release shows where the stock goes and any open nonconformance that blocks it.

## 4.6 Free movements

These need no approval. Each is a form under **Actions** on the lot's page, and each refusal re-displays the form
with the reason beside the field.

| Movement | Who | Rules |
|---|---|---|
| **Transfer** | storekeepers | to another location on the same site; between sites only through a transit location (two moves). Part of a lot is split off first. Quarantined stock moves only between hold locations; other stock never goes into a hold location (quarantine it instead) or to scrap |
| **Count** | storekeepers | booked at once when within the count variance tolerance (2%) of the lot's baseline (below); outside it, nothing is booked and the page offers the adjustment instead. A serial-numbered lot counts 1 or 0 |
| **Split** | storekeepers | part of a lot becomes a new lot in the same place, with the same batch and status. Not for a serial number, nor for a lot held for an open nonconformance |
| **Merge** | storekeepers | lots of the same part, batch, status and location merge into one. Never serial numbers, never lots without a batch, never lots held for an open nonconformance |
| **Mark** | storekeepers | ATTENTION or DAMAGED: out of use where it is. Back to OK only through a release (4.7.2). Not for quarantined stock |
| **Quarantine** | any member of staff | all of a lot, or the affected quantity split off, goes to the site's quarantine (or MRB) location as QUARANTINED. "Containment never waits." It leaves only through a release or a nonconformance's disposition |

(inventory-policy; hubproto/inventory.py, `transfer`, `count`, `split`, `merge`, `mark`, `quarantine`.)

**How to count a lot** (a storekeeper)

1. Open the lot (from **Parts and stock**, the part, or the location) and open **Count** under **Actions**.
2. Give **Counted** and an optional **Note**, and press **Book the count**.
3. Within the tolerance the status line says "Counted LOT-...: 98 (-2, within the tolerance)". Outside it, the page
   says **Not booked: the count needs an approval**, with the variance, and offers **Request the adjustment to *n***
   with **Why the stock differs** filled in (4.7.1).

**The count baseline.** A count is measured from the quantity the lot was received or last adjusted to, not from the
last count, "so a run of small counts shares one tolerance". A split takes its share of the baseline and a merge adds
the baselines, so splitting stock off, counting it down and merging it back does not start a fresh tolerance
(inventory-policy; hubproto/inventory.py, `count_baseline`). Checked by running the code: a lot received as 100 was
counted at 98 (booked); a count of 97 was then refused, "a variance of 3.0% from 100 ... is outside the count variance
tolerance of 2%". The Count form says "Within 2% of *the current quantity*", not of the baseline.

**How to hold stock in quarantine** (any member of staff)

1. On the lot's page open **Quarantine**. Give the **Quantity affected** (empty for all of it) and **Why it is held**.
2. Press **Hold it in quarantine**. The stock goes to the site's quarantine location at once. To report the problem,
   open a nonconformance naming the lot (chapter 3.8); a nonconformance that names stock quarantines it itself.

## 4.7 Movements that need an approval

### 4.7.1 Stock adjustments

An adjustment sets a lot to the quantity it should hold: a count outside the tolerance, a loss, a correction.

**Who starts it:** storekeepers (dave, the stores cog), from **Request an adjustment** on the lot's page, from the
"Not booked" card after a count, or **Home** > **Request a stock adjustment**. Fields: **Lot\***, **New quantity\***,
**Reason\***. Records are ADJ-*n* in Inventory/Adjustments.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Request the stock adjustment | storekeeper | ADJ-*n* filed |
| 2 | Check the adjustment | automatic | the lot's quantity now, the variance and its percentage, the value (the variance times the lot's unit cost), whether it is within the count tolerance **measured from the baseline**, and who approved the order the stock came from ("ordered by"). Stops if the lot is not in stock |
| 3 | Score the risk | automatic | high: value over the adjustment approval threshold (500 USD); medium: outside the tolerance; low otherwise |
| 4 | Approve the stock adjustment | Gate (below) | |
| 5 | Apply the adjustment | automatic | books the new quantity on the ledger and makes it the lot's new baseline. Closes as **Applied** |

**Routing** (inventory-policy, in this order): over 500 USD to the controller (bob); under 100 USD to the
purchasing-assistant cog; any other to the controller.

**Approval criteria:** "The purchasing-assistant cog may approve stock-adjustment stock adjustments under 100 USD when
the variance is within tolerance." Within tolerance is measured from the baseline, like a free count, so the cog
signs only a change a storekeeper could also have booked as a count; signing it moves the baseline (chapter 10, I3).
A lot with no unit cost has a value of 0.

**Segregation of duties:** "The requester and the approver of a stock-adjustment must be different people." "The
ordered-by and the approver of a stock-adjustment must be different people." Checked by running the code: for stock
the purchasing assistant had ordered, the cog's route was skipped ("they are the ordered by of this item") and the
adjustment went to bob; for stock bob had ordered (an order over 10,000 USD), an adjustment of 100 USD or more
stopped: "Nobody can take “Approve the stock adjustment”" (chapter 10, I2).

**Due date:** within 3 days; no escalation sentence. **On reject:** Rejected.

**An adjustment is applied only to what was checked.** If the lot changed between the check and the approval (a
split, a count, part of it moved), applying stops: "LOT-... changed since the adjustment was checked: it holds 500
now, not 1000; nothing was booked." **Fix and retry** then starts again from the check, and the approval given on the
old check is void, so it is approved again on what the lot holds now (hubproto/inventory_cogs.py,
`AdjustmentApplier`; hubproto/runner.py, `retry`).

### 4.7.2 Releases from quarantine

A release returns a lot that is QUARANTINED, ATTENTION or DAMAGED to OK stock.

**Who starts it:** any member of staff, from **Request a release** on the lot's page (offered when no open
nonconformance names the lot) or **Home** > **Request a release from quarantine**. Fields: **Lot\***, **To location**
(left empty, the stock goes back where it was quarantined from), **Reason\***. Records are REL-*n* in
Inventory/Releases.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Request the stock release | staff | REL-*n* filed |
| 2 | Check the lot | automatic | stops when the lot is not held ("LOT-... is OK: nothing to release"), when an open nonconformance names it ("its disposition releases it") or when it has expired |
| 3 | Score the risk | automatic | medium when the lot was held for a nonconformance, low otherwise |
| 4 | Approve the stock release | quality manager (carol) | may set where it goes and the reason |
| 5 | Release the stock | automatic | OK again, in the location chosen, else back where it was quarantined from, else in the site's stores; never into a hold or scrap location. Closes as **Released** |

**Segregation of duties:** "The requester and the approver of a stock-release must be different people." Checked by
running the code: a release alice asked for was approved by carol; one carol asked for stopped, because she is the
only person quality manager. **Due date:** within 5 days; no escalation sentence.

### 4.7.3 Nonconformance dispositions

A nonconformance may name stock under **Stock affected** (one line per lot: "lot number, quantity affected", or the
lot alone for all of it). Filing it quarantines that stock at once, without a Gate; the reporter must be allowed to
quarantine stock (all staff are). After the disposition is signed, the disposition is applied to the lots it holds
(chapter 3.8; hubproto/inventory_cogs.py, `StockQuarantiner`, `DispositionApplier`):

| Disposition | What happens to the held lots |
|---|---|
| scrap | DESTROYED, quantity 0, moved to the site's scrap location; state Scrapped |
| use as is | released: OK, back where it was quarantined from |
| rework | ATTENTION, moved to the site's production location; it needs a release (4.7.2) before it is available |
| return | RETURNED, quantity 0; state Returned |

Checked by running the code for each of the four. While a nonconformance is open, the lots it holds are not split,
merged, marked or released on their own.

### 4.7.4 Part requests

**Who starts it:** any member of staff, from **Request a part** on **Parts and stock** or **Home**. Fields:
**Title\***, **Category**, **Units**, **Tracked by**, **Purchaseable**, **Minimum stock**, **Description**.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Request the part | staff | PN-*n* filed in Inventory/Parts |
| 2 | Approve the part | buyer (dave) | may set title, category, units, tracking, purchaseable and minimum stock. Closes as **Active** |

No due date; the preparer rule applies, so a part dave asks for stops: nobody else is a buyer (checked by running the
code). The approved part has no manufacturer part or supplier part; the part's page then says "No supplier part: the
part cannot be ordered until one is added", and there is no page to add one (chapter 10, I6).

## 4.8 Buying and receiving into stock

Purchasing works on parts (chapters 3.2 and 3.3):

* A **purchase request** names a part and, usually, a supplier part. The supplier check takes the named supplier
  part, or the part's primary active supplier part from the named supplier, prices the request from its price breaks
  and computes the amount. The purchasing assistant's criterion is "when the supplier is approved and the part is
  active".
* The **purchase order** line carries the part, supplier part, SKU, MPN, quantity and unit cost.
* A **goods receipt** books the confirmed lines into a stock location as lots, at the order line's unit cost, and
  marks the order received when nothing is outstanding.

**How to receive an order** (any member of staff; the buyer then confirms it)

1. Open the purchase order. Under **Receiving**, each line shows what was ordered, received and is outstanding.
2. For each line give **Receiving now**, the **Batch** (required for a lot-tracked part) or the **Serial numbers**
   (one per unit, for a serial-tracked part), and an **Expiry** if it has one. Choose **Into** (the receiving
   location by default; the list leaves out structural, hold, scrap and transit locations) and give the **Packing
   slip** and **Notes** if you have them.
3. Press **Record the receipt**. The form refuses a line before anything is filed when serial numbers are missing,
   repeated or already in stock, when a lot-tracked part has no batch, or when the quantity is more than is
   outstanding plus the over-receipt tolerance of 5% ("The over receipt tolerance is 5 percent.").
4. The receipt GR-*n* waits for the buyer (dave) at **Confirm the receipt**. When it is confirmed, the stock is
   booked: the receipt's page lists the lots.

A receipt can also be recorded from **Home** > **Record a goods receipt**, with one line per part ("part number,
quantity, batch"). That form lists every location, and its checks differ from the order's form (chapter 10, I4 and
I5). Checked by running the code: 106 received against an order of 100 stopped at the receipt's check ("received 106
of PN-00002, outstanding 100 on PO-0002 (over-receipt tolerance 5%)"); 105 waited for the buyer.

## 4.9 Cogs and stock

* **stores** (storekeeper, staff; autonomy act) keeps stock "as a storekeeper does": through its tools it finds parts
  and their stock, moves, counts and quarantines lots, and asks for adjustments and releases, only where the person it
  works for may too. It approves nothing: "you never sign a write-off, and you never release stock from quarantine"
  (inventory-policy; cogs.frame.md; hubproto/agent_inventory.py). A count outside the tolerance is never booked by a
  cog; the tool says so and points the cog to asking for an adjustment. An adjustment it asks for on someone's behalf is
  filed in that person's name.
* **purchasing-assistant** holds the stock-reviewer role: it signs small adjustments within its criteria (4.7.1). It
  may not move or count stock; asked to, it answers that it "keeps no stock ... A storekeeper does it on the lot's
  page in the hub". Like everyone on the staff it may quarantine stock.

The inventory tools are `find_parts`, `stock_of`, `transfer_stock`, `count_stock`, `quarantine_stock`,
`request_stock_adjustment` and `request_stock_release`. There is no tool to split, merge or mark a lot (chapter 10,
I7).

## 4.10 Hubs that had inventory items

The earlier **item** records (ITEM-00001 ...) are archived: they stay readable under **Engineering** > **Archived
items**, nothing new is filed as an item and nothing on one changes. A purchase request or order that names an item
shows it as "Item (archived)". An order placed before inventory, whose lines name items, cannot be received into
stock: its **Receiving** card says "its lines name items, which are archived: nothing on it is received into stock",
and a receipt recorded against it stops at its check (tests/test_inventory_ui.py).
