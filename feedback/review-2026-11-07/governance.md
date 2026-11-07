# Governance and audit review (adversarial committee, 2026-11-07)

Lens: ISO 9001 / regulated-QMS lead auditor trying to break the integrity of approvals and records
(7.5 control of documented information, 8.5.6 control of changes, 7.2 competence evidence, 10.2 CAPA,
segregation of duties, 21 CFR Part 11-style e-signature expectations: signature bound to the record and its
meaning, audit trail that cannot be altered without detection).

Method: code reading plus reproductions against throwaway sandbox hubs (frames copied from the repo at commit
`d36910b`, `HUBPROTO_ALLOW_STAND_IN=1`, `HUBPROTO_COG_QUEUE=inline`). The live hub at :8080 was not touched.
Every finding below was reproduced; line numbers are from the tree as of this review (others were committing in
parallel, so they may drift by a few lines).

Common prelude for every repro (a fresh bootstrapped sandbox per script):

```python
# D = a copy of a hub made with: hubproto --root $D init; hubproto --root $D bootstrap-release --as bob --second carol
import os; os.environ["HUBPROTO_ALLOW_STAND_IN"] = "1"; os.environ["HUBPROTO_COG_QUEUE"] = "inline"
from pathlib import Path
from hubproto.app import Hub
from hubproto.agent import AgentTools, AgentStore
hub = Hub(D); R = hub.runner
def tools(cog, requester="carol"):
    return AgentTools(hub, cog, "probe", "act", 20, AgentStore(Path(D) / "state" / "agents"), requester=requester)
```

---

## 1. BLOCKER: A cog-written controlled SOP becomes effective with no human, and supersedes the human-approved one

**What I did**

```python
R.submit("document-control", {"record": {"title": "Calibration Procedure", "change_type": "major",
         "body": "# Calibration Procedure\n\n1. Calibrate every 90 days."}}, owner="alice")
# bob approves -> DOC-0001 effective, approved_by bob
out = tools("qa-manager").execute("draft_document", {"title": "Calibration Procedure",
         "text": "1. Calibration is optional.", "change_type": "minor", "submit": True})
```

**What happened** `{'document': 'DOC-0002', 'state': 'draft', 'change_type': 'minor', 'submitted': True,
'status': 'completed'}`. DOC-0002 (author qa-manager, revision A, a brand-new document) was routed "with low
risk" to the doc-reviewer cog, approved "within fixed criteria: change_minor holds", published, and DOC-0001
(bob's approved SOP) went to `obsolete` with `DOC-0002 supersedes DOC-0001`. No person was assigned, asked, or
countersigned at any point; the requester (carol) was only told afterwards. The same works for a person (alice
files a new SOP with `change_type: minor`). The tool even reports `'state': 'draft'` for a document that is
already effective.

**Why it matters** 7.5.2/7.5.3 require documented information to be reviewed and approved for adequacy. The SOP
text says the cog may approve "a minor *revision*"; the implementation trusts the author's own `change_type`
declaration, applies it to new documents (revision A), and `publish_document` supersedes any effective document
with the same *title*, so an AI author plus an AI approver can replace a human-approved procedure. The README
claim "Segregation of duties means it never signs its own drafts" is true but empty: another cog signs it.

**Suggested fix** Derive "minor" from the system (a revision of an existing effective document, with a diff
below a threshold), never from the submitter's field; treat a new document (no predecessor) as major. Supersede
only through an explicit `supersedes` link on a revision of the same document number, never by title match.
Require a person on any route where the author is a cog (or forbid cog-to-cog approval chains).

**Where** `hubproto/agent.py:277` (change_type from the agent's argument), `hubproto/kernel.py:514`
(supersede by title), `frames/iso9001/document-control-sop.frame.md:43,48` (low-risk route and minor criterion).

---

## 2. BLOCKER: A cog can propose dropping its own countersignature and the document SoD, and one owner releases it

**What I did**

```python
ops = ("@@ replace: The qa-manager cog may approve document-control documents when its review passes, "
       "countersigned by a quality manager.\n"
       "@@ with: The qa-manager cog may approve document-control documents when its review passes and the risk is not high.\n"
       "@@ delete: The author and the approver of a document must be different people.\n")
out = tools("qa-manager").execute("propose_policy_change", {"frame": "document-control-sop", "reason": "streamline", "ops": ops})
p = hub.proposals.load(out["proposal_id"])
hub.review(p["id"], "bob", approve_all=True, meaning="ok"); hub.release(p["id"], "bob")
rid = R.submit("document-control", {"record": {"title": "Major rewrite", "change_type": "major", "body": "# I\n\n1. x"}}, owner="alice")
hub.send("alice", "qa-manager", "Approval", "@qa-manager please approve this document", record=R.load(rid)["record"])
```

**What happened** Every change was classified narrowing (`widenings: []`), including
`countersign: 'quality-manager' -> None` and `remove segregation of duties on document: ['author', 'approver']`.
`readiness` was empty after bob alone; bob released. The next major document became `effective` on the
qa-manager's signature alone.

**Why it matters** README: "Removing a countersignature from a criterion is a widening, which needs two policy
owners." The two-owner rule for widenings is the hub's main policy-change control (and ISO 5.3 / 7.5 for the
policy documents themselves). Here the AI whose authority widens is the one proposing it, and a single owner can
release it. The unit test only covers `_widens`, not the paired "change" path that is actually taken.

**Suggested fix** In `_widens_over` treat a dropped `countersign`, a new/different condition set that is not a
superset, and any change of `conditions` as widening for `approval_criteria`; make removal of any `sod` rule (and
of a human checkpoint from a process) a widening; add a test that goes through `drift_guard` for each.
Consider forbidding a cog from proposing changes to its own criteria, profile or autonomy.

**Where** `hubproto/guards.py:290` (`widening = widening and _widens_over(...)`), `hubproto/guards.py:294`
(removals widen only for deny permissions), `hubproto/guards.py:305-316` (`_widens_over` ignores countersign).

---

## 3. BLOCKER: Approved and finished records are rewritten in place by cogs (`update_fields`)

**What I did**

```python
# (a) approved change request, awaiting the requester's verification
rid = R.submit("change-control", {"record": {"title": "Tighten torque spec",
      "description": "Change torque from 5 Nm to 6 Nm on line 2", "impact": "WI-3"}}, owner="alice")
st = R.load(rid); R.decide(rid, "carol", "approve", meaning="approve 6 Nm change on line 2",
      payload_digest=R.seen_digest(st), fields={"plan": "update WI-3", "verification": "audit line 2"})
tools("purchasing-assistant", "dave").execute("update_fields", {"number": "CR-2026-0001",
      "fields": {"description": "Remove torque check entirely on all lines", "impact": "none", "plan": "skip"}})
# alice verifies -> closed
# (b) acknowledged training record
#   carol starts training-acknowledgement for alice on DOC-0001; alice acknowledges -> TRN-00001 'acknowledged'
tools("qa-manager").execute("update_fields", {"number": "TRN-00001",
      "fields": {"document": "DOC-0099", "acknowledged": "2026-01-01", "person": "bob"}})
```

**What happened** (a) `{'ok': True, 'set': ['description', 'impact', 'plan']}`; the CR closed reading "Remove
torque check entirely on all lines" with carol's decision "approve 6 Nm change on line 2" still attached (its
`record_digest` no longer matches the record). (b) `{'ok': True}`: the acknowledged training record now says bob
was trained on DOC-0099 on 2026-01-01, signed by alice. A scan of terminal states shows `FINISHED_STATES` misses
`approved`, `acknowledged`, `ordered`, `received`, `active`, `accepted`, `released`; qa-manager,
quality-assistant and purchasing-assistant can rewrite ordered purchase requests, received receipts, active
items and acknowledged training records. The same tool also edits a record between a cog's signature and the
countersignature (verified on a CR: the countersigner signs different content from what the cog reviewed).
Permission falls back from `update` to `create`, so "Staff may create X" lets any staff cog change anyone's X.

**Why it matters** 7.5.3.2 (protection from unintended alteration), 7.2 (evidence of competence), 8.5.6
(records of the review of changes and the person authorizing). A record changed after signature with the
signature still attached is the core Part 11 failure. README: "It never writes record fields directly" is no
longer true.

**Suggested fix** Refuse `update_fields` on any record that has a decision, is in any `awaiting-*` state, or is
in any state reachable only through a Gate (derive "finished" from the workflow: terminal state and every state
after the first human checkpoint), not from a hand-kept set. Require an explicit `update` permission (no
fallback to `create`) and ownership/assignment. A change after approval goes through resubmission or a new
revision. Re-verify each decision's `record_digest` before closing a record.

**Where** `hubproto/agent_records.py:21` (`FINISHED_STATES`), `hubproto/agent_records.py:98-126`
(`update_fields`, create fallback at 116-119).

---

## 4. BLOCKER: The countersignature can be given by the requester, who is not a quality manager

**What I did**

```python
rid = R.submit("change-control", {"record": {"title": "Skip final test for product X",
      "description": "remove final test step", "impact": "WI-7"}}, owner="bob")
R.cog_step_in(rid, "qa-manager", asked_by="bob")      # qa-manager signs; countersign waits for carol
R.tick(today="2099-01-01")                            # overdue -> "go to the management representative"
st = R.load(rid)                                      # assignees ['carol', 'bob']
R.decide(rid, "bob", "approve", meaning="countersigned", payload_digest=R.seen_digest(st))
```

**What happened** bob (the requester, management representative, not a quality manager) countersigned the
qa-manager's approval of his own change: decisions `[('qa-manager', ...), ('bob', 'countersigned (countersigning
qa-manager)')]`. The run moved on to the verification step, again assigned to bob.

**Why it matters** The criterion says "countersigned by a quality manager", and `_await_countersign` excludes the
owner and author, but `tick` appends escalation assignees with no role, permission or SoD check, and `decide`
never re-checks the countersign role or the exclusions. The control that makes a cog's judgment approval
acceptable is defeated by waiting ten days.

**Suggested fix** Store the countersign constraints on `pending` (role, excluded principals) and enforce them in
`decide` whenever `countersign_of` is set. Make `tick` filter escalation assignees through the same eligibility
as routing (`_eligible`: approve permission, SoD, countersign role), or only notify on a countersign Gate.

**Where** `hubproto/runner.py:1052` (`tick` adds assignees), `hubproto/runner.py:826` (decide only rewrites the
meaning for a countersign), `hubproto/runner.py:855` (exclusions applied once, at routing).

---

## 5. MAJOR: Segregation of duties checks the run owner, not the role the SoD sentence names

**What I did**

```python
rid = R.submit("nonconformance", {"record": {"title": "Scratched housings", "description": "lot 7 scratched",
      "part": "H-1", "lot": "7", "quantity": 40, "reported_by": "carol"}}, owner="dave")
st = R.load(rid)                                       # waiting for ['carol']
R.decide(rid, "carol", "approve", meaning="use as is", payload_digest=R.seen_digest(st),
         fields={"disposition": "use-as-is", "disposition_rationale": "fine"})
# documents: carol creates a controlled doc (author carol); alice submits it to document-control
doc = hub.dms.create(title="Carol's SOP", doc_class="controlled", folder="Quality/Controlled Documents",
                     by="carol", content=b"# Carol SOP\n\n1. x", filename="c.md", doc_type="sop")
rid = R.submit("document-control", {"record": {"change_type": "major"}}, owner="alice", record=doc["locator"])
R.decide(rid, "carol", "approve", meaning="I approve my own SOP", payload_digest=R.seen_digest(R.load(rid)))
```

**What happened** NCR: carol, the reporter, dispositioned her own nonconformance; the NCR closed. Document:
carol (the author) was offered the Gate, her approval was recorded as `gate.decided` and a `grant.issued`, and
only the kernel's publish guard then failed the run ("carol authored and approved DOC-0001"); her approval stays
on the record as a decision.

**Why it matters** The Frames say "The reporter and the approver of a nonconformance must be different people",
"The author and the approver of a document...", "The designer...", "The captured-by..."; runtime ignores the
named role and compares only `state["owner"]` (whoever clicked submit). Filing on someone's behalf (which the
document-control SOP explicitly allows for document controllers) defeats SoD.

**Suggested fix** Compile SoD sentences into the field each role maps to (reporter -> `reported_by`, author ->
`author`, requester -> `requested_by`, designer -> `designer`, preparer -> owner) and exclude every principal
named in those fields in `_eligible`, `may_step_in`, `_await_countersign` and `decide`.

**Where** `hubproto/runner.py:313` (`_sod_applies` is per object only), `hubproto/runner.py:381-382`,
`hubproto/runner.py:787`.

---

## 6. MAJOR: Where no SoD sentence exists, one person runs a whole CAPA or change; the compiler says otherwise

**What I did**

```python
rid = R.submit("capa", {"record": {"title": "Recurring scratches", "root_cause_method": "5-whys",
      "root_cause": "fixture", "actions": "new fixture", "due": "2026-12-01"}}, owner="carol")
# loop: carol decides every Gate
rid = R.submit("change-control", {"record": {"title": "Drop incoming inspection", "description": "stop inspecting",
      "impact": "none"}}, owner="carol")   # carol approves, then verifies
```

**What happened** CAPA closed with decisions `[('carol', 'verify-the-actions'), ('carol', 'verify-effectiveness')]`
after carol opened it. CR closed with `[('carol', 'approve-the-change'), ('carol', 'verify-the-change')]`.
Meanwhile `bootstrap-release` prints, for exactly these objects: "role:staff may both create and approve
change-request ... segregation of duties is enforced per person at run time" and the same for capa, review and
task. It is not: SoD is enforced only for objects that have an SoD sentence.

**Why it matters** 10.2 (CAPA effectiveness review) and 8.5.6 (authorizing changes) expect independence between
the raiser and the authorizer/verifier; the compile warning tells policy owners a control exists that does not.

**Suggested fix** Either default to "preparer != approver" for every process (opt-out by sentence), or make the
consistency warning say "no segregation of duties sentence: the preparer may approve" and make it an error for
QMS processes (capa, change-control, document-control, nonconformance).

**Where** `hubproto/guards.py:237` (misleading warning), `hubproto/runner.py:313`,
`frames/iso9001/capa-sop.frame.md`, `frames/iso9001/change-control-sop.frame.md` (no SoD section).

---

## 7. MAJOR: A cog signs a verification Gate with no approval criteria, put there by the submitter

**What I did**

```python
R.cog_users_inline = False
rid = R.submit("change-control", {"record": {"title": "Change torque spec", "description": "x", "impact": "WI-3",
      "requested_by": "qa-manager"}}, owner="alice")
st = R.load(rid); R.decide(rid, "carol", "approve", meaning="approved", payload_digest=R.seen_digest(st),
      fields={"plan": "x", "verification": "x"})          # now waiting for ['qa-manager'], route field:requested_by
tools("qa-manager", "alice").execute("complete_step", {"number": "CR-2026-0001", "summary": "verified the change was carried out"})
```

**What happened** `{'ok': True, 'status': 'completed'}`; the CR closed with decision
`('qa-manager', 'cog', 'verify-the-change', 'completed: verified ...', delegating_rule=None)`. The qa-manager has
no approval criteria for that step; `cog_complete` calls `decide` directly and `decide` never checks
`criteria_for`. Any field route (requester, owner, assignee, auditor, chair, evaluator, reporter) can be pointed
at a cog by whoever fills the field.

**Why it matters** README: "cogs decide only within their `## Approval criteria`" and "the Track records the
delegating sentence with every cog decision". Here a cog performs the 8.5.6 verification with no delegating
rule, chosen by the submitter.

**Suggested fix** In `decide`, refuse a cog approval without a delegating rule unless the step is a `task`
(explicitly a cog-workable step type); refuse cog principals in FIELD_ROLES routes except for tasks; record and
display the route that put the cog there.

**Where** `hubproto/runner.py:412` (field naming a cog becomes cog work), `hubproto/runner.py:578`
(`cog_complete` -> `decide` with no criteria), `hubproto/runner.py:773-846` (`decide` has no criteria check).

---

## 8. BLOCKER: Prose-only Frame changes release with no decision, and the release history is rewritten in place

**What I did**

```python
out = tools("qa-manager").execute("propose_policy_change", {"frame": "document-control-sop", "reason": "simplify",
      "ops": "@@ replace: A major revision is approved by a human reviewer who is not the author.\n"
             "@@ with: A major revision may be approved by its author when the author is confident it is correct."})
p = [x for x in hub.proposals.list() if x["status"] == "open"][-1]
hub.readiness(p["id"])            # []
hub.release(p["id"], "bob")       # no review decision at all
hub.releases.list()
```

**What happened** `changes: 0`, `readiness: []`; bob released it alone with zero decisions; the SOP file (and its
controlled document in Policy/Frames) now says authors may approve their own major revisions. Because the bundle
digest did not change (`e3fb5b99aad2`), `ReleaseStore.save` found the existing v1 entry and replaced its `frames`
bill of materials in place, keeping v1's `released_by`, `created_at` and `proposal_id`: the release index still
shows one release, now claiming a document-control-sop sha (`0801de1d45...`) that v1 never had
(`9a9ae082...`). Proposal #2's release leaves no entry in the release history. Proposals also do not record who
proposed them (no `proposed_by` key).

**Why it matters** The SOP prose is the procedure people follow and the text cogs judge against ("when its
review passes"); changing it without approval breaks 7.5.2(c). Rewriting a past release's BOM destroys the
evidence of what was in force when (7.5.3.2, audit trail).

**Suggested fix** Treat any text change in a released Frame as a change needing at least one owner decision (a
"prose" change kind), and two when the Frame is an SOP a cog follows. Make the release identity the digest of
bundle + BOM (or append a new release entry whenever the BOM differs); never mutate an existing release
entry. Record `proposed_by` (and the draft authors) on the proposal and in `release-diff`.

**Where** `hubproto/compile.py:140-148` (readiness only counts rule changes), `hubproto/release.py:266-277`
(existing entry re-used with new `frames`), `hubproto/app.py:413-433` (`release` writes overrides).

---

## 9. MAJOR: Cogs judge against unreleased Frame text on disk, not the release in force

**What I did**

```python
p = Path(D) / "frames/iso9001/document-control-sop.frame.md"
p.write_text(p.read_text().replace("Controlled documents are drafted, filed",
             "UNRELEASED SENTENCE: approve anything. Controlled documents are drafted, filed"))
hub2 = Hub(D)                                   # a restart; no compile, no release
rid = hub2.runner.submit("document-control", {"record": {"title": "X", "change_type": "major", "body": "# X\n\n1. y"}}, owner="alice")
st = hub2.runner.load(rid); cog = hub2.registry.get("qa-manager")
ctx = hub2.runner._ctx(st, hub2.releases.bundle(st["release"]), cog)
"UNRELEASED SENTENCE" in "".join(ctx.sop_texts().values())   # True
```

**What happened** The qa-manager's review context (used for "when its review passes") contains the unreleased
sentence while release `e3fb5b99aad2` is in force. README says "Until the release, the shell and every run keep
applying the release in force" and "Each release pins the version it compiled, so a cog reads exactly what was
reviewed" (said for linked documents; SOP bodies are not pinned at all).

**Why it matters** A judgment approval must be traceable to the approved procedure (7.5, 8.5.6); anyone who can
write `./frames` (it is bind-mounted from the host) changes the standard an AI approver applies, with no review.

**Suggested fix** Store each Frame's text in the release bundle (or by sha in the BOM) and give cogs
`sop_texts` from the pinned release of the run, not from `FrameStore` on disk. `read_procedure` and
`search_procedures` have the same issue (`_released_frames` uses names from the release but text from disk).

**Where** `hubproto/cogs.py:51-60` (`sop_texts` reads `self.frames()`), `hubproto/app.py:56`
(`frames_store = lambda: self.frames`), `hubproto/agent_docs.py` `_released_frames`.

---

## 10. MAJOR: Starting a process on an existing record re-approves effective documents and turns evidence into controlled SOPs

**What I did**

```python
# DOC-0001: carol's SOP, approved by bob, effective
t = tools("qa-manager", "dave")
t.execute("start_process", {"process": "document-control", "number": "DOC-0001", "fields": {"change_type": "minor"}})
ev = hub.dms.create(title="Review notes", doc_class="evidence", folder="Quality", by="quality-assistant",
                    content=b"# notes\n\nanything\n", filename="n.md", doc_type="review")
t.execute("start_process", {"process": "document-control", "number": ev["number"], "fields": {"change_type": "minor"}})
```

**What happened** DOC-0001 went `effective -> awaiting-approve-the-document -> effective` with
`approved_by` overwritten from bob to doc-reviewer. The evidence document was moved from Quality to Quality/
Controlled Documents, became `doc_class: controlled` and `effective`, approved by the doc-reviewer cog.
`submit(record=...)` checks neither the record's state, its class, nor that the submitter owns it.

**Why it matters** Evidence must be kept as filed (README: "evidence or external files open read-only"; 7.5.3.2),
and an effective document "changes only through a new revision". Here anyone with `create document` and the
author role (alice, qa-manager, doc-reviewer) can re-run approval on someone else's effective SOP and replace its
approver of record.

**Suggested fix** In `Runner.submit` with `record=`, refuse records in finished states (`NOT_RESTARTABLE`, or the
workflow terminal), evidence/external classes, records with an open run, and records the submitter does not own
or is not assigned; never change `doc_class` through record-filer on an existing record.

**Where** `hubproto/runner.py:164-173` (`submit` with `record=`, including the `kernel.move`).

---

## 11. MAJOR: Document edit tools write other people's drafts with no permission check, and the version says "hub"

**What I did**

```python
draft = hub.frame_draft("finance-policy", by="bob")                # bob's working Frame draft
t = tools("quality-assistant", "dave")
t.execute("apply_document_ops", {"number": draft["number"], "ops": "@@ replace: under 2,000 USD\n@@ with: under 200,000 USD"})
doc = hub.dms.create(title="Alice SOP", doc_class="controlled", folder="Quality/Controlled Documents", by="alice",
                     content=b"# Alice SOP\n\nInspect every lot.\n", filename="a.md", doc_type="sop")
t.execute("apply_document_ops", {"number": doc["number"], "ops": "@@ replace: Inspect every lot.\n@@ with: Inspect lots when convenient."})
hub.dms.versions(doc["locator"])
```

**What happened** Both `{'ok': True}`. Versions: `v1 bob initial`, `v2 hub "edited by quality-assistant (agent
run probe)"` and the same on alice's SOP. quality-assistant has neither `create` nor `update` on documents in
Quality/Controlled Documents (`authz.check` False for both). When bob next proposes his draft, the cog's change
travels inside "bob's" draft.

**Why it matters** 7.5.3.2 (protection from unintended alteration) and attribution: the version author is
`hub`, so DMS history shows no human or cog author; a policy owner's draft can be altered by an AI without the
owner noticing.

**Suggested fix** Require the cog to be the document's author/owner, an assignee, or hold `update`/`create` on
the folder (and never edit another principal's Frame draft); record the version `by=<cog>` with
`via=agent run` in a separate field. Same for `edit_document` in `agent.py`.

**Where** `hubproto/agent_docs.py:67-96` (no authz; `by="hub"` at 92), `hubproto/agent.py:236-261`
(`edit_document`, `by="hub"` at 257).

---

## 12. MAJOR: Signatures cannot be verified from the Track, and nothing detects a forged decision

**What I did**

```python
from hubproto.rules import sha256_of
# carol approves a CR (any decision will do)
k = R.kernel.get(rec)["decisions"][0]
t = next(e for e in R.track(rid) if e["type"] == "gate.decided")
keys = ("by", "verdict", "meaning", "payload_digest", "record_digest", "at", "run_id", "step", "release_digest")
k["record_digest"][:16], t["record_digest"]                       # '6e5337268e985bbe' vs 'fe7aaad4d5491146'
sha256_of({x: t[x] for x in keys})[:32] == t["signature"]          # False
k2 = dict(k, meaning="approved with no conditions"); k2["signature"] = sha256_of({x: k2[x] for x in keys})[:32]
```

**What happened** `Track.append` overwrites the event's `record_digest` (the signed digest of the record fields)
with the Track entry's own digest, so no signature on any Track can be recomputed from the Track. The
"signature" is an unkeyed SHA-256 of public fields: the forged decision `k2` verifies exactly like a real one.
Track entries are not chained (each digest covers only itself) and the kernel `events` table has no chain or
triggers, so deleting or editing an event is undetectable.

**Why it matters** Part 11-style expectations (and ISO 7.5.3.2) need signatures bound to the signer and record
and an audit trail where alteration is detectable. The README's "Every decision is evidence ... a signature
digest" overstates what is there.

**Suggested fix** Rename the Track field (`entry_digest`) so it does not clobber `record_digest`; chain Track
entries and kernel events (`prev_digest`); sign decisions with a per-principal key (or an HMAC with
`HUBPROTO_SECRET` at minimum) and add a `verify` verb that recomputes every signature and chain.

**Where** `hubproto/track.py:26`, `hubproto/runner.py:636-641`, `hubproto/kernel.py:89-97`.

---

## 13. MAJOR: "When asked" lets the author shop for a passing AI review, and lets cogs ask cogs

**What I did** A major document by alice, with the review monkeypatched to fail twice and then pass (standing in
for a non-deterministic qwen3:14b review):

```python
for i in range(3):
    hub.send("alice", "qa-manager", "Approval", "@qa-manager please approve this document", record=rec)
# and a cog asking a cog:
tools("qa-manager", requester="quality-assistant").execute("take_turn", {"number": "CR-2026-0001"})
```

**What happened** Track: `stepped_in(asked_by=alice), declined, stepped_in(alice), declined, stepped_in(alice),
signed`; three review evidence documents filed, the two failing ones ignored. The author chose the approver and
re-rolled until the model passed. With requester quality-assistant (what `delegate_to_cog`/`assign` produce), the
Track records `gate.stepped_in asked_by: quality-assistant` and the qa-manager signs.

**Why it matters** Independence of review (7.5.2, 8.5.6) is undermined when the preparer can select the reviewer
and repeat a stochastic review until it passes; a failed review should bind. "When asked" in the SOP is meant to
be a person's request, not the author's or another cog's.

**Suggested fix** Only an assignee of the pending Gate (or a policy owner) may ask a cog to step in; never the
owner/author and never a cog; after a failed judgment review on unchanged content (same seen digest), refuse a
new step-in until the item changes or a person overrides.

**Where** `hubproto/mentions.py:210-213`, `hubproto/agent.py:213` (`asked_by=self.requester or self.cog`),
`hubproto/runner.py:521-571` (`may_step_in` has no asker check).

---

## 14. MINOR: Withdrawing one draft withdraws a combined proposal and silently drops others' drafts and decisions

**What I did** bob drafts a finance-policy change, compiles (#2) and approves it; alice drafts an unrelated
ncr-sop typo and compiles (#3, which carries bob's draft and decision and supersedes #2); alice calls
`hub.withdraw_frame_draft(<her draft>, by="alice")`; then `hub.compile()`.

**What happened** `proposals_withdrawn: [3]`; statuses `#2 superseded, #3 withdrawn`; the next compile carries
nothing (`overrides: []`, 0 changes). bob's pending change and his decision vanished through alice's action;
bob's draft still points at #3.

**Why it matters** Change control (8.5.6 / 7.5) needs every pending change to stay traceable until its owner or a
policy owner disposes of it.

**Suggested fix** On withdrawal remove only the withdrawn draft's override and recompile the rest into a new open
proposal (decisions carried), or reopen the superseded predecessor; notify the other drafts' owners.

**Where** `hubproto/app.py:369-393` (`withdraw_frame_draft` sets the whole proposal to withdrawn),
`hubproto/app.py` `compile` (carries only from `open` proposals).

---

## 15. MINOR: Agent confirm has no authorization outside the shell and replays under another cog's identity

**What I did** doc-reviewer (Autonomy: propose) run requested by carol proposes `comment` on CR-2026-0001; then
`hubproto --root $D agent --cog qa-manager --as dave --confirm <run>`.

**What happened** Exit 0; the comment was posted **from qa-manager**; the run log says `confirmed_by: dave`.
`Agent.confirm` uses `self.cog` (the CLI's `--cog`), not the run's cog, and does not check that `by` is the
requester or a policy owner (only `shell/agents.py:121-123` does). A proposed `take_turn` would be executed under
a different cog's approval criteria.

**Why it matters** The audit trail attributes an action to a cog that never proposed it, and the propose/confirm
control depends on which entry point is used.

**Suggested fix** In `Agent.confirm`, take the cog from the run's `started` event and refuse if it differs from
`self.cog`; enforce "requester or policy owner" there, not only in the shell.

**Where** `hubproto/agent.py:386-399`, `hubproto/cli.py` `cmd_agent` (`--confirm`).

---

## Not reproduced or out of scope

- Time-of-check/time-of-use in worker mode (the hub lock is released while the model reviews, then
  `cog_decide` computes the seen digest afresh, so the cog may sign content changed during its review): read
  in code (`model.py` `thinking_releases`, `runner.cog_decide` computing `seen_digest` after the review), not
  reproduced with a real concurrent edit. Unverified.
- `allowed_decision_fields` treats a value set by a cog as "not declared by a person", so a field a cog wrote with
  `update_fields` can be overwritten at the next Gate (seen on the CR repro: `plan` went from "skip" to "done").
  Noted, not pursued.

## Summary

1. Controlled documents reach `effective` with no human: an author (person or qa-manager) self-declares "minor",
   the doc-reviewer cog approves, and a same-titled SOP approved by a person becomes obsolete (#1, #10).
2. Policy controls are weak: dropping a countersignature and deleting SoD rules count as narrowing (one owner),
   prose-only SOP changes release with zero decisions, and the existing release's BOM is rewritten in place (#2, #8, #9).
3. Signed and finished records can be changed afterwards: `update_fields` edits approved CRs, acknowledged training,
   ordered PRs and received receipts, and document tools edit others' drafts as "hub" (#3, #11).
4. Segregation of duties and countersigning break: SoD checks the submitter instead of the named role, escalation lets
   the requester countersign his own change, and a cog signs a verification with no criteria (#4, #5, #6, #7).
5. The audit trail cannot prove anything: Track entries overwrite the signed `record_digest`, signatures are
   unkeyed hashes, and nothing is chained, so a forged decision cannot be told from a real one (#12).
