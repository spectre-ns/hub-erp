# Inventory / stores controller findings

Persona: inventory and stores controller. Drove the scenario end to end on
2026-11-06 against release `b7da4af94d3c571f`: design DSN-2026-0001 (run
48a36eb74e) released by bob, which created ITEM-00001 (100n, CL10B104KB8NNNC)
and ITEM-00002 (10k, RC0603FR-0710KL) and raised PR-00001/PR-00002; then
requests PR-00003/PR-00004/PR-00005/PR-00024 as alice, orders PO-0001,
PO-0002, PO-0005, PO-0013, and receipts GR-00001 to GR-00004, GR-00007 to
GR-00009, GR-00012, GR-00013, GR-00015, GR-00016. Other panelists were
active in the same hub at the same time; record numbers not prefixed `[INV]`
and not listed above belong to them and are cited only as corroborating
evidence.

All shell steps below were driven with `curl` against
`http://127.0.0.1:8080` (POST `/start` with the form fields `as`, `process`,
`title`, `supplier`, `json`; POST `/decide` with `as`, `run`, `verdict`,
`meaning` and extra fields). CLI steps are `docker compose exec hub hubproto ...`.

## Items

### F-inventory-1: [INV] No way for a person to create or edit an item; the "known item" of the scenario cannot be set up
Severity: blocker
Area: shell
Steps:
1. `http://127.0.0.1:8080/?as=alice` (design engineer, who per purchasing-policy line 37 and design-control-sop line 34 "may create items in Inventory/Items"): the "Processes you can start" list has no process that files an `item`.
2. `http://127.0.0.1:8080/files?as=alice&folder=Inventory/Items`: no "new item" form; only the record list.
3. `http://127.0.0.1:8080/record/rec:item:ITEM-00001?as=alice` and `?as=bob`: the only forms on the page are `/hold` and `/snapshot`; no field editing.
4. `hubproto --help`: there is no `record-new` / `set-fields` command; only `records`, `record`, `link`, `attach`, `snapshot`.
5. Before the design was released, `hubproto records --type item` returned nothing.
Expected: an item master form (or process) that a design engineer or stores controller can use to create an item with MPN, manufacturer, approved-manufacturer flag, unit, supplier, unit cost, lead time and opening stock, and to edit those fields later. Scenario step 3 ("the small request for the known item from the approved supplier") needs an item with a supplier and unit cost to exist before the design is released; nothing in the product lets any user create it (the test suite does it with `kernel.create`).
Actual: items only come into existence as a side effect of releasing a design with unresolved BOM lines; they then carry no supplier, unit cost or lead time, so every request raised from a design is "no supplier named", amount 0, and goes to the buyer. The cog path of the scenario is unreachable without hand-written JSON on a purchase request.
Evidence: `hubproto record ITEM-00001` (fields: footprint, manufacturer, mpn, name, stock only); PR-00001 `supplier_note: no supplier named`, `amount 0.0`.
Suggested fix: an `item` process (`[design-engineer|staff] Create the item (record-filer)`) exposed on the Start page with typed inputs, plus a field edit form on the record page under an `edit` permission.

### F-inventory-2: [INV] Items created from a BOM miss the type defaults and have no owner of their own
Severity: minor
Area: records
Steps:
1. Release DSN-2026-0001 as bob (run 48a36eb74e).
2. `hubproto record ITEM-00001`, `hubproto record ITEM-00002`.
Expected: `config/record-types.json` gives `item` defaults `unit: ea`, `approved_manufacturer: true`, `stock: 0`; a new item should show them, and `supplier`, `unit_cost`, `currency`, `lead_time_days` should at least be present (empty) so a buyer knows what to fill in.
Actual: fields are `footprint, manufacturer, mpn, name, stock` only; no `unit`, no `approved_manufacturer`. The item says `created ... by bob` and owner `bob` (the design authority who approved the design), although bob never created an item; alice, the designer, does not appear. Stock is stored as a float (`1.0`, `5.0`) for a unit of `ea`.
Evidence: `hubproto record ITEM-00001` output; item page "owner bob".

### F-inventory-3: [INV] The item board is empty even though items exist
Severity: major
Area: shell
Steps:
1. `http://127.0.0.1:8080/boards?as=dave&type=item` and `...&type=item&state=any`.
2. Compare `http://127.0.0.1:8080/files?as=dave&folder=Inventory/Items`.
Expected: a board of the items (by state, or by folder/stock status) as the scenario step 6 asks ("the boards for ... item").
Actual: the board shows only two columns, `failed 0` and `rejected 0`; ITEM-00001 and ITEM-00002 (state `open`) are not shown. The folder list shows both. The `purchase-order` and `receipt` boards do show their records, so the board derives its columns from a workflow and `item` has none ("No workflow in the release covers this record type").
Evidence: board page text "failed 0 rejected 0"; Items folder lists ITEM-00001, ITEM-00002.

### F-inventory-4: [INV] The item page has no stock summary: on hand only, no on-order, no location, no value
Severity: minor
Area: records
Steps:
1. `http://127.0.0.1:8080/record/rec:item:ITEM-00002?as=dave` after PO-0002, PO-0005 and receipts GR-00002, GR-00007, GR-00008.
Expected: on hand, on order (open PO quantity), allocated/reserved by released designs, per-location quantities, average cost and value, and a stock movement list with a running balance.
Actual: a single `stock` number. The Activity tab does carry `stock.received` events (`quantity 1, po PO-0002, receipt GR-00002`), which is good, but there is no running balance, no on-order figure, and the "run" shown in the header is whichever receipt last touched the item (`run caf7c0f868`, later `929e0c6dee`), which is confusing for a master record.
Evidence: item page header and Activity rows 1116-1117.

## Purchase requests and orders

### F-inventory-5: [INV] The Start forms for purchase-request and goods-receipt have no item, quantity, unit cost or PO/lines inputs
Severity: major
Area: shell
Steps:
1. `http://127.0.0.1:8080/?as=alice`, card "purchase-request": inputs are title, description, amount, currency, date, po_ref, supplier, change_type, severity, training_roles and "other fields (JSON object)".
2. Card "goods-receipt": the same inputs.
3. Start goods-receipt as alice with the visible `po_ref` field = `PO-0002` and no JSON: run d76986da1b, GR-00001.
Expected: a request form with item (picker), quantity, unit, unit cost, needed-by, reason; a receipt form with a PO picker that pre-fills the lines, and quantity received per line.
Actual: a clerk has to type `{"item":"rec:item:ITEM-00002","quantity":2,"unit_cost":0.02}` or `{"po":"rec:purchase-order:PO-0002","lines":[{"item":"rec:item:ITEM-00002","quantity":1}]}` into the JSON box, knowing the internal locator syntax. The visible `po_ref` field is not the receipt's `po` field: GR-00001 is `failed` with "no purchase order None" and the value is stored as a stray `po_ref` field on the record. `change_type`, `severity` and `training_roles` are shown on every form regardless of type.
Evidence: GR-00001 (`hubproto record GR-00001`: fields `po_ref PO-00002`, transition "open -> failed ... no purchase order None"); run d76986da1b.

### F-inventory-6: [INV] The buyer's unit cost given at approval is discarded; the PO is issued at 0.00 and the supplier check is not re-run
Severity: blocker
Area: workflow
Steps:
1. PR-00001 (1 x CL10B104KB8NNNC, raised by the design release) waits for dave with `amount 0.0`, `supplier_note: no supplier named`.
2. As dave, decide run daa585f4c0 approve with `supplier=acme-metals`, `unit_cost=0.10`, meaning "order from Acme".
3. `hubproto record PR-00001`; `hubproto record PO-0001`; PR-00001 page Activity tab.
4. Repeat on PR-00002 (run e66122a24a) with `supplier=acme-metals`, `unit_cost=0.05`, `amount=0.10`.
Expected: PO-0001 for 1 x at 0.10 USD = 0.10 USD to acme-metals; PR-00001 `supplier_approved True`; alice told "Ordered 1 x ... (0.1 USD)".
Actual: Activity row 716 `record.fields_set by dave {"supplier": "acme-metals", "unit_cost": 0.1}` is immediately followed by row 723 `record.fields_set by po-issuer {"amount": 0.0, "po": ..., "unit_cost": 0.0}`. PO-0001: `amount 0.0`, line `unit_cost 0.0, amount 0.0`. PR-00001 still says `supplier_approved False`, `supplier_note no supplier named` although `supplier acme-metals` is now on it. alice's DM: "Ordered 1 x CL10B104KB8NNNC from acme-metals (0.0 USD)". Same on PR-00002 -> PO-0005 (`amount 0.0`). The PO issuer takes its figures from the previous step's payload, not from the record the buyer just updated. A 0.00 PO cannot be three-way matched to the supplier's bill (scenario step 5).
Evidence: PR-00001 Activity rows 716, 717, 723; `hubproto record PO-0001`, `PO-0005`; alice inbox lines for PO-0001 and PO-0005. Other panelists hit the same: PO-0006, PO-0007, PO-0014, PO-0015 all issued at 0.0 USD.
Suggested fix: po-issuer and supplier-checker read the record after the Gate (or the decision merges fields into the payload); re-run the supplier check when the approver names a supplier.

### F-inventory-7: [INV] The buyer cannot name a supplier or a unit cost from the inbox; the decision card only asks for "po"
Severity: major
Area: shell
Steps:
1. `http://127.0.0.1:8080/inbox?as=dave`, card "Approve the purchase: PR-00005".
Expected: inputs for supplier (from the approved list) and unit cost, since scenario step 3 says dave "approves it naming acme-metals and a unit cost".
Actual: the decision form has `po`, `meaning`, `verdict` only, with the hint "ordered needs: po". `po` is the field the po-issuer fills afterwards; a buyer typing something into it is wrong, and there is no supplier or unit-cost input at all. I could only name the supplier by crafting the POST with curl.
Evidence: inbox form field names for runs 0e98b5b362, 5d89958250 etc.: `['po']`.

### F-inventory-8: [INV] A buyer can approve a request to a supplier that is not approved; a PO is issued to it
Severity: major
Area: policy
Steps:
1. As alice, start purchase-request "[INV] 3 x 10k from NewCo", `supplier=newco-tools`, JSON `{"item":"rec:item:ITEM-00002","quantity":3,"unit_cost":0.02}` -> PR-00005, `supplier_note: supplier NewCo Tools is registered but not approved`, routed to dave. Nothing on the record or the inbox card blocks approval.
2. Corroboration from another panelist: PR-00010 (newco-tools, 100 USD) was approved by dave ("[ACCT] buyer approves unapproved supplier") and PO-0009 was issued to newco-tools; PR-00018 likewise -> PO-0012.
Expected: purchasing-policy says "The supplier must be on the approved list". A request naming an unapproved supplier should fail a Guard at approval (or require a supplier evaluation first, or route to the controller with a problem), not just carry a warning.
Actual: the restriction only binds the cog's approval criteria; a person can approve anything.
Evidence: `hubproto record PR-00005` (risk high, supplier_approved False, assignees dave); `hubproto record PR-00010` (decision by dave, po PO-0009); alice's DM "PO-0009 issued ... from newco-tools (100.0 USD)".

### F-inventory-9: [INV] There is no purchase order document to send to the supplier
Severity: major
Area: DMS
Steps:
1. `hubproto record PO-0002`: `documents: none`. `http://127.0.0.1:8080/record/rec:purchase-order:PO-0002?as=dave`: "No documents attached"; dave "cannot attach documents here".
2. The only action is "Render snapshot": POST `/snapshot` with `record=rec:purchase-order:PO-0002` as dave -> DOC-0131 `PO-0002-received.md`.
3. Open `http://127.0.0.1:8080/document/rec:document:DOC-0131?as=dave`.
Expected: on "Issue the purchase order" a controlled PO document (PDF) with supplier name and address, ship-to, PO number, line table (item, MPN, quantity, unit, unit price, amount), currency, delivery date, terms, and an email to the supplier (the DMS has email intake, nothing sends).
Actual: the snapshot is an internal evidence dump: "# PO-0002 (purchase-order)", state, folder, release digest, and the lines as raw JSON (`[{"amount": 0.04, "item": "rec:item:ITEM-00002", ...}]`). Nothing is sent to acme-metals. The purchase request gets a rendered snapshot (DOC-0076) at approval; the PO gets nothing.
Evidence: DOC-0131 text; `hubproto record PO-0001` documents: none.

### F-inventory-10: [INV] No open-quantity or expected-receipts view; the PO loses the needed-by date
Severity: minor
Area: records
Steps:
1. `http://127.0.0.1:8080/files?as=dave&folder=Purchasing/Orders`; `http://127.0.0.1:8080/boards?as=dave&type=purchase-order`.
2. `hubproto record PO-0001` right after issue.
Expected: a list of open POs with ordered / received / outstanding quantity per line and the expected date; `needed_by` (PR-00001: 2026-11-20) carried onto the PO; `received: false` visible from issue.
Actual: the folder and board show only `issued` / `received` states; no quantities, no dates. PO-0001 at issue shows no `received` field at all (the type default `received: false` is not applied when the po-issuer creates the record); it only appears after a receipt books it.
Evidence: Orders folder table (columns Number, Type, State, Title, Owner, Assignees, Due, Updated, Run); PO-0001 fields before 14:47.

### F-inventory-11: [INV] A request for an item already in stock is ordered without any check; no reorder points
Severity: minor
Area: workflow
Steps:
1. ITEM-00002 stock = 6 (after GR-00002, GR-00007, GR-00008 and another panelist's receipt).
2. As alice, start purchase-request "[INV] 1 x 10k by number while 6 in stock", `supplier=acme-metals`, JSON `{"item":"ITEM-00002","quantity":1,"unit_cost":0.02}` -> run 841cc5aafc, PR-00024.
Expected: at least a problem "stock on hand (6) covers the request" for the approver; and, in the other direction, a reorder point / minimum on the item that raises a request automatically when stock falls below it.
Actual: supplier-checker and risk-scorer pass, the cog approves "within fixed criteria", PO-0013 is issued. There is no reorder point, minimum, or min/max field on the item, and no process that watches stock.
Evidence: run 841cc5aafc steps; `hubproto record PO-0013`.

### F-inventory-12: [INV] An item given by bare number is accepted and produces a PO whose line cannot be received by locator
Severity: major
Area: workflow
Steps:
1. PR-00024 raised with `"item":"ITEM-00002"` (number, not `rec:item:ITEM-00002`). supplier-checker reports the item as known; PO-0013 line is `{"item": "ITEM-00002", ...}`.
2. As alice, start goods-receipt JSON `{"po":"rec:purchase-order:PO-0013","lines":[{"item":"rec:item:ITEM-00002","quantity":1}]}` -> run 71b17f4d84, GR-00015.
3. Start the same with `"item":"ITEM-00002"` -> run 07e196d995, GR-00016; confirm as dave.
Expected: one canonical identifier; a receipt for the item on the order matches whichever spelling the clerk uses.
Actual: GR-00015 fails "line rec:item:ITEM-00002 is not on PO-0013" (the receipt is left in state `failed`); GR-00016 passes and books stock. Two spellings of the same item are two different things to the receipt checker.
Evidence: `hubproto record PO-0013` (lines, fulfilled-by GR-00015 and GR-00016); run pages 71b17f4d84, 07e196d995.

### F-inventory-13: [INV] Every decision is listed twice on the record
Severity: minor
Area: records
Steps:
1. `hubproto record PR-00001`, `PR-00003`, `GR-00002`, `GR-00003`, `PR-00010`.
Expected: one signature line per decision.
Actual: each shows the same decision and signature digest twice, e.g. PR-00003 `purchasing-assistant (cog) approve: approved within fixed criteria sig=0a7f5fc151367dd2eb83cd51` on two consecutive lines.
Evidence: CLI output of the records above.

### F-inventory-14: [INV] A zero-amount request is routed with the wrong Why sentence
Severity: question
Area: workflow
Steps:
1. PR-00001 (amount 0.0, no supplier) transitions "routed by Approvals in purchase-request go to the buyer."
Expected: either the routing sentence "Approvals in purchase-request under 500 USD go to the purchasing-assistant cog" is cited (0 is under 500) with the reason the cog was skipped (criteria not met), or a request with no cost is not treated as having an amount at all and goes to the buyer with a problem "no unit cost".
Actual: the outcome (buyer) is sensible, but the Track cites the catch-all sentence, so the Why link does not explain why the cog did not get it.
Evidence: `hubproto record PR-00001` transitions.

## Goods receipt

### F-inventory-15: [INV] A partial receipt marks the whole PO received; the outstanding quantity is lost
Severity: major
Area: workflow
Steps:
1. PO-0002: 2 x ITEM-00002.
2. As alice, start goods-receipt JSON `{"po":"rec:purchase-order:PO-0002","lines":[{"item":"rec:item:ITEM-00002","quantity":1}]}` -> run caf7c0f868, GR-00002. Confirm as dave.
3. `hubproto record PO-0002`; `hubproto record ITEM-00002`.
Expected: stock +1 (correct), PO-0002 "partially received" with 1 outstanding, the next delivery checked against the remaining 1, and the bill matched against received quantity.
Actual: stock 1.0 (correct), but PO-0002 `received True`, state `received`, transition "issued -> received by stock-booker". Nothing records that 1 is still due. A second receipt GR-00007 for the remaining 1 (run ea76f60503) books with no check and the PO transitions "received -> received".
Evidence: `hubproto record PO-0002` (two `fulfilled-by` receipts, two transitions); `hubproto record GR-00002`.

### F-inventory-16: [INV] Over-receipt is a warning only and is booked in full; the buyer's confirm card does not show it
Severity: major
Area: workflow
Steps:
1. PO-0001: 1 x ITEM-00001.
2. As alice, goods-receipt JSON `{"po":"rec:purchase-order:PO-0001","lines":[{"item":"rec:item:ITEM-00001","quantity":5}]}` -> run c2ef6c1787, GR-00003. Run page shows "over-receipt received 5 of rec:item:ITEM-00001, ordered 1" (warning).
3. `http://127.0.0.1:8080/inbox?as=dave`: the confirm card for GR-00003 has only Approve / Reject; the warning is not on the card.
4. Confirm as dave; `hubproto record ITEM-00001`.
Expected: an over-delivery tolerance in the purchasing policy (for example "Receipts may exceed the order by 5 percent"), the Guard blocking beyond it or forcing the buyer to accept the overage explicitly, and the problem shown where the buyer decides.
Actual: ITEM-00001 stock 5.0 against an order of 1; event `stock.received quantity 5`.
Evidence: run c2ef6c1787 Gate `gate-pending` with problem `over-receipt`; ITEM-00001 Activity row 1124.

### F-inventory-17: [INV] A negative receipt quantity is accepted and reduces stock
Severity: major
Area: workflow
Steps:
1. As alice, goods-receipt JSON `{"po":"rec:purchase-order:PO-0001","lines":[{"item":"rec:item:ITEM-00001","quantity":-2}]}` -> run fd81cb3a71, GR-00009. receipt-checker: no problem. Confirm as dave.
2. `hubproto record ITEM-00001`: stock 5.0 -> 3.0; Activity row 1279 `stock.received {"quantity": -2, ...}`.
Expected: quantity must be positive; a return to supplier is its own document (see F-inventory-21).
Actual: a "receipt" silently removes stock, recorded as `stock.received`. Corroborating, another panelist got PO-0018 issued for "-5 x RC0603FR-0710KL (-0.25 USD)", so negative quantities pass the request side too.
Evidence: GR-00009; ITEM-00001 Activity rows 1278-1279; alice DM "PO-0018 issued for PR-00030 ... Ordered -5 x".

### F-inventory-18: [INV] A receipt against an already-received PO passes without a warning
Severity: major
Area: workflow
Steps:
1. PO-0002 is `received` after GR-00002 (see F-inventory-15) and GR-00007.
2. PO-0001 is `received` after GR-00003; then GR-00009 (-2), GR-00013 (+1) and GR-00012 were all accepted against it.
Expected: receipt-checker problem "PO-0001 is already received" (error, or warning requiring the buyer's explicit override), and ordered-minus-received arithmetic.
Actual: receipt-checker only compares each line to the ordered quantity in isolation; the PO's state is ignored. PO-0001 has been "received" 1 + 5 - 2 + 1 = 5 net against 1 ordered, in four accepted receipts.
Evidence: `hubproto record PO-0001` links `fulfilled-by GR-00003, GR-00004, GR-00009, GR-00012, GR-00013`; ITEM-00001 stock 4.0.

### F-inventory-19: [INV] No segregation of duties on receipts: the buyer can record and confirm his own receipt
Severity: major
Area: policy
Steps:
1. As dave (buyer, also staff), start goods-receipt "[INV] dave self-receipt PO-0001", JSON `{"po":"rec:purchase-order:PO-0001","lines":[{"item":"rec:item:ITEM-00001","quantity":1}]}` -> run 6af54cfff7, GR-00013.
2. As dave, approve run 6af54cfff7.
Expected: the purchasing policy has "The requester and the approver of a purchase-request must be different people" but nothing for receipts; expected "The receiver and the confirmer of a receipt must be different people" and the runner refusing the self-confirmation (as it does for requirements: "not an assignee").
Actual: grant decided by dave, stock-booker passed, ITEM-00001 stock 3.0 -> 4.0. One person can create stock.
Evidence: run 6af54cfff7 page ("Grant 558a76ef decided by dave"); ITEM-00001 Activity rows 1495-1496.

### F-inventory-20: [INV] A receipt that fails the order check dies as a `failed` record: no message to the clerk, no way to correct it
Severity: major
Area: workflow
Steps:
1. GR-00001 (form `po_ref` used instead of `po`, run d76986da1b): "no purchase order None".
2. GR-00004 (wrong item, run 5feefff018): "line rec:item:ITEM-00002 is not on PO-0001".
3. GR-00015 (locator vs bare number, run 71b17f4d84).
4. `hubproto inbox --as alice`: no message about any of them. `hubproto record GR-00004`: state `failed`, still linked `fulfils PO-0001`. The failed runs appear only on `/runs?as=alice`.
Expected: the design-control SOP has "Rejections return to Submit the design"; goods-receipt has no return line, so a failed check should at least (a) be a Guard at submission with a readable message so no record is created, or (b) return the record to "Record the receipt" for correction and notify the owner. The link to the PO should not be made before the check passes.
Actual: three dead receipt records in Inventory/Receipts, one of them linked to the PO as if it fulfilled it.
Evidence: `hubproto records --type receipt` (GR-00001, GR-00004, GR-00015 `failed`); `hubproto record GR-00004` links; alice inbox.

### F-inventory-21: [INV] Rejected goods go nowhere: no quarantine, no nonconformance, no return (RMA)
Severity: major
Area: workflow
Steps:
1. As alice, goods-receipt "[INV] receipt to be rejected PO-0001", 1 x ITEM-00001 -> run 89f5cc4e11, GR-00012.
2. As dave, reject with meaning "damaged on arrival, hold for inspection".
3. `hubproto record GR-00012`: state `rejected`. `hubproto record ITEM-00001`: unchanged. `hubproto inbox --as alice`: nothing.
Expected: on reject, the goods are physically in the building; expected a quarantine location/status for the received quantity, an NCR opened automatically (the `ncr-opener` cog exists, and `wi-disposition-stock` describes dispositions rework / scrap / use-as-is / return), and on "return" a return-to-supplier (RMA) document that reverses the stock and creates a debit note for payables. None of this exists; the NCR disposition in `wi-disposition-stock` does not move stock either.
Actual: a `rejected` receipt record and nothing else. There is no inspection step between "Record the receipt" and "Book the stock"; confirmed goods go straight to available stock.
Evidence: GR-00012 decisions and transitions; `grep -ril quarantine frames profiles config` finds only the quality-manual prose, no process or field.

### F-inventory-22: [INV] Missing: lots, serial numbers, expiry; locations and bins
Severity: major
Area: records
Steps:
1. `config/record-types.json` `receipt` fields: `po, lines, received_by, received, location (text, default "MAIN"), notes`; `item` has a single `stock` number.
2. `hubproto record GR-00002`: `location MAIN`, lines `[{"item": ..., "quantity": 1}]`.
Expected: per receipt line a lot/batch number (and serials for serialized items), supplier lot, expiry/shelf life; a location model (warehouse, location, bin, quarantine, scrap) with stock held per item per location per lot; the item page showing that breakdown. ISO 9001 clause 8.5.2 (identification and traceability) and the design-control chain (which part revision used which lot) need it.
Actual: one free-text location on the receipt header that nothing reads (stock is booked onto the item total regardless of the location typed), no lot or serial anywhere.
Evidence: record-types.json; GR-00002 fields; ITEM-00002 fields.

### F-inventory-23: [INV] Missing: the confirm-receipt step has no inputs (quantity correction, packing slip, inspection result)
Severity: major
Area: shell
Steps:
1. `http://127.0.0.1:8080/inbox?as=dave`, cards "Confirm the receipt: GR-00002 / GR-00003 / GR-00007".
Expected: the buyer (or an inspector) to see the ordered vs received quantities per line, correct the counted quantity, enter the supplier's delivery note / packing slip number, record the inspection result (accept / quarantine / reject per line) and the putaway location.
Actual: the form has `meaning`, Approve, Reject and nothing else; the lines are not shown on the card.
Evidence: inbox decide forms for runs caf7c0f868, c2ef6c1787, ea76f60503: field names `[]`.

## Counts, transfers, adjustments, valuation

### F-inventory-24: [INV] Missing: stock counts, adjustments and transfers
Severity: major
Area: workflow
Steps:
1. `http://127.0.0.1:8080/?as=alice` and `?as=dave`: the only process that touches Inventory is `goods-receipt`.
2. `hubproto --help`: no command changes stock.
3. `grep -ril "stock count\|cycle count\|adjustment\|transfer\|reorder" frames profiles config`: no matches in any Frame or record type.
Expected: a stock-count process (count sheet by location, counted vs system quantity, variance approval by the controller above a threshold, posting of the variance), a stock-adjustment process with reason codes (damage, scrap, found) and approval, and a transfer between locations/bins. The policy grammar supports these as `## Process:` sections with Guards and Gates; none is written.
Actual: once stock is booked, nothing can correct it except another "receipt" (including the negative one in F-inventory-17).
Evidence: Start page process list; frames/purchasing/purchasing-policy.frame.md has only `purchase-request` and `goods-receipt`.

### F-inventory-25: [INV] Missing: valuation and posting of stock to the ledger
Severity: major
Area: workflow
Steps:
1. Confirm GR-00002 (1 x ITEM-00002 at PO cost 0.02) and GR-00003 as dave.
2. `hubproto ledger`: every journal is a bill (`5000 Purchases` / `2000 Accounts payable`); no entry for any receipt. `hubproto record ITEM-00002`: no `unit_cost`, no value.
3. frames/finance/finance-policy.frame.md: `grep -i "inventor\|stock\|GRNI"` finds nothing.
Expected: receipt posts Dr Inventory / Cr Goods received not invoiced (GRNI) at PO cost; the matched bill clears GRNI against AP; the item carries a cost (average or FIFO layers) updated on receipt; a stock valuation report (quantity x cost by item/location). The plan's "one record system for all modules" and "balanced journal" claims cover payables only; inventory is not an asset in this ledger.
Actual: stock has quantities but no value; purchases are expensed at bill time, so the balance sheet never shows inventory and the 0.00 POs of F-inventory-6 would value it at zero anyway.
Evidence: `hubproto ledger` output (JE-00001 to JE-00015, all bills); ITEM fields.

## Links

### F-inventory-26: [INV] Where-used is incomplete: an item does not link to the design or part that created it, and receipts are two hops away
Severity: minor
Area: records
Steps:
1. `hubproto record ITEM-00001` after release of DSN-2026-0001 (which created it): `links: none`. Later links are only `concerned-by PO-...` and `realizes PN-00003`, `realizes PN-00005` (other panelists' parts whose BOMs resolved against the now-existing item).
2. `hubproto record PN-00001`: links `defined-by DSN-2026-0001` only; no item links.
3. DSN-2026-0001 Links tab: no item links; `items_created` is a plain field.
4. From ITEM-00002 the receipts GR-00002/GR-00007 are reachable only via PO-0002 -> `fulfilled-by`.
Expected: at release, `PN-00001 realized-by ITEM-00001/ITEM-00002` (the part-releaser links only the already-resolved items, the shortage-requester creates the new items afterwards without linking), so that where-used on an item lists every design/part that uses it; and a receipts list (or `stock.received` events with links) on the item.
Actual: where-used works for designs released after the item existed, not for the one that created it; the first design of a new part is exactly the one you want to find.
Evidence: CLI outputs above; design page Links tab (defines PN-00001, concerned-by PR-00001/PR-00002/PO-0001/PO-0005, no items).
