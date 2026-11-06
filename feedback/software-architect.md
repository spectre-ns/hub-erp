# Architecture review of hubproto (Software Architect panelist)

Scope: the prototype at commit d3b632d against the "Intelligence Hub ERP Plan"
(sections 3, 4, 5, 7, 8, 9.1, 9.6, 11, 13), the Frame Spec v0.2 and Op Spec /
op-manifest ideas as the plan describes them. I read README.md, DEV-NOTES.md,
all of `hubproto/*.py`, `config/*.json`, `frames/**`, `profiles/iso9001/`,
`docker/*`, `docker-compose.yml` and the shape of `tests/*.py`. I did not change
anything outside `feedback/`. Where a finding says "reproduced", I ran the
check in a throwaway hub under my scratchpad (the live stack is shared, so I
did not write to it); the scripts are quoted inline so the maintainer can
rerun them. Everything else is from reading the code.

Severity is used as follows. **blocker**: a design flaw that must be resolved
before anything is built on this code, because every module above it inherits
the flaw. **major**: a flaw that will produce wrong results or an outage in a
real deployment, or breaks a claim the README makes. **minor**: a shortcut
that keeps the plan's seam and can be replaced in place. **question**: a point
where the plan and the code disagree and either could be right.

Sections: 1 fidelity to the plan (F-arch-1 to 13), 2 spec alignment (14 to
16), 3 production path (17 to 26), 4 data model (27 to 31), 5 security (32 to
36), 6 testability and operability (37 to 41), 7 the ten changes with most
leverage, then counts.

---

## 1. Fidelity to the plan

The seams the plan names are all present and recognisable, which is the
prototype's main achievement: a release bundle is the only thing the runner
reads (`Runner.submit` pins `releases.current(at)["digest"]` and every later
call uses `state["release"]`); Guards return findings and never decide
(`guards.py`, `envelope.gate_default`); a person or a cog-within-criteria
decides at a Gate; code cogs are the only writers in intent; records, links,
labels, holds and documents are one model (`kernel.py`, `dms.py`); the
workflow is rendered from the same process section as the Op manifest
(`release.render_manifest`, `release.render_workflow`); cogs are members with
roles in the org-model Frame; the CAD worker speaks the `/invoke` envelope.
The findings below are where the realisation diverges from the seam, and
whether that matters.

### F-arch-1: The kernel does not enforce grants or `reaches`; "only code cogs write under a grant" is cog self-discipline
Severity: blocker
Area: architecture
Steps:
1. Read `hubproto/kernel.py`: `Kernel.create`, `set_fields`, `transition`, `link`, `assign`, `set_labels`, `append` take `by`, `run_id`, `release` and nothing else. Only `post_journal` and `publish_document` look at a grant, and the check is `grant.get("run_id") == run_id`.
2. Read `hubproto/cogs.py`: `Cog._require_grant` is called by the cog itself, in the five cogs whose authors remembered to.
3. Read `ThreeWayMatcher` (`reaches = ["kernel:read"]`) and `procurement.SupplierChecker`, `ReceiptChecker`, `cad.BOMReconciler` (all `kernel:read`): each calls `ctx.kernel.set_fields(...)`. `RiskScorer` declares no `reaches` and writes `risk` and `conditions`. `StockBooker` and `POIssuer` call `ctx.kernel.append(...)` directly, writing raw events (`stock.received`, `po.issued`) that bypass every kernel invariant.
Expected: Plan section 5 ("Authority and grants") and section 3 ("Cogs -> kernel"): "The kernel verifies the run token and the grant on every call, so a mis-built Cog cannot post"; code cogs reach "only the kernel endpoints their reaches declares" (section 11).
Actual: The kernel has no notion of a caller. Any object holding the `Kernel` (every cog, through `RunContext.kernel`) can create records, move them to any state, link them and append arbitrary events, with any `by` string. The `reaches` declaration is documentation; it is inaccurate on at least five cogs today and nothing would notice if it were dropped. The README's claim "Only code Cogs write, only under a grant" is true for two commands.
Evidence: `hubproto/kernel.py` (`post_journal`, `publish_document` are the only functions that mention `grant`); `hubproto/cogs.py` `ThreeWayMatcher.task_run` and `RiskScorer.task_run`; `hubproto/procurement.py` `StockBooker.task_run` (`ctx.kernel.append("stock.received", ...)`), `POIssuer.task_run` (`ctx.kernel.append("po.issued", ...)`).
Suggested fix: Give the kernel one command entry point that takes a caller token (`run_id`, cog id, grant) and refuses writes whose grant does not cover the command and the aggregate; have `Runner._ctx` hand cogs a `KernelClient` built from the cog's declared `reaches` (read-only for `kernel:read` and for every `kind: context` cog) instead of the `Kernel`; make `Kernel.append` private to the kernel. This is the one change that turns the plan's central safety claim from a convention into a property, and it is cheaper now than after the next six modules.

### F-arch-2: Grants are run-wide, unscoped, and a parent grant admits any child process without authorization
Severity: major
Area: architecture
Steps:
1. Read `Runner._record_decision`: on any approve it creates `grant = {id, run_id, decided_by, payload_digest, steps: [every step except this one]}` and stores it on the run.
2. Read `Runner.submit` with `parent_run`: if the parent holds any grant, the child is admitted with `decision = {"allowed": True}`; the `authz.check(... "create" ...)` and the first-step role check are skipped entirely, for whichever process and owner the calling cog chose.
3. Read `procurement.ShortageRequester` and `cogs.CAPAOpener`, `NCROpener`, `DocumentPublisher`: each submits a child process under the parent's grant with `owner=` chosen by the cog.
Expected: Plan section 5: a grant is "short-lived ... scoped to the targets the run owner admitted and, for writes, to the changes a person approved". Section 7.3: a cog's tier 1 filing runs under "a create permission on its role".
Actual: One approval anywhere in a run authorises every later write in that run (the grant on `widget-request`'s first checkpoint already covers the steps after the second), and any process reachable from a cog, for any owner, with no permission check. The grant has no expiry, no target list, and no binding to the approved payload beyond carrying its digest. A design approval by bob admits purchase requests owned by alice into `Purchasing/Requests` without alice's `create purchase-request` permission being consulted.
Evidence: `hubproto/runner.py` `_record_decision` (grant construction), `submit` (`if parent_run:` branch); `hubproto/procurement.py` `ShortageRequester.task_run` (`owner=ctx.owner, parent_run=ctx.run_id`).
Suggested fix: Scope the grant to the steps between this Gate and the next, to the record locator(s) the Gate decided, and to a `changes_digest`; let the process grammar declare which child processes a step may start ("5. [system] Order the shortages (shortage-requester) starts purchase-request") so the Consistency Guard can check it and `submit` can require it; keep the child owner's create permission check even under a parent grant.

### F-arch-3: Record-type and link configuration is read live from the package, not from the pinned release
Severity: major
Area: policy
Steps:
1. Read `hubproto/kernel.py` `Kernel.__init__`: `CONFIG_DIR = Path(__file__).parent.parent / "config"`; `self.types`, `self.link_vocab`, `self.retention` are loaded from there on every process start, regardless of the hub root or the release.
2. Read `hubproto/compile.py` `compile_proposal`: `config_digests` are recorded in the proposal and copied into the release metadata (`ReleaseStore.save(config=...)`), and `record_types` is stored in the proposal and used by `render` to pick `record_type` per workflow.
3. Read `Runner.submit` and `Runner.decide`: `required_missing`, `defaults`, enum checks all use `self.kernel.types`, the live file.
Expected: Plan section 8.1: record type definitions and numbering series are "versioned configuration documents referenced from Frames and pinned in the release bill of materials"; section 4.6: a release "still pins which table version was in force".
Actual: The release pins a digest of the config, nothing reads the pinned copy, and an in-flight run that "finishes on the release it started on" (README claim) finishes on whatever `config/record-types.json` the container was built with. Editing the file and restarting changes required fields, enums and defaults for every open run and every historical replay, with no Gate. The config is also package-relative, so a hub root cannot carry its own.
Evidence: `hubproto/kernel.py` `CONFIG_DIR`, `Kernel.__init__`; `hubproto/release.py` `ReleaseStore.save` (meta `config`, not bundle); `hubproto/runner.py` `submit` (`self.kernel.types.get(wf["record_type"])`).
Suggested fix: Store the three config documents inside the release directory next to `bundle.json`, and have `Runner` pass the release's copy into the kernel calls it makes (or construct a per-release `TypeRegistry`). Changing a record type then goes through compile, drift and the Gate like any widening.

### F-arch-4: Policy constants live in cog and kernel code, and the risk scorer guesses the record type from the payload's shape
Severity: major
Area: policy
Steps:
1. Read `hubproto/cogs.py` `RiskScorer.task_run`: thresholds 10,000 / 2,000 / 500, the mapping of severity words to tiers, and which conditions matter are literals. The branch taken depends on which keys happen to be present (`quantity` -> purchase, `amount` -> bill, `change_type`/`doc_class` -> document, `kind in (mechanical, ...)` or `bom_lines` -> design, else NCR).
2. Read `NCRClassifier.task_run` (keyword lists), `LedgerPoster.task_run` (accounts "5000 Purchases" and "2000 Accounts payable"), `Kernel.publish_document` (review date = today + 1 year), `Kernel.set_labels` (sensitivity order), `Runner._human_gate` and `extract._FIELD_ASSIGNEES` (the list of field roles), `guards._WIDENING_KINDS`.
3. Confirm the release bundle (`release.render`) carries `thresholds` only for sentences; `purchase-approval-threshold` and `controller-approval-threshold` exist in the bundle but `RiskScorer` never reads them.
Expected: Plan section 7.4: "Risk is computed, not judged ... The factors and their weights are a configuration document referenced from the finance or quality Frame, versioned and in the bill of materials." Section 4.6: thresholds are "Named constants in the release. Code Cogs read them by name; never hard-coded." Chart of accounts is a configuration document.
Actual: The risk tier that decides whether a cog may approve is computed from literals in Python that no policy owner sees at any Gate, and the same cog scores a bill as a purchase request if the payload ever carries a `quantity` key. The ledger accounts are literals. The routing sentence "over 10,000 USD" and the scorer's `amount >= 10000` are two copies of one rule that can drift apart silently.
Evidence: `hubproto/cogs.py` `RiskScorer.task_run`, `LedgerPoster.task_run`; `hubproto/kernel.py` `publish_document`; bundle thresholds vs. scorer literals.
Suggested fix: Make the scorer read a `risk-factors` configuration document pinned in the release (per record object: factors, weights, tier bands), and dispatch on `ctx.extras["object"]` rather than payload keys. Move the accounts to a chart-of-accounts document referenced from the finance Frame. Classifier keyword lists can stay as stand-ins, but mark them `kind: context` stand-ins in the catalog card.

### F-arch-5: An item's state lives in three places and `failed` is a dead end
Severity: minor
Area: workflow
Steps:
1. Read `Runner._save` (status derived from the Track), `state["pending"]`, `state["steps"]` (run-local truth) and `Kernel.transition` (record state). Read `Runner._fail`: moves the record to `failed`, which is not a state of any rendered workflow and has no transition out; `tick` and `decide` skip it.
2. Read `Runner._rewind`: pops later steps from `state["steps"]` and rewrites `pending` from the Track rather than from the kernel.
Expected: Plan section 8.4: "A record's state is the projection of its transitions ... nothing moves an item outside a run"; the Gate vocabulary includes retry and escalate.
Actual: The runner is the workflow engine, which the plan asks for, but the operational truth is `state.json` and the Track, with the kernel's state written as a side effect. A Guard error (a CAD worker down for longer than the timeout, an enum typo) strands the record in `failed` with no retry, no escalation and no route back to the author; the only recovery is a new run on a new record. Three copies of "where is this item" will drift as soon as a write fails halfway (see F-arch-18).
Evidence: `hubproto/runner.py` `_fail`, `_save`, `_rewind`; `hubproto/release.py` `render_workflow` (no `failed` state).
Suggested fix: Treat the kernel as the single truth for record state and assignees, derive `pending` from it, and add `retry` (re-run the failed step) and `escalate` (route to a person with the Guard findings) as outcomes of the default Gate, which is what the plan's Gate vocabulary says.

### F-arch-6: Every cog receives the whole kernel, runner, DMS and inbox, including context cogs
Severity: minor
Area: architecture
Steps: Read `cogs.RunContext` and `Runner._ctx`: the same context, with `kernel`, `runner`, `dms`, `inbox`, is handed to `kind: context` cogs (`InvoiceExtractor`, `DocumentDrafter`, `NCRClassifier`, every `ReviewerCog`) and to code cogs alike. `DocumentDrafter` (context) writes a DMS version; `ReviewerCog.task_inbox` starts runs.
Expected: Plan section 11: "context Cogs reach only the model gateway and the Frames API; code Cogs reach only the kernel endpoints their reaches declares."
Actual: A prototype shortcut (in-process Python objects) that preserves the call seam (`Cog.invoke(task, input, ctx) -> envelope`) but not the capability seam: replacing a stand-in with a model-backed cog gives the model write access to the system of record through `ctx`. Same root cause as F-arch-1; listed separately because the fix is in `RunContext`, not the kernel.
Evidence: `hubproto/cogs.py` `RunContext`, `DocumentDrafter.task_run`, `ReviewerCog.task_inbox`; `hubproto/runner.py` `_ctx`.
Suggested fix: Build the context per cog from its card: context cogs get read-only record access and the Frames; code cogs get the client for their `reaches`.

### F-arch-7: Two steps with the same title silently drop a step from the manifest
Severity: major
Area: workflow
Steps (reproduced in a throwaway hub):
1. Write a process section with two steps titled "Render the record" (a realistic SOP: render after filing and again after approval).
2. Run `ConventionExtractor().extract(frame)`, `consistency_guard(rules, cog_ids, types)`, `render_manifest(process)`.
Expected: Either distinct step ids, or a compile error naming the duplicate.
Actual: `step ids: ['open-the-task', 'render-the-record', 'approve-the-task', 'render-the-record']`; `consistency findings about ids: []`; `manifest step ids: ['open-the-task', 'render-the-record', 'render-the-record']`. `Runner.advance` keys `state["steps"]` by id, so after the first "render-the-record" passes the second is skipped as already passed; the Track shows one step; the second snapshot is never filed. `Rejections return to <title>` and routing `At <title>` resolve to the first match as well.
Evidence: `hubproto/extract.py` `ConventionExtractor.extract` (`"id": slug(title)`), `hubproto/guards.py` `consistency_guard` (no uniqueness check), `hubproto/runner.py` `advance` (`state["steps"].get(sid)`).
Suggested fix: In `consistency_guard`, error on duplicate step ids within a process; see F-arch-27 for stable ids.

### F-arch-8: Gate-on-the-previous-step and the synthetic `noop` carrier are rendering conventions that leak into the Track and the manifest
Severity: minor
Area: workflow
Steps: Read `release.render_manifest`: a checkpoint becomes `gate: {type: human}` on the preceding cog step; two consecutive checkpoints insert a `reach-<id>` step on the `noop` cog. Read a Track for `capa` or `internal-audit`: `step.started`/`step.result` entries for `reach-verify-effectiveness` with cog `noop`.
Expected: Plan section 8.4: checkpoints are human transitions; the Op manifest has "human checkpoints" as steps in their own right.
Actual: A prototype shortcut that preserves the seam (the human Gate is a Gate on a step, with `route`, `title`, `role`), but auditors reading a Track will see steps the SOP does not contain, and a payload digest signed at a Gate is the digest of the *previous cog's* envelope, which is fine for the first checkpoint and odd for the carrier (`noop` passes `prior` through, so the digest is of a copy).
Evidence: `hubproto/release.py` `render_manifest`; `hubproto/cogs.py` `Noop`.
Suggested fix: Render checkpoints as first-class `kind: human` steps in the manifest and let the runner pause on them; drop `noop`. Low cost now; costly after more Tracks exist.

### F-arch-9: The `approve` alias is lexical and silent
Severity: minor
Area: policy
Steps (reproduced): For every process in the compiled bundle, print the checkpoints and `checkpoint_for(proc, "approve")`.
Expected: "Approvals in X" names one unambiguous checkpoint or is refused.
Actual: The alias binds to the first checkpoint whose id starts with `approv`, `accept`, `confirm` or `authori`; eight of the sixteen processes have no such checkpoint (`review-the-design`, `disposition-the-nonconformance`, `verify-the-actions`, ...), so "Approvals in design-release" is a compile error there, which is loud and good, while `change-control` (`approve-the-change`, `verify-the-change`) and `internal-audit` (`accept-the-findings`, `record-the-findings`) bind to the first match. Renaming "Approve the change" to "Sign off the change" turns a valid routing sentence into a compile error; renaming "Record the findings" to "Accept the plan" silently re-routes approvals. The rule is in code, not in the grammar Frame.
Evidence: `hubproto/release.py` `checkpoint_for`; `frames/reference/writing-policy-frames.frame.md` does not mention the alias.
Suggested fix: Either document the alias in the grammar Frame with the prefix list, or require `At <title>` whenever a process has more than one checkpoint and make the Consistency Guard say so.

### F-arch-10: Authorization without OpenFGA keeps the seam; sensitivity labels are carried but never consulted
Severity: minor
Area: security
Steps: Read `hubproto/authz.py` `check`: roles from the bundle, allow/deny per (role, action, object, folder prefix), deny wins, returns the sentence. Read `Kernel.set_labels` (labels stored, lowering needs a decision) and search for any reader of `labels["sensitivity"]`: none in `authz`, `runner`, `shell` (beyond display), `inbox`.
Expected: Plan section 5 "Sensitivity": a run's effective label is the strictest of its inputs; a Policy Guard refuses payloads above a label to an external provider; section 7.2: a cog "cannot forward content above its channel's ceiling".
Actual: A prototype shortcut that preserves the seam well: `authz.check` is the single entry point, takes the bundle, and returns "Why?". Note for the OpenFGA swap: OpenFGA answers yes/no and does not return the tuple that matched, so the "Why?" sentence needs a parallel explain path (the bundle's permission list keyed by tuple). Labels are a field with no consumer: no Guard, no routing condition, no channel ceiling. Not a fault of the shortcut, but "missing" against the plan.
Evidence: `hubproto/authz.py`; `grep -n sensitivity hubproto/*.py` (writers only).
Suggested fix: Keep `authz.check`'s signature; add an `effective_label(run)` in the runner and one Policy Guard that reads it, so the label has a consumer before model-backed cogs arrive.

### F-arch-11: Cog identity is a class id with a shared version string; no package digest and no model binding on hub-side envelopes
Severity: minor
Area: architecture
Steps: Read `cogs.Cog.version = "0.1.0"` (never overridden); `Runner.advance` records `cog_version` from it; `CogRegistry` is a hard-coded class list; `make_envelope` has no `binding` field; only `worker.invoke` adds `binding`.
Expected: Plan sections 4.3 and 7.1: Cog identity is `host/repo@digest`; principal and package are separate; a model binding record sits on every envelope.
Actual: Every Track entry says `cog_version: 0.1.0` for every cog, so "which version of the payables reviewer approved this" cannot be answered once the code changes. The principal id (`ap-reviewer` in the org model) and the package id (`APReviewer.id`) are the same string, so the plan's "principal is stable, package changes" split is not modelled. Acceptable in a prototype; a cheap seam to add now.
Evidence: `hubproto/cogs.py` `Cog`, `CogRegistry.cards`; `hubproto/envelope.py` `make_envelope`.
Suggested fix: Give `Cog` a `digest` (hash of its source for now) and add `binding` to the envelope; record `principal` and `package` separately on `gate.cog_review`.

### F-arch-12: Segregation-of-duties sentences name a role pair that the runtime ignores
Severity: minor
Area: policy
Steps: Read `Runner._sod_applies`: true if any `sod` rule's `object` equals the process object; the `roles` pair is never read. The check is then `approver == run owner`.
Expected: "The designer and the approver of a design must be different people" binds those two roles.
Actual: The sentence is a flag: "preparer != approver" for that object. The role words are decorative, which means a policy owner could write "The auditor and the reporter of a nonconformance must be different people" and get the same rule as today. `Kernel.post_journal` checks `prepared_by != approved_by` and `publish_document` checks `author != approved_by`, so SoD lives in three slightly different shapes.
Evidence: `hubproto/runner.py` `_sod_applies`; `hubproto/kernel.py` `post_journal`, `publish_document`.
Suggested fix: Render the pair as `(field_a, field_b)` on the record (e.g. `designer`, `approved_by`) and evaluate it in one place, the kernel, at `record.decision`.

### F-arch-13: Tier 3 (human-only) cannot be expressed, and the runner accepts a cog rejection
Severity: minor
Area: policy
Steps: Read `Runner._human_gate`: a cog candidate with criteria may return `approve`, `reject` or `escalate`; `reject` ends the run. Search for any way to mark a checkpoint as requiring an individual's signature: none; a criteria sentence plus a routing sentence delegates any checkpoint.
Expected: Plan section 7.3: tier 3 is "not delegable"; section 7.4: "A cog never rejects on its own."
Actual: `ReviewerCog` never emits `reject`, so this is latent, but the runner's contract allows it, and nothing stops a future criteria sentence delegating CAPA closure or document effectiveness. The plan's list of non-delegable meanings has no home in the release.
Evidence: `hubproto/runner.py` `_human_gate` (`if verdict == "reject"`); `hubproto/rules.py` KINDS (no signature-policy kind).
Suggested fix: Add a `signature policy` statement kind ("Closure in capa requires an individual's signature") rendered into the bundle, have the Consistency Guard refuse cog routes to those checkpoints, and treat a cog `reject` as `escalate`.

---

## 2. Spec alignment

### F-arch-14: Frame Spec v0.2: `scope: reference` is overloaded, `visibility` is unused, and "child overrides parent" is not implemented
Severity: question
Area: policy
Steps:
1. Read `hubproto/frames.py`: four required fields, `type: frame [0.2]`, `inherits`, transitive resolution disclosed as `"inheritance": "transitive"` in release metadata. Good.
2. Read `app.default_scopes`: every Frame whose `scope != "reference"` is compiled; `writing-policy-frames` uses `scope: reference` to opt out.
3. Read `shell.page_frame`: any `as` value can open any Frame; `visibility: internal` is displayed, not enforced.
4. Read `compile.compile_proposal`: "Later Frames override earlier ones: a rule with the same key stated twice keeps the last source" — `rule_key` excludes the source, so this only de-duplicates identical rules. A child Frame cannot narrow or replace a parent's sentence; both rules apply, and only `deny` beats `allow`.
Expected: The plan treats `compile:` and `clauses:` as frontmatter extensions (allowed, "unknown fields are ignored"), uses `scope` for the org tree, and describes override transparency at the Gate (section 4.7) and "child wins, later parents win" (section 4.1).
Actual: Two extensions are sound (`compile`, `clauses`). Using `scope: reference` as a compile switch is a third, undocumented extension that collides with the spec's meaning of `scope`; `compile: none` already exists for the purpose. Overrides are not implemented, so the plan's "override transparency" Gate feature has nothing to show, and a department Frame that wants to narrow a company allow can only add a deny. Is "deny-only narrowing" the intended policy model? If so the plan should say so; if not, rules need a (frame, section, subject, action, object, folder) identity so a child statement replaces a parent one.
Evidence: `hubproto/app.py` `default_scopes`; `frames/reference/writing-policy-frames.frame.md` frontmatter; `hubproto/compile.py` `compile_proposal` (by_key).
Suggested fix: Use `compile: none` for the reference Frame and reserve `scope`; decide the override model explicitly and record it in DEV-NOTES.

### F-arch-15: Op manifest / envelope alignment: envelope matches, Gate outcomes are a subset, consensus is absent, and the Track is not hash-chained
Severity: minor
Area: architecture
Steps:
1. Read `hubproto/envelope.py`: `envelope: 1`, `ok`, `problems[{check, detail, severity}]`, `cog{id, version}`, `task`, `payload`, `error{code, detail}`, `raw`, `timing`. `raw` is never populated (the worker discards subprocess output; see F-arch-35).
2. Read `gate_default`: pass / pass-with-problems / fail. Read `Runner.advance`: fail = stop the run.
3. Search for `repeat` or `require` in `release.render_manifest`: none. The plan's purchase-to-pay step "extract-invoice ... Consensus Guard runs a second extractor (repeat with require: 2)" is not expressible.
4. Read `track.Track.append`: `record_digest = sha256(record)[:16]` of the entry itself; no reference to the previous entry's digest; `seq = len(self.read()) + 1`.
Expected: Plan section 2 (Gate: pass, pass with problems, retry, escalate to a person, stop), section 5 (repeat/require), section 9.1.2 ("The Track: append-only, server time-stamped, hash-chained per record").
Actual: The envelope is a faithful v1. The declared extensions (a `role` on a step, `gate.route`, `record_type`/`folder`/`object` on the manifest, the cog-review Gate recorded with its delegating sentence) are sound and the README says so. The gaps: no retry or escalate outcome (F-arch-5), no consensus step, and a Track whose entries each carry a digest of themselves, which detects corruption of one line but not deletion or reordering of lines, so it is not the audit trail section 9.1.2 promises.
Evidence: `hubproto/envelope.py`; `hubproto/track.py` `append`; `hubproto/release.py` `render_manifest`.
Suggested fix: `record["prev"] = previous record_digest` in `Track.append` (one line, and it makes the per-entry digest meaningful); add `repeat`/`require` to the manifest schema even if only the extractor uses it.

### F-arch-16: Electronic-signature semantics: the payload digest is optional, the meaning is free text, and the decision is not chained to the record
Severity: major
Area: security
Steps:
1. Read `Runner.decide(... payload_digest=None ...)`: the digest is compared only "if payload_digest"; `cli.cmd_decide` never passes one; `shell.post_decide` passes the hidden form field, which the client controls.
2. Read `_record_decision`: `signature = sha256({by, verdict, payload_digest, at})[:24]`; `meaning` is whatever the form says, default "approved <title>"; the kernel gets a `record.decision` event carrying the dict, and the next `record.transitioned` event does not reference it.
3. Read `dms.render_markdown`: the signature page prints `by`, `at`, `meaning`, the 24-hex signature and 16 hex of the payload digest.
Expected: Plan section 9.1.2: meaning from a closed list; the decision carries the digest of the payload the person saw; "the record's next event carries the decision id. Verification re-hashes both"; step-up authentication at signing.
Actual: A CLI approval signs nothing in particular (no digest), a shell approval signs a digest the browser supplied, the meaning is uncontrolled text, and the signature is a truncated hash with no link to the release digest, the run, the step or the previous Track entry. These are all easy to fix and they are what the README means by "every decision is evidence", so they should be fixed before anyone shows an auditor a snapshot.
Evidence: `hubproto/runner.py` `decide`, `_record_decision`; `hubproto/cli.py` `cmd_decide`; `hubproto/shell.py` `post_decide`.
Suggested fix: Make `payload_digest` mandatory in `decide` (the CLI can fetch it from `pending`); validate `meaning` against a closed list rendered from a signature-policy statement; include `release`, `run_id`, `step` and the previous Track digest in the signed material; have `Kernel.transition` take the `decision["signature"]` and store it on the transition event.

---

## 3. Production path

### F-arch-17: Cog steps execute synchronously inside the HTTP request, under one process-wide lock; hub and worker timeouts disagree
Severity: major
Area: performance
Steps:
1. Read `shell.Handler.do_GET`/`do_POST`: `with self.lock:` (an `RLock` on the class) around every request.
2. Read `shell.post_start` -> `Runner.submit` -> `advance` -> `cad.CADExporter.task_run` -> `call_worker(timeout=120.0)`, three to five worker calls per design, each up to 120 s, all under that lock.
3. Read `worker.freecad_export` / `kicad_run`: `subprocess.run(..., timeout=600)`.
Expected: Plan section 3: "Workers run as separate pods"; the shell is a client of a run API; runs pause with "exit code 3, pending decision" rather than blocking a request.
Actual: While alice's design export runs, every other user's GET blocks (the lock covers reads too). If the worker takes more than 120 s the hub records `worker-unreachable`, fails or warns the step, and the worker keeps running for up to 600 s on work nobody will collect; there is no job id to retrieve it. `docker compose exec hub hubproto run ...` from a second process is not under the lock at all (F-arch-20, F-arch-21).
Evidence: `hubproto/shell.py` `Handler.lock`, `do_POST`; `hubproto/cad.py` `call_worker`; `hubproto/worker.py` `freecad_export`.
Suggested fix: Make `submit` enqueue and return; run `advance` on a worker thread or process with a per-run lock; make `/invoke` asynchronous (job id, poll or callback) and record the job id on the Track so a restart can collect results. The lock can then cover only the kernel write path.

### F-arch-18: Runs are neither durable nor idempotent across a crash; interrupted runs are never resumed
Severity: major
Area: architecture
Steps:
1. Read `Runner._save`: `state.json` is written with `write_text` (no temp file + rename). Read `Track.append`: open-append-write, no fsync.
2. Read `Runner.advance`: a step is marked in `state["steps"]` only after its envelope returns. A process that dies after `POIssuer` created the PO record and appended `po.issued`, but before `_save`, will on the next `advance` run `po-issuer` again and issue a second PO; the same holds for `ledger-poster` (second journal), `capa-opener` (second CAPA run), `shortage-requester` (duplicate purchase requests).
3. Search for code that scans runs on startup and resumes or marks them interrupted: none (`entrypoint.sh` starts the shell; `Runner` has no `recover`).
Expected: Plan section 5 "Durability": runs "survive controller restarts and resume exactly where the Track says they were"; section 13 calls this the Phase 0 blocker and proposes "a minimal checkpoint-on-Track resume" if dbos slips.
Actual: The Track is written before each step (`step.started`) which is the right checkpoint, but nothing reads it to resume, writes are not atomic, and the cogs with side effects have no idempotency key. The plan's own mitigation (checkpoint-on-Track resume) is half built.
Evidence: `hubproto/runner.py` `_save`, `advance`; `hubproto/track.py` `append`; `hubproto/procurement.py` `POIssuer.task_run`.
Suggested fix: Write `state.json` via temp file and `os.replace`; give every `step.started` an attempt id that the cog passes as an idempotency key to kernel commands (the kernel refuses a second `record.created` with the same key); add `Runner.recover()` on boot that replays the Track and either resumes or marks `interrupted`.

### F-arch-19: The record projection is rebuilt on every append, so per-command cost grows linearly with history
Severity: major
Area: performance
Steps (reproduced; script `perf_probe.py` in my scratchpad, quoted here):
1. `k = Kernel(tmp/"kernel.db")`; loop `k.create("task", "Tasks", {...})` then `k.set_fields(...)` 3,000 times, timing each 500.
2. Observe `Kernel.append` sets `self._cache = None`; `create` calls `next_number` (full rebuild) and `get` (full rebuild); `set_fields` calls `require` and `get` (rebuild).
Expected: Roughly constant cost per command.
Actual:
```
records=  500 events=  1000  avg  1.7 ms per command
records= 1000 events=  2000  avg  4.6 ms per command
records= 2000 events=  4000  avg 17.7 ms per command
records= 3000 events=  6000  avg 30.7 ms per command
one full projection rebuild at 6000 events: 0.054s
2000 Track.append: 2.91s  (each append re-reads the whole file to compute seq)
```
A nine-step run performs about 40 kernel appends, so at 6,000 events a design-release costs about 1.2 s of rebuilds under the global lock, at 60,000 events about 12 s, and `query`, `links_of` (which scans every record's links), `Runner.list` (reads every `state.json`; called by `page_home`, `page_inbox`, `run_for_record`) and `Inbox.for_principal` (reads the whole JSONL) scale the same way. This is the plan's "Event store + projections" without projections.
Evidence: `hubproto/kernel.py` `Kernel.append` (`self._cache = None`), `_records`, `next_number`, `links_of`; `hubproto/runner.py` `list`, `run_for_record`; `hubproto/track.py` `append`.
Suggested fix: Maintain the projection incrementally (apply the appended event to the cached record instead of discarding the cache) and persist it (a `records` table with `locator`, `type`, `folder`, `state`, `fields` JSON, plus a `links` table and a per-type counter), rebuilt from events only on demand. Keep `seq` in the Track as a counter in memory or from the last line. This is the first thing the Postgres move needs anyway.

### F-arch-20: The shell and any CLI or `tick` process disagree about records until the shell happens to write
Severity: major
Area: records
Steps (reproduced; script `stale_probe.py`, same mechanism as the documented `docker compose exec hub hubproto ...` and `docker compose run --rm tick`):
1. Process A: `Hub(root)`; create a document; `a.kernel.get(locator)` (warms `Kernel._cache`).
2. Process B (`subprocess`): `Hub(root)`; `dms.create(...)` a second document; print its locator.
3. Process A: `a.kernel.get(loc)`, `a.kernel.query(type_="document")`; then `a.kernel.append("probe.touch", ...)` and `get` again.
Expected: A sees B's write on the next read.
Actual:
```
second process created: rec:document:DOC-0002
process A sees it right after: False
process A query(type=document) count: 1
process A sees it after its own append: True
```
`Kernel._cache` is invalidated only by appends in the same process. In the shipped compose setup the shell holds its `Hub` for the life of the container; `hubproto upload`, `hubproto run`, `hubproto decide` and `hubproto tick` (a separate container) write through their own connection. Records, assignees added by `tick`, and transitions made by CLI decisions do not appear in the shell until some shell request appends an event. The same applies to `Hub.frames` (F-arch-41). The `Handler.lock` comment in `Kernel.__init__` ("callers serialize writes") is not true across processes.
Evidence: `hubproto/kernel.py` `Kernel._records` / `append`; `docker-compose.yml` `tick` service; `README.md` "Run it".
Suggested fix: Invalidate on `MAX(seq)` change (one indexed query per read is cheap) until F-arch-19's persisted projection makes the cache unnecessary.

### F-arch-21: SQLite is used as a single shared connection with no WAL, no busy timeout and no uniqueness on record creation; numbering is a count
Severity: minor
Area: records
Steps: Read `Kernel.__init__`: `sqlite3.connect(..., check_same_thread=False)`, default journal mode, default 5 s busy timeout; `events` has only the autoincrement key. Read `next_number`: `len(existing) + 1`. Search for `gapless`: the flag in `config/record-types.json` is read nowhere.
Expected: Plan section 8.1: gapless where required (journals, invoices); section 11: Postgres with one cluster.
Actual: With two writer processes (F-arch-20) two `create` calls can compute the same number and both insert, since nothing is unique; `gapless: true` is decorative; yearly numbering uses `date.today()` in server local time. For the Postgres move: the events table shape (`seq`, `at`, `type`, `aggregate`, `data`, `run_id`, `release_digest`, `by`) is fine; it needs an `aggregate_version` and a unique `(aggregate, aggregate_version)` for optimistic concurrency, and a `numbering` table with a row per series.
Evidence: `hubproto/kernel.py` `__init__`, `next_number`, `create`; `config/record-types.json` (`"gapless": true` on `journal`, `purchase-order`).
Suggested fix: As above; in the meantime `PRAGMA journal_mode=WAL` and a `UNIQUE(aggregate)` partial index on `record.created` events.

### F-arch-22: Extracted document text is stored in record fields and the event stream
Severity: minor
Area: DMS
Steps: Read `dms.add_version`: `text = extract_text(content, mime)` (up to 20,000 characters) is written to `record.fields_set` as `text` and 2,000 characters to `document.version_added`. Read `Kernel.query(text=...)`: `json.dumps(rec["fields"])` per record per query.
Expected: Plan section 8.2: text extraction feeds search (OpenSearch or pgvector); content is in object storage.
Actual: Every version of a text document adds up to 20 KB to the projection every reader rebuilds (F-arch-19), and full-text search is a JSON dump scan. `BlobStore` (content-addressed, dedup) is the right seam for object storage; the text should go to an index, not the record.
Evidence: `hubproto/dms.py` `add_version`, `extract_text`; `hubproto/kernel.py` `query`.
Suggested fix: Store `text` beside the blob (or in a `document_text` table) and give `query(text=)` an index.

### F-arch-23: Identity is a bare member name everywhere; there is no OIDC-subject mapping seam
Severity: minor
Area: security
Steps: Read `authz.principal_name` (strips `person:`/`cog:`), `Runner.submit` (`owner_name`), kernel `by` columns, `Inbox` `to`/`from`, `shell._dispatch` (`self.user`), the `## Members` grammar (`- alice (person): ...`).
Expected: Plan section 7.1 and 11: principals are OIDC subjects, never emails or names; Keycloak is the only identity provider; cogs are service accounts in the same realm.
Actual: The org-model Frame is the identity source and the name is the key. Moving to Keycloak touches the member statement (needs `sub` or an external id), `authz` (roles by subject), every `by`/`owner`/`to` field, and the shell's principal resolution, which is an inline expression in `_dispatch` rather than a function.
Evidence: as listed.
Suggested fix: Introduce `principal_id` as the key now (the org-model sentence can carry `alice (person, sub=...)`), route every "who" through one `resolve_principal(request)` in the shell and one `principals.py` for the rest.

### F-arch-24: No company dimension; periods, numbering and folders are global
Severity: minor
Area: records
Steps: Read `Kernel.periods`, `next_number`, `release.render` (`folders` is one flat list), `post_journal` (period by date only).
Expected: Plan section 13 tenancy decision: "One organization per Hub, multiple companies inside it ... multi-company accounting is a kernel concern."
Actual: One organisation per Hub root works as designed. Multiple companies would need a company on every financial record, per-company periods and numbering series, and a scope tree deeper than folder strings. Not a prototype fault; a note for the kernel's record envelope before accounting grows.
Evidence: as listed.
Suggested fix: Add `company` to the record envelope (next to `folder`) and to numbering series now, defaulting to one.

### F-arch-25: Hub-side cogs have no manifest, and the registry is a hard-coded class list
Severity: minor
Area: architecture
Steps: Read `CogRegistry.__init__` (class tuple plus `cad.register`, `procurement.register`); `Cog` has `id`, `version`, `kind`, `reaches`, `tasks`, `description` as class attributes, no input/output schema, no digest. Compare `worker.py`: a real manifest-like `/cogs` endpoint, `/invoke`, binding and timing.
Expected: Plan section 3: run controllers "materialize Cog workers from pinned registry digests, invoke them through declared interfaces".
Actual: The worker proves the remote seam; the twenty-odd hub cogs do not use it. Moving a hub cog to a pod means giving it the worker's shape (manifest, HTTP envelope, no `RunContext`), which today is a rewrite per cog because each one reaches into `ctx.kernel`.
Evidence: `hubproto/cogs.py` `CogRegistry`; `hubproto/worker.py` `Handler`.
Suggested fix: Define a cog card as data (JSON next to the class, or a `manifest()` method), make `CogRegistry` load cards, and route every invocation through one `invoke(card, task, input, ctx)` that can be local or HTTP. Pairs with F-arch-1 and F-arch-6.

### F-arch-26: Blast radius per module for the production shape
Severity: question
Area: architecture
Steps: Cross the plan's production components (section 3 and 11) with the module map in DEV-NOTES.
Expected: The README says the production shape "keeps the same seams". This table is my estimate of what that costs; the question for the maintainer is whether it matches theirs.
Actual:

| Module | Postgres | Object storage | OpenFGA | Keycloak | Remote cogs | Durability | Multi-company | Estimate |
|---|---|---|---|---|---|---|---|---|
| `kernel.py` | event table + persisted projections + numbering series + idempotency keys | - | - | `by` becomes a subject | command API with caller token (F-1) | - | company on envelope | rewrite of storage; API survives |
| `dms.py` / `BlobStore` | versions table | `put/get` to S3; text to index | - | - | - | - | - | small |
| `authz.py` | - | - | replace `check`; add explain path | subject keys | - | - | - | small code, large semantics |
| `release.py` | - | bundle to OCI (Harbor) | render model + tuples | - | - | - | - | medium (`render` grows a second target) |
| `runner.py` | run state to tables | - | - | - | HTTP invoke, job ids | queue, resume, atomic writes, per-run locks | - | core rewrite (`submit`, `advance`, `_human_gate`, `decide`, `tick`) |
| `cogs.py`, `procurement.py`, `cad.py` | - | - | - | - | drop `ctx.kernel`; use client; cards | idempotency keys | - | every cog touched, mechanically |
| `inbox.py` | messages table | - | - | - | - | - | - | medium; channel adapters are new |
| `track.py` | Track table or object-locked files | - | - | - | - | chain, retention classes | - | small |
| `shell.py` | - | - | - | OIDC session replaces `as` | shell becomes an HTTP client of a run API | - | - | all ~1,100 lines call Python objects; rewrite as a client |
| `cli.py`, `cli_records.py` | - | - | - | token | same client | - | - | medium |
| `compile.py`, `extract.py`, `guards.py`, `frames.py` | - | - | - | - | - | - | - | unchanged (Frames source moves to git + registry) |
| `worker.py` | - | - | - | service token | auth, async jobs, limits | - | - | small |
| `config/*.json` | - | - | - | - | - | - | - | become release-pinned documents (F-3) |

The policy side (compile, extract, guards, frames, release) is production-shaped already; that is the part of the plan the prototype was meant to prove and it holds. The execution side (runner, cogs, kernel, shell) keeps the seams in name and needs its storage, concurrency and capability model rebuilt; that is normal for a prototype and the point of this review is that F-1, F-19 and F-18 should be done before more cogs are written on the current shape.
Evidence: `DEV-NOTES.md` module map; the modules named.

---

## 4. Data model

### F-arch-27: State names are derived from step titles, so `required_by_state` keys, `reject_to` and routes silently detach when a sentence is edited; two keys are already dead
Severity: major
Area: workflow
Steps (reproduced; script `wf_probe.py`): compile the repository Frames in a throwaway hub, release, and for every `required_by_state` key check whether any workflow for that record type produces that state.
Expected: Every key names a state that exists, or the compile refuses.
Actual:
```
document.required_by_state['in-review']  (workflow states for document: ['awaiting-approve-the-document', 'effective', 'open', 'rejected'])
part.required_by_state['released']       (no process produces records of type part)
```
`in-review` is never entered: the runner creates documents in `open` and the waiting state is `awaiting-approve-the-document`, so the "title and owner before review" rule never fires. `part.released` is entered only by `PartReleaser` calling `Kernel.create(state="released")`, and `create` does not evaluate `required_by_state` (only `transition` does), so the "title and revision before release" rule never fires either. The keys that do work (`awaiting-disposition-the-nonconformance`, `awaiting-verify-the-actions`, ...) are slugs of SOP step titles: rewording "Disposition the nonconformance" to "Disposition the NCR" compiles cleanly and drops the NCR's required-fields Guard with no finding, because `consistency_guard` never looks at the record-type config's keys. The same slug is the key for `Rejections return to`, `At <title>` routes and SLAs (those three are checked, which is good).
Evidence: `hubproto/extract.py` `ConventionExtractor.extract` (`"id": slug(title)`); `hubproto/release.py` `render_workflow` (`f"awaiting-{step['id']}"`); `hubproto/kernel.py` `create` (no `required_missing`), `transition`; `hubproto/guards.py` `consistency_guard` (no cross-check); `config/record-types.json` `document`, `part`.
Suggested fix: Give checkpoints a stable id in the grammar ("4. [quality-manager] Disposition the nonconformance #disposition") and key states, routes, SLAs, rejections and `required_by_state` by it; or key `required_by_state` by checkpoint title and resolve at render time. Either way, add a Consistency Guard that errors on a `required_by_state` key no workflow of that type reaches, and make `Kernel.create` honour `required_by_state` for the initial state.

### F-arch-28: Suppliers, purchase orders and periods are a second storage model, and module invariants sit in the kernel class
Severity: minor
Area: records
Steps: Read `Kernel._latest`, `suppliers`, `purchase_orders`, `periods` (aggregates keyed `supplier:`, `po:`, `period:` with "last write wins" merge, outside the record model: no number, no state, no links, no labels). Read `procurement.POIssuer`: creates a `purchase-order` record *and* appends `po.issued` to `po:<number>` so `ThreeWayMatcher` can find it. Read `procurement.supplier_approved`: two sources of truth (seed aggregate or an approved `supplier-evaluation` record). Read `Kernel.file_bill`, `mark_bill`, `post_journal`, `file_document`, `publish_document`: finance and quality logic inside the generic kernel.
Expected: Plan section 8.1: "Module code adds fields and invariants; it never adds a second way to store, number, link, attach, label or retain"; module commands are "thin wrappers" outside the kernel.
Actual: A prototype shortcut from the first baseline that the later modules worked around rather than removed; each new module that reads a PO or a supplier now has to know both shapes. The kernel file will keep growing with per-module methods.
Evidence: `hubproto/kernel.py` (`_latest` and the module command section); `hubproto/procurement.py` `POIssuer.task_run`, `supplier_approved`.
Suggested fix: Make `supplier` and `period` record types, drop the `po:` aggregate in favour of the `purchase-order` record, and move `post_journal`/`publish_document` into `finance.py`/`quality.py` modules that call the kernel's generic commands plus their own Guards.

### F-arch-29: Link vocabulary has duplicate directions and cogs probe it at runtime
Severity: minor
Area: records
Steps: Read `config/links.json`: both `"defines": "defined-by"` and `"defined-by": "defines"` are forward relations, as are `realized-by`/`realizes`; `PartReleaser` links `part defined-by design`, so "what defines this part" can be stored either way. Read `NCROpener`: `"caused-by" if "caused-by" in ctx.kernel.link_vocab else "concerns"`. Search for cardinality checks: none.
Expected: Plan section 8.1: a closed vocabulary with cardinality rules as Guards.
Actual: Two ways to store one fact; a cog that guesses relation names at runtime; no cardinality. Small today, hard to clean once links exist.
Evidence: `config/links.json`; `hubproto/cad.py` `PartReleaser.task_run`; `hubproto/cogs.py` `NCROpener.task_run`.
Suggested fix: One forward name per pair, inverse derived; a `cardinality` field per relation enforced in `Kernel.link`; make an unknown relation a compile-time error in the process sentence rather than a runtime fallback.

### F-arch-30: Numbering series are not a first-class, pinned configuration
Severity: minor
Area: records
Steps: Read `next_number` (prefix, digits, yearly from the type definition; count of existing records as the sequence). See F-arch-21 for concurrency and F-arch-3 for pinning.
Expected: Plan section 8.1: numbering series are configuration documents with prefix, sequence, optional fiscal-year reset, gapless where required.
Actual: Series are three fields on the type; there is no series object to share between types, no fiscal year, no persisted counter. Fine for the prototype; the fix is part of F-arch-19's persisted projection.
Evidence: `hubproto/kernel.py` `next_number`.

### F-arch-31: No aggregate version; inputs cannot reference the event version they read
Severity: minor
Area: records
Steps: Read `Kernel.append` (global `seq` only) and `Runner._human_gate` (`payload_digest` is the only binding between what a person saw and the record).
Expected: Plan section 4.3: "Domain record: Aggregate id + event sequence number ... inputs reference the event version they read."
Actual: A Gate signs a digest of a cog's payload, not a record version, so a `set_fields` by another cog between the Gate's `pending` and the person's decision is invisible to the digest check (the record changed; the payload did not). Fits with F-arch-16.
Evidence: `hubproto/kernel.py` `append`; `hubproto/runner.py` `_human_gate`, `decide`.
Suggested fix: Store `version` per aggregate on each event; include the record version in `pending` and refuse a decision when it moved.

---

## 5. Security architecture

### F-arch-32: Identity is the `as` parameter and principal resolution is inline
Severity: minor
Area: security
Steps: Read `shell._dispatch`: `self.user = form["as"] or query["as"] or DEFAULT_USER`; any string is accepted; `_page` adds unknown names to the member list with type `?`. Cogs can be selected (`?as=ap-reviewer`): `/decide` is safe (cogs are never assignees), but `/upload` and `/start` run under the cog's permissions and record the cog as `filed_by`.
Expected: README: "identity is the as parameter; no SSO in the prototype" (by design). Plan: Keycloak OIDC.
Actual: A declared shortcut. The architectural gap is that there is no single function to replace: the principal is computed in `_dispatch`, trusted by every handler, and re-read from the form by `hidden_as`. See F-arch-23.
Evidence: `hubproto/shell.py` `_dispatch`, `_page`.
Suggested fix: `resolve_principal(headers, form, query) -> Principal` in one place, with a `type` the shell can refuse (`cog` cannot drive the UI).

### F-arch-33: Inbox: spoofable sender, unlabelled inbound content, a status query that ignores permissions, and attachment contents written straight into record fields
Severity: minor
Area: security
Steps:
1. Read `cli.cmd_mail_in` / `inbox.parse_eml`: `From:` header becomes the sender; `shell.post_msg`: sender is `self.user`. Nothing verifies either.
2. Read `ReviewerCog.task_inbox`: `"status" in text` returns every waiting item in the organisation (`ctx.runner.list()`) to any sender; `"file"`/`"please"`/`"process"` plus an attachment submits `att["content"]` as the record's fields with `owner=self.id`; `Kernel._check_fields` keeps unknown keys, so arbitrary JSON lands in the event stream, unbounded.
3. Search for a sensitivity label or "untrusted" marker on inbound messages: none.
Expected: Plan section 7.2: "Inbound content is data, not instruction"; attachments get a label before any model sees them; a cog answers only within its roles.
Actual: The stand-in cogs are deterministic so prompt injection is moot today, but the seam a model-backed cog would plug into hands it the raw body with no label and lets it act (file, list status) without checking the sender's permissions. The HR-record case in the plan (a cog must not forward above its channel ceiling) has no hook.
Evidence: `hubproto/inbox.py` `send`; `hubproto/cogs.py` `ReviewerCog.task_inbox`; `hubproto/cli.py` `cmd_mail_in`.
Suggested fix: Label every inbound message (`label`, `trusted: false`, verified sender or `unknown`); have `task_inbox` consult `authz` with the sender as principal for status and filing; validate attachment JSON against the record type's fields before `submit`; cap sizes.

### F-arch-34: Upload and download handling, and tracebacks in responses
Severity: minor
Area: security
Steps: Read `shell._read_body` (whole body into memory, `Content-Length` trusted, no limit); `_file`; `page_download` (`Content-Type` from the stored mime, which came from the uploader's filename; `Content-Disposition: attachment` set, good); `_dispatch`'s last `except Exception` (full traceback rendered to the client); `BlobStore.put` (content-addressed path, no traversal).
Expected: Bounded uploads; no stack traces to browsers in anything beyond a local prototype.
Actual: Acceptable for the panel; `attachment` disposition and `html.escape` everywhere I looked (`esc`, `href` via `urlencode`) keep stored content from executing in the shell. Note for production: `page_download` should set `X-Content-Type-Options: nosniff`, and uploads need a size cap and a quarantine class before the classifier cog reads them.
Evidence: `hubproto/shell.py` `_read_body`, `page_download`, `_dispatch`.

### F-arch-35: The CAD worker is unauthenticated, runs untrusted files with no sandbox or limits, and drops the tools' stderr
Severity: major
Area: security
Steps:
1. Read `worker.Handler.do_POST`: any client on the network can POST `/invoke`; no token, no size limit on `content_b64`, `ThreadingHTTPServer` with no concurrency cap; `subprocess.run(..., timeout=600)` with `env={**os.environ, ...}` (the worker's whole environment reaches the tool process).
2. Read `freecad_export`: FreeCAD opens the uploaded `.FCStd` in its own interpreter. FreeCAD documents can carry `App::PropertyPythonObject` values, which FreeCAD unpickles on load; a crafted file is code execution inside the worker. KiCad is invoked as `kicad-cli` on the file, which is the plan's containment and is fine.
3. Read `freecad_export` / `kicad_run`: `capture_output=True`, output discarded; a failure surfaces as "FreeCAD produced no output" with nothing on the Track; `envelope.raw` stays `None`.
Expected: Plan section 11: workers "run as separate pods with network policies"; section 9.6: tools "in their own worker image"; envelope `raw` for the tool's output; Guards decide from reports.
Actual: The separate image is right and the hub never imports the tools (confirmed: `cad.py` imports only stdlib and `envelope`). But the worker will execute whatever an authenticated shell user uploads, with the worker's privileges, and a user who can `/start design-release` can reach it. A network policy is the plan's answer, which is not a property of this code; the code should carry a shared-secret header, a byte limit, a per-request subprocess sandbox (`seccomp`/no network, `ulimit`), and a scrubbed environment at least.
Evidence: `hubproto/worker.py` `Handler.do_POST`, `invoke`, `freecad_export`, `kicad_run`; `docker/cad-worker/Dockerfile` (tools installed as `worker` user, no further isolation).
Suggested fix: As above; put `stdout`/`stderr` tails into `envelope.raw` so a failed export is diagnosable from the Track.

### F-arch-36: GPL containment holds; write the rule down before the next CAD cog breaks it
Severity: question
Area: architecture
Steps: Read `docker/cad-worker/Dockerfile` (KiCad and FreeCAD in a separate image; only `envelope.py` and `worker.py` copied in), `worker.kicad_run` (CLI subprocess, no `pcbnew` import), `worker._FREECAD_SCRIPT` (runs inside FreeCAD's interpreter, LGPL), `cad.py` (stdlib parsing of FCStd XML and KiCad S-expressions in the hub, no tool import).
Expected: Plan section 9.6: "KiCad is driven through its CLI in a separate process so the GPL stays contained; FreeCAD's LGPL needs nothing special."
Actual: Matches the plan. The risk is the next contributor importing `pcbnew` for a DRC detail the CLI does not expose, which would pull GPLv3 into the worker's own code. The containment rule is not stated in DEV-NOTES or the worker's docstring. Is the hub's own licence decided? That decides how strict this needs to be.
Evidence: as listed.
Suggested fix: One paragraph in DEV-NOTES under "CAD and ECAD": tools are reached only through their CLI or the tool's own interpreter; never `import pcbnew`; the worker's Python stays Apache/MIT-compatible.

---

## 6. Testability and operability

### F-arch-37: Time is not injectable
Severity: minor
Area: architecture
Steps: `grep -n "date.today()\|utcnow()" hubproto/*.py`: `runner` (due dates, `tick`), `cogs` (CAPA due, effectiveness date, training assignment), `kernel` (yearly numbering, `publish_document`), `dms`, `release.current(at)` (the one injectable path, used by compile tests).
Expected: SLA, escalation, cutover and period logic testable without monkeypatching the clock.
Actual: Tests pass `today=` to `tick` and monkeypatch elsewhere; cutover and period tests depend on the real calendar (`Kernel.seed` opens the months around today). Cheap to fix now: one `clock` on `Hub`.
Evidence: as listed; `tests/test_workflow.py` `test_tick_escalates_overdue_items_once` (passes `today`).

### F-arch-38: No logs, metrics or meaningful health check
Severity: minor
Area: architecture
Steps: Read `shell.Handler.log_message` and `worker.Handler.log_message` (both silenced); search for `logging`: none; `/healthz` returns `ok` without touching the database, the release store or the worker.
Expected: Plan section 3: OTel from the operator; an operator can see which run is blocking the lock (F-arch-17) and why a worker call failed (F-arch-35).
Actual: The Track is the only trace and it records outcomes, not timings of locks or request ids; a hung CAD call shows as nothing anywhere. `healthz` would report healthy with a corrupt `kernel.db`.
Evidence: as listed.
Suggested fix: `logging` with a request id in the shell and worker; `/healthz` runs `SELECT 1`, reads `releases.current()`, and reports the worker's `/healthz` as a sub-status.

### F-arch-39: No schema or format versioning for the stores
Severity: minor
Area: architecture
Steps: Read `Kernel.__init__` (`CREATE TABLE IF NOT EXISTS events`, no version row); `state.json` and `track.jsonl` carry no format version; `envelope: 1` and `profile: hubproto/policy-release [0.1]` do (good).
Expected: A state volume that survives image rebuilds (Dockerfile comment) implies migrations.
Actual: The first change to the `events` columns or the run state shape has no migration path for the `hub-state` volume.
Evidence: `hubproto/kernel.py` `__init__`; `Dockerfile` (`VOLUME ["/hub/state"]`).
Suggested fix: A `meta(key, value)` table with `schema_version`, and a `version` key in `state.json`.

### F-arch-40: Test suite: strong on compile and workflow semantics, silent on the failure modes this review found
Severity: minor
Area: architecture
Steps: `python -m pytest -q`: 171 passed in 9.3 s. Read the test names in `tests/*.py`.
Expected: Tests that would have caught F-arch-7, 18, 19, 20, 27 and 35.
Actual: Good: every suite runs a real `Hub` on a temp directory, the shell is tested over HTTP, the worker in fake mode, and the grammar has a "every sentence in the test frames parses" test. Missing: a duplicate-title test; a crash-between-step-and-save resume test; any test with two `Hub` processes or threads; a test that `required_by_state` keys are reachable states; a property or table test for the regexes in `extract.py` (they are the policy parser; `_ROUTING` alone has five alternations); any run of the worker with real tools in CI (the compose default `CAD_TOOLS=full` is untested). The `widgets` fixture copies the company Frames, so the workflow suite depends on the example org model.
Evidence: `tests/conftest.py`; `tests/test_workflow.py`; `tests/test_cad.py` (`fake_worker`).

### F-arch-41: The shell holds Frames loaded at start, so the advertised host-edit-and-recompile flow shows stale "Why?" lines
Severity: minor
Area: policy
Steps: Read `Hub.__init__` (`FrameStore(frames_dir)` once); `shell.page_frame`, `page_why` (`self.hub.frames`); README "Run it" (edit a Frame on the host, `docker compose exec hub hubproto compile`, review, release).
Expected: After a release from a second process, the shell's `/frame/<name>#L<n>` link from a "Why?" opens the line the new release cites.
Actual: The compile ran in a `docker compose exec` process with its own `FrameStore`; the shell process still serves the text it loaded at container start, so line anchors from the new release point into the old text until restart. The release itself is correct (bundle and rules are read from disk per request). Same family as F-arch-20.
Evidence: `hubproto/app.py` `Hub.__init__`; `hubproto/shell.py` `page_frame`.
Suggested fix: Reload `FrameStore` when a Frame file's mtime or the release digest changes, or serve Frame text from the release's bill of materials (the `sha256` is already recorded) rather than from the working tree.

---

## 7. The ten changes with most leverage, in order

1. **Kernel command API with a caller token and capability-scoped clients** (F-arch-1, 6). `hubproto/kernel.py` every public command; `hubproto/cogs.py` `RunContext`, `Cog._require_grant`; `hubproto/runner.py` `Runner._ctx`. Makes the plan's central claim a property and gives every later module one write path.
2. **Stable checkpoint ids and a Guard that cross-checks record-type keys** (F-arch-27, 7). `hubproto/extract.py` `_PROCESS_STEP`, `ConventionExtractor.extract`; `hubproto/guards.py` `consistency_guard`; `hubproto/release.py` `render_workflow`; `hubproto/kernel.py` `create`; `config/record-types.json`.
3. **Persisted, incrementally maintained projection; no per-process cache** (F-arch-19, 20, 21, 30). `hubproto/kernel.py` `Kernel.append`, `_records`, `get`, `query`, `links_of`, `next_number`; `hubproto/track.py` `Track.append`. Removes the cliff and the two-process disagreement in one change.
4. **Run execution off the request thread with per-run locks; asynchronous `/invoke`** (F-arch-17). `hubproto/runner.py` `submit`, `advance`, `decide`; `hubproto/shell.py` `Handler.do_POST`; `hubproto/worker.py` `Handler.do_POST`; `hubproto/cad.py` `call_worker`.
5. **Atomic saves, idempotency keys on side-effecting steps, `Runner.recover()` on boot** (F-arch-18). `hubproto/runner.py` `_save`, `advance`; `hubproto/procurement.py` `POIssuer`, `StockBooker`, `ShortageRequester`; `hubproto/cogs.py` `LedgerPoster`, `CAPAOpener`, `NCROpener`, `DocumentPublisher`.
6. **Mandatory payload digest, closed meanings, chained Track, decision id on the transition** (F-arch-16, 15, 31). `hubproto/runner.py` `decide`, `_record_decision`; `hubproto/track.py` `append`; `hubproto/kernel.py` `transition`; `hubproto/cli.py` `cmd_decide`.
7. **Pin the configuration documents in the release and read them from there** (F-arch-3). `hubproto/release.py` `ReleaseStore.save`, `bundle`; `hubproto/kernel.py` `Kernel.__init__`; `hubproto/runner.py` `submit`, `decide`.
8. **Risk factors as a release-pinned document; scorer dispatches on the process object** (F-arch-4). `hubproto/cogs.py` `RiskScorer.task_run`, `LedgerPoster.task_run`; `hubproto/compile.py` `compile_proposal` (bill of materials).
9. **Scoped grants and declared child processes** (F-arch-2, 13). `hubproto/runner.py` `_record_decision`, `submit`; `hubproto/extract.py` process step grammar; `hubproto/guards.py` `consistency_guard`.
10. **Cog cards as data, one invoke path local or remote, worker authentication and limits** (F-arch-25, 35, 11). `hubproto/cogs.py` `CogRegistry`, `Cog.invoke`; `hubproto/worker.py` `Handler.do_POST`, `invoke`, `freecad_export`; `hubproto/cad.py` `call_worker`; `hubproto/envelope.py` `make_envelope` (add `binding`).

Everything in sections 4.x of the plan (compile, Guards, Gate over changes, deterministic digest, cutover) is already in the shape the plan describes and needs none of the above; it is the execution half that must be reshaped before more domain modules land on it.

---

## Counts

- blocker: 1 (F-arch-1)
- major: 11 (F-arch-2, 3, 4, 7, 16, 17, 18, 19, 20, 27, 35)
- minor: 26
- question: 3 (F-arch-14, 26, 36)
- total: 41

Five most important: F-arch-1 (kernel does not enforce grants or `reaches`), F-arch-27 (states keyed by step titles; dead `required_by_state` keys), F-arch-19 with F-arch-20 (projection rebuilt per append; shell and CLI disagree), F-arch-17 with F-arch-18 (synchronous execution under a global lock; runs not durable or idempotent), F-arch-16 (signature semantics: optional digest, free-text meaning, no chain).
