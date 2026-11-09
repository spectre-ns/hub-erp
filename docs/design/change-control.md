# Design: configuration changes through change orders (change control v2)

Status: agreed with the product owner on 2026-11-09, being built on branch `change-control-v2`.
This note is the specification every workstream builds from, and the source the user manual is
updated from. Where the code and this note disagree after the build, one of them is wrong: say which
in the PR.

## 1. Principles

1. **SOPs are the single source of truth.** Every business process, every access permission (for
   people and cogs alike), every limit, route, approval criterion, segregation-of-duties rule and
   workflow is stated in a controlled SOP, work instruction or policy, written in **prose**.
2. **The software runs on approved configuration, never on prose.** A model reads prose only at
   change time and *proposes* configuration. People approve it. The release freezes the approved
   configuration together with the text. No model is in the enforcement path.
3. **No configuration change without a change order (CO).** Rules, workflow, data model (record
   types, fields), form layouts, cog profiles and personas all change only through a CO. Every
   other path that changes configuration refuses and says to use a change order.
4. **The reviewed object is the configuration delta**: the list of configuration changes a CO would
   make against the release in force, each with where it came from.
5. **Humans make the final decision.** Cogs compile, review, ask and suggest. Humans decide each
   delta item, may override with an accepted warning, and sign the CO.
6. **Cogs are members like people.** They hold roles; their authority is what the SOPs give their
   roles (and them by name), within the guards. Be explicit: never hand a cog outsized permissions.

## 2. What lives where

| Kind | Examples | Home | Changes by |
|---|---|---|---|
| Rules | members and roles, folders, permissions, thresholds, routing, due dates, approval criteria (cog rails), segregation of duties, duties, processes/workflows | the SOP or policy that owns the process; people, roles and folders in the org model | a revision of that document carried on a CO |
| Data model and presentation | record types and fields, custom fields, form layouts, templates | versioned configuration objects, named from the SOPs | carried on the same CO, released with it |
| Cog identity | a cog's bundled model, its persona | cog profiles (policy document) | a CO |
| Cog behaviour | tone, stance, how it reviews | persona documents | a CO |
| Installation | Ollama URL, ports, secrets, queue mode, backup, agents kill switch | environment | IT change control (IT SOP), outside the release; the release records what it ran against |

## 3. The change order, start to finish

```
 Prepare ──▶ Make the changes ──▶ Request review ──▶ Round n: compile, suggest panel, panel reviews
                ▲                                        │
                └────────── Revise (answer questions, accept/reject suggestions) ◀──┘
                                                         │ requester: Send for approval
                                                         ▼
                         Decide the delta (item by item, warnings accepted, widenings 2 owners)
                                                         ▼
                         Approve the change order (quality manager signs; carried revisions approved with it)
                                                         ▼
                         Release (automation): config in force, revisions effective, forms, training,
                         divergences recorded, every open CO recompiled, related changes sent back
```

### 3.1 Prepare
Any member of staff opens a CO (`CO-yyyy-n`, Quality/Changes) and links the change requests it
implements. The CO's **stage** field (not its record state) tracks the loop:
`preparing → in-review → revising → (in-review …) → awaiting-approval → approved → released`.
The record state follows the change-order process (open while preparing/reviewing/revising; the
approval Gate's state while awaiting approval; then approved, released).

### 3.2 Make the changes
A CO may carry, mixed:
- **Prose revisions** of SOPs, policies, cog profiles or personas (draft revisions, edited online).
- **Direct changes**: an edit made in Settings, the forms editor (and later the workflow graph
  editor). A direct change is stored on the CO as a delta item with origin `direct` (who, when,
  what). It is **not** written into the prose by Settings itself: the qa-manager cog drafts the prose
  for it in round 1 (3.4).
- **Data-model changes**: record-type changes and form layouts (form layouts already work this way).

### 3.3 Request review → a round
The requester (or a document controller) presses **Request review**. A round always runs, in order:
1. **Compile** (first step of the first round; later rounds recompile only what changed since the
   last compile). See 4.
2. **Draft prose for direct changes**: the qa-manager cog writes, into the carried revision of the SOP
   the rule belongs to, the prose that states each direct change not yet stated. The committee then
   compiles that prose and checks it gives back the same item (round trip). A mismatch is a question.
3. **Suggest the panel**: the quality-assistant cog reads the change-control SOP's section on reviewing
   changes and the delta, and proposes reviewers, each with its reason. Recomputed every round.
4. The requester may add or remove reviewers (people or cogs) and confirm; the panel then reviews.
5. **Panel review**: each reviewer posts **findings** (blocking or advisory), **questions** and
   **suggested delta items**. A reviewer in round n>1 is given only what changed since its last
   round (delta of the delta), plus its earlier findings.

### 3.4 Revise
The author answers questions **in the prose** (the next compile closes a question when it no longer
arises; the author may also mark it answered with a pointer to the line), accepts or rejects
suggestions (an accepted suggestion becomes a delta item with origin `suggested`, accepted by whom),
then requests review again. Rounds repeat until the requester sends for approval. Unresolved
findings and questions do not block sending; they become warnings at approval.

### 3.5 Decide the delta
Approvers decide each item: **approve** or **reject**, with a meaning. An item that carries a warning
(unresolved finding, open question, round-trip mismatch, hand edit, divergence) is approved only by
ticking **I accept: ...** for each warning; the acceptance is recorded with the signature. A
**widening** (anyone may do more) needs both policy owners. Approvers may **override** an item (edit
its value); the override is a warning the approver accepts. An override **may leave configuration
and prose disagreeing**: that is a **divergence** (6).

### 3.6 Approve the change order
The change-order Gate goes to the quality manager. It may be signed only when every item is decided,
every warning accepted, the delta is current (compiled against the release in force, nothing stale)
and every implemented CR is approved and not flagged. **Signing it also approves every revision the
CO carries** (document control's approval moves into this step; the document-control signature is
recorded on each revision). Segregation of duties: the CO approver is not the author of a carried
revision and not the maker of a direct change on it.

### 3.7 Release (automation `release-change-order`)
Existing steps (check, frame into force, publish revisions, forms, training, tell) plus:
- **Record divergences** in the release, and raise a change request for each new one, to the SOP's
  owner ("bring <sop> line n in line with what is in force").
- **Apply state maps** (5.2).
- **Recompile every open change order** against the new release (mechanical; unchanged prose is not
  re-read by a model; human-made and accepted items are kept; items that no longer apply become
  questions). Tell each requester.
- **Find related changes and send them back**:
  - *Related by definition* (hub, no model): an open CO that touches the same SOP, process, rule
    slot, record type, form or role as the released CO. Cannot be dismissed.
  - *Related by meaning* (the released CO's panel cogs, each in its area, look at open COs and at
    CRs that are approved but not yet carried by a CO): a finding with a reason.
  - A related CO: stage back to `revising`, flag "baseline changed", its review results and item
    decisions marked stale; it cannot be sent for approval until another round has run. Its
    requester is told why.
  - A related CR: a review task to its approver with the cog's reason; the CR is flagged and no CO
    may be approved implementing it until the task is done.
  - The requester may dismiss a *by-meaning* flag with a reason and an accepted warning.

## 4. Compilation

- **Fast path, deterministic:** sentences in the shapes the grammar knows (DEV-NOTES.md, "Sentence
  grammar") are read exactly, no model.
- **Prose, by the compile-frame committee** (organization + qa-manager cogs, quorum as today):
  reading is **diff-based**. For a frame whose text differs from the version in force, the reader is
  given the unified diff with line numbers and the rules currently in force for that frame (and the
  processes it touches), and returns: rules to add, remove or change (each with line and exact
  quote), and **questions** where the prose is ambiguous ("larger bills go to the controller": over
  what amount?). An unchanged frame keeps the release's rules for it with no model call.
- Every guard still applies (grounding: the quote is at that line; schema; consistency; scope;
  widening needs two owners).
- The local model is `qwen3:14b` (`HUBPROTO_EXTRACTOR_MODEL` may name another installed model). If
  extraction quality is a problem, an administrator may switch hosted models on for compilation only
  (Claude Opus via the existing Claude extractor), with an API key; cogs keep their bundled models.
- Tests never call a model: the readers take a `call=` fake, and the stand-in (HUBPROTO_ALLOW_STAND_IN)
  answers deterministically.

## 5. Work in flight

### 5.1 Items
A work item's **state is unchanged** when a release comes into force. From its **next move** (a
decision, a retry, a reassignment, the next automatic step) it follows the **release in force at
that moment** (routing, limits, checks, next steps). A Gate already waiting keeps its assignees
until the item moves. The run's Track records each change of release. (Today a run pins the
release it started under; this changes.)

### 5.2 Removed or renamed states
When a delta removes or renames a state that items are in, the compile counts them and raises a
question ("4 nonconformances are in Awaiting disposition, which this change removes: where do they
go?"). The answer is a **state map** on the CO `{process: {old_state: new_state}}`, required before
approval. The release moves those items and records why on each.

## 6. Divergences

A divergence is recorded in the release: `{id, frame, line, prose, in_force (the rule), co,
accepted_by, meaning, at}`. It shows:
- on the SOP's page, in the viewer and in downloaded/printed copies: a marker at that line, "In force
  differs from this line: ... (accepted by bob on CO-2026-0007)";
- to cogs: any cog whose context includes that line gets the rule in force next to the prose;
- on the release diff and the Conformance page (a count, a list).

Compiling a later CO **keeps** an open divergence's in-force rule for that slot (it does not revert
it because the prose still says otherwise), with a warning naming the divergence, until a CO
resolves it (changes the prose to match, or changes the configuration back). Resolving closes it.

## 7. Cogs

- **Roles like people.** A cog holds roles in the org model. What it may do comes from the SOPs'
  `## Permissions` (for its roles, or naming it), routing, approval criteria and the guards.
- **Persona = a behaviour document**, shared by any number of cogs, cog-only: tone (formal or
  informal), stance (reviewer, executor, author, advisor), how hard it reviews (lenses, number of
  adversarial passes), when it escalates, and its behaviour prompt. Role personas go away: a role's
  required reading is derived (the SOPs that name the role).
- **Cog profile = identity**: name, a one-paragraph description, bundled model, persona.
  `Prompt:`, `Lenses:`, `Autonomy:`, `Follows:` and `Duty:` leave the profile (prompt and lenses to
  the persona; duties to the SOPs as sentences; follows and autonomy are derived).
- **What a cog follows (its context) is derived**: the company scope (org model, quality manual,
  tasks SOP) plus every SOP whose rules name one of its roles or the cog.
- **Autonomy is derived per action**: a cog **acts** where an SOP gives it (or its role) the step or
  the permission; **proposes** where an SOP says a person confirms; otherwise it only **reads**.
- **Cogs author** records and documents within what the SOPs and guards allow; they may fill in
  fields at a Gate their role may set (the old "a cog supplies no fields" limit goes).
- "A person signs a cog's work, never another cog" becomes an **SOP sentence** (segregation of
  duties), so it is configuration. "A cog never signs its own work" stays a **guard** (it binds
  people too).
- **Context by process**: at a Gate or on a task a cog gets its persona, the governing process's
  steps, its own routes and criteria there, the sentences they cite, any divergence on those lines,
  and a tool to read any other section of an SOP it follows. Not whole SOPs.
- **New members**: an IT cog (`it-admin`, role `system-administrator`, the shared executor persona,
  the new IT SOP as its context) and a management cog (`management`, role `management-reviewer`,
  follows the management-review SOP). **bob** also holds `system-administrator`.
- **New IT SOP** (`it-sop`): configuration changes only by CO; access management (who gets which
  roles, cogs included); validation of configuration deltas (risk-based, CSA style); backup and
  restore; incidents. Segregation of duties: whoever made a direct configuration change on a CO is
  not its only approver; an internal auditor does not audit system administration they perform.
- **Change-control SOP** gains a prose section on reviewing changes, which the quality-assistant
  reads to suggest the panel, e.g. "A change that touches permissions, record types or forms is
  reviewed by the it-admin cog. A change to roles, limits or segregation of duties is reviewed by the
  management cog. A change to a quality process is reviewed by the qa-manager and the
  quality-assistant." Its stand-in applies such sentences literally.

## 8. Data shapes (contracts between workstreams)

Proposal change (the delta item) gains:
```
"origin": {"kind": "prose"|"direct"|"suggested"|"override"|"divergence",
           "by": "<member>", "frame": "...", "line": n, "quote": "...", "read_by": "<cog>|null", "at": "..."},
"warnings": [{"code": "finding"|"question"|"round-trip"|"override"|"divergence"|"related",
              "text": "...", "ref": "<id>", "accepted": {"by", "meaning", "at"} | null}],
"stale": false
```
Proposal gains `"questions": [{"id", "text", "frame", "line", "raised_by", "status": "open"|"answered"|"overridden", "answer", "by"}]`,
`"co": "<CO number>"`, `"round": n`.

CO fields gain: `stage`, `rounds: [{"n", "requested_by", "at", "compiled": "<proposal id>",
"panel": [{"who", "kind": "cog"|"person", "reason", "suggested_by"}], "results": {"<who>":
{"status": "queued"|"done"|"failed", "findings": [{"id", "severity": "blocking"|"advisory", "text",
"about": {...}}], "questions": [...], "suggestions": [<delta item>]}}}]`, `direct_changes: [<delta item>]`,
`state_map`, `baseline_changed: {"by": "<CO>", "reasons": [...], "dismissed": {...} | null}`,
`record_type_changes`.

Release meta gains `divergences: [...]` (6) and records which CO released it.

## 9. Workstreams (who builds what)

| WS | Scope | Main files |
|---|---|---|
| 1 Change-order core | stages and rounds, panel suggestion and review, delta items with origin/warnings, item decisions, overrides, approval readiness, revision approval moved into CO approval, release steps (divergences, recompile all open COs, related changes, CR flags), change-control SOP text | `eco.py`, `automation.py`, new `hubproto/changecontrol.py`, `frames/iso9001/change-control-sop.frame.md`, `config/record-types.json` (change-order fields), tests |
| 2 Compiler | diff-based committee reading, questions, round-trip check, qa-manager prose drafting for direct changes, Settings edits as direct changes | `llm_extract.py`, `compile.py`, `sop_settings.py`, `settings.py`, tests |
| 3 Cog model | persona documents, identity-only profiles, derived follows and required reading, derived autonomy, context by process, new cogs/roles/IT SOP, bob as system administrator, new SoD sentences | `frames/**` (personas, cogs, org-model, scopes, it-sop), `extract.py` (persona/profile grammar), `release.py`, `scopes.py`, `authz.py`, `agent.py`, `agent_config.py`, `cogs.py`, `app.py` (propose_new_cog), tests |
| 4 Runner and closure | items follow the release in force from the next move, state maps, cogs may supply fields, "cog signs cog" as an SOP rule, no configuration path outside a CO (CLI compile/review/release, record types, frame-propose), record-type changes carried on COs | `runner.py`, `kernel.py`, `cli.py`, `app.py` (compile/release guards), tests |
| 5 Shell | CO page (stage, rounds, panel, results, questions, delta decisions with warnings/overrides, send for approval, related flags), Settings as direct changes, divergence markers on SOP pages, Conformance count | `hubproto/shell/**`, tests |
| 6 Manual | high-level workflow overview, chapters 3, 6, 7, 9, 10 | `docs/manual/**` |
| 7 Workflow graph engine | processes as graphs (statuses, transitions, gates human/cog/deterministic, validators, actions), grammar and compile, runner execution, items follow the release in force from the next move, state maps, cogs may supply fields, "cog signs cog" as an SOP rule | `runner.py`, `release.py` (workflows), `extract.py` (process grammar), `routing.py`, tests |
| 8 Workflow graph editor | a smooth graph editor and viewer modelled on a study of Jira's workflow editor (section 10); edits are direct changes on a CO | `hubproto/shell/**` (new workflow editor module), tests |

Workstream 4 therefore covers closure only (CLI, record types, compile/release guards); every
runner change belongs to workstream 7.

## 10. Workflow graphs

From the study `docs/design/jira-workflow-study.md` (read it: sections 4.1 to 4.5 are adopted as the
MVP unless this section says otherwise). Jira made editing smooth by making publishing cheap; ours is
deliberately not cheap (a change order), so smoothness comes from the draft, live checks and the
review view.

### 10.1 Model (contract between workstreams 7 and 8)
One graph per process (one process per record type, no schemes), in the release bundle at
`workflows[<process>]`:
```
{"process", "record_type",
 "statuses": [{"id", "name", "category": "todo"|"in_progress"|"done", "outcome": "completed"|"rejected"|"cancelled"|"superseded" (done only), "sla_days"?}],
 "transitions": [{"id", "type": "initial"|"directed"|"global", "from": [<status id>] (directed), "except": [...] (global; default the done statuses),
                  "to", "name",
                  "gate": {"kind": "person", "role", "not": ["author"|"<transition id>"]}
                        | {"kind": "cog", "cog", "fallback": "<role>"}
                        | {"kind": "auto", "when": "<closed-vocabulary condition>"|"actions-succeed"},
                  "validators": [<closed list: fields_set, form condition, status_visited, linked records>],
                  "actions": [<code step ids, notify, set field>], "fields": [<field names collected>]}],
 "source": {"frame", "lines"}}
```
- Status and transition **ids are stable** (a rename keeps the id and needs no state map).
- A failed action moves the item to the system `failed` status with a Retry loop. Today's returned,
  failed, rejected and cancelled exits become ordinary statuses; Cancel and Put on hold become globals.
- The **waiting decision at a status is derived**: one inbox Gate per status and decider, its options
  the gated transitions out of that status (buttons named by the transitions), the due date from the
  status. That is today's checkpoint, generalised.
- **Layout is presentation**, not policy: `{process: {status id: [x, y]}}` on an 8 px grid, a
  versioned configuration object carried on a CO like form layouts. Layout-only edits are one delta
  item "Layout changed: no effect on how items move".
- **Today's linear processes compile into this shape without changing meaning**; the whole existing
  suite must still pass.

### 10.2 Prose
The SOP's `## Process:` section stays the source. The numbered-steps form remains valid (a linear
shorthand). A graph form adds sentences the fast path reads exactly, for example:
```
Statuses: Open (to do), Awaiting disposition (in progress), Closed (done, completed), Cancelled (done, cancelled).
From Open to Awaiting disposition: automatically, after the classifier, the filer and the risk scorer.
From Awaiting disposition to Closed ("Disposition"): the quality manager decides, not the author; needs disposition; then the disposition applier and the CAPA opener run.
From Awaiting disposition to Open ("Return"): the quality manager.
From any status except done to Cancelled ("Cancel"): the quality manager.
```
Workstream 7 fixes the exact grammar; workstream 2's prose drafter writes these sentences as its
stand-in, so editor changes round-trip.

### 10.3 Checks (live in the editor, and at compile)
Errors: no initial transition; an unreachable status; a non-done dead end; a role nobody holds; a cog
gate without approval criteria; overlapping or gapped auto transitions out of one status. Warnings:
widening; removing a status items are in; more than about 25 statuses. **Widening** includes a gate
weakened (person → cog → auto), a validator or SoD rule removed, a global transition added, and a
**mandatory gate bypassed** (every path from the initial transition to a `completed` status used to
cross a person or cog gate and no longer does; the warning shows the path).

### 10.4 Delta and visual diff
Graph changes become delta items keyed by element id (status or transition added, removed, renamed,
recategorised, rerouted; gate, validator, action or field changed), in words; a removed status is one
parent item with its transitions as children. The CO page and the editor show **one drawing of old and
new together** (added "+", removed dashed and struck "−", changed "~", rest muted; never colour alone),
with a linked Changes list and Before/After views.

### 10.5 Editor (MVP)
Server-rendered inline SVG for the read-only graph on record pages (current status, path taken) and in
Settings, **replacing Mermaid** (the shell's only CDN asset). The editor: toolbar, canvas and side panel
on one page; Add status / Add transition dialogs (several From, Any status); drag from a handle to
connect; click to select; separate curves for opposite transitions, arcs for loops, draggable labels;
gate badge on each edge; every rule shown as a sentence; checks after every edit naming and selecting
the element; Tidy (deterministic layered layout, undoable); zoom/pan/fit; undo/redo over an operation
log; one autosaved draft per person and process, **Carry on a change order**; a complete no-JavaScript
Text view; keyboard operation and accessible names for every element. State map asked at delete time
in the editor, shown again on the CO page with item counts and consequences, applied at release
without running rules, with pending decisions withdrawn explicitly and the move recorded on each item.

### 10.6 Ownership
- **Workstream 8 owns `hubproto/workflow_model.py`**: pure functions over the 10.1 shape: `validate`,
  `checks` (10.3, including the bypass search), `diff(old, new) -> delta items` (10.4), `tidy(graph)`,
  `apply_op(graph, op)` for the editor operations, and `from_linear(...)` helpers if useful. Plus the
  SVG renderer, the editor, drafts, carrying on a CO, the visual diff and the state-map UI.
- **Workstream 7 owns** the 10.2 grammar and compile into the 10.1 shape, the runner executing graphs
  (derived Gates, options, auto transitions, failed/Retry, globals), items following the release in
  force from the next move (5.1), applying state maps at release (5.2), cogs supplying fields, and "a
  person signs a cog's work" as an SOP rule. It calls `workflow_model.checks` lazily if present.

Rules for every workstream: stdlib only; keep the full suite green (`python -m pytest -q`); update
tests that encoded the old behaviour rather than deleting them; no model calls in tests; say in the
final report every place you needed another workstream's code and what you assumed.

## 11. Groups and committees (agreed 2026-11-09; workstream 9, after workstreams 3 and 7 merge)

- **Groups** are named sets of members, people and cogs, defined in the org model and changed by a
  change order like everything else. A group is listed ("quality board: carol, bob, qa-manager") or
  defined by role ("everyone holding quality-manager", following role changes). **Groups may carry
  permissions** (e.g. "Administrators may ...") as roles do, and are also simply a way of grouping
  members. Permission checks consider a member's roles and groups; the per-cog permissions listing
  (principle 6) includes what a cog gains through a group.
- **Committees** are defined on a group and **decide by consensus**: an item passes only when every
  member agrees; an objection with a reason blocks it and returns it for revision. Committees are
  used as a gate kind in workflow graphs, as reviewers on a change order's panel, and as the
  compile-frame committee (whose members' readings must agree; a disagreement becomes a question).
- Created and edited in Settings > People and roles > Groups and committees; each edit is a direct
  change on a change order.
- **No standing aside**: every member must agree; there is no abstention.
- **Human intervention** when a cog member's approval criteria do not hold, and when a member has not
  responded by the due date: the committee decision pauses and goes to a person who intervenes
  (proposed: each committee names a person as its chair; the chair may decide in that member's place
  with a recorded meaning, extend the due date, or return the item). Nothing passes by default.

### 11.1 Senior and junior cogs; the compile Op (agreed 2026-11-09)

- **Seniority distinguishes models.** A senior cog is bundled with `claude-opus-5-5`; a junior cog
  with `qwen3:14b` (local). Groups `senior-cogs` and `junior-cogs` carry what the SOPs give each.
- **Compilation is an Op** run by the existing Op machinery: the `compile-frame` automation, started
  as the first step of a review round (section 3.3), each step recorded and retryable.
- **The compile Op's committee is composed of senior cogs**: the compile-frame committee is defined on
  the `senior-cogs` group and decides by consensus. Initial seniors: `senior-organization` (knows the
  organization; reads SOPs into rules; signs nothing) and `senior-qa-manager` (the quality system),
  new cogs on Opus with the existing personas. The existing `organization` and `qa-manager` stay as
  juniors on qwen for day-to-day work (chat, drafting, reviews).
- Seniors are usable only where the administrator has switched hosted models on
  (`HUBPROTO_HOSTED_MODELS`, `ANTHROPIC_API_KEY`, IT SOP); an unavailable senior means the committee
  cannot reach consensus and a person intervenes (a `member-unavailable` question).

## 12. Roles and responsibilities, and required training (agreed 2026-11-09)

- **Every document ends with a Roles and responsibilities table** (`| Role | Responsibilities |`;
  a row may name a role, a group or a cog). **The table defines who must read the document**: a
  person's required reading and a cog's context are the documents whose tables name their roles (or
  the cog). This replaces deriving them from any mention of a role.
- The compile warns when an SOP gives a role or cog a step, route, criteria, duty or
  create/approve/update permission that its table does not list, and when a table names a role
  nobody holds.
- **A required-training document** (training matrix, Quality/Training) summarises the links: per
  role, each document to read, its revision in force and the responsibility. It is generated from the
  tables and republished by the release automation when a table or a listed revision changes.
  Training acknowledgement tasks are assigned from the tables when a revision becomes effective.
- **Committees exist to bring differing perspectives with different contexts**: each member works
  from its own persona and responsibilities; the shared material (diff, rules in force, output
  format) is the same, so differences surface as disagreements for a person to resolve.
