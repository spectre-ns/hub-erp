# InvenTree's inventory model: a brief for Hub ERP

**Sources.** I read InvenTree `master` at commit `35428bc` (`1.8.0 dev`; the
latest release is 1.7.0, 2026-11-03): its models, status codes, rulesets and
setting defaults, plus docs.inventree.org/en/latest. I did not run it.
*Unconfirmed* marks what I could not check.

Older guides are stale: parameters became generic (1.2.0),
`SupplierPart.primary` replaced `Part.default_supplier` (1.3.0), stock history
moved to the audit log (1.7.0), and the BOM `overage` field is gone.

## 1. Parts

- **Part** is the concept (stock is a separate StockItem): `name`, `IPN`,
  `revision`, `revision_of`, `category`, `variant_of`, `units`,
  `default_location`, `default_expiry`, `minimum_stock`, `maximum_stock`,
  `responsible_owner`. Unique on (`name`, `IPN`, `revision`); duplicate IPNs
  are allowed by default, and `PART_IPN_REGEX` can enforce a format.
- **Flags:** `is_template` (variants point to it through `variant_of`; serials
  are unique across the tree), `assembly`, `component`, `trackable` (batch or
  serial numbers, integer quantities), `testable`, `purchaseable`, `salable`,
  `virtual` (no stock), `consumable` (1.5.0; never allocated), `active`,
  `locked` (blocks deletion and BOM and parameter edits).
- **Revisions are separate parts.** Each is linked by `revision_of` and has its
  own stock and BOM, so existing stock and orders stay on the old revision.
  Templates cannot be revised.
- **Units** are pint units; blank means pieces. Supplier packs convert to them,
  so `100 ft` becomes 30.48 m.
- **PartCategory** is a tree with `structural` and `default_location`
  (inherited down the tree); category parameter templates seed a
  `default_value`.
- **Parameters:** `ParameterTemplate` (`name`, `units`, `choices`,
  `checkbox`, `selectionlist`, `model_type`, `unique`) and `Parameter`
  (`data`, `data_numeric`, `note`), unit-checked; "not used for any core
  business logic".
- **Minimum stock:** when stock falls below `minimum_stock`, subscribers get a
  notice. I found no automatic reorder.
- **Pricing** (its own app since 1.7.0) caches min/max ranges per source,
  behind a separate `pricing` role.

## 2. Stock

- **StockItem** is one quantity of one part in one location: `status`,
  `batch` (a date code by default), `serial` (forces quantity 1),
  `expiry_date`, `packaging`, `purchase_price`, `supplier_part`,
  `purchase_order`, `build`, `parent` (split source), `is_building`,
  `consumed_by`, `belongs_to` (installed in), `customer`, `sales_order`,
  `stocktake_date`, `delete_on_deplete`, `owner`.
- **`StockStatus`:**

  | Status | Code | Counts as available |
  |---|---|---|
  | OK | 10 | yes |
  | ATTENTION ("hasn't been checked or tested yet") | 50 | yes |
  | DAMAGED | 55 | yes |
  | DESTROYED | 60 | no |
  | REJECTED | 65 | no |
  | LOST | 70 | no |
  | QUARANTINED | 75 | no |
  | RETURNED | 85 | yes |

  Items that are installed, consumed, sent to a customer, assigned to a sales
  order or still in production never count, whatever their status. Custom
  states are "Display Only" relabels.
- **StockLocation** is a tree with `structural`, `external`, `owner` and
  `location_type` (name, icon). `exclude_from_stock` exists on master only.
- **Adjustments:** count, add, remove, move, merge, split, serialize,
  install/uninstall and disassemble. Count, add, remove and move take items
  and free-text `notes`; there are no reason codes.
  - A count stores the new quantity.
  - A merge needs the same part. A user can override the supplier-part and
    status checks. The check ignores batch codes, and the merged-in item is
    deleted.
  - A split keeps the batch, sets `parent` and copies the test results.
- **History** is a named `StockHistoryCode` event per operation (STOCK_COUNT
  10, STOCK_MOVE 20, SPLIT_FROM_PARENT 40, BUILD_CONSUMED 57,
  RECEIVED_AGAINST_PURCHASE_ORDER 70, ...). Since 1.7.0 these live in the
  audit log (StockItemTracking was dropped). With `AUDIT_LOG_ENABLED` off, "no
  stock tracking history is recorded", and old entries can be pruned.
- **Allocation** reserves stock without deducting it.
  - BuildItem: `build_line`, `stock_item`, `quantity`, `install_into`.
  - SalesOrderAllocation: `line`, `shipment`, `item`, `quantity`.
  - Available stock is in-stock minus allocated. Expired stock is blocked from
    builds and sales by default.
- **Stocktake** is not a count. `PartStocktake` (`date`, `item_count`,
  `quantity`, `cost_min`, `cost_max`) is a periodic snapshot of value, off by
  default.

## 3. BOMs and builds

- **BomItem fields:** `sub_part`, `raw_amount` (with units), `quantity`,
  `reference`, `optional`, `consumable` (never allocated), `setup_quantity`,
  `attrition` (%), `rounding_multiple`, `piece_count`, `inherited` (applies to
  variants), `allow_variants`, `validated`. Substitutes live in
  BomItemSubstitute.
- **There is no `overage` field.** It was removed before 1.0
  (`0139_remove_bomitem_overage`) and replaced by setup quantity, attrition and
  rounding.
- **BOM validation:** an MD5 hash covers each line's part, quantities,
  reference and flags. "Validate BOM" stores `bom_checksum`, `bom_checked_by`
  and `bom_checked_date`; any change to a hashed field un-validates it.
  Validating needs the same `bom` role as editing.
  `BUILDORDER_REQUIRE_VALID_BOM` and `BUILDORDER_REQUIRE_LOCKED_PART` (both off
  by default) block build creation.
- **Build:**
  - Fields: `part` (cannot change), `quantity`, `completed`, `batch`,
    `take_from`, `destination`, `responsible`.
  - States: PENDING 10 → PRODUCTION 20 ⇄ ON_HOLD 25 → COMPLETE 40, or
    CANCELLED 30. Neither end can be reopened.
  - BuildLine holds `bom_item`, `quantity` and `consumed`.
- **Outputs** are stock items with `is_building` set.
  - Completing an output marks untracked allocations `consumed_by` the build
    and installs tracked ones in the output (`belongs_to`).
  - Scrapping an output sets REJECTED.
  - Required tests block completion only if
    `PREVENT_BUILD_COMPLETION_HAVING_INCOMPLETED_TESTS` is on (off by default).
  - Completion offers "Accept Unallocated", "Accept Incomplete" and "Accept as
    consumed".

## 4. Purchasing, suppliers, sales and returns

- **Company** has `is_supplier`, `is_manufacturer`, `is_customer`,
  `is_internal`, `active` and `currency`. There is no approval field.
- **ManufacturerPart** has `part`, `manufacturer` and `MPN`.
- **SupplierPart** has `supplier`, `manufacturer_part`, `SKU`, `primary`,
  `active`, `pack_quantity` (with units), `packaging`, `multiple` and
  `base_cost`.
  - It is priced per pack, through SupplierPriceBreak (`quantity`, `price`).
  - **There is no lead time.** The field is commented out with a TODO.
- **PurchaseOrder** has `supplier`, `destination`, `receiving_company` and
  `responsible`.
  - States: PENDING → PLACED → COMPLETE, plus ON_HOLD, CANCELLED, LOST and
    RETURNED.
  - Each line has a SupplierPart, a `quantity` in packs, `received`,
    `purchase_price` and `destination`.
- **Receiving** works on PLACED orders only and needs the purchase-order *add*
  permission.
  - It creates stock with a `status` (OK by default), a batch, an expiry date,
    serials and a unit price.
  - I found no check against the ordered quantity, so over-receipt goes
    through.
  - There is no "incoming" status; "on order" is derived from open lines.
- **Sales orders** allocate stock to shipments.
- **Return orders** receive items as QUARANTINED; line outcomes (RETURN,
  REPAIR, REPLACE, REFUND, REJECT) are "a manual, record-keeping step only". I
  found no return-to-supplier flow.

## 5. Traceability and quality

- **Batch and serial:**
  - Batches carry through splits.
  - Serials are unique per part tree but editable by default
    (`STOCK_ALLOW_EDIT_SERIAL`).
  - Depleted items are deleted by default; their history stays on the part.
- **Genealogy** comes from `parent`, `belongs_to`, `consumed_by` and `build`.
- **Tests:**
  - PartTestTemplate (`test_name`, `key`, `required`, `requires_value`,
    `requires_attachment`, `enabled`) cascades to variants.
  - StockItemTestResult (`result`, `value`, `attachment`, `user`,
    `test_station`) uses the latest result.
  - Deleting a template deletes its results.
- **Quarantine** is a status that any stock editor can set or clear.
- **Missing:** neither the code nor the docs has a nonconformance,
  disposition, concession or CAPA object.

## 6. Permissions and governance

- **Roles:** users belong to groups; a group holds 12 rulesets (part, bom,
  stock, build, purchase_order and others), each with view, add, change and
  delete. Grants are unioned, and `stock_stockitem` sits in both `stock` and
  `build`, so build rights include stock rights. Superusers bypass all checks.
- **Ownership** (an `Owner` is a user or a group) is off by default and "just
  hides buttons". I found no `check_ownership` call outside the tests.
- **Nothing here needs approval:** placing and receiving orders, adjustments,
  QUARANTINED → OK, scrapping, deleting, validating a BOM, locking and
  unlocking, build overrides and serial edits. Completed orders are locked
  unless `*_EDIT_COMPLETED_ORDERS` is set.
- **Hooks are code only.** A `TransitionMixin` plugin can veto order and build
  transitions; a `ValidationMixin` plugin sees field changes on any model.

## 7. Borrow and avoid

**Borrow:**

- separate records for part, manufacturer part, supplier part and stock item;
- category and location trees with `structural`;
- the stock-item identity fields;
- the QUARANTINED and REJECTED statuses, and quarantine on return;
- the history codes as our movement vocabulary;
- allocating before consuming, and installing tracked parts in their outputs;
- the BOM line semantics and the BOM checksum;
- tests that block completion;
- the serial syntax (`1-5`, `~+3`).

**Avoid:**

- history that can be switched off or pruned;
- status changes as ordinary edits;
- adjustments without reasons or limits;
- counting DAMAGED and RETURNED stock as available;
- merges across batches, editable serials, and deletion on depletion;
- people validating their own BOMs;
- overrides with only one signature;
- ownership enforced only in the UI.

## 8. Proposal for Hub ERP

Today `item.stock` is one number, `receipt.location` is text (`MAIN`), the
NCR's `part`, `lot` and `quantity` are free fields, and dispositions move no
stock.

### 8.1 Record types

| Type | Key fields | Lifecycle |
|---|---|---|
| `part` (PN) | `title`, `revision`, `category`, `units`, `assembly`, `component`, `purchaseable`, `salable`, `tracking` (none, lot or serial), `testable`, `active`, `default_location`, `minimum_stock`, `shelf_life_days`, `inspection_required`, `design`; derived: `available`, `allocated`, `on_order` | part-request Gate (buyer; design authority for made parts); revisions only via design-release |
| `manufacturer-part` (MP) | `part`, `manufacturer`, `mpn`, `approved` | approved manufacturer list (AML) entry; design authority |
| `supplier-part` (SP) | `part`, `manufacturer_part`, `supplier`, `sku`, `pack_quantity`, `price_breaks`, `lead_time_days`, `primary`, `active` | buyer Gate; orderable only while `supplier_approved` |
| `part-category` (CAT) | `name`, `parent`, `structural`, `default_location`, `parameter_templates` | no Gate |
| `stock-location` (LOC) | `name`, `parent`, `site`, `kind` (store, receiving, inspection, quarantine, mrb, scrap, production, transit), `structural`, `counts_as_available` | changing `kind` or `counts_as_available` needs a Gate |
| `stock-item` (LOT) | `part`, `part_revision`, `lot`, `supplier_lot`, `serial`, `quantity`, `location`, `expiry`, `unit_cost`, `supplier_part`, `receipt`, `po`, `build_order`, `parent`, `installed_in`, `consumed_by`, `ncr`, `tests` | states `inspection`, `available`, `quarantined`, `rejected`, `consumed`, `installed`, `shipped`, `scrapped`, `lost`; only `available` counts |
| `bom` (BOM) | `assembly`, `revision`, `design`, `lines` (`refdes`, `part`, `quantity`, `setup_quantity`, `attrition_pct`, `optional`, `consumable`, `substitutes`), `checksum`, `validated_by` | filed by `part-releaser`; never edited, only superseded |
| `build-order` (BO) | `part`, `revision`, `bom`, `bom_checksum`, `quantity`, `lot`, `serials`, `take_from`, `destination`, `allocations`, `outputs` | "Release the build" Gate (BOM released, checksum matches, allocated); Gate for overrides or use above attrition; quality releases testable outputs |
| `receipt` (GR) | `po`, `location` (a LOC), `packing_slip`, `lines` (`po_line`, `packs`, `quantity`, `lot`, `supplier_lot`, `serials`, `expiry`, `inspection`) | goods-receipt, plus an "Inspect the goods" Gate (inspector) when `inspection_required` |

A lot's quantity and location come from chained kernel events
(`stock.received`, `.moved`, `.counted`, `.split`, `.quarantined`,
`.released`, `.consumed`, `.installed`, `.scrapped`, `.shipped`), each with
quantity, from, to, reason and run; they cannot be switched off. Lots pin
`part_revision` because `part-releaser` revises a PN in place.

### 8.2 Movements

| Movement | Rule |
|---|---|
| Transfer within a site; split; merge within one lot | Free. Guards refuse structural locations and merges across lots. |
| Transfer between sites | Free, in two steps through `transit` (like InvenTree's TransferOrder) |
| Move into quarantine | Free, so containment never waits |
| Count within tolerance | Free; the event records the variance |
| Count outside tolerance; adjustment over the threshold; lost; serial or lot correction | Gate: the stores controller, or the controller above the value threshold. The counter cannot approve. |
| Release from quarantine or inspection | Gate: the quality manager or inspector. Guards: no open NCR on the lot, tests passed, not expired. |
| Scrap, use-as-is, return, rework | NCR disposition only |

The gated movements run as new processes (`stock-adjustment`,
`stock-release`) in an `inventory-policy` Frame, which would add these
sentences:

- "The count variance tolerance is 2 percent."
- "The adjustment approval threshold is 500 USD."
- "The over-receipt tolerance is 5 percent."
- "Approvals in stock-adjustment over 500 USD go to the controller."
- "The counter and the approver of a stock-adjustment must be different
  people."

Cog criteria need new conditions ("the variance is within tolerance"); today's
`no_open_ncr` checks for any open NCR, not one on this lot.

### 8.3 NCR, quarantine and disposition

1. The NCR gets `stock_items` (quantity per lot); `part`, `lot` and
   `quantity` become derived.
2. After "Open the nonconformance", `stock-quarantiner` splits off the
   affected quantity, sets it `quarantined` with `ncr`, and moves it to MRB,
   with no Gate.
3. "Disposition the nonconformance" decides each lot; `disposition-applier`
   then acts under the grant:
   - **scrap:** `scrapped` and written off (the controller too above the
     threshold);
   - **use-as-is:** `available`, citing the concession;
   - **rework:** a rework build-order;
   - **return:** a supplier return and a debit note.
4. Rejected receipt lines and failed tests call `ncr-opener`, which today
   handles only complaints and calibrations.

### 8.4 Lot and serial traceability

- Every receipt line creates a lot. Serial-tracked parts get one lot per
  serial.
- Add the links `split-from`, `consumed-by`, `installed-in` and
  `quarantined-by` to `config/links.json`.
- A trace then runs from an output serial back to component lots, receipt, PO
  and supplier lot, and forward to shipments: ISO 9001 8.5.2, and a CAPA's
  scope.

### 8.5 Changes to existing types and migration

1. Release the new types, the Frame and the links alongside `item`.
2. Convert each ITEM:
   - it becomes a PN, keeping `legacy_number`;
   - `mpn` and `manufacturer` go to a manufacturer-part;
   - `supplier`, `unit_cost` and `lead_time_days` go to a supplier-part;
   - `stock` becomes an opening lot in "MAIN" (`stock.opening_balance`);
   - the PN `supersedes` the ITEM (an existing link).
3. Update the cogs:
   - `bom-reconciler` matches through manufacturer-parts;
   - `shortage-requester` nets available − allocated + on order;
   - `stock-booker` creates lots.
4. Each `purchase-order` line gains `line`, `supplier_part`, `received` and
   `needed_by`.
   - The order-level `received` flag is derived from the lines, with a new
     `partially-received` state. Bill matching keeps reading the flag.
   - Existing lines are back-filled from the `stock.received` events.
5. `receipt.location` becomes a LOC reference, and receipt lines gain the lot
   fields shown in 8.1.
6. Archive `item`; `item-request` becomes `part-request`.

## 9. Not confirmed

- Which release added transfer orders (migration dated 2026-02-27).
- Whether a locked part's own fields stay editable.
- Whether a merged item's history survives: the code comment says deleted,
  the docs say kept.
- Pricing internals.
- Every "InvenTree has no X" claim: from searching code and docs, not running
  it.

## Sources

**Docs** (each link is `https://docs.inventree.org/en/latest/` plus the path):

- Parts:
  [part/](https://docs.inventree.org/en/latest/part/),
  [part/template/](https://docs.inventree.org/en/latest/part/template/),
  [part/revision/](https://docs.inventree.org/en/latest/part/revision/),
  [part/trackable/](https://docs.inventree.org/en/latest/part/trackable/),
  [part/consumable/](https://docs.inventree.org/en/latest/part/consumable/),
  [part/test/](https://docs.inventree.org/en/latest/part/test/),
  [part/stocktake/](https://docs.inventree.org/en/latest/part/stocktake/),
  [part/pricing/](https://docs.inventree.org/en/latest/part/pricing/)
- Concepts:
  [concepts/parameters/](https://docs.inventree.org/en/latest/concepts/parameters/),
  [concepts/units/](https://docs.inventree.org/en/latest/concepts/units/),
  [concepts/audit/](https://docs.inventree.org/en/latest/concepts/audit/),
  [concepts/custom_states/](https://docs.inventree.org/en/latest/concepts/custom_states/)
- Stock:
  [stock/](https://docs.inventree.org/en/latest/stock/),
  [stock/status/](https://docs.inventree.org/en/latest/stock/status/),
  [stock/adjust/](https://docs.inventree.org/en/latest/stock/adjust/),
  [stock/tracking/](https://docs.inventree.org/en/latest/stock/tracking/),
  [stock/traceability/](https://docs.inventree.org/en/latest/stock/traceability/),
  [stock/test/](https://docs.inventree.org/en/latest/stock/test/),
  [stock/expiry/](https://docs.inventree.org/en/latest/stock/expiry/),
  [stock/owner/](https://docs.inventree.org/en/latest/stock/owner/),
  [stock/availability/](https://docs.inventree.org/en/latest/stock/availability/),
  [stock/transfer_order/](https://docs.inventree.org/en/latest/stock/transfer_order/)
- Manufacturing:
  [manufacturing/bom/](https://docs.inventree.org/en/latest/manufacturing/bom/),
  [manufacturing/build/](https://docs.inventree.org/en/latest/manufacturing/build/),
  [manufacturing/allocate/](https://docs.inventree.org/en/latest/manufacturing/allocate/),
  [manufacturing/output/](https://docs.inventree.org/en/latest/manufacturing/output/)
- Purchasing and sales:
  [purchasing/supplier/](https://docs.inventree.org/en/latest/purchasing/supplier/),
  [purchasing/manufacturer/](https://docs.inventree.org/en/latest/purchasing/manufacturer/),
  [purchasing/purchase_order/](https://docs.inventree.org/en/latest/purchasing/purchase_order/),
  [sales/sales_order/](https://docs.inventree.org/en/latest/sales/sales_order/),
  [sales/return_order/](https://docs.inventree.org/en/latest/sales/return_order/)
- Permissions and plugins:
  [settings/permissions/](https://docs.inventree.org/en/latest/settings/permissions/),
  [plugins/mixins/transition/](https://docs.inventree.org/en/latest/plugins/mixins/transition/),
  [plugins/mixins/validation/](https://docs.inventree.org/en/latest/plugins/mixins/validation/)

**Code** at
<https://github.com/inventree/InvenTree/tree/35428bcb9fca49504864ba2d60accdc22ae4ccf4>,
under `src/backend/InvenTree/`:

- `part/models.py`, `stock/models.py`, `build/models.py`, `order/models.py`,
  `company/models.py`
- `stock/status_codes.py`, `build/status_codes.py`, `order/status_codes.py`
- `common/models.py` (`ParameterTemplate`, `Parameter`, `PriceBreak`),
  `pricing/models.py`
- `users/ruleset.py`, `users/tasks.py` (`update_group_roles`),
  `common/setting/system.py` (setting defaults)
- `part/migrations/0061_..._0142_...py` (removes `overage`),
  `stock/migrations/0129_migrate_stock_tracking_to_audit_log.py`
- [`CHANGELOG.md`](https://github.com/inventree/InvenTree/blob/master/CHANGELOG.md)

**Hub ERP files read:**

- `config/record-types.json`, `config/links.json`
- `hubproto/procurement.py`, `hubproto/cad.py`, `hubproto/kernel.py`,
  `hubproto/rules.py`, `hubproto/cogs.py`
- `frames/purchasing/purchasing-policy.frame.md`,
  `frames/iso9001/wi-disposition-stock.frame.md`,
  `frames/iso9001/ncr-sop.frame.md`,
  `frames/engineering/design-control-sop.frame.md`
- `feedback/inventory.md`
