# The configuration as JSON (issue #33, part of #27)

Everything in force on a hub can be read as one JSON document, edited, and proposed on a change order. The document is
data: the hub parses it, checks it and carries what it changes through the paths that already exist. Nothing in it is
run, and nothing changes until the change order is approved and released. This is the format the scripts of #34 read
and write. Its JSON Schema is [config-json.schema.json](config-json.schema.json); the code is `hubproto/configjson.py`.

## Reading it

| Where | What |
|---|---|
| `hubproto config export [--out FILE]` | writes the document (standard output by default) |
| `GET /api/config?as=<member>` | the same bytes, for a member who may read the policy (`read` on the `Policy` folder); `&download=1` names it `hub-config.json`. Otherwise 401 (nobody named), 403 (not a member, or no read on `Policy`), 409 (no release in force) |
| A change order's page, *What it carries* | links to the download |

The export is deterministic: sorted keys, every list in a stable order, no timestamps. Two exports of one
configuration are the same bytes, so a diff of two exports is a diff of two configurations.

## The document

```json
{
  "schema": "hub-erp/config/1",
  "hub": {"id": "<32 hex: a one-way HMAC of the hub's signing secret>"},
  "release": {"digest": "<sha256 of the release in force>", "version": 3, "effective_from": "2026-11-12T09:00:00+00:00"},
  "versions": {"record_types": 2, "links": 1, "retention": 1, "forms": {"ncr": 1}},
  "policy": {
    "roles": [{"name": "quality-manager"}],
    "members": [{"principal": "alice", "principal_type": "person", "roles": ["author", "staff"]}],
    "permissions": [{"action": "read", "effect": "allow", "object": "*", "resource": "folder:Finance", "subject": "role:controller"}],
    "thresholds": [{"name": "purchase-approval-threshold", "unit": "USD", "value": 10000.0}],
    "...": []
  },
  "record_types": {"ncr": {"prefix": "NCR", "digits": 4, "fields": {"title": "text"}, "...": "..."}},
  "forms": {"ncr": {"type": "ncr", "sections": [{"id": "details", "title": "Details", "fields": [{"field": "title"}]}], "start": [], "custom_fields": {}}},
  "links": {"concerns": "concerned-by"},
  "retention": {"standard": {"years": 1, "evidence": ["release_digest", "manifest_digest", "decisions"]}}
}
```

| Part | What it holds | How a change reaches a change order |
|---|---|---|
| `schema` | `hub-erp/config/1` | must be exactly this |
| `hub` | `{id}`: the hub the document was exported from | required; propose refuses a document from another hub (two hubs built from the same Frames have the same release digest) |
| `release` | the release the document was taken from | required; propose refuses a document taken from a release no longer in force: it would undo what was released since. Export again |
| `versions` | the versions in force of what changes without a new release: `record_types`, `links`, `retention`, `forms` (type -> released layout version) | required, every key: each must be the one in force. A change order carrying only these makes no new release, so without them an old document (or a script's output) could undo what was released since |
| `policy` | the rules of the release, one section per kind (below) | **direct changes**, as Settings records them (`changecontrol.rule_changes`, `add_direct_change`): the review round's qa-manager cog writes the SOP prose for each and checks it reads back, so the SOP stays the single source of truth |
| `record_types` | type -> definition, as `config/record-types.json` writes one (no `x_` custom fields: they are the forms') | `recordtypes.carry`: only non-destructive changes (new types, fields, enum options, states, required fields, retention, sensitivity, defaults, system and decision fields, archiving) |
| `forms` | type -> its layout in force (the base layout until one is released) | a layout that differs is saved as the person's draft and carried (`formlayout.save_draft`, `carry`), as the forms editor does |
| `links` | relation -> its inverse label | `configdocs.carry` (links and retention are under change control: below) |
| `retention` | class -> `{years, evidence}` | `configdocs.carry` |

### Policy sections

Each entry is the rule as the hub keeps it (`hubproto/rules.py`), without `kind` (the section says it) and without
`source` (the SOP sentence; a changed rule gets its sentence from the review round). A rule stated in several SOPs is
one entry.

| Section | Kind | Entry |
|---|---|---|
| `roles` | role | `{name}` |
| `members` | member | `{principal, principal_type: person\|cog, roles}` |
| `groups` | group | `{name, members, roles}` |
| `committees` | committee | `{name, group, chair, purpose, ...}` |
| `folders` | folder | `{path}` |
| `permissions` | permission | `{subject: role:x\|group:x\|cog:x, action, object, resource: folder:<path>, effect: allow\|deny\|propose, confirmer?}` |
| `thresholds` | threshold | `{name, value, unit}` |
| `routing` | routing | `{process, step, condition, assignee, mode: assign\|notify}` |
| `due_dates` | sla | `{process, step, days}` |
| `approval_criteria` | approval_criteria | `{approver: cog:x, process, max_amount, currency, conditions, ...}` |
| `segregation_of_duties` | sod | `{object, roles, only?, rule?}` |
| `duties` | duty | `{cog, task, every}` |
| `processes` | process | a whole workflow, as the workflow editor carries it |
| `cogs` | cog (profile) | `{name, model, about?, persona?, prompt_from?, references?}` |
| `personas` | persona | `{name, stance, behaviour, tone?, lenses?, passes?, escalates?}` |
| `validation_classes`, `validation_needs` | the IT SOP's validation | `{risk, kinds}`, `{risk, evidence}` |
| `responsibilities` | responsibility | **read only**: the Roles and responsibilities rows of each SOP |
| `scopes` | scope | **read only**: the scoping documents |

A field a kind does not have is refused (the fields a kind has are those of `rules.KINDS`, those the rules in force
carry, and a short list of optional ones in `configjson.OPTIONAL`); a process is checked whole by `rules.validate_rule`.

## Proposing it

| Where | What |
|---|---|
| `hubproto config propose FILE.json --as PERSON [--co CO] [--reason TEXT]` | exit 0 carried (or nothing to carry), 1 refused, whatever is wrong with the document, not JSON included (every reason printed; nothing carried), 2 a mistake in the command (no such file, unknown change order) |
| A change order's page, *What it carries*: *Propose a configuration file (JSON)* | for whoever may change what it carries (its requester or a document controller), while it is being prepared |

Without `--co` the changes go where Settings puts them: the person's open settings change order, started when they
have none. A person proposes, never a cog.

* **Partial documents.** A part or a policy section left out is left as it is. A part or section that is given says the
  whole of it: a rule, relation or class it leaves out is removed; a record type it leaves out is refused (record types
  are archived, never dropped); a form it leaves out keeps its layout.
* **No change.** A document that says what is in force carries nothing and starts no change order.
* **All or nothing.** Every part is checked before any is carried (the same checks the paths make: `validate_rule`,
  `recordtypes.check`, `formlayout.validate`, `configdocs.check`); if carrying still stops part-way, the change order is
  put back as it was. A refusal is a list of sentences, for example: "the document has no field ncr.title: a field is
  not removed this way", "concerns is never removed: the hub's own steps link records with it", "policy.responsibilities
  differs from what is in force: the Roles and responsibilities rows are each SOP's table: revise the SOP on a change
  order to change them".
* **Data, never code.** Only JSON values are read, and only these: at most 16 MB, nested at most 64 deep (counted before
  parsing; 24 within one entry), no key given twice in one object ("the key 'schema' is given twice in one object"),
  finite numbers of at most 10^15 (`1e400`, `NaN` and `Infinity` are refused), no control characters in strings. A
  shape the checks did not foresee is still a sentence, never a traceback.
* **Names as Settings writes them.** In what a document adds or changes, member, role, group, committee, cog, persona and
  limit names, and the names in a permission's subject, an approver, a route's assignee, a confirmer or a
  countersigner, are lowercase letters, digits and hyphens (Settings' `_slug`); a record's field (`field:requested_by`)
  as record types write fields. A look-alike is refused naming the character ("U+0430 CYRILLIC SMALL LETTER A, which
  only looks like a letter of a name"), as are a zero-width character and a direction control. A folder path is in
  its normal form (NFKC), each folder name in one script, with no invisible characters; no text the document adds
  holds one.
* **Same guards.** Widenings, the panel, reviews, the two-owner rule, segregation of duties, validation evidence and the
  signature are the change order's, exactly as for the same edit made on screen.

## Links and retention under change control

`config/links.json` and `config/retention.json` are treated as `config/record-types.json` is
([config-paths.md](config-paths.md)): once the SOPs are documents, what is in force is kept by the hub in
`state/config/links/` and `state/config/retention/` (version 1: the file when the hub first kept it; each released
change order's changes the next version), changed only by an approved change order (CO field `config_changes`, module
`hubproto/configdocs.py`), and brought into force by the release automation's step *Apply the link and retention
changes* (before the record types, which may be kept under a class the same change order adds). A file on disk that
differs is reported by `status --check` and not used.

Refused: removing a relation a record link uses or one the hub's own steps link with (the shipped file lists it);
removing `standard`, a class a record type is kept under, or one records are labelled with. Keeping a class for fewer
years is a warning shown with the change.

## Moving a hub, and its identity

The hub's identity (`hub.id`) is a one-way HMAC of its signing secret, `state/.secret`: it says which hub a document
came from without revealing the secret. A backup holds `.secret`, so a hub restored from its backup (on the same machine
or another) is the same hub: its earlier exports still propose there, as long as nothing was released since. A new hub
built from the same Frames has a secret, and so an identity, of its own: an export from one is refused on the other
("the document was exported from another hub"). To carry a configuration from one hub to another, export the target,
make the edit there, and propose that. If an operator replaces `state/.secret` (which also voids the signatures it made),
earlier exports are refused: export again.
