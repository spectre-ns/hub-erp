# Jira Cloud as a reference: automation, workflow, configuration

Research brief, 2026-11-07, from Atlassian docs plus practitioner and
community write-ups (sources at end). **[unconfirmed]** and **[community]**
mark claims not found on a current Atlassian Cloud page. Cloud docs now say
**flow** (rule), **work item** (issue), **work type** and **space** (project).

---

## 1. Jira Automation (Cloud)

### How Jira does it

**Model.** A flow is one **trigger**, then **conditions**, **actions** and
**branches** (which run later steps against related items or a JQL result).
**Smart values** are templated expressions such as `{{now}}`; `{{#debug}}` and
a **Log action** write values to the audit log. A template library offers
"hundreds" of prebuilt flows.

**Triggers.** **Manual** runs from a work item's Actions menu, can be limited
to a group, and can ask for input. **Scheduled** (interval or cron, optional
JQL) disables itself after **10 consecutive failed runs**. **Incoming
webhook** gives a unique URL and secret.

**Scope and actor.** A flow covers one space, several spaces, or all of them.
Anything wider than one space reportedly needs "Administer Jira"
**[community]**. "Any changes performed by an automation flow are seen as
being performed by the flow actor." The default actor is the **Automation for
Jira** user, whose rights come from the `atlassian-addons-project-access` role.
Anyone who can edit a flow can set the actor to themselves or to that user, and
app admins can set **anyone**. The flow's creator, its **owner**, is emailed
when it errors.

**Execution.** Runs are queued: "the number of items processed in parallel is
limited". Spikes can delay runs by **an hour or more**, so actions may land on
changed items. Ordering across branches and flows is not guaranteed
(AUTO-208). An "Allow rule trigger" option governs flow-to-flow chaining
**[community]**.

| Service limit | Value | When exceeded |
|---|---|---|
| Steps per flow | 65 (500 for advanced flows) | not stated |
| Items from a scheduled JQL search | 999 | THROTTLED |
| Processing time | 60 min per 12 h | THROTTLED |
| Loop detection | 10 self- or cross-triggers | stopped, LOOP |
| Associated items per flow | 5,000 | flow disabled |
| Items queued globally | 50,000 | flows stop running site-wide |
| Concurrent flows | 5 / 10 / 20 / 30 by plan | not stated |
| Items per branch | 150 | only the first 150 run |

**Audit log.** For each run it records the time, an audit log ID, the flow, a
status and the total time. Expanding a run shows each step's run time and
messages. Retention is **90 days**, and there are per-flow and global views.

Statuses, as defined in the Data Center docs: **Success** (all actions
succeeded), **No actions performed** (ran, but usually a condition stopped
it), **Some errors**, **Loop**, and **Throttled** ("breached a service
limit"). Cloud also shows **FAILURE**; an older **ABORTED** is recalled
**[community]**. Cloud's docs defer the full list to the in-product legend.
Bug AUTO-2212 reports throttled runs logged as "no actions performed".

**Watching and versioning.** I found no live view of a run; you inspect it in
the audit log afterwards **[could not confirm a live view]**. I found no
native version history or rollback for Cloud flows. Edits are reportedly
logged as **CONFIG CHANGE** with who and when, not what (Data Center ticket
JIRAAUTOSERVER-611; not verified for Cloud). Teams back up flows with JSON
export or third-party tools. Rewind reports a silently widened JQL scope that
did damage and a renamed field that broke three flows without error.

### Borrow / avoid

- **Borrow:** the trigger, condition, action and branch model; a distinct
  status for every run; loop detection; limits that surface as THROTTLED;
  auto-disable after repeated failures; role-limited manual triggers with an
  input form; debug and log steps.
- **Avoid:** a **shared, powerful actor**. Recording every change as
  "Automation for Jira" hides who designed, approved and caused a run, and an
  editor can point the actor at a more privileged identity, which defeats
  segregation of duties. Also avoid edits that go live with no diff, review or
  rollback, results visible only after a run ends, and silent queue lag.

---

## 2. Workflow engine

### How Jira does it

**Statuses** each belong to one of three fixed **categories** (To Do, In
Progress, Done), which cannot be renamed or extended; progress indicators and
`statusCategory = Done` build on them **[community, citing Atlassian docs]**.
A **transition** can start from several statuses; a **global transition**
from any.

**Transition rules** come in four kinds, run in order: **Restrict
transition** (formerly conditions; hides the transition), **Request input** (a
screen), **Validate details** (formerly validators; "If the validator fails,
the work item won't progress" and later actions don't run), and **Perform
action** (formerly post functions; runs "as soon as the transition is
completed").

**Workflow schemes** map work types to workflows, and one scheme can be shared
across spaces. "Changing a shared workflow scheme will affect all spaces using
that scheme." Default schemes cannot be edited, and a scheme in use cannot be
deleted. Limits from September 2026: **150 workflows per scheme** and **200
statuses per workflow**.

**Publishing.** Editing an active scheme "automatically create[s] a draft";
publishing it starts a migration that maps items whose status no longer
exists (*Associate*, then *Acknowledge*). The classic editor also drafts,
offers an inactive backup copy, and reportedly cannot delete statuses in a
draft **[community]**. The new editor (diagram and text modes) publishes
immediately on **Update workflow**, shows *Relevant spaces* beforehand, and
guides a move of items out of a deleted status. Workflow rules reportedly do
not run during that move **[unconfirmed]**.

**JSM approvals.** An approval is attached to a *status*. "Include approval
step" is offered only when the status has two or more outgoing transitions,
one to approve and one to decline. Its properties: `approval.condition.type`
(`number`, `percent`, `numberPerPrincipal`) with `approval.condition.value`;
`approval.field.id` (approver source); `approval.transition.approved` and
`.rejected`; and `approval.exclude` (for example assignee, reporter).

"Existing approval properties cannot be edited or deleted once set."
Approvals are for company-managed spaces only and take effect on **Publish
draft**. I could not confirm what one decline does when several approvals are
needed.

### Borrow / avoid

- **Borrow:** fixed categories under per-process statuses; the restrict,
  input, validate, act order, where a failed validation blocks the actions;
  gates with a threshold type, approver source, exclusions and both routes;
  an impact preview before publishing.
- **Avoid:** publishing straight into live processes; status deletions that
  move items in bulk without a record or without running the rules; schemes
  shared so widely that one edit changes everything; gate settings that
  cannot be edited once set.

---

## 3. Configuration engine

### How Jira does it

**Schemes.** A company-managed space is assembled from shared schemes:
**permission**, **notification**, **work type**, **workflow**, **field
configuration** (visibility per work type), **screen** (create, edit and view
screens) and **work type screen**. A field shows only if it is on the screen
and visible in the field configuration. Atlassian recommends granting to
**space roles** rather than groups, so one scheme serves many spaces while
each space keeps its own membership.

**Team-managed vs company-managed.** Team-managed spaces keep all
configuration local, edited by the space admin, with nothing shared;
company-managed spaces use shared schemes owned by Jira admins.

**Custom field contexts** set a field's options and default value for chosen
spaces and work types. A space-specific context overrides the global one.

**Limits.** Enforced since March 2026: **700 fields** and **150 work types**
per space. From September 2026: **20,000 options per field** and **50 grants
per permission**. At a limit, Jira blocks only new additions, keeps existing
configuration working, and rejects a bulk action whole instead of applying
part of it.

**Safety mechanisms:**
- **Sandboxes** (Premium and Enterprise) are isolated copies of production.
  Flows copied into a sandbox start disabled.
- **Deploy configuration changes from a sandbox** (**beta**) scans changes
  since the last deployment, auto-selects dependencies, runs *Review
  deployment* (conflicts, impacted statuses), then deploys and logs
  `Summary.csv` and `RequiresAttention.csv`. It covers workflows, schemes,
  fields and contexts, screens, statuses, work types and automations. It
  **excludes** team-managed spaces, removing statuses from workflows, field
  type changes and automation connections. The docs mention **no rollback**.
- **Release tracks** (continuous, bundled, preview) govern *Atlassian's*
  product changes, **not** customer configuration: bundled lands monthly
  (second Tuesday, 22:00 UTC); a preview sandbox gets it a month early.
- The **admin audit log** records configuration changes (permissions, field
  configurations) with configurable retention.

### Borrow / avoid

- **Borrow:** named configuration bundles that spaces reference, with roles
  resolved per space; contexts; limits that block additions without breaking
  what exists; deployment with dependency collection, impact preview and a
  report; a preview track ahead of a monthly bundle.
- **Avoid:** unreviewed edits straight to production (Jira's default outside a
  sandbox); configuration without versions or rollback; two configuration
  models side by side; deployment gaps that force manual changes.

---

## 4. Proposal for Hub ERP

### Automation job model

- **Job:** one run of a *released rule version* against one subject, such as
  a document, a record or a release.
- **Live steps:** `pending → running → succeeded / failed / skipped`, with
  timestamps, redacted inputs and outputs, and messages, streamed to the UI.
- **Job statuses:** `QUEUED`, `RUNNING`, `WAITING` (for a gate or a person),
  `SUCCESS`, `NO_ACTION`, `PARTIAL`, `FAILED`, `THROTTLED`, `LOOP` and
  `CANCELLED`. They are never merged, which avoids the AUTO-2212 problem.
- **Retries:** set per step, with idempotency keys, bounded backoff and a cap.
  After N consecutive failed jobs the rule is suspended and its owner is
  notified. Each step re-checks its preconditions before it acts, in case the
  queue lagged.
- **Identity:** each cog is a named principal with role-scoped grants, never
  a shared superuser. Every effect records actor, rule version, and who
  started or approved the run. Automation can never satisfy a human gate.

### Rules governed as released policy

- **Versioned data.** Every rule and every permission, routing, limit or
  approval setting goes through `draft → in review → released → superseded`.
  A released version is immutable.
- **Release review** by someone other than the author, showing a diff, an
  impact preview (affected processes, open items), validation, and an
  optional dry run against recent events.
- **Pinning and rollback.** Jobs keep the version they started with; rollback
  re-releases an earlier version.
- **Audit.** The audit log stores what changed, not just "CONFIG CHANGE".

### Workflow editor model

- **Processes.** A process is a list of numbered steps with a diagram view.
  Each step has a status in a fixed category (Open, Active or Closed), an
  owning role and transitions. Each transition runs its rules in order:
  restrict, request input, validate, act.
- **Gates** are first-class steps with an approver source, a threshold
  (count, percentage, or count per role), exclusions ("author cannot
  approve"), approve and reject routes, and a signature reason. Unlike JSM
  approvals, a gate stays editable in drafts.
- **Publishing** creates a new process version (draft, review, release).
  In-flight items stay on their version or move under a reviewer-approved
  status mapping, recorded per item. A status holding items cannot be removed
  until they are mapped.

---

## Sources

Atlassian documentation:
- https://support.atlassian.com/cloud-automation/docs/automation-service-limits/
- https://support.atlassian.com/cloud-automation/docs/what-is-the-automation-audit-log/
- https://support.atlassian.com/cloud-automation/docs/debug-an-automation-rule/
- https://support.atlassian.com/cloud-automation/docs/jira-automation-triggers/
- https://support.atlassian.com/cloud-automation/docs/components-in-jira-automation
- https://support.atlassian.com/cloud-automation/docs/what-is-a-rule-actor/
- https://support.atlassian.com/automation/kb/automation-rule-actor-vs-automation-rule-owner/
- https://support.atlassian.com/jira/kb/automation-error-actor-does-not-have-permission-to-view/
- https://support.atlassian.com/jira/kb/automation-trigger-delays/
- https://confluence.atlassian.com/automation/troubleshoot-automation-rules-1141480666.html (Data Center status definitions)
- https://confluence.atlassian.com/automation/troubleshooting-jira-automation-some-errors-and-failure-status-1518962345.html
- https://jira.atlassian.com/browse/AUTO-2212
- https://jira.atlassian.com/browse/AUTO-208
- https://jira.atlassian.com/browse/JIRAAUTOSERVER-611
- https://www.atlassian.com/software/jira/automation-template-library
- https://support.atlassian.com/jira-cloud-administration/docs/work-in-text-mode/
- https://support.atlassian.com/jira-cloud-administration/docs/edit-an-issue-workflow/
- https://support.atlassian.com/jira-software-cloud/docs/what-are-the-different-workflow-rule-types/
- https://support.atlassian.com/jira-cloud-administration/docs/configure-workflow-schemes/
- https://support.atlassian.com/jira-service-management-cloud/docs/add-an-approval-to-a-workflow/
- https://support.atlassian.com/jira/kb/jira-service-management-cloud-approval-workflow-properties/
- https://support.atlassian.com/jira-cloud-administration/docs/data-limits-and-guardrails/
- https://support.atlassian.com/jira-cloud-administration/docs/edit-a-custom-field-context
- https://support.atlassian.com/jira-cloud-administration/docs/map-field-configurations-to-work-types/
- https://support.atlassian.com/jira-cloud-administration/docs/configure-notification-schemes/
- https://support.atlassian.com/jira/kb/grant-access-to-single-jira-project-that-uses-a-shared-permission-scheme/
- https://support.atlassian.com/jira-service-desk-cloud/docs/project-configuration-in-classic-and-next-gen-projects
- https://support.atlassian.com/organization-administration/docs/what-are-sandboxes
- https://support.atlassian.com/organization-administration/docs/what-data-is-copied-to-sandboxes
- https://support.atlassian.com/organization-administration/docs/deploy-configuration-changes-from-a-sandbox/
- https://support.atlassian.com/organization-administration/docs/data-deployed-from-sandboxes/
- https://support.atlassian.com/organization-administration/docs/what-are-release-tracks/
- https://confluence.atlassian.com/adminjiracloud/auditing-in-jira-applications-818578846.html

Practitioner and community sources:
- https://rewind.com/blog/jira-automation-rules-backup/
- https://www.salto.io/blog-posts/navigating-the-atlassian-cloud-sandbox-best-practices-use-cases-and-essential-tips-for-jira-admins (dated; predates sandbox deployment)
- https://community.atlassian.com/t5/Jira-questions/How-can-Jira-automation-rules-be-editable-for-project-admin/qaq-p/2341412
- https://community.atlassian.com/t5/Jira-questions/Where-can-one-find-the-definitions-of-all-the-Statuses-in-Jira/qaq-p/2261220
- https://community.atlassian.com/t5/Jira-questions/Sequence-of-execution-of-automation-rules/qaq-p/2517434
- https://community.atlassian.com/forums/Jira-articles/Jira-Image-of-the-Day-Status-Categories/ba-p/2646552
- https://community.atlassian.com/forums/Jira-questions/How-to-save-your-original-workflow-as-an-inactive-backup/qaq-p/1812207
