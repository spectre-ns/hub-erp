# Developer notes (read before changing anything)

This is the from-scratch prototype of the Intelligence Hub ERP plan. Standard
library Python 3.11+, SQLite, files. No new runtime dependencies without a
reason written in the PR. Tests: `python -m pytest -q` (all must pass).

## Module map and ownership

| File | Purpose | Owner |
|---|---|---|
| `hubproto/frames.py` | Frame Spec v0.2 loading, inheritance resolution | core |
| `hubproto/extract.py` | sentence grammar -> rule statements with citations | core |
| `hubproto/rules.py` | statement kinds, canonical JSON, rule keys, condition vocabulary | core |
| `hubproto/guards.py` | schema, grounding, consistency, drift Guards | core |
| `hubproto/compile.py` | proposal, per-change Gate decisions, finalize | core |
| `hubproto/release.py` | render bundle (permissions, routing, workflows, op manifests), digest, ReleaseStore | core |
| `hubproto/authz.py` | permission check with folder inheritance; deny wins; returns the sentence | core |
| `hubproto/routing.py` | routing candidates, approval criteria, thresholds, SLAs | core |
| `hubproto/kernel.py` | generic record system + ledger command on an event store | core |
| `hubproto/dms.py` | blob store, document versions, classes, check-out, snapshots, intake | core |
| `hubproto/runner.py` | Op runner = workflow engine; Gates, grants, Track, tick (SLA escalation) | core |
| `hubproto/cogs.py` | Cog catalog (code cogs, context stand-ins, reviewer cogs) | core |
| `hubproto/inbox.py` | DMs and email to people and cogs | core |
| `hubproto/cad.py` | CAD/ECAD: FreeCAD and KiCad stdlib extraction, worker client, cad-exporter, bom-reconciler, part-releaser cogs | core |
| `hubproto/worker.py` | the CAD worker (its own image, docker/cad-worker): /invoke envelope seam over FreeCADCmd and kicad-cli, fake mode | core |
| `frames/engineering/design-control-sop.frame.md` | design-release process (submit, export, reconcile BOM, risk, review, release part) | core |
| `hubproto/app.py` | `Hub` wiring | core |
| `hubproto/changecontrol.py` | the change-order loop (change control v2): stages and review rounds, panel suggestion and reviews, the delta (origins, warnings, item decisions, overrides), send/approval readiness, revision approval with the CO, release steps (divergences, related changes); the API the CO pages use | core |
| `hubproto/closure.py` | no configuration change without a change order once the SOPs are documents: the guards `Hub.compile`, `review`, `release` and `frame_draft` call; the sentence (`CO_ROUTE`) | core |
| `hubproto/recordtypes.py` | record types under change control: the record types in force (`state/record-types`), changes carried on change orders (`carry`, `carried_reasons`, `delta_items`, `activate`), the file's drift | core |
| `hubproto/cli_recordtypes.py` | CLI verbs `record-types status`, `show`, `carry` (`register(sub)`) | cli agent |
| `hubproto/cli.py` | command line: policy, run, message and operations verbs; `main` returns the exit code; shared helpers (`fail`, `clean`, `require_member`, `resolve_*`) | cli agent |
| `hubproto/shell/` | the workspace shell package (handler, pages, inbox, forms, actions, policy, css, text; stdlib http.server) | shell agent |
| `hubproto/cli_records.py` | CLI verbs for runs waiting on people, records and documents (`register(sub)`) | cli agent |
| `docker/entrypoint.sh`, `README.md`, `tests/test_cli.py` | container first start and later-start drift warning; operator docs; the CLI suite | cli agent |
| `hubproto/profile.py` | seed profiles: install, conformance checklist (`register(sub)`) | profile agent |
| `profiles/iso9001/**` | the ISO 9001 seed profile | profile agent |
| `frames/**` | the example company's Frames | core (profile agent may add `frames/iso9001/` via the seed command) |
| `config/*.json` | record types, link vocabulary, retention classes | core (profile agent may add record types; tell core) |
| `tests/test_compile.py`, `tests/test_runtime.py`, `tests/conftest.py` | existing suites | core |
| `tests/test_records.py`, `tests/test_workflow.py`, `tests/test_dms.py` | new suites | test agent |
| `tests/test_profile.py` | seed profile suite | profile agent |
| `tests/test_shell.py`, `tests/test_shell_ux.py` | shell smoke suite; UX-panel regression suite over HTTP | shell agent |

Do not edit files you do not own. If you need a core change, say exactly
what in your final report and work around it meanwhile.

## Command line conventions

* `cli.main(argv)` returns the exit code: 0 done, 1 when the hub refused or the outcome is a failure (error
  findings, `DENIED`, a failed run), 2 for a mistake in the command (unknown id, bad JSON, missing file, bad
  `--scope`, wrong `--root`). Verbs return their code; `fail(message, code)` prints `error: <message>` to stderr and
  raises `CliExit` (a `SystemExit` carrying the sentence and the code). `main` turns `CompileError`, `RunError`,
  `KernelGuardError`, `FrameError`, `ProfileError`, JSON and file errors into such sentences, so nothing prints a
  traceback. The console script does `sys.exit(main())`; tests call `cli.main` in-process and check the return value
  or `pytest.raises(SystemExit)` with `e.value.code`.
* Every verb but `init` and `restore` needs `<root>/state/kernel.db` (checked in `main`); a malformed Frame only
  stops the verbs in `NEEDS_FRAMES`, the others run with a warning on stderr.
* `--as`, `--second`, `--to` and a message's sender go through `require_member` (members of the release in force;
  before the first release, of the proposal or Frames being bootstrapped). Identifiers: `resolve_record` (number
  in any case or locator), `resolve_run` (id prefix, ambiguity listed), `resolve_proposal` (`#n` or an id prefix;
  numbers are given once in creation order and kept in `state/proposal-numbers.json`), `resolve_release` (`v<n>`
  or a digest prefix). Digests print 12 characters (`short`).
* Anything printed from a record field goes through `clean` (ANSI escapes and C0/C1 controls stripped, line
  breaks made spaces, `width` truncates with an ellipsis). Listings have a header row.
* `--effective-from` takes `YYYY-MM-DD` or an ISO timestamp (`iso_timestamp`); `tick --today` takes `YYYY-MM-DD`.
* `collapse_changes` folds an add/remove pair whose rules differ only in source position (a re-rendered process)
  into one "re-rendered, no semantic change" line; `describe_change` words a change without `up to None`.
* `release_diff` compares the sentences two releases' `rules.json` cite, by Frame: added, removed, and changed
  (a removed and an added sentence on the same file line, or close by `difflib`).
* `backup <dir>` copies `kernel.db` with `sqlite3.Connection.backup` plus `runs/`, `releases/`, `proposals/`,
  `blobs/`, `inbox/` and `proposal-numbers.json`, with a `backup.json` manifest; `restore <dir>` refuses a partial
  set and an existing state without `--force`. `status --check` exits 1 when no release is in force or the Frames
  on disk differ from the release's bill of materials (the entrypoint prints that on later starts); once the SOPs are
  documents, also when `config/record-types.json` differs from the record types in force or another config file from
  what the release recorded.
* Once the SOPs are documents a configuration change outside a change order is refused by the hub
  (`closure.ChangeOrderRequired`, a `CompileError`): the verbs exit 1 with a sentence ending "Configuration changes only
  through a change order: open one under Quality > Change orders." (`compile`, `review`, `release`,
  `bootstrap-release --force`, `seed`). See `docs/design/config-paths.md`.

## The model in one paragraph

Frames (Markdown + YAML frontmatter) hold policy prose. `hub.compile()`
extracts rule statements (each cites its sentence), runs Guards, and makes a
proposal; policy owners (members with role `policy-owner`) approve each
change (widenings need two); `hub.release()` renders the **policy release**:
members, roles, folders, permissions, thresholds, routing, approval criteria,
segregation of duties, processes, generated Op manifests (`ops`) and
**workflows** (state machines per process). Runs pin the release digest.
A run creates a record (type from the workflow), executes automatic cog
steps, and at each checkpoint routes to a person or a cog; the record waits
in state `awaiting-<checkpoint-id>` with assignees and a due date; a
decision (`runner.decide`) is the electronic signature; the run continues
and closes the record in the process's terminal state. Everything is on the
run's Track (`state/runs/<id>/track.jsonl`) and on the record's events.

## Sentence grammar (convention mode)

Only inside these sections: `## Roles`, `## Members`, `## Folders`,
`## Permissions`, `## Thresholds`, `## Routing`, `## Approval criteria`,
`## Segregation of duties`, `## Duties`, `## Process: <name>`, and the keyed sections
`## Scope:`, `## Committee:`, `## Cog:`, `## Persona:`. Frontmatter may carry
`compile: convention|guided|none` (default convention) and `clauses: [...]`. Every document ends with
`## Roles and responsibilities`, a table `| Role | Responsibilities |` (a row names a role, `the <name> cog` or
`the <name> group`); it is read even with `compile: none` and says who must read the document.

```
## Roles            - quality-manager
## Members          - alice (person): finance-clerk, staff      - ap-reviewer (cog): payables-reviewer
## Folders          - Quality/Nonconformances
## Permissions      Quality managers may approve nonconformances in Quality/Nonconformances.
                    Finance clerks may not approve bills in Finance/Payables.
                    Controllers may read Finance.
                    Staff may comment on anything in Quality.                 (anything = every object)
                    The it-admin cog may not approve documents in Policy.     (a permission naming one cog: subject cog:it-admin)
                    The stores cog may move stock items in Inventory/Stock when a storekeeper confirms.
                                                                (effect propose, confirmer storekeeper; "when a person confirms": whoever gave the task)
## Thresholds       The match tolerance is 2 percent.      The effectiveness check interval is 90 days.
                    (name words + threshold|tolerance|limit|interval; units USD EUR GBP percent days)
## Routing          Approvals in purchase-to-pay over 10,000 USD go to the controller.
                    At Disposition the nonconformance in nonconformance with low risk go to the quality-assistant cog.
                    At Acknowledge the document in training-acknowledgement go to the assignee.
                    Approvals in document-control for drawings with a minor change go to the doc-reviewer cog.
                    Overdue items in nonconformance go to the management representative.
                    Disposition the nonconformance in nonconformance is due within 5 days.      (SLA)
                    assignees: the <role words> | the <cog-id> cog | <person> | the assignee|owner|author|requester|auditor|chair|evaluator|reporter (field)
                    conditions: over|above|under|below|at or under|at or over <amount> <CUR>; with low|medium|high risk;
                                for <doctype>s; with a minor|major change; (Overdue items ...) 
## Approval criteria  The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved.
                    conditions (closed vocabulary, rules.py CONDITION_VOCABULARY): the three-way match is within tolerance;
                    the supplier is approved; the risk is low; the risk is not high; the change is minor; the document is a minor revision; no open nonconformance exists
## Segregation of duties  The preparer and the approver of a bill must be different people.   (object = record object noun)
                    Whoever made a direct configuration change on a change-order is not its only approver.   (only: true)
## Duties           Every day|week|month the quality-assistant cog finds nonconformances that ... .   (a cog's standing goal)
## Cog: ap-reviewer About: <one paragraph: what it does>   Model: ollama/qwen3:14b   Persona: reviewer
                    [Prompt from: DOC-n (its job from a pinned document)   References: DOC-n, ...]
## Persona: reviewer  Tone: formal|informal   Stance: reviewer|executor|author|advisor   Lenses: compliance, consistency, impact
                    Reviews: 3 adversarial passes   Escalates: when ...   Behaviour: <the behaviour prompt, may run on>
## Process: nonconformance
Records are filed in Quality/Nonconformances.
The record type is ncr.                      (optional; otherwise derived from the first step's object)
Closes as closed.                            (terminal state; default closed)
Rejections return to Open the nonconformance.   (optional; otherwise a rejection ends in state rejected)
1. [staff] Open the nonconformance (ncr-classifier)        <- step with a cog = automatic
2. [system] File the nonconformance (record-filer)
3. [system] Score the risk (risk-scorer)
4. [quality-manager] Disposition the nonconformance         <- no cog = human checkpoint; state awaiting-disposition-the-nonconformance
5. [system] Open corrective action (capa-opener)
```

The **object** of a process is the first step's title minus its verb and article
("Open the nonconformance" -> `nonconformance`). Permission sentences use the
same noun, pluralized ("may create nonconformances in ..."). The run owner
must hold the first step's role and a `create <object>` permission on the
filing folder; a person deciding a checkpoint must hold `approve <object>`
there. The record type is the config type whose `object` equals that noun,
unless `The record type is X.` says otherwise.

**Cogs** (change control v2, section 7). A cog's profile is its identity; its persona (shared) says how it behaves;
the bundle's `cogs[name]` is the merged view (`about`, `model`, `persona`, the persona's fields, the composed `prompt`).
What it **follows** (`follows`, `follows_why`; its training) is derived from the Roles and responsibilities tables
(`release.follows_for`): the company scope plus every document whose table lists it or one of its roles. A person's
required reading is every document whose table lists one of their roles (`scopes.required_reading`). The compile warns
when a document gives a role or cog a step, route, criteria, duty or create/approve/update permission its table does not
list, and when a table names a role nobody holds (`guards.responsibilities_guard`). Training (`hubproto.training`): a
release that makes a revision effective assigns acknowledgements to the people its table names (`training_roles`
overrides); the release automation republishes the generated training matrix; a cog acknowledges nothing (the release that
put a revision in its context is its training record). **Autonomy** is derived per write (`authz.autonomy`, used by
`AgentTools.decide` with each tool's `intent`): a deny refuses, a "when ... confirms" sentence proposes (it narrows a broader
allow), an allow acts, its own step (its turn at a Gate, a step assigned to it) acts; with none, a tool that applies its own
rule decides, anything else is refused ("describe what should be done instead"). A cog's context is its process
(`cogs.process_context`): steps, its part with the sentences cited, divergences, and an index of the SOPs it follows,
read on demand with `read_sop_section`.

A checkpoint with no routing sentence routes to the role on its step (warning).
A routed role with no member is an error ("fill the org model"): this is how
seed placeholders work.

## Record types (`config/record-types.json`)

`prefix`, `digits`, `yearly`, `object`, `fields` (types: text, int, number,
money, date, bool, list, object, ref, principal, enum:a,b), `required_by_state`
(state -> fields that must be set before the record may enter it; waiting
states are `awaiting-<checkpoint-id>`), `defaults` (`$owner`, `$today`),
`retention`, `sensitivity`, `gapless`.

The file is the source only until the SOPs are documents. From then on the
record types in force are the hub's (`state/record-types/active.json`: the file
as of the release in force, then each change order's `record_type_changes`
released by `recordtypes.activate`), `kernel.types` is installed from there
(`Kernel.types_hook` picks up a version another process released), and a file
that differs is reported by `status --check` and not used. Change the file only
to ship new types or fields with the software; on a converted hub, carry them
on a change order (`hubproto record-types carry CO --from-file`). Tests that
edit the file point `hubproto.kernel.CONFIG_DIR` at a copy (see
`tests/test_config_closure.py`).

## APIs you will use

```python
from hubproto.app import Hub
hub = Hub(root)                      # root has frames/ and state/
hub.kernel.seed()                    # suppliers, POs, periods
p = hub.compile(); hub.review(p["id"], "bob", approve_all=True); hub.review(p["id"], "carol", approve_all=True)
meta = hub.release(p["id"], "bob")   # -> {"digest", "effective_from", ...}
bundle = hub.current_bundle()        # members, permissions, routing, workflows, ops, processes, thresholds, sod, approval_criteria
hub.why(user, action, obj, folder)   # {"allowed", "rule", "why"}
run_id = hub.runner.submit(process, {"record": {...}}, owner="alice")
state = hub.runner.load(run_id)      # id, process, owner, release, record (locator), steps, pending{step,checkpoint,assignees,due,payload_digest,title}, grant, status
hub.runner.decide(run_id, "bob", "approve"|"reject", meaning="...", fields={...})
hub.runner.track(run_id); hub.runner.tick(today=None); hub.runner.run_for_record(locator); hub.runner.list()
k = hub.kernel
k.query(type_=, folder=, state=, linked_to=, text=, assignee=, overdue_at=)   # list of records
rec = k.get("rec:ncr:NCR-2026-0001") or k.get("NCR-2026-0001")
# record: locator number type folder state fields labels hold archived created_at created_by run_id release events links decisions transitions assignees due
k.links_of(ref); k.link(ref, rel, target, by=); k.set_fields(ref, {...}, by=); k.flat(rec); k.types; k.link_vocab; k.retention
d = hub.dms
d.create(title=, doc_class=, folder=, by=, content=bytes, filename=, mime=, doc_type=, attach_to=locator)
d.add_version(ref, bytes, by=, filename=, note=); d.versions(ref); d.content(ref) -> (bytes, version)
d.checkout(ref, by=); d.checkin(ref, bytes, by=); d.attach(record_ref, doc_ref, by=); d.documents_of(record_ref)
d.render_snapshot(record_ref, by=); d.intake(folder=, by=, content=, filename=, channel=, sender=, attach_to=)
hub.inbox.for_principal(p); hub.inbox.notify(...); hub.send(sender, to, subject, body, channel="dm"|"email", attachments=[{"name","content"}])
hub.frames.frames  # name -> Frame(path, name, version, scope, maintainer, inherits, body, lines, frontmatter)
hub.releases.list()/current()/bundle(digest)/get(digest)
hub.registry.cards()
```

Cog ids available for process steps: record-filer, record-linker, notifier,
document-renderer, risk-scorer, invoice-extractor, three-way-matcher,
ledger-poster, document-drafter, document-publisher, ncr-classifier,
capa-opener, ncr-opener, capa-drafter, effectiveness-scheduler,
audit-checklist-drafter, document-classifier; reviewer cogs (members):
ap-reviewer, doc-reviewer, quality-assistant.

Workflow bundle shape: `bundle["workflows"][process] = {record_type, object,
states, transitions[{from,to,kind:automatic|human,steps|step,title,on_reject,sla_days}],
checkpoints[{id,title,role,state,sla_days}], terminal, reject_to, sla{checkpoint: days}}`.

## CAD and ECAD

Attach `.FCStd`, `.kicad_sch` or `.kicad_pcb` files to a design record (the
submission's `attachments` list is filed by record-filer; the shell's upload
does the same). `cad-exporter` reads parts lists and BOMs in the hub, calls the
worker (`HUBPROTO_CAD_WORKER_URL`) for STEP, PDF, Gerbers, ERC and DRC, files
every export as evidence, and raises error-severity problems when a check
exceeds `The design rule error limit is N percent.` `bom-reconciler` matches
BOM lines to `item` records by MPN and manufacturer and attaches proposals for
unknown lines. `part-releaser` creates or revises the `part` after the design
authority's approval. The worker answers with synthetic exports when
`HUBPROTO_CAD_FAKE=1` (tests, and `CAD_TOOLS=none` images).
