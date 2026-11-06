# [ACCT] Accountant / controller's deputy: accounting controls and the books

Panelist: accountant (controller's deputy). I drove purchase-to-pay as alice
(finance clerk) from the shell Start form (`POST /start`, process
`purchase-to-pay`, fields title/amount/currency/date/po_ref/supplier), decided
as dave (payables reviewer, buyer) and bob (controller) from `/inbox`, and read
the books with the CLI (`hubproto ledger`, `bills`, `records --type journal`,
`record JE-nnnnn`, `track <run>`). Release in effect for the whole session:
`b7da4af94d3c571f` (finance-policy v1.2.0, purchasing-policy v1.0.0). Seeded
POs: PO-1001 (1500 USD, received), PO-1002 (12000 USD, received), PO-1003
(4000 USD, not received). Other panelists were filing at the same time, so
record numbers in my evidence are not contiguous; every number quoted below is
one I created or read myself.

Where I say "missing" I compared against the plan's section 9.3 Accounting
("Chart of accounts, dimensions, fiscal years and periods with open/closed
state; journals (immutable); sub-ledgers for payables, receivables, bank,
fixed assets, inventory; suppliers, customers, payment terms, tax codes;
trial balance, aged payables; period-close; manual-journal; Guards: ... account
allows posting; tax code valid; reversal references the original") and Phase 4
("a month closes with every journal traceable ...; the trial balance reconciles
to the sub-ledgers").

## 1. Purchase-to-pay

### F-acct-1: Duplicate bills against the same PO are not detected; the cog approves and posts every one
Severity: blocker
Area: workflow
Steps:
1. As alice, `/?as=alice`, Start purchase-to-pay: title `Acme bill 1`, amount 1500, currency USD, date 2026-11-06, po_ref PO-1001, supplier acme-metals. Run 9e507b6763 -> BILL-0001, JE-00001.
2. Repeat with identical fields (title `B2dup`). Run 0aa4274300 -> BILL-0002, JE-00002.
3. Repeat with amounts 1529 and 1530 (within 2 percent). Runs b18a56fdcc, f28cfe88dd -> BILL-0003/JE-00003, BILL-0004/JE-00004.
4. Later in the session the same PO took BILL-0018, BILL-0024, BILL-0025, BILL-0029 (filed by the ap-reviewer cog from email) and BILL-0037, all 1500 USD, all posted.
5. Same with a PO the hub issued itself: PO-0003 (100 USD, received via GR-00005): bills of 100, 101 and 100 again (runs 51b0354de3, bfa1c76883, 84ebc949ae) -> BILL-0030/0031/0032 all `posted`.
Expected: the second bill against a PO that is already fully billed fails the match (billed-to-date + bill > PO amount + tolerance), or at least loses the cog and goes to a person with a "possible duplicate" finding.
Actual: `hubproto records --type bill --q PO-1001` lists 10 posted bills against the 1,500 USD PO; the ledger carries 13,559 USD of AP against it (15,059 posted less the -1,500 of JE-00007). PO-0003 (100 USD) carries 301 USD. The cog's checks on BILL-0002 (`track 0aa4274300` entry 11) are "match holds, supplier approved, amount under 2000" and nothing about prior bills. The three-way-matcher compares the bill amount to the PO amount only (`match.variance_percent 0.0` on every duplicate).
Evidence: `hubproto ledger` JE-00001..JE-00005, JE-00012, JE-00013, JE-00021, JE-00022; `hubproto record BILL-0002`; `track 0aa4274300`.
Suggested fix: keep billed-to-date on the PO aggregate (`po.billed` event), match against the remaining open amount, and treat "same supplier + same amount + same PO within 30 days" or the same supplier invoice number (F-acct-16) as an error-severity duplicate problem.

### F-acct-2: A negative bill is approved and posted as a journal with negative debits and credits
Severity: blocker
Area: workflow
Steps:
1. As alice, Start purchase-to-pay: title `B11-negative`, amount -1500, USD, 2026-11-06, PO-1001, acme-metals. Run 40c98a3d15 -> BILL-0013, risk high, "variance 200.00% exceeds tolerance", routed to dave.
2. As dave, `/inbox?as=dave`, Approve with meaning `[ACCT] negative`.
Expected: a negative amount is refused at extraction (or treated as a credit note with its own process); the balanced-journal Guard rejects lines with negative debits.
Actual: JE-00007 posted: `5000 Purchases debit -1500.0`, `2000 Accounts payable credit -1500.0`. The Guard only checks `sum(debit) == sum(credit)`, so -1500 == -1500 passes. The ledger now contains a journal no accountant can read as a credit note, and it silently nets the AP balance.
Evidence: `hubproto record JE-00007`; `hubproto record BILL-0013` (state posted).

### F-acct-3: No reversal, correction or manual-journal path for a posted journal
Severity: blocker
Area: workflow
Steps:
1. Take the wrong postings above (JE-00002 duplicate, JE-00007 negative, JE-00006 posted without a receipt).
2. Look for any way to correct them: `hubproto --help` (no `reverse`, `journal`, `void`); `/record/rec:journal:JE-00001?as=bob` ("Why: may bob approve journal in Finance/Payables? denied ... no rule in the release lets any of [controller, ...] approve journal"; "Workflow: No workflow in the release covers this record type"; only actions offered are Render snapshot and Put on hold, and holds "need an approve permission here", which nobody has).
3. There is no manual-journal process in any Frame and no `reverses` relation in the link vocabulary.
Expected: the plan's "Corrections are reversals, never edits": a manual-journal / reversal Op with a human Gate, SoD, and a Guard "reversal references the original"; the original journal gets a `reversed-by` link.
Actual: missing. Once a journal is posted the books can only be changed by editing state files. The wrong journals from this session (duplicates, the negative JE-00007, JE-00010 posted while on hold, JE-00006 without a receipt) are permanent.

### F-acct-4: Bills dated in a closed, future-unopened or malformed period are approved and granted, then die as "failed" runs with no way back to the clerk
Severity: major
Area: workflow
Steps:
1. As alice, Start purchase-to-pay: `B8-closedperiod`, 1500 USD, date 2025-12-15, PO-1001, acme-metals. Run bd4f286e15 -> BILL-0010.
2. Same with date 2027-03-01 (`B9-future`, run dab69900c4 -> BILL-0011) and date `06/11/2026` (`B19-baddate`, run 07eeb8116b -> BILL-0021).
Expected: the period is checked before the Gate (a matcher/scorer problem "period 2025-12 is closed", routed to a person), or the extractor rejects a non-ISO date; a failed posting returns the bill to the clerk to re-date, keeping the approval evidence; the clerk is told.
Actual: in all three the ap-reviewer cog approved "within fixed criteria" (the criteria do not mention the period), a grant was issued (grant 93cc1cf8, 27753bcd, 18d0eceb), then ledger-poster refused (`period-open: period 2025-12 is not open`, `period 2027-03 is not open`, `period 06/11/2 is not open`) and the run ended `run.failed`. The bills sit in state `failed` with an approval decision and a grant attached and no retry, resubmit or edit action on `/record/rec:bill:BILL-0010?as=alice`. alice's inbox (`/inbox?as=alice`) has no message about any of them; she only finds out by scanning `/runs` (dozens of failed runs listed there at the time).
Evidence: `track bd4f286e15` entries 10-15; `track dab69900c4`; `track 07eeb8116b`; `hubproto bills` rows BILL-0010/0011/0021 `state=failed`.

### F-acct-5: Supplier mismatch, currency mismatch, missing/unknown PO and bad amounts end as "failed" runs instead of a rejection a person sees
Severity: major
Area: workflow
Steps:
1. As alice file: `B6-newco` 1500 USD PO-1001 supplier newco-tools (run 9a1b75ad90, BILL-0008); `B7-EUR` 1500 EUR PO-1001 (c2e9324815, BILL-0009); `B10-nopo` 900 USD no po_ref (5e2dea08fe, BILL-0012); `B13-nosuchpo` PO-9999 (72c5474827, BILL-0015); `B17-nodate` (4955448ee1, BILL-0019); `B18-lowercase-usd` currency `usd` (c502406332, BILL-0020); amount `1500 USD` (8a6e61647e, BILL-0038).
Expected: the finance policy says "Everything else is approved by a person": these should reach dave with the Guard findings, or be rejected to the clerk with a message. A non-PO bill (utilities, services) needs a path at all.
Actual: every one of them is `run.failed` at `match-the-bill-to-purchase-orders` ("step result has error-severity problems") or at `extract-the-bill`; state `failed`; nobody is assigned; no inbox message to alice or dave. The bill against a different supplier's PO (BILL-0008) is correctly not approvable, but it is also never reviewed by anyone and the clerk sees only a failed run. Lower-case `usd` is a "currency differs" failure (no normalisation). There is no non-PO bill process in any Frame.
Evidence: `track 9a1b75ad90` entry 7-8 (`po-supplier`, `supplier`); `track c2e9324815` (`po-currency`); `track 5e2dea08fe` (`no purchase order None`); `/run/9a1b75ad90?as=alice` shows the failure only in the step table.

### F-acct-6: Out-of-tolerance and receipt-missing bills can be approved by the reviewer with no override reason; an empty signature meaning is accepted
Severity: major
Area: workflow
Steps:
1. As alice: `B5-PO1003-notreceived` 4000 USD PO-1003 (not received). Run 66c0ca5e30 -> BILL-0007, risk high, Guard "receipt: goods have not been received" (warning). Routed to dave.
2. As dave, Approve with meaning `[ACCT] approve without receipt` -> JE-00006 posted, 4000 USD to AP, PO-1003 still not received.
3. As alice: `B15-PO1002-10000` 10000 USD against PO-1002 (12000) -> BILL-0017, variance 16.67%, risk high, routed to dave. As dave, Approve with an empty meaning field (`POST /decide` with `meaning=`) -> JE-00011 posted.
Expected: a person may override a match failure, but the policy requires a stated reason and the Track should carry it; an empty electronic-signature meaning is refused; a receipt-missing override should at least be labelled as an accrual-without-receipt and block payment.
Actual: price-variance and receipt problems are warnings only; the person's approval is unconditional; `meaning` may be blank (`track 11c2ebf8ea` gate.decided `"meaning": ""`). The 10,000 bill is 2,000 under the PO and the whole 10,000 went to 5000 Purchases; there is no price-variance account or PO-remaining amount.
Evidence: `hubproto ledger` JE-00006, JE-00011; `track 66c0ca5e30`; `track 11c2ebf8ea`.

### F-acct-7: Three-way match is amount-only; a partially received PO counts as fully received
Severity: major
Area: workflow
Steps:
1. PO-0002 (another panelist's): lines quantity 2 x ITEM-00002, amount 0.04 USD. GR-00002 received `quantity 1`; stock-booker set `received: True` on the PO and transitioned it `issued -> received`.
2. As alice: Start purchase-to-pay `[ACCT] PO-0002 full bill after partial receipt`, amount 0.04 USD, PO-0002, acme-metals. Run 8ddbae0801 -> BILL-0039.
Expected: a bill for the full PO amount when half the goods are received fails the quantity/receipt match and goes to a person.
Actual: match `received: true, variance 0.0, within_tolerance: true`; the ap-reviewer cog approved and JE posted. The bill record has no lines or quantity, the PO's `received` flag is a boolean, and the receipt quantities are never compared with the bill.
Evidence: `hubproto record PO-0002` (lines quantity 2, received True); `hubproto record GR-00002` (quantity 1); `hubproto record BILL-0039` (state posted).

### F-acct-8: A buyer can approve a purchase request naming a supplier that is not approved; a PO is issued to newco-tools
Severity: major
Area: workflow
Steps:
1. As alice, Start purchase-request `[ACCT] PR-E-100-newco`, other fields `{"item":"rec:item:ITEM-00002","quantity":5,"unit_cost":20,"supplier":"newco-tools"}`. Run 3b76541dc2 -> PR-00010, supplier_approved False, "supplier NewCo Tools is registered but not approved", cog skipped, routed to dave.
2. As dave, Approve.
Expected: purchasing-policy prose says "The supplier must be on the approved list". The cog is blocked, but the buyer should be too (or the controller must co-sign with a reason).
Actual: PO-0009 issued to newco-tools, 100 USD, `approved_by dave`. The approved-list rule is only a cog criterion, not a Guard. From the AP side this means the "supplier is approved" check on the bill later passes or fails on the seeded list only, and a bill against PO-0009 would fail the supplier check (F-acct-5) with a PO that purchasing considers valid.
Evidence: `hubproto record PO-0009`; `track 3b76541dc2`.

### F-acct-9: Posting into next month is allowed; periods are seeded, never managed
Severity: major
Area: workflow
Steps:
1. As alice: `[ACCT] B20-dec` 1500 USD dated 2026-12-15 PO-1001 -> run abcb200597, BILL-0024 -> JE-00012 dated 2026-12-15, cog-approved and posted on 2026-11-06.
2. Look for a way to open or close a period: none in `hubproto --help`, none in the shell, none in any Frame. `kernel.seed()` opens last month, this month and next month and closes last December.
Expected: a period-close Op (plan 9.3) with open/closed state driven by the finance Frame (close calendar); posting into a future period needs a controller decision; the finance clerk cannot post into a month that has not started.
Actual: future-dated bills post; there is no close, no lock, no reopen with authority, no close checklist. Period state cannot be read anywhere by a user.
Evidence: `hubproto ledger` JE-00012; `hubproto/kernel.py` seed.

### F-acct-10: Bills carry no supplier invoice number, due date, payment terms or lines, and the invoice image is not attached
Severity: major
Area: records
Steps:
1. `hubproto record BILL-0001`: fields are supplier, amount, currency, date, po_ref, description, citations, match, risk, journal, title, filed_by. `documents: none`.
2. Email path: `hubproto msg --from alice --to ap-reviewer --channel email --attach acct_inv.json --body "please file this invoice"` -> ap-reviewer files BILL-0029; `record BILL-0029` also has `documents: none`.
Expected: supplier invoice number (the natural duplicate key), invoice and due dates, payment terms, tax, line items, and the source document attached as evidence class `external`.
Actual: missing. Without an invoice number the duplicate check of F-acct-1 cannot even be specified; without a due date there is no aged payables; the emailed invoice file is not kept with the bill.

### F-acct-11: There is no payment step: the process ends at "posted" and AP never clears
Severity: major
Area: workflow
Steps: `frames/finance/finance-policy.frame.md` process purchase-to-pay "Closes as posted"; no payment-run, bank or remittance process in any Frame; `grep -ri payment hubproto/ frames/finance/` finds nothing.
Expected: the panel scenario says "then receipt and payment"; a payment Op (proposal, approval under the threshold table, bank journal Dr 2000 AP / Cr bank, remittance to the supplier, bill state `paid`).
Actual: missing. Every bill ends in `posted`; 2000 Accounts payable only ever grows.

## 2. Controls

### F-acct-12: A bill is approved and posted while the record is on hold
Severity: major
Area: workflow
Steps:
1. As alice: `B14-PO1002-11999` 11999 USD PO-1002 -> run cb5c7cc2db, BILL-0016, routed to bob.
2. As dave (approve permission): `POST /hold` record `rec:bill:BILL-0016` on=1 reason `[ACCT] hold test` -> 303, `record BILL-0016` shows `hold=True`.
3. As bob, `/inbox?as=bob`, Approve BILL-0016.
Expected: a held record cannot be decided or posted; the inbox card shows the hold and its reason.
Actual: approval accepted, JE-00010 posted for 11,999 USD; `hubproto record BILL-0016` still shows `hold=True` and `assignees=bob` after posting. The runner never looks at the hold.
Evidence: `hubproto record BILL-0016`; `hubproto ledger` JE-00010.

### F-acct-13: Bill approvals have no due date, never escalate, and nobody can step in for the assignee
Severity: major
Area: workflow
Steps:
1. `track 4a7f168323` entry 10 `gate.pending`: `"assignees": ["bob"], "due": null`. Same for every bill (`track 533d0d9d7c`, `track 66c0ca5e30`). Purchase requests, by contrast, carry `due 2026-11-09` from "Approve the purchase in purchase-request is due within 3 days."
2. As bob (controller, holds "approve bills in Finance/Payables"), try to approve BILL-0005 assigned to dave: `POST /decide` -> "Not done. bob is not an assignee of this decision (assigned: dave)".
Expected: the finance Frame carries an SLA sentence for "Approve the bill" and an "Overdue items in purchase-to-pay go to the controller" sentence; the controller can take over an aging bill; `hubproto tick` escalates.
Actual: no SLA, `due: null`, so `tick` can never escalate a bill; a bill assigned to an absent reviewer waits forever; the controller is locked out. (Policy gap in `frames/finance/finance-policy.frame.md`, not code; the Guard that warns about a checkpoint with no SLA would help.)

### F-acct-14: Segregation of duties works as specified (negative tests passed); but the journal's preparer/approver check is the only SoD and nobody but the run owner is "preparer"
Severity: minor
Area: workflow
Steps:
1. alice approves her own BILL-0005 (`POST /decide as=alice run=533d0d9d7c`) -> "alice is not an assignee" (also `hubproto decide 11c2ebf8ea --as alice` -> same error). ap-reviewer cog via shell and CLI -> refused. carol -> refused. dave on the 12,000 bill routed to bob -> refused.
2. Cog-filed bill BILL-0029: `track a70d2e7702` entry 10 `gate.skipped` "segregation of duties: the cog prepared this item" -> dave. Good.
3. dave, bob and carol cannot start purchase-to-pay ("no rule in the release lets any of ['buyer', 'payables-reviewer', 'staff'] create bill in Finance/Payables"). Good.
Expected / Actual gap: the kernel SoD (`prepared_by == approved_by`) is evaluated on the run owner, and the assignee check is the real control. There is no rule that the person who approved the PO (dave approved PR-00006 -> PO-0008) may not also approve the bill against it (dave is the payables reviewer for bills under 10,000 that fail the cog): the same person approves the commitment, confirms the receipt (`At Confirm the receipt in goods-receipt go to the buyer`) and approves the invoice. On PO-0008 dave did two of the three (approved PR-00006, confirmed GR-00010) and would approve any out-of-tolerance bill against it. Suggest a Frame sentence "The approver of a purchase request and the approver of its bill must be different people" and a receiver/buyer split.

### F-acct-15: Approval-threshold sentences are inconsistent at the boundaries and the compiler did not flag it
Severity: minor
Area: policy
Steps:
1. As alice, bill of exactly 2000 USD against PO-0008 (2000, received): run 52a779a7de, BILL-0033. `track`: entry 10 `gate.cog_review ... "reasons": ["amount 2000.0 is not under 2000.0 USD"]`, then `gate.pending` -> dave.
2. As alice, bill of exactly 10000 USD (BILL-0017): routed to dave ("over 10,000" is false) but the risk-scorer reason says "amount at or above 10,000" and marks it high.
Expected: "Approvals ... at or under 2,000 USD go to the ap-reviewer cog" and "may approve ... under 2,000 USD" agree, or the compiler's consistency Guard warns that a routed amount can never be approved by the route's cog; the 10,000 boundary uses one wording everywhere.
Actual: works by fall-through, but the policy is contradictory at 2,000.00 and the risk tier and routing disagree at 10,000.00.

### F-acct-16: Grant required to post: holds (positive), but a grant survives a failed posting and the refusal is not visible on the bill
Severity: minor
Area: workflow
Steps: see F-acct-4. `track bd4f286e15` entry 12 `grant.issued` then 14 `step.result error guard-refused period-open`.
Expected: the Guard refusal appears on the bill record's Activity/Decisions and the grant is marked consumed/void.
Actual: the bill record shows the approval decision (twice, see F-acct-18) and state `failed`; the reason is only in the run Track.

### F-acct-17: Rounding: a bill amount with sub-cent precision is stored as filed and the journal differs from it
Severity: minor
Area: records
Steps: as alice, `B16-cents` amount `1500.004` -> BILL-0018 `amount 1500.004`; JE-00005 lines 1500.00.
Expected: money is normalised to the currency's minor unit at extraction (or refused); the bill and its journal agree to the cent.
Actual: `hubproto bills` shows `1500.004 USD`; the journal shows 1500.00; the comma form `1,500.00` is accepted (good) but `1500 USD` as an amount fails the run (F-acct-5).

### F-acct-18: Audit trail of a journal: approval evidence lives on the bill (duplicated), the journal has no transitions, no link to PO or receipt, and the seeded POs have no record
Severity: minor
Area: records
Steps:
1. `hubproto record JE-00001`: `prepared_by alice`, `approved_by ap-reviewer`, `grant 20868423`, `links: settles BILL-0001`, `decisions: none`, `transitions: none` (created directly in state posted). `record JE-00008` (controller approval): `approved_by bob`; the signature digest, meaning and release digest are only in `track 4a7f168323` entries 11-12, not on the journal page.
2. `hubproto record BILL-0001` decisions: the ap-reviewer decision is listed twice with the same signature; BILL-0016 likewise lists bob's decision twice.
3. `hubproto record PO-1001` -> "no record 'PO-1001'": the seeded POs are aggregates only, so a bill's `po_ref` cannot be followed and the journal has no `concerns` link to the PO or the receipt.
4. `/snapshot` of JE-00001 as alice -> DOC-0118, labels `retention: quality` although the journal is `retention: financial-reporting`.
5. After posting, BILL-0016 still shows `assignees=bob`.
Expected: the journal page shows who prepared, who approved (with meaning and signature digest), the release digest, the grant and the delegating sentence for a cog decision, links to the bill, PO and receipt; one decision entry; evidence snapshots inherit the record's retention class.
Actual: the Track has it all (release digest, manifest digest, cog versions, Guard problems, delegating sentence, signature digest: good), but the journal record itself is thin and the duplicated decisions make the record's own history unreliable.

### F-acct-19: A rejected bill is terminal and the clerk is not told
Severity: minor
Area: workflow
Steps: as alice `[ACCT] B22-reject` 1531 USD PO-1001 -> BILL-0027 -> dave; as dave Reject with meaning `[ACCT] price variance not agreed`. `record BILL-0027` state `rejected`; `/inbox?as=alice` has no message.
Expected: "Rejections return to Extract the bill" (the grammar supports it) so the clerk can correct the amount, or at least a DM to the preparer with the reason.
Actual: the bill is dead; the clerk must file a new one and never learns why.

## 3. The books

### F-acct-20: Chart of accounts is two hard-coded strings in the poster
Severity: major
Area: architecture
Steps: `hubproto ledger`: every journal is `5000 Purchases` / `2000 Accounts payable`. The only occurrence of an account in the codebase is `hubproto/cogs.py` LedgerPoster (`"5000 Purchases"`, `"2000 Accounts payable"`). No `account` record type in `config/record-types.json`, no chart-of-accounts Frame.
Expected (plan 9.3): chart of accounts as records, dimensions (cost centre, project, department from the org model), Guard "account allows posting", account chosen from the PO line/item (inventory vs expense), separate price-variance and GR/IR accounts.
Actual: missing. Inventory purchases (PO-0003 resistors) and services alike are expensed to 5000; EUR amounts land in the same account (F-acct-22).

### F-acct-21: No AP sub-ledger, supplier balances, aged payables or trial balance; the ledger listing has no totals, filters or shell page
Severity: major
Area: CLI
Steps: `hubproto ledger --help` has no options; output is every journal with its two lines; no per-supplier view (`hubproto bills` lists bills, not balances); no `trial-balance`, no `aged`, no `/ledger` page in the shell (the Files view is the only way, F-acct-24). To know acme-metals' balance I had to `awk` the ledger output (62,859 USD AP credit at one point, mixing EUR, see F-acct-22).
Expected (plan 9.3, Phase 4 exit): sub-ledgers for payables, trial balance that reconciles to them, aged payables, all as projections from the event store.
Actual: missing.

### F-acct-22: Multi-currency: EUR journals sit in the same ledger with no currency shown and no functional-currency amount
Severity: major
Area: records
Steps:
1. As alice, purchase-request `[ACCT] PR-F-EUR-100` with `{"item":"rec:item:ITEM-00002","quantity":5,"unit_cost":20,"supplier":"acme-metals","currency":"EUR"}`; dave approves -> PO-0010 EUR 100; goods-receipt as alice (GR-00011), dave confirms (run 68321d304b).
2. As alice, bill `[ACCT] PO-0010 bill EUR 100` amount 100 EUR PO-0010 -> run 9f5ed3f315, BILL-0034; no `gate.cog_review` at all (the USD routing sentences do not match a EUR bill) -> dave approves -> JE-00020.
Expected: a functional currency in the finance Frame, an FX rate on the bill/journal, journal lines in functional currency with the document currency and rate kept, and a routing/criteria rule that states what happens to non-USD bills.
Actual: `hubproto ledger` prints JE-00020 as `100.00 / 100.00` with no currency column; `record JE-00020` has `currency EUR` but the lines are bare numbers; any total across the ledger adds EUR to USD. The cog is silently bypassed for EUR bills without a policy sentence saying so. A EUR bill against a USD PO fails the run outright (F-acct-5, BILL-0009).

### F-acct-23: Tax is absent from bills and journals
Severity: major
Area: records
Steps: `config/record-types.json` bill and journal fields; `grep -ri "tax\|vat" hubproto/ config/` -> nothing. The invoice-extractor fields are supplier, amount, currency, date, po_ref, description.
Expected (plan 9.3): tax codes on suppliers, a tax amount/lines on the bill, Guard "tax code valid for the party's jurisdiction", an input-tax account.
Actual: missing; an invoice with VAT/sales tax can only be entered gross and the tax is expensed to 5000 Purchases.

## 4. Procurement accounting

### F-acct-24: No accrual on receipt, no inventory valuation, no standard cost; items have no cost at all
Severity: major
Area: workflow
Steps:
1. Goods receipt for PO-0003: as alice Start goods-receipt `{"po":"rec:purchase-order:PO-0003"}` (run 308862898e, GR-00005); dave confirms. `hubproto ledger` shows no journal from that run; `hubproto/procurement.py` StockBooker only adds quantity to `item.stock` and sets `po.received`.
2. `hubproto record ITEM-00002`: fields footprint, manufacturer, mpn, name, stock 126; no `unit_cost`, no `currency` (the type allows them; nothing sets them). The purchase request took `unit_cost` 20 from my JSON, not from the item.
3. PO-0008 was issued for 2000 USD (100 x 20) while PO-0003 was issued for the same item at 20 each and PO-0002 at 0.02 each: no standard cost, no purchase-price-variance.
Expected (plan Phase 4): a GR/IR accrual at receipt (Dr inventory / Cr goods-received-not-invoiced at PO price), the bill clearing GR/IR with a price-variance line, inventory valued at standard cost rolled up from released BOMs, a billed-to-date and received-to-date on the PO.
Actual: missing. The books know nothing until the invoice; stock is quantity only; the same item is ordered at three different prices in one day with no variance.

### F-acct-25: PO amount versus bill amount: no commitment tracking, no PO close, no remaining-to-bill
Severity: minor
Area: records
Steps: `hubproto record PO-0003` after three bills (F-acct-1): state `issued`, `received` True, no billed amount, no link to any bill (bills link only to their journal). `record PO-0002` shows transitions `issued -> received` then `received -> received` (a second receipt re-transitions).
Expected: the PO carries ordered, received and invoiced amounts and quantities, closes when fully billed, and lists its bills; the bill links `fulfils`/`concerns` the PO and receipt.
Actual: missing; see also F-acct-18 item 3 for seeded POs.

## 5. Usability for finance

### F-acct-26: Files view: Finance shows "No records", journals are under Payables, and the Payables listing has no amount, supplier, PO or currency columns
Severity: minor
Area: shell
Steps:
1. `/files?folder=Finance&as=alice`: "Records: No records in this folder." (the panel README and scenario step 5 say the journal is in `/files?folder=Finance`; the poster files it in the bill's folder Finance/Payables).
2. `/files?folder=Finance%2FPayables&as=alice`: bills and journals interleaved by number; columns Number, Type, State, Title/summary, Owner, Assignees, Due, Updated, Run. No amount, supplier, PO, currency, date; no state or type filter on the page; a journal row shows "Updated 14:48:39" because I rendered a snapshot, not because the books changed.
3. `/boards?type=bill&as=alice`: cards show title and assignee only, no amounts; columns `open / awaiting-approve-the-bill / posted / rejected / failed` (failed bills visible, good).
Expected: a payables view with amount, currency, supplier, PO, invoice date, due date, state; a journal view with date, memo, debit/credit totals; journals in a Finance/Journals folder (or General ledger) rather than mixed with bills.

### F-acct-27: The bill record page and inbox card show the match as raw JSON and do not link the PO, receipt or invoice
Severity: minor
Area: shell
Steps: `/record/rec:bill:BILL-0028?as=bob` (12,000 USD waiting for the controller): Details show `match {"po_found": true, "received": true, ...}` and `conditions {...}` as JSON; Links "No links"; Documents "No documents attached"; the PO amount, the receipt and the supplier's invoice are not shown, so the controller approves 12,000 USD from a title and a JSON blob. The inbox card (`/inbox?as=dave`) is the same summary plus Guard findings. The Workflow table lists `awaiting-approve-the-bill -> posted` twice (automatic and human).
Expected: PO amount and remaining, receipt date and quantities, variance in money and percent, supplier's approval status, invoice image, prior bills on this PO, all on the approval card.

### F-acct-28: `hubproto ledger` output: memo text is backslash-escaped, no currency column, no totals, no date range
Severity: minor
Area: CLI
Steps: `hubproto ledger` line for JE-00009: `Bill BILL-0023 from acme-metals: \[MGMT\]\  prepared=alice ...` (square brackets in the description are escaped); JE-00020 (EUR) prints as `100.00 0.00 / 0.00 100.00` indistinguishable from USD; no sum per account; `ledger --help` has no filters.
Expected: a clean columnar listing with date, number, memo, currency, per-account totals and `--from/--to/--account/--supplier`.

### F-acct-29: The clerk has no feedback loop: failures, rejections and cog decisions produce no message to the preparer
Severity: minor
Area: shell
Steps: after filing 40 bills as alice, `/inbox?as=alice` Messages contain only PO-issued updates (and those are for other people's requests, "PO-0018 issued for PR-00030" arrived because alice was the requester); nothing for BILL-0010 (failed), BILL-0027 (rejected) or BILL-0001 (posted). The Start form redirects to the run page, which is the only place the outcome is visible.
Expected: a DM to the preparer on failure (with the Guard problem), rejection (with the meaning) and posting (with the journal number).

### F-acct-30: Something restarted the hub container mid-session; the state survived, but there is no maintenance signal
Severity: question
Area: architecture
Steps: around 14:47 UTC `docker compose ps` listed only `cad-worker`; `hubproto` commands answered `service "hub" is not running`; `/` returned connection refused; it was back within a minute with the same release digest and all records (`hub-state` volume). My in-flight `POST /start` and `/decide` calls were lost without a response and had to be repeated.
Expected: not a finding against the product if another panelist rebuilt it, but a governed ledger should refuse writes cleanly during restart and report it in the Track; I could not tell whether any of my lost calls were half-applied (none appear to be).
