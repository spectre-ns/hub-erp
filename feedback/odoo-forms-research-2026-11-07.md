# How Odoo customizes forms (Studio, 17 and 18): a brief for Hub ERP

2026-11-07. Sources: Odoo 17.0/18.0 docs and `odoo/odoo` on GitHub. Studio's
code (`web_studio`) is in Odoo's private enterprise repository, so Studio
internals come from the docs or Studio hooks in open-source code, or are marked
*unconfirmed*.

## 1. Studio's form editor

- **Fields.** Drag a type from *Add > New Fields*, or an *Existing Field*, onto
  the view; *Remove from view* affects that view only. New fields are created
  only from form or list views (20 Studio types over Odoo's 15). Technical names
  get `x_studio_`, editable in Studio only at creation; later renames go through
  *Settings > Technical > Fields* after removing the field from every view,
  keeping `x_`. In code a custom field is an `ir.model.fields` row with
  `state='manual'`; an SQL constraint rejects names without `x_`. "Fields
  created using Studio are saved as columns in your Odoo database."
- **Properties** (18.0): Invisible, Required, Readonly, each with *Conditional*
  ("creating a filter"); Label; Help Tooltip; Widget; Placeholder; Default value
  ("when a record is created"); Allow/Forbid visibility to groups; Class and
  Dynamic Placeholder are new in 18. Label, widget, group visibility and
  invisible/required/readonly "can be configured independently for different
  views".
- **Layout.** A *Tabs and Columns* element; header buttons that run a server
  action or call a Python method; smart buttons (label, icon, related field).
  The status bar is a selection field with the *Status Bar* widget, clickable
  unless *Clickable* is off. The underlying arch: `header` (buttons plus
  `widget="statusbar"`), `sheet`, a `button_box`, `group` (2 columns,
  `col`/`colspan`), `notebook`/`page`, `separator`, chatter.
- **Views edited:** form, activity (XML only), search, kanban, list, map,
  calendar, cohort, gantt, pivot, graph; developer mode adds an XML editor.
- **Storage.** Each drag-and-drop edit generates "a specific Studio inherited
  view and its corresponding XPath". Direct edits to standard views "would be
  reset and lost during updates or module upgrades". The source notes "all views
  created via studio have a priority=99 to be loaded last". Everything belongs
  to a generated module, `studio_customization`.
- **Export/import.** That module exports as a ZIP (18 adds per-model data with
  Demo/Attachments/Updatable flags and a domain). Import needs "the same Odoo
  version" and "the same apps and modules": "Studio does not add the underlying
  modules as dependencies".
- **Upgrades and conflicts.** Odoo's upgrade service covers "all customizations
  created with the Studio app, as long as Studio is still installed"; customers
  test an upgraded copy ("Are there views that are deactivated...?"). After a
  module update the loader re-checks views without an xml id and only logs
  `invalid custom view(s) for model`. A view keeps one previous arch and its
  file arch for resets, with a diff wizard.

## 2. Conditional logic

- Since 17.0, `invisible`/`readonly`/`required` are Python expressions over the
  view's field values, `parent`, `context`, `uid`, `today`, `now`, e.g.
  `required="fname_c != 3"` or `invisible="state != 'done'"`. The 16.0 `attrs`
  domains (`{'invisible': [('sale_ok', '=', False)]}`) are rejected: "Since 17.0,
  the "attrs" and "states" attributes are no longer used." The source says these
  expressions "are only executed client side".
- The web client's `ExpressionEditor` turns an expression into a condition tree
  and back; what it can't parse shows "This expression is not supported" with a
  reset button, and only developer mode gets a code box (that Studio uses it is
  *unconfirmed*).
- **Related** custom fields follow a dotted path (`partner_id.email`) and are
  readonly by default. **Computed** custom fields hold Python in
  `ir.model.fields.compute`, run by `safe_eval` with comma-separated `depends`
  (Studio docs cover only related). Automation rules add *On UI change*,
  *Execute Code* only.
- **Approval rules** sit on buttons. 18: per-step approvers or group, a filter,
  *Approval Order*, *Exclusive Approval* (no user approves two steps),
  delegation "forever if left empty", revocation (higher steps can revoke lower
  ones unless exclusive); logged in chatter and *Studio Approval Entries*. 17 had
  an allowed group, a responsible user and notification order.

## 3. Safety and governance

- **Field access.** A code field's `groups` removes it from views and
  `fields_get` and makes reads/writes raise. A view node's `groups` only drops
  the node for non-members. For custom fields `ir.model.fields.groups` is "CLEANME
  unimplemented field (empty table)" in 17 and 18, so Studio's group visibility
  is, I infer, display-only. Even code-level `readonly` "only has an impact on the
  UI".
- **What breaks.** Deleting a custom field runs `DROP COLUMN ... CASCADE`; it is
  refused only for module fields, fields another custom field depends on, and
  fields "still present in views". **That fields can't be deleted once they hold
  data is not confirmed; the code has no such check.** Removing an option from a
  non-required custom selection nulls existing values; renaming one rewrites rows.
  Type changes are refused ("drop it and create it again"); base fields can't be
  altered. Making a field required back-fills only a code default, then tries
  `SET NOT NULL`, logging a warning if it fails.
- **Arch validation.** `_check_xml` runs on every arch write against the combined
  view (all patches applied): parse, patch targets, fields exist, the RelaxNG
  schema, no `attrs`; failure rolls back. 17 also refused conditions on fields
  missing from the view or restricted to other groups; 18 adds them invisibly and
  logs the mismatch.

## 4. View inheritance

`ir.ui.view` has `arch`, `priority` (default 16), `inherit_id`, `mode`.
Resolution: resolve the parent, apply this view's specs, then its **extension**
children depth-first in `priority, id` order. A **primary** child is its own
view: the parent is fully resolved, then the child's specs, and the result is
the child's arch (the docs' case: a derived model). A spec locates the first
`<xpath expr>` match, a `<field name>`, or an element with identical attributes;
`position` is `inside` (default), `before`, `after`, `replace` (`$0` re-inserts
the original), `attributes` (set, remove, add/remove tokens) or `move`. A missing
target raises "Element ... cannot be located in parent view".

## Borrow and avoid

Borrow:

- a base plus ordered patches;
- a reserved custom-field namespace;
- validation of the merged result on every save;
- a tree editor for conditions;
- refusing type changes;
- dependency checks before removal;
- exclusive approval.

Avoid:

- positional XPath anchors;
- browser-only conditions;
- group "visibility" passed off as access control;
- dropping columns or nulling values;
- warn-only breakage after upgrades;
- exports without dependencies;
- open-ended delegation;
- approvals configured in the form editor;
- live edits with no review.

## Proposal for Hub ERP

Hub stores fields in hash-chained events, not columns, so the risk is less
schema damage than ungoverned or retroactive rules.

1. **Data rules stay on the record type.** Types, `required_by_state`,
   defaults, `system_fields`, `decision_fields`, plus new per-field label, help
   and sensitivity, enforced by the kernel (`required_missing` already blocks
   transitions). Layouts show "required to close" but store no
   required/readonly of their own.
2. **Base layouts as data**: a `layout` per record type (workflow status bar,
   never clickable; smart buttons from typed links; title; sections with columns
   and rows; tabs) plus derived `start` and `decide:<checkpoint>` forms (Odoo's
   *primary*). Seed them from `START_INPUTS`, `REQUIRED_EXTRA` and
   `TEXTAREA_FIELDS` (`hubproto/shell/forms.py`) and the `lead` order and tabs in
   `pages.py`, so the first release changes nothing.
3. **Site overlays** in `config/form-overlays.json` (every `config/*.json`
   digest is already recorded in the release): ordered ops anchored on field
   names or section/tab ids, applied kernel, profile, then site, stamped with the
   base digest.

   ```json
   {"target": "ncr.form", "base": "sha256:...", "ops": [
     {"op": "add", "field": "x_supplier_lot", "after": "lot"},
     {"op": "set", "field": "disposition_rationale",
      "visible_if": {"field": "disposition", "in": ["use-as-is", "return"]}}]}
   ```

4. **Conditions** are JSON trees (`all`/`any`/`not`; field `=`, `in`, `set`,
   `<`; `state`; viewer `role`) evaluated by one server function, mirrored in the
   browser; writes to hidden or readonly fields are refused. No code strings.
5. **Editor.** Drag-and-drop edits a draft overlay. Each save validates the
   merged layout: undeclared field, system field made editable, condition on a
   field some viewers can't read, field required in a state but hidden there.
   Preview renders a chosen record as a chosen person in a chosen state, with a
   diff against the release in force.
6. **Release.** Proposals list sentences ("NCR: Supplier lot required to
   close"). Data-rule changes need two policy owners, like widenings. The release
   pins the layout digest. After a kernel or profile upgrade, an op whose anchor
   no longer resolves blocks the release; it is never just a log line.
7. **Custom fields**: `x_` names, existing type specs, optional
   `required_by_state`, default, sensitivity; name and type immutable. The kernel
   keeps undeclared fields today; the editor offers only declared ones.
8. **Existing records.** Adding a field writes nothing; defaults apply at
   creation. A new `required_by_state` entry bites at the next transition, and the
   proposal shows the impact ("14 open NCRs will need Supplier lot to close").
   Back-fills are reviewed, attributed `record.fields_set` batches. Removal is
   retirement: off layouts, new writes refused, values kept in events, history,
   snapshots and exports under retention. Compile refuses while a layout,
   condition, Frame or cog prompt uses the field. Enum options are retired, never
   deleted or renamed in place.

## Not confirmed

How Studio stores group visibility and default values; whether *Conditional*
uses `ExpressionEditor`; what "Security rules" (listed on the Studio overview,
no page) covers; whether Studio views count as views without an xml id in the
post-update check; any Studio UI for computed fields.

## Sources

- <https://www.odoo.com/app/studio>
- <https://www.odoo.com/documentation/17.0/applications/studio.html>
- <https://www.odoo.com/documentation/18.0/applications/studio.html>
- <https://www.odoo.com/documentation/17.0/applications/studio/fields.html>
- <https://www.odoo.com/documentation/18.0/applications/studio/fields.html>
- <https://www.odoo.com/documentation/18.0/applications/studio/views.html>
- <https://www.odoo.com/documentation/17.0/applications/studio/models_modules_apps.html>
- <https://www.odoo.com/documentation/18.0/applications/studio/models_modules_apps.html>
- <https://www.odoo.com/documentation/17.0/applications/studio/approval_rules.html>
- <https://www.odoo.com/documentation/18.0/applications/studio/approval_rules.html>
- <https://www.odoo.com/documentation/18.0/applications/studio/automated_actions.html>
- <https://www.odoo.com/documentation/17.0/developer/reference/user_interface/view_records.html>
- <https://www.odoo.com/documentation/17.0/developer/reference/user_interface/view_architectures.html>
- <https://www.odoo.com/documentation/18.0/developer/reference/user_interface/view_architectures.html>
- <https://www.odoo.com/documentation/16.0/developer/reference/backend/views.html> (`attrs`)
- <https://www.odoo.com/documentation/17.0/developer/reference/backend/orm.html>
- <https://www.odoo.com/documentation/17.0/developer/reference/backend/security.html>
- <https://www.odoo.com/documentation/18.0/administration/upgrade.html>
- <https://github.com/odoo/odoo/blob/17.0/odoo/addons/base/models/ir_ui_view.py>
  (`_check_xml`, `_combine`, `_get_inheriting_views`, `_postprocess_access_rights`,
  `reset_arch`); 18.0 same file (`_add_missing_fields`)
- <https://github.com/odoo/odoo/blob/17.0/odoo/tools/template_inheritance.py>
- <https://github.com/odoo/odoo/blob/17.0/odoo/addons/base/models/ir_model.py>
  (`IrModelFields`: `_prepare_update`, `_drop_column`, `write`,
  `_instanciate_attrs`; `IrModelSelection._process_ondelete`); 18.0 same file
- <https://github.com/odoo/odoo/blob/17.0/odoo/fields.py> (`update_db_notnull`),
  <https://github.com/odoo/odoo/blob/17.0/odoo/models.py> (`_init_column`),
  <https://github.com/odoo/odoo/blob/17.0/odoo/modules/registry.py>
  (`finalize_constraints`)
- <https://github.com/odoo/odoo/blob/17.0/odoo/modules/loading.py>
  (`_validate_custom_views` call)
- <https://github.com/odoo/odoo/blob/17.0/addons/web/static/src/core/expression_editor/expression_editor.js>
  and `expression_editor.xml`
