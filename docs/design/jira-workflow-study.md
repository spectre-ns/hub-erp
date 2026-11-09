# Study: how Jira models and edits workflows (input to change-control section 10)

Status: research note, 2026-11-08, for workstreams 7 (graph engine) and 8 (graph editor). Sources are
numbered at the end: Atlassian documentation and the Jira Cloud REST API are primary; tickets and
community threads are evidence of user pain. Jira Cloud now says *work items* for issues and *spaces*
for projects; this note does too.

## 1. Jira's workflow model

**Workflow.** A set of statuses and transitions. Transitions are one-way (back and forth takes two),
and a transition may loop to its own status to collect input or run actions [1][3].

**Statuses** are shared objects: one status can sit in many workflows, and renaming it renames it
everywhere, which also changes reports keyed by name [2]. Each belongs to one of three fixed categories,
To Do (grey), In Progress (blue) or Done (green), which drive lists, boards and reports [2]. Whether an
item is open or closed depends on its *Resolution* field, not its status [3]. Since October 2026
team-managed spaces can reuse organisation-wide statuses, to stop duplicates fragmenting reports [32].

**Transitions** have three types in the API [8]: `INITIAL` ("Create", the start point, whose post
functions create the item [7]); `DIRECTED` (one target, one or more sources, a "shared transition" with
one set of rules [6]); and `GLOBAL` (from any status, set on the target with *Allow all statuses to
transition to this one* [4]). "Any status → itself" is a looped action available everywhere [3].

**Rules** come under four verbs in the editor [5][6]:

| Verb | Old name | Effect |
|---|---|---|
| Restrict transition | conditions | Who is offered the transition, and when. Groups combine with `ALL`/`ANY` and nest [8]. |
| Request input | screen | A form of fields shown during the transition. |
| Validate details | validators | Checked on submit; on failure the item does not move and no action runs. |
| Perform actions | post functions | Run after the move: set or copy a field, assign, call a webhook, start an AI agent. |

Built-in rules include `system:separation-of-duties` (a user may not take this transition if they took
a given earlier one) and `system:trigger-agent`, which starts a Jira AI agent *after* the transition,
asynchronously [8]. Jira's built-in AI acts after a move; it does not decide it.

**Approvals** are configured on a *status* (approvers, a count or percentage, exclusions) and name the
transitions to fire on approval and on decline [8]. Approval steps reached Jira Cloud Premium and
Enterprise in February 2026, with each decision logged on the item [31].

**Other parts.** *Triggers* move items on development-tool events [7]. *Properties* are free key-value
pairs on statuses and transitions, an escape hatch [7]. *Layout* is stored in the workflow: x and y per
status, one of eight ports per link end, a start point, a box for looped transitions [8].

**Schemes and space types.** A workflow scheme maps work types to workflows and is shared by spaces;
publishing an edited scheme ("Associate") migrates the items [9]. Company-managed spaces share workflows
through schemes and have every rule, trigger and property. Team-managed spaces have one workflow per
work type, five rule types, new statuses reachable from any status by default, and limits of 50
statuses per workflow and 200 transitions [10][11][12].

**Data Center** keeps the older designer. Editing an active workflow makes a draft, published with an
optional backup copy; a draft cannot delete a status that items are in, which needs a copied workflow
and a scheme migration that "can't be paused or canceled" [20][21].

## 2. The Cloud editor, interaction by interaction

The old diagram and text editor was removed in July 2026; the "new" editor is the only one [18][19].

1. **Open**: Settings › Work items › Workflows › ••• › Edit; in team-managed spaces, Space settings ›
   Work types › Edit workflow [5].
2. **Screen**: a toolbar (add status, transition, rules), *Update workflow* and *Discard*, a side panel
   for the selection, the diagram, and *Relevant spaces* listing the spaces affected. A Text/Diagram
   toggle lists statuses with their transitions, opening the same panel [6].
3. **Add a status**: *Add status*, pick an existing status or *Create a new status* (name, category),
   *Add*; optionally tick *Allow all statuses to transition to this one* [4].
4. **Add a transition**: *Add transition* with From (several, or *Any status*), To and a name; or drag a
   line between two statuses on the diagram. From equal to To makes a loop [3][11].
5. **Edit**: click a line or its label to open the transition's panel (name, From/To, rules,
   properties, triggers, *Delete transition*). A status's panel has name, category, the global option,
   properties and *Delete status*; deleting a status deletes its transitions and their rules [10][11].
6. **Rules**: Rules › *Add*, pick a verb and a rule, fill its form, *Add* [5]. Since July 2026 *View
   all* shows a transition's rules in one tabbed dialog [18]; post functions can be reordered since
   November 2025 [24].
7. **Undo**: Ctrl+Z / Cmd+Z while editing (documented for team-managed spaces) [10]. Zoom, fit,
   auto-arrange, search and keyboard navigation of the diagram are not documented.
8. **Diagram aids**: collapsible labels; a global transition drawn as an "Any status" marker on its
   target instead of many lines; shared transitions [27][10].
9. **Publish**: *Update workflow* publishes "instantly" to every space using the workflow [6]. There is
   no draft; the editor "modifies the Active (published) workflow directly" [16].
10. **Migrate**: after removing a status, *Update workflow* opens a modal with a *New status* column per
    removed status, plus *Move work items in certain spaces / work types to other statuses* and *Add
    exception*; confirming moves the items without running any workflow rule [13]. Long updates go
    through a "temporary workflow", with editing locked [15]. Teams are not notified, and the audit log
    does not say which status went [14].
11. **After**: *More actions › Restore workflow to an earlier version* keeps versions "up to a month"
    (November 2025) [17]; the history API keeps 60 days [8]. Nothing compares two versions visually,
    and an October 2026 suggestion asks for the audit log to say what changed inside a workflow [25].
12. **AI**: with Rovo, a Workflow builder agent edits statuses, transitions and some rules from a
    prompt; nothing goes live until an admin presses *Update workflow* [17].

## 3. What feels smooth and what hurts

**Smooth:** one screen and one side panel for statuses, transitions and rules [6]; deleting a status
from a live workflow, with the migration folded into publishing (Data Center needs a copied workflow and
a scheme switch) [6][20]; fewer lines [27]; *Relevant spaces*, undo and restore [6][10][17].

**Painful**, with evidence:
- **Overlapping lines**: opposite transitions between two statuses draw on top of each other, and their
  labels cannot be moved (JRACLOUD-98588, 2026) [22].
- **Rules behind generic names** ("Field Condition", "Validation Rule") that must be opened one by one
  (JRACLOUD-96230) [23][28]; *View all* (July 2026) is the answer so far.
- **Gaps while switching editors**: missing rules, fixed post-function order, looped transitions, XML
  export [29][30].
- **Instant, global publishing with no review**: no draft, no reviewer, no notification, an audit
  entry that says only "updated", and rollback for one month [14][16][17][25].
- **Migration edge cases**: rules do not run while items move [13]; deleting a status that has an
  approval leaves in-flight approvals "permanently" unresolvable (JSDCLOUD-17539, open) [26].
- **Indirection**: newcomers find schemes the hardest part [36]; a global rename changes every report
  [2]; contest entries show 39 statuses with 1,007 transitions [37].

Jira made editing smooth by making publishing cheap. Our publishing is deliberately not cheap (a change
order), so the smoothness has to come from the draft, the checks and the review view.

## 4. Recommendations for the hub

### 4.1 Data model

One graph per process, one process per record type, no schemes. A proposed bundle shape (workstream 7
owns it):

```json
{"record_type": "ncr",
 "statuses": [
   {"id": "open", "name": "Open", "category": "todo", "at": [40, 120]},
   {"id": "disposition", "name": "Awaiting disposition", "category": "in_progress", "at": [280, 120]},
   {"id": "closed", "name": "Closed", "category": "done", "outcome": "completed", "at": [520, 120]},
   {"id": "cancelled", "name": "Cancelled", "category": "done", "outcome": "cancelled", "at": [520, 240]}],
 "transitions": [
   {"id": "t1", "type": "initial", "to": "open", "name": "Open the nonconformance",
    "gate": {"kind": "person", "role": "staff"}, "fields": ["title", "severity"],
    "actions": ["ncr-classifier", "record-filer", "risk-scorer"]},
   {"id": "t2", "from": ["open"], "to": "disposition", "name": "Send for disposition",
    "gate": {"kind": "auto"}},
   {"id": "t3", "from": ["disposition"], "to": "closed", "name": "Disposition",
    "gate": {"kind": "person", "role": "quality-manager", "not": ["author"]},
    "validators": [{"fields_set": ["disposition"]}], "actions": ["disposition-applier", "capa-opener"]},
   {"id": "t4", "from": ["disposition"], "to": "open", "name": "Return",
    "gate": {"kind": "person", "role": "quality-manager"}},
   {"id": "t5", "type": "global", "except": ["done"], "to": "cancelled", "name": "Cancel",
    "gate": {"kind": "person", "role": "quality-manager"}}]}
```

| Jira | Hub | Notes |
|---|---|---|
| Status, category | status with `category` `todo`, `in_progress` or `done` | Fixed colours. Ids never change, so a rename needs no state map and breaks no report. |
| Resolution | `outcome` on done statuses: completed, rejected, cancelled, superseded | Replaces today's exit buckets in kanban and dashboards. |
| Restrict, approvals | `gate` on each transition: `person` (a role; routing picks the assignee), `cog` (decides within its approval criteria, otherwise the item goes to a `fallback` role), `auto` (a closed-vocabulary condition, or "when the actions succeed") | The waiting decision at a status is derived: one inbox Gate per status and decider, its options the gated transitions, the SLA on the status. That is today's checkpoint. Jira's built-in AI rule does not decide; a cog may, so the gate kind must be visible everywhere. |
| Separation of duties | `gate.not`: the author, or the decider of another transition | As `system:separation-of-duties`. |
| Validators | a closed list: fields set, a form-layout condition (all/any/not), a status visited, linked records ("no open nonconformance exists") | Always shown as a sentence. |
| Post functions | `actions`: ordered code steps, notify, set a field | A failed action moves the item to the system `failed` status, which has a Retry loop. |
| Screen | `fields` collected at the decision, reusing form-layout rows | |
| Global, looped | `type: global` with `except` (default: done statuses); a loop has `from` equal to `to` | Cancel and Put on hold become ordinary globals. |
| Initial | `type: initial` with the start form and first actions | |
| Properties | none; a typed field for each one needed (`locked`, button order) | Free properties are how Jira hides behaviour. |
| Layout | `at` per status, start position | Presentation only (4.2). |

Today's linear processes compile into this shape without changing meaning; returned, failed, rejected
and cancelled become explicit statuses.

### 4.2 The change as a reviewable delta and a visual diff

- **Delta items**, one per change of meaning, keyed by element id (status or transition added, removed,
  renamed, recategorised or rerouted; gate, validator, action or field changed). A removed status is one
  parent item with its transitions as children, decided once. Items have origin `direct` and words like
  the forms editor's ("Disposition: decided by the quality-assistant cog under ... instead of the quality
  manager").
- **Layout-only edits** collapse into one item, "Layout changed: no effect on how items move", as
  `collapse_changes` folds re-rendered rules. Camunda highlights only changes that affect execution [33]
  and bpmn-js-differ reports layout changes separately [35].
- **Widening checks** (two owners, 3.5): a gate weakened (person to cog to auto); a validator or
  separation-of-duties rule removed; a global transition added; and, which Jira cannot do, **a mandatory
  gate bypassed**. For each person or cog gate, check whether every path from the initial transition to a
  `completed` status crosses it; if it did before and does not now, warn with the path ("Open → Fast
  close → Closed avoids the quality manager's disposition"). A breadth-first search suffices.
- **Visual diff** on the CO page and in the editor: one drawing of old and new together. Added elements
  green with "+", removed red, dashed and struck through with "−" at their old positions, changed amber
  with "~", the rest muted. Use the contrast-checked `ok`, `crit` and `warn` tokens, and never colour
  alone (WCAG 1.4.1): badges, dash patterns and `<title>` text.
- **A Changes list** beside the graph links each line to its delta item; selecting either highlights
  the other, as in Camunda [33] and ServiceNow's compare view [34]. Views: *Changes*, *Before*, *After*.
- **Round trip**: the qa-manager cog drafts each direct item into the SOP's process section, which is
  recompiled and compared by element id (3.3).

### 4.3 The editor

**Copy from Jira**: toolbar, canvas and side panel on one page; the *Add status* and *Add transition*
dialogs (several From statuses, *Any status*); drag from a status's handle to connect; click a line or
label to select; Text and Diagram views sharing the panel; "Any status" markers, shared transitions,
collapsible labels; Ctrl+Z; and a "where used" panel richer than *Relevant spaces* (items per status,
routing and SLA sentences, SOP lines, open COs on the process).

**Do differently:**
1. **Render the read-only graph on the server** as inline SVG, as `charts.py` does, on record pages
   (current status and the path taken) and in Settings. It replaces Mermaid, the shell's only CDN asset,
   so it works offline and without JavaScript.
2. **Draft, not publish**: one autosaved draft per person and process, based on a named release, then
   *Carry on a change order*, as in the forms editor. If another CO is released first, rebase by element
   id and turn each clash into a question.
3. **Separate curves** for opposite transitions, small arcs for loops, labels on a background that can
   be dragged (JRACLOUD-98588 [22]).
4. **Rules readable on the canvas**: a gate badge per transition (person and role, cog name, or a gear
   for auto) with counts of validators and actions; in the panel, every rule as a sentence.
5. **Checks after every edit**, each naming an element (as Jira's validation API does [8]) and selecting
   it on click. Errors: no initial transition, an unreachable status, a dead end that is not done, a role
   nobody holds, a cog gate without criteria, auto transitions out of one status that overlap or leave a
   gap. Warnings: widening, removing a status that items are in, more than about 25 statuses.
6. **Tidy**: a deterministic layered layout in Python (longest path from the initial status, done
   statuses last) for converting today's processes, placing new statuses and an undoable *Tidy* button.
   Positions are integers on an 8 px grid, saved in the draft.

**Plain JavaScript, no framework or CDN**: the graph as JSON in `<script type="application/json">` (as
`forms_ui` does); each edit an operation (`add_status`, `move`, `connect`) posted to the server, which
saves the draft and returns checks and words; undo and redo over that log; pointer events for mouse,
touch and pen; zoom and pan through the SVG `viewBox`. The Python renderer and the editor share a small
geometry (nearest-side ports, quadratic curves) pinned by shared test fixtures.

**Keyboard and accessibility**: the Text view is a complete no-JavaScript alternative, so no edit needs
dragging (WCAG 2.5.7). Statuses and transitions are focusable, named `<g>` elements ("Awaiting
disposition, in progress, 2 ways out, 4 items"): arrows move a status one grid step, Enter opens the
panel, *Connect from here* picks the target from a list, Delete and Escape work. Changes are announced in
the shell's live region. Editor keys work only while the canvas has focus; wider ones use the shell's
leader key and its shortcuts-off switch (WCAG 2.1.4). Focus uses the `focus` token in both themes.

### 4.4 Status migration (the state map, 5.2)

- **In the editor, at delete time** (Jira asks only on publish): "4 items are in Awaiting disposition.
  Move them to: [select]", prefilled with the rejection target or the previous status, stored in the
  draft's `state_map`.
- **On the CO page**, a table: old status; items now (a link to them); move to (only statuses in the new
  graph, done ones only when chosen deliberately); consequences, meaning the waiting decisions withdrawn
  and the new Gate's assignees (avoiding JSDCLOUD-17539 [26]) and any fields the target requires that
  items lack (a warning to accept). *Add exception* sends chosen items elsewhere.
- **At release**, move the items in the release step without running validators or actions, as Jira does
  [13]; cancel withdrawn Gates explicitly; record "Moved from X to Y by CO-n (state map), approved by ..."
  on each item's Track; tell the assignees. A rename needs no map.

### 4.5 MVP and later

**MVP**: the graph model and compiler (workstream 7); the server-rendered SVG view replacing Mermaid;
the Text view editor with every edit; the diagram editor (select, move, connect, panel, undo, delete,
zoom and fit, *Tidy*); live checks including the gate bypass; drafts carried on a CO; delta items in
words with the visual diff and Changes list; the state map in the editor, on the CO and at release.

**Later**: rebasing drafts across releases; comparing any two releases of a process; hand-placed bend
points; simulating an item's path under today's routing; search and a minimap; copying transitions and
templates; a cog that drafts a graph from a prompt, like Jira's builder agent, landing as `suggested`
items on a CO rather than going live.

## Sources

Read 2026-11-08 to 2026-11-09. Community pages marked † could not be fetched directly (they render
client-side); what this note says about them comes from search results.

1. Understand workflows. https://support.atlassian.com/jira-cloud-administration/docs/work-with-issue-workflows/
2. What is a workflow status? https://support.atlassian.com/jira-cloud-administration/docs/what-is-a-workflow-status/
3. Create workflow transitions. https://support.atlassian.com/jira-cloud-administration/docs/create-workflow-transitions/
4. Add statuses to a Jira workflow. https://support.atlassian.com/jira-cloud-administration/docs/add-statuses-to-a-jira-workflow/
5. Edit a workflow's rules. https://support.atlassian.com/jira-cloud-administration/docs/edit-an-issue-workflow/
6. What is the workflow editor? https://support.atlassian.com/jira-cloud-administration/docs/what-is-the-new-workflow-editor/
7. Configure advanced work item workflows (triggers, properties, initial transition). https://support.atlassian.com/jira-cloud-administration/docs/configure-advanced-issue-workflows/
8. Jira Cloud REST API v3, Workflows group: `/workflows/update`, `/workflows/update/validation`, `/workflows/capabilities` (rule keys), `/workflow/history`. https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-workflows/ (schemas read from https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json)
9. Configure workflow schemes. https://support.atlassian.com/jira-cloud-administration/docs/configure-workflow-schemes/
10. Create, edit and delete statuses in team-managed spaces. https://support.atlassian.com/jira-software-cloud/docs/create-edit-and-delete-statuses-in-team-managed-projects/
11. Manage work item transitions in team-managed spaces. https://support.atlassian.com/jira-software-cloud/docs/create-edit-and-delete-transitions-in-team-managed-projects/
12. Add or remove workflow rules in team-managed spaces. https://support.atlassian.com/jira-software-cloud/docs/add-or-remove-workflow-rules-in-team-managed-projects/
13. Move work items to new statuses while updating your workflow. https://support.atlassian.com/jira-software-cloud/docs/move-issues-to-new-statuses-while-updating-your-workflow/
14. Remove a status from a workflow. https://support.atlassian.com/jira-cloud-administration/docs/remove-a-status-from-a-workflow/
15. What is a temporary workflow? https://support.atlassian.com/jira-cloud-administration/docs/what-is-a-temporary-workflow/
16. KB: New Jira workflow editor doesn't reflect changes from drafts in old editor. https://support.atlassian.com/jira/kb/new-workflow-editor-doesnt-save-transition-changes-if-draft-exists-in-old-workflow-editor/
17. Atlassian Cloud changes Nov 3 to Nov 10, 2025 (restoring earlier workflow versions; AI Workflow builder agent). https://confluence.atlassian.com/cloud/blog/2025/11/atlassian-cloud-changes-nov-3-to-nov-10-2025
18. Atlassian Cloud changes Jul 6 to Jul 13, 2026 (old editor removed; *View all* rules dialog). https://confluence.atlassian.com/cloud/blog/2026/07/atlassian-cloud-changes-jul-6-to-jul-13-2026
19. † Final notice: old workflow editor deprecation in July 2026. https://community.atlassian.com/forums/Jira-Cloud-Admins-articles/Final-notice-old-workflow-editor-deprecation-in-July-2026/ba-p/3247499
20. Jira Data Center 11.3: Working with workflows. https://confluence.atlassian.com/adminjiraserver/working-with-workflows-938847362.html
21. KB (Data Center; requires login, content from search results): Cannot add transitions or delete steps in draft workflows. https://confluence.atlassian.com/jirakb/cannot-add-transitions-or-delete-steps-in-draft-workflows-203392961.html
22. JRACLOUD-98588: Allow manual repositioning of transition lines in the new workflow diagram to avoid overlapping. https://jira.atlassian.com/browse/JRACLOUD-98588
23. JRACLOUD-96230: Users unable to identify conditions, validators, and post-functions in the new workflow editor. https://jira.atlassian.com/browse/JRACLOUD-96230
24. JRACLOUD-95036: Enable reordering of post functions in the new Jira workflow editor (resolved 2025-11-04). https://jira.atlassian.com/browse/JRACLOUD-95036
25. JRACLOUD-99889: Audit log, webhooks and Forge events should record what changed inside workflows. https://jira.atlassian.com/browse/JRACLOUD-99889
26. JSDCLOUD-17539: Deleting a status configured with an approval leaves approvals on in-flight work items unresolvable. https://jira.atlassian.com/browse/JSDCLOUD-17539
27. † Small improvements for a better diagram view of the new workflow editor. https://community.atlassian.com/forums/Jira-articles/Small-improvements-for-a-better-diagram-view-of-the-new-workflow/ba-p/2713440
28. † New Workflow Editor: Losing visibility on Validators and Field Rules. https://community.atlassian.com/forums/Jira-questions/New-Workflow-Editor-Losing-visibility-on-Validators-and-Field/qaq-p/3180944
29. † Transition post-function order in the new workflow editor. https://community.atlassian.com/forums/Jira-Service-Management/Transition-post-function-order-in-the-the-new-workflow-editor/qaq-p/3109769
30. † Share your feedback on Jira's new workflow editor. https://community.atlassian.com/forums/Jira-Cloud-Admins-articles/Share-your-feedback-on-Jira-s-new-workflow-editor/ba-p/2918621
31. Jira launch note: Approvals that keep work moving (2026-02-20). https://jirareleases.atlassian.com/announcements/approvals-that-keep-work-moving
32. Jira launch note: Align team workflows with company-wide standards (2026-10-26). https://jirareleases.atlassian.com/announcements/align-team-workflows-with-company-wide-standards
33. Camunda Web Modeler: Versions (compare, visual view, Changes list). https://docs.camunda.io/docs/components/modeler/web-modeler/versions/
34. ServiceNow: Flow history compare view (Australia release). https://www.servicenow.com/docs/r/HGWDpzjgkfvaJWTdMzNy2w/s1EBN4jOoE1vQKPnK8LtPA
35. bpmn-js-differ README (diff categories: added, removed, changed, layout changed). https://cdn.jsdelivr.net/npm/bpmn-js-differ@3.2.0/README.md
36. † What has been the hardest thing for you when you started working with Jira? https://community.atlassian.com/forums/New-to-Jira-discussions/What-has-been-the-hardest-thing-for-you-when-you-started-working/td-p/1768812
37. † Worst Jira Admin Contest: A Thousand Transitions. https://community.atlassian.com/t5/Jira-articles/Worst-Jira-Admin-Contest-A-Thousand-Transitions/ba-p/2553153
