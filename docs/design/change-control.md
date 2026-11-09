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

To be written from the study of Jira's workflow model and editor (statuses, transitions, rules,
editor interactions, migration of items in removed statuses), before workstreams 7 and 8 start.

Rules for every workstream: stdlib only; keep the full suite green (`python -m pytest -q`); update
tests that encoded the old behaviour rather than deleting them; no model calls in tests; say in the
final report every place you needed another workstream's code and what you assumed.
