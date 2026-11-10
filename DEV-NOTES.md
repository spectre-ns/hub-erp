# Developer notes (read before changing anything)

This is the from-scratch prototype of the Intelligence Hub ERP plan. Standard
library Python 3.12+, SQLite, files. No new runtime dependencies without a
reason written in the PR. Tests: `python -m pytest -q -n auto` (all must pass; `pip install -r requirements-dev.txt`
for pytest-xdist, a development-only dependency; without `-n auto` the suite runs serially and must pass that way too).

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
| `hubproto/runner.py` | Op runner = workflow engine walking process graphs; derived Gates and options, grants, Track, tick (SLA escalation), following the release in force, state maps | core |
| `hubproto/process_graph.py` | processes as graphs (change control 10.1): compile numbered steps and graph sentences into one shape, read it (options, verdicts, globals), write it back as sentences, the earlier shape's view | core |
| `hubproto/cogs.py` | Cog catalog (code cogs, context stand-ins, reviewer cogs) | core |
| `hubproto/groups.py` | groups (listed, by role) and committees (on a group, a chair, consensus): who is in each, resolving a role-like name to a group | core |
| `hubproto/org_cog.py` | the organization cog (junior) and the senior cogs of the compile-frame committee | core |
| `hubproto/inbox.py` | DMs and email to people and cogs | core |
| `hubproto/cad.py` | CAD/ECAD: FreeCAD and KiCad stdlib extraction, worker client, cad-exporter, bom-reconciler, part-releaser cogs | core |
| `hubproto/worker.py` | the CAD worker (its own image, docker/cad-worker): /invoke envelope seam over FreeCADCmd and kicad-cli, fake mode | core |
| `frames/engineering/design-control-sop.frame.md` | design-release process (submit, export, reconcile BOM, risk, review, release part) | core |
| `hubproto/app.py` | `Hub` wiring | core |
| `hubproto/changecontrol.py` | the change-order loop (change control v2): stages and review rounds, panel suggestion and reviews, the delta (origins, warnings, item decisions, overrides), send/approval readiness, revision approval with the CO, release steps (divergences, related changes); the API the CO pages use | core |
| `hubproto/closure.py` | no configuration change without a change order once the SOPs are documents: the guards `Hub.compile`, `review`, `release` and `frame_draft` call; the sentence (`CO_ROUTE`) | core |
| `hubproto/recordtypes.py` | record types under change control: the record types in force (`state/record-types`), changes carried on change orders (`carry`, `carried_reasons`, `delta_items`, `activate`), the file's drift | core |
| `hubproto/cli_dev.py`, `hubproto/dev_demo.py`, `hubproto/__main__.py` | `python -m hubproto`; CLI verbs `dev serve`, `dev list`, `dev stop`, `dev clean` (`register(sub)`): development hubs on localhost, the registry, ports; the demo data a served hub gets | dev tooling |
| `hubproto/cli_recordtypes.py` | CLI verbs `record-types status`, `show`, `carry` (`register(sub)`) | cli agent |
| `hubproto/cli.py` | command line: policy, run, message and operations verbs; `main` returns the exit code; shared helpers (`fail`, `clean`, `require_member`, `resolve_*`) | cli agent |
| `hubproto/shell/` | the workspace shell package (handler, pages, inbox, forms, actions, policy, css, text; stdlib http.server) | shell agent |
| `hubproto/cli_records.py` | CLI verbs for runs waiting on people, records and documents (`register(sub)`) | cli agent |
| `docker/entrypoint.sh`, `README.md`, `tests/test_cli.py` | container first start (filling the empty frames volume from the image's Frames), later-start drift warning; operator docs; the CLI suite | cli agent |
| `docker-compose.yml`, `docker-compose.frames-dir.yml`, `Dockerfile` | the services; the hub's Frames in the `hub-frames` volume, never the checkout (issue #5); the development override that mounts the checkout's `frames/` and `profiles/` | cli agent |
| `hubproto/profile.py` | seed profiles: install, conformance checklist (`register(sub)`) | profile agent |
| `profiles/iso9001/**` | the ISO 9001 seed profile | profile agent |
| `frames/**` | the example company's Frames | core (profile agent may add `frames/iso9001/` via the seed command) |
| `config/*.json` | record types, link vocabulary, retention classes | core (profile agent may add record types; tell core) |
| `tests/test_compile.py`, `tests/test_runtime.py`, `tests/conftest.py` | existing suites | core |
| `tests/test_records.py`, `tests/test_workflow.py`, `tests/test_dms.py` | new suites | test agent |
| `tests/test_profile.py` | seed profile suite | profile agent |
| `tests/test_shell.py`, `tests/test_shell_ux.py` | shell smoke suite; UX-panel regression suite over HTTP | shell agent |
| `tests/test_cli_dev.py`, `requirements-dev.txt` | the `hubproto dev` suite; the development-only dependencies | dev tooling |

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
  `blobs/`, `inbox/`, `proposal-numbers.json` and the hub's `frames/` (less what a restore set aside), with a
  `backup.json` manifest; `restore <dir>` refuses a partial set and an existing state without `--force`, and puts the
  backup's Frames in place of the hub's (those set aside in `frames/.before-restore-<time>/`; an older backup without
  Frames leaves them alone). `status --check` exits 1 when no release is in force or the Frames
  on disk differ from the release's bill of materials (the entrypoint prints that on later starts); once the SOPs are
  documents, also when `config/record-types.json` differs from the record types in force or another config file from
  what the release recorded.
* Once the SOPs are documents a configuration change outside a change order is refused by the hub
  (`closure.ChangeOrderRequired`, a `CompileError`): the verbs exit 1 with a sentence ending "Configuration changes only
  through a change order: open one under Quality > Change orders." (`compile`, `review`, `release`,
  `bootstrap-release --force`, `seed`). See `docs/design/config-paths.md`.
* A hub's **first release converts the SOPs into documents** at once (`closure.after_release` ->
  `sopdocs.convert_after_release`), however it is made (`bootstrap-release`, the entrypoint's first start, `release`, the
  shell): on a new hub the first release is the only change released without a change order. `bootstrap-release` says
  what it converted; a blocked conversion exits 1 with why, `status --check` exits 1 until it is done, and running
  `bootstrap-release` again finishes it. The opt-out is explicit and recorded: `bootstrap-release --keep-frame-files
  REASON` or `sopdocs.keep_frame_files(hub, by=, reason=)` (state/frame-files-kept.json, shown by `status`) keeps the
  Frame files as the source (the pre-conversion mode); `sops convert` ends it.
* Tests: `tests/conftest.py`'s `release_all` opts out (`keep_frame_files=True`, the default) because most suites test
  the Frame-file workflow (edit a Frame, compile, review, release), and so does every fixture built on it (`released`,
  and the suites that call `sopdocs.convert` themselves afterwards). Pass `keep_frame_files=False`, or bootstrap with the
  CLI, for the converted default (`tests/test_config_closure.py`).

## Running hubs for development and for agents

Never use the hub at http://localhost:8080 (Docker) for development, and never bind 8080, 9980 or 11434. Give each
workstream its own worktree and its own hub:

```bash
git worktree add ../hub-erp-proto-wt/ws12-foo -b cc/ws12-foo change-control-v2
cd ../hub-erp-proto-wt/ws12-foo
python -m hubproto dev serve --detach --name ws12-foo   # prints the URL, e.g. http://127.0.0.1:8103/
python -m hubproto dev list                   # name, port, pid, branch, commit, started, url, worktree, state dir
python -m hubproto dev stop ws12-foo          # state kept; `python -m hubproto dev clean ws12-foo` removes it
```

`python -m hubproto` runs the command line from the code in the current directory; the venv's `hubproto` script runs
the checkout the venv installed (`pip install -e`), which may not have `dev` yet. Either way the served hub runs the
`--worktree`'s code (default: the git checkout you are in).

* `dev serve` builds a **fresh** hub from that worktree's code (child processes with `PYTHONPATH=<worktree>`, its
  `.venv` interpreter when it has one) and Frames, as the container's first start does: `init`, `seed iso9001`, the
  first release by bob and carol (the SOPs become documents), then with `--seed demo` (the default) an open NCR waiting
  for carol, a bill under the ap-reviewer limit (the cog approves it, the ledger posts it) and an open change order
  alice prepared carrying one revision of the finance policy (`hubproto/dev_demo.py`). `--seed empty` skips the demo
  data, `--keep` reuses the hub already in the state directory, `--port N` picks the port (default: the first free one
  from 8100), `--state-dir DIR` puts the hub elsewhere. It takes about 3 seconds.
* Cogs answer with the deterministic stand-in (`HUBPROTO_ALLOW_STAND_IN=1`); `--models ollama` uses the local Ollama
  instead (slow; only when the work is about model behaviour).
* State: `~/.cache/hubproto-dev/<name>/` is the hub root (`frames/`, `state/`, `hub.log`, `hub.pid`); the CLI verbs
  work on it with `hubproto --root ~/.cache/hubproto-dev/<name> <verb>`. `HUBPROTO_DEV_HOME` moves the whole tree (the
  tests use it). The registry (`registry.json`) is written under a lock, so agents starting hubs at the same time never
  get the same name or port; `dev list --json` is the machine-readable form.
* In a browser or a headless one (Playwright MCP, `curl`): open `<url>?as=carol`; every link carries `?as=`, so the
  session stays that person. `<url>healthz` answers `ok` once the shell is up. The Playwright Chromium under
  `~/.cache/ms-playwright` lacks `libnspr4` on the shared dev machine, so use a browser tool, not its CLI.
* Foreground (no `--detach`): Ctrl-C stops it. Stop yours by name when the workstream is done, then `dev clean` it;
  `dev stop --all` and `dev clean --all` act on every instance in the registry, other agents' too.

### Tests in parallel

`python -m pytest -q -n auto` runs the suite on every core (pytest-xdist; `-n 8` on a shared machine): about 40 s
against 3.5 minutes serially on the 20-core development machine (988 tests; `-n 8` takes about 42 s). Each worker is
its own process, and a test may run on any worker in any order, so:

* a test never depends on another having run first: a module-scoped hub (the `shell` fixtures) is set up with what its
  tests read, not filled in by an earlier test;
* a server binds port 0 and reads the port back (`serve_in_thread(hub, "127.0.0.1", 0)`, then
  `server.server_address[1]`), never `free_port()` then bind, which races with the other workers;
* files go under `tmp_path`, environment changes through `monkeypatch`, the date through the `today` fixture.

To check a suite for order dependence, run it reversed per file (a plugin with `items.reverse()` and
`--dist loadfile`) and each test on its own.

## The model in one paragraph

Frames (Markdown + YAML frontmatter) hold policy prose. `hub.compile()`
extracts rule statements (each cites its sentence), runs Guards, and makes a
proposal; policy owners (members with role `policy-owner`) approve each
change (widenings need two); `hub.release()` renders the **policy release**:
members, roles, folders, permissions, thresholds, routing, approval criteria,
segregation of duties, processes, generated Op manifests (`ops`) and
**workflows** (one graph of statuses and transitions per process). A run
creates a record (type from the workflow) and walks the graph: an automatic
transition runs its actions (cog steps) in order; at a status with gated
transitions the item waits at a Gate routed to a person or a cog (the record's
state is the status, e.g. `awaiting-<checkpoint-id>` for numbered steps) with
assignees and a due date; a decision (`runner.decide`, or `runner.choose` with
a transition id) is the electronic signature and takes one of the Gate's
options; a failed action moves the item to `failed` (Retry resumes it); a done
status closes it. From each move on, a run follows the release in force then
(it no longer pins its starting release); the Track records each change. Everything is on the
run's Track (`state/runs/<id>/track.jsonl`) and on the record's events.

## Sentence grammar (convention mode)

Only inside these sections: `## Roles`, `## Members`, `## Groups`, `## Folders`,
`## Permissions`, `## Thresholds`, `## Routing`, `## Approval criteria`,
`## Segregation of duties`, `## Duties`, `## Process: <name>`, and the keyed sections
`## Scope:`, `## Committee:`, `## Cog:`, `## Persona:`. Frontmatter may carry
`compile: convention|guided|none` (default convention) and `clauses: [...]`. Every document ends with
`## Roles and responsibilities`, a table `| Role | Responsibilities |` (a row names a role, `the <name> cog` or
`the <name> group`); it is read even with `compile: none` and says who must read the document.

```
## Roles            - quality-manager
## Members          - alice (person): finance-clerk, staff      - ap-reviewer (cog): payables-reviewer
## Groups           - quality-board: carol, bob and qa-manager                  (listed members, people and cogs)
                    - quality-managers: everyone holding quality-manager       (by role: follows role changes; "or" for several roles)
                    - reviewers: dave and everyone holding reviewer            (both)
## Folders          - Quality/Nonconformances
## Permissions      Quality managers may approve nonconformances in Quality/Nonconformances.
                    Finance clerks may not approve bills in Finance/Payables.
                    Controllers may read Finance.
                    Staff may comment on anything in Quality.                 (anything = every object)
                    The it-admin cog may not approve documents in Policy.     (a permission naming one cog: subject cog:it-admin)
                    The quality-board group may read Quality.                 (a permission for a group: subject group:quality-board)
                    Administrators may read Policy.                           (a plural that names a group, not a role, is the group's)
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
                    A cog's work is signed by a person.      (or "A cog's work on a <object> is signed by a person.": a cog never signs an
                                                              item another cog prepared or wrote; a cog never signs its own work is a guard)
                    Whoever made a direct configuration change on a change-order is not its only approver.   (only: true)
## Duties           Every day|week|month the quality-assistant cog finds nonconformances that ... .   (a cog's standing goal)
## Committee: design-review   Group: design-reviewers   Chair: bob (a person, required)   Purpose: <what it decides>
                    (decides by consensus; "Members:" and "Agree: n of m" of earlier texts are still read: Agree is not used)
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

### Processes as graphs (change control 10.2)

A `## Process:` section may instead state its statuses and transitions (not
both forms in one section). The fast path reads these sentences exactly:

```
## Process: nonconformance
Records are filed in Quality/Nonconformances.
The record type is ncr.
Statuses: Open (to do), Awaiting disposition (in progress, due within 5 days), Closed (done, completed), Cancelled (done, cancelled).
Starts in Open ("Open the nonconformance"): the staff; needs title and description.
From Open to Awaiting disposition: automatically, after the ncr classifier, the record filer and the risk scorer.
From Awaiting disposition to Closed ("Disposition"): the quality manager decides, not the author; needs disposition; then the disposition applier and the capa opener run.
From Awaiting disposition to Open ("Return"): the quality manager.
From any status except done to Cancelled ("Cancel"): the quality manager.
```

* `Statuses:` lists `Name [id] (category[, outcome][, due within N days])`, comma-separated. Category: `to do`, `in progress`
  or `done`; a done status has an outcome `completed` (default), `rejected`, `cancelled` or `superseded`. The id is the
  name's slug unless `[id]` names it, so a rename keeps its id. It must come before the transitions.
* `Starts in <status> ("<Name>"): <the role>[; needs <fields>][; then <actions>].` is the initial transition: who starts
  the process and what the start form needs. Its name gives the object ("Open the nonconformance" -> `nonconformance`);
  without it the item starts in the first status, anyone who may create the object starts it, and the object is the
  record type's.
* `From <status>[, <status> and <status>] to <status>[ ("<Name>"[ [id]])]: <gate>[; needs <fields>][; only if <checks>]
  [; then <actions>[ run]].` is a directed transition (`from` equal to `to` is a loop); `From any status[ except done |
  except <statuses>] to <status> ...` is a global one (Cancel, Put on hold). The id is the name's slug, `[id]` when two
  share a name, or `<from>-to-<to>` (`any-to-<to>`) when unnamed.
* Gates: `the <role words>[ or an approver][ decides][, not the author | not the <field role> | not whoever decided
  "<Name>"]` (a person; field roles: the owner, assignee, author, requester, auditor, chair, evaluator, reporter; "or an
  approver" adds anyone who may approve the object, as numbered steps' Cancel and Retry); `the <cog id words> cog
  decides, else the <role words>` (the cog decides within its approval criteria, else the role); `automatically[ when
  <condition>][, after <actions>]` (conditions from the closed vocabulary; with no condition it fires when its actions
  succeed). Auto transitions out of a status are tried in order; the first that holds fires, else the item waits at the
  Gate there.
* `needs <fields>` collects those fields at the decision and checks they are set (a validator). `only if <status> was
  visited` or `only if <condition>` are further checks. A check that fails blocks the move with a sentence.
* Actions, in order: `the <cog id words>` (a cog step: "the record filer" is `record-filer`), `the <cog> with the <cog>`
  (a swarm: they review together and their findings merge), `notify the <role words | owner>`, `set <field> to <value>`.
  A failed action moves the item to the system status `failed` with a Retry loop. `From Returned to <status>
  ("Resubmit"): the owner; then <actions> run.` is a rework loop: a rejection to Returned waits for the owner to rework the
  item, and resubmitting runs those actions again.
* Ids: a status or transition is named, and its id is the one its name gives (numbered steps compile to such ids too),
  so prose carries no `[id]`. A rename keeps its id through a map beside the prose, in the frame's front matter:
  `workflow_ids: {<process>: {<id the new name gives>: <id kept>}}` (`process_graph.id_map(graph)` gives it;
  `to_sentences(graph, ids=False)` writes the sentences without ids). `[id]` in prose still reads, for hand-written text.
* Every graph the compiler produces, numbered steps included, writes back as these sentences and reads back to the same
  graph (`tests/test_workflow_graph.py::test_every_repository_process_round_trips_through_its_graph_sentences`).
* A change order's approval (any gate kind: a person, a committee, a cog, automatic) is always a person's, and only when
  the change order is ready (`changecontrol.approval_reasons`); a cog may review it, never approve it.
* Routing and SLA sentences name a decision by its transition name ("At Disposition in nonconformance with low risk go
  to the quality-assistant cog."; "Approvals in ..." is the first transition whose id starts approv/accept/confirm/authori).
* `the <name> committee decides` is a committee gate (`{"kind": "committee", "committee": "<name>"}`): every member of
  the committee votes (consensus). A status decided by a committee needs a way back for an objection, also decided by the
  committee (`From Review to Draft ("Return"): the design-review committee decides.`); the compile says so otherwise.
  ("the design review committee" without "decides" is still a person gate of the role `design-review-committee`.)
* `hubproto.process_graph.to_sentences(graph)` writes a graph back as these sentences (what a direct change says).

### Groups and committees (change control 11; `hubproto.groups`)

* **Groups** (`## Groups` in the org model): `bundle["groups"][name] = {members (listed), roles (everyone holding),
  resolved, source}`; `groups.group_members(bundle, g)` resolves it now (listed first, then role holders by name), so a
  group by role follows role changes. A permission or a route naming a group is the group's (`group:<name>`; a plural
  that is not a role but names a group is resolved at compile, `groups.resolve_group_refs`). `authz.check` and
  `authz.autonomy` consider roles and groups and say "(through the x group)"; `authz.permissions_of(bundle, member)` lists
  every permission that reaches a member with `via` (role, group or name): the per-cog listing. A table row
  `| the <name> group | ... |` makes its members read the document (`release.principal_names` includes groups;
  training assigns acknowledgements to the people in it; the matrix has a section per group). A group may not share a
  name with a role; a group change is a widening (two policy owners).
* **Committees** (`## Committee:`): `bundle["committees"][name] = {group, chair, purpose, members (resolved now), quorum
  (= every member), listed, agree (earlier texts)}`; `groups.committee(bundle, name)` resolves it. Guards: the group
  exists, the chair is a person of the organization, someone is in it; the compile-frame committee's members are cogs.
* **At a gate** (`hubproto.runner`): `pending["committee"] = {name, group, chair, purpose, members, types, votes{member:
  vote}, paused[{member, kind: criteria|unavailable|excluded|late|differ, reason, interveners, who}], interveners, due}`,
  `pending["assignee_type"] = "committee"`, `pending["assignees"]` the members still to vote (and, while paused, whoever
  intervenes). A person votes with `runner.decide(run, member, "approve"|"reject", meaning=..., payload_digest=...)` (or
  `choose` with a committee transition); each vote is a signed `gate.vote` on the Track. A cog member votes through
  `committee_cog_vote` (its queue's gate task): it agrees where its approval criteria hold; otherwise (criteria, not
  available, kept out by segregation of duties, not trained on the SOP) the decision pauses (`gate.paused`) and the chair
  gets an `intervention` notification. Every member agreeing on one way forward moves the item (`gate.decided` by
  `committee:<name>`, `by_type` committee, with `votes` and `voters`); one objection (reject, with a reason) returns it at
  once; there is no abstention. `runner.intervene(run, chair, "decide"|"extend"|"return", member=, meaning=, verdict=,
  due=, payload_digest=)`: decide in the member's place (a vote signed by the chair, with `intervention`), extend the due
  date (late members and paused cogs are asked again) or return the item. `tick` pauses the decision for each member late
  past the due date and escalates a pause past due under the overdue rule; when the chair is the member in question,
  absent (not a person of the organization) or kept out of the item, the pause goes at once to the people the overdue
  rule names ("Overdue items in <process> go to ..."); with nobody, the run fails ("nobody can intervene"). A vote given
  on an earlier version of the item no longer counts (`gate.vote_voided`).
* **On a change order's panel** (`hubproto.changecontrol`): a review sentence may name a committee ("... is reviewed by
  the design-review committee."); every member is on the panel with `committee`. `committee_reviews(hub, co)`: agreed,
  objected (a blocking finding), waiting or paused (a member's review failed: its chair is told and may
  `intervene_review`); a committee that has not agreed is a reason the change order cannot be sent for approval.
* **The compile-frame committee** is defined on the `senior-cogs` group (cogs bundled with `claude-opus-5-5`, change
  control 11.1); `hub.compile_committee()` gives its members with their models, those unavailable (hosted models off, no
  key) and its chair; an unavailable member is a `member-unavailable` question naming the chair, never skipped.

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
hub.runner.decide(run_id, "bob", "approve"|"reject", meaning="...", fields={...})   # or transition="<id>" (verdict None)
hub.runner.choose(run_id, "bob", "<transition id>", meaning="...", payload_digest=..., fields={...})  # an option, Retry, Resubmit, a global
state["at"], state["pending"]["options"]   # the status; the Gate's options [{id, name, to, verdict, fields, assignees? (another decider)}]
from hubproto.runner import apply_state_map, items_in_removed_statuses   # change control 5.2 (the release automation calls the first)
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

Cogs have no private memories (change control 13): a cog's judgment for an approval is filed as a review record on
the item (evidence, with `judgment: {cog, key (the version it judged), verdict, findings}`) and read back only from
there (`ReviewerCog._judgment_on_item`); a failed judgment binds for that version of the item. Adversarial lens reviews
likewise: one review record per lens (`lens_review: {cog, key, lens, passes, ...}`) and the adversarial reviews as one
(`adversarial: {cog, key, state}`), read back by `ReviewerCog._lens_results`; nothing is kept in `state/judgments`.

Cog ids available for process steps: record-filer, record-linker, notifier,
document-renderer, risk-scorer, invoice-extractor, three-way-matcher,
ledger-poster, document-drafter, document-publisher, ncr-classifier,
capa-opener, ncr-opener, capa-drafter, effectiveness-scheduler,
audit-checklist-drafter, document-classifier; reviewer cogs (members):
ap-reviewer, doc-reviewer, quality-assistant.

Workflow bundle shape (change control 10.1): `bundle["workflows"][process] = {process, record_type, form: steps|graph,
statuses[{id, name, category: todo|in_progress|done, outcome (done), sla_days?, system? (failed)}],
transitions[{id, type: initial|directed|global, from[] (directed), except[] (global), to, name,
gate {kind: person, role, not[]?, also?} | {kind: cog, cog, fallback} | {kind: auto, when: actions-succeed|<condition>},
validators[{fields_set}|{status_visited}|{condition}], actions[<step id> | {set} | {notify}], fields[], system?}],
source {frame, lines{element id: line}}}` plus a derived view of the earlier shape for its readers: `object, states,
checkpoints[{id,title,role,state,sla_days}], terminal, reject_to, sla{checkpoint: days}` (`process_graph.graph_only` drops
it; `release.legacy_transitions(wf)` gives the earlier `transitions[{from,to,kind,steps|step,title,on_reject,sla_days}]`).

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
