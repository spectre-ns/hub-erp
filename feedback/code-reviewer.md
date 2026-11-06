# Code review findings (persona: code-reviewer)

Scope: every module under `hubproto/`, `config/*.json`, `docker/*`, `Dockerfile`,
`docker-compose.yml`. Method: read the code, ran the suite (171 passed), and
reproduced each runtime claim below with throwaway scripts against a fresh hub
(copy of `frames/`, seeded kernel, `release_all`, fake CAD worker) or against
`shell.serve_in_thread`. "Evidence" quotes the probe output. Line numbers are
from the tree as of this review.

Counts: blocker 4, major 9, minor 12, question 2.

---

### F-code-reviewer-1: A purchase request with no price is approved by the cog for any quantity, and the price the buyer enters at the Gate is thrown away
Severity: blocker
Area: workflow
Steps:
1. Create an `item` from `acme-metals` with no `unit_cost` (the BOM-reconciler's proposals never have one, so every item created by `shortage-requester` is in this state).
2. As alice, start `purchase-request` with `{"item": <that item>, "quantity": 1000000}`.
3. Observe the run; then start a second request for an item with no supplier, let it wait for dave, and have dave approve with `fields={"supplier": "acme-metals", "unit_cost": 5000}` (as the panel scenario, step 3, asks him to).
Expected: a request with no amount cannot be inside any "under 500 USD" criterion; a buyer who prices a request at 100 x 5000 USD has produced a 500,000 USD order that must go to the controller; the PO carries that amount.
Actual: `supplier-checker` (`procurement.py:52-55`) sets `unit_cost = 0`, so `amount = 0.0`; routing (`routing.py:30-33`) finds `amount < 500` true and the `purchasing-assistant` cog approves (`routing.py:66`, `0 < 500`), `po-issuer` issues PO-0001 for 1,000,000 units at 0.00. For the second request the amount at routing time is 0.0 (it goes to dave, never to bob), and dave's `unit_cost` never reaches `po-issuer`: `Cog._prior` (`cogs.py:87`) only takes a record field when the prior payload value is in `(None, "", [], {})`, and the prior payload says `unit_cost: 0.0`, so the PO is issued at **0.0, approved_by dave**, the controller threshold is never consulted, and the downstream bill can never three-way match (variance against a 0 PO is 100 %).
Evidence: probe output `status completed state ordered amount 0.0 risk low / decided by [('purchasing-assistant','cog')] / PO: ('PO-0001', 0.0, 1000000)` and `run2 waiting for ['dave'] amount at routing 0.0 / PO amount 0.0 approved_by dave controller involved? False`. `tests/test_procurement.py:100-103` asserts only the supplier of `po2`, not its amount, so the suite does not see this.
Suggested fix: treat "no unit cost" as a routing fact (`amount_known: False`) that fails every cog criterion and the "under N" routes; re-evaluate routing after a Gate decision changes `amount`/`unit_cost` (or forbid changing them at the Gate and require pricing before routing); in `_prior` treat `0`/`0.0`/`False` as "empty" only for fields the step itself did not compute, or better, let record fields set by a person at a Gate (tracked in `record.fields_set` events by a person) override the payload.

### F-code-reviewer-2: Rejecting a design leaves the record in a state that does not exist and the run can never continue
Severity: blocker
Area: workflow
Steps:
1. As alice start `design-release` (with the two example CAD files); it waits for bob.
2. As bob reject it.
3. Open the record and the run.
Expected: per `design-control-sop.frame.md:67` ("Rejections return to Submit the design") the design goes back to the engineer, who can resubmit; or the release compiler refuses a `reject_to` that is not a checkpoint.
Actual: `reject_to` is the slug of step 1, a **cog** step. `Runner._record_decision` transitions the record to `awaiting-submit-the-design` (`runner.py:357-359`), a state not in `workflow.states` (`['open','awaiting-review-the-design','released']`), then `_rewind` (`runner.py:388-391`) finds no Gate for that checkpoint and returns silently. The run's status is `rejected`, `pending` is `None`, the record has no assignees, `decide` says "not waiting for a decision", `advance` returns immediately. `consistency_guard` (`guards.py:171`) accepts any step id as `reject_to`, and `render_workflow` (`release.py:112-113`) renders `on_reject` without checking it names a checkpoint.
Evidence: probe `after reject: run status rejected pending None / record state awaiting-submit-the-design assignees [] in workflow states? False / alice decide -> RunError run ... is not waiting for a decision / advance -> rejected`.
Suggested fix: in `consistency_guard`, error when `reject_to` is not a checkpoint (or define "return to a cog step" as: re-run from that step, which needs a new run state `returned` and an owner action to resubmit); never transition a record to a state outside `workflow.states` (`Kernel.transition` should validate against the workflow pinned on the run).

### F-code-reviewer-3: A Frame edit can add its own policy owners, who then approve the widening and release it
Severity: blocker
Area: security
Steps:
1. With a release in effect, edit `frames/company/org-model.frame.md` and add `- mallory (person): policy-owner, controller, staff` and `- eve (person): policy-owner, staff` to `## Members` (the compose file mounts `./frames` from the host, so anyone with write access to the checkout can do this).
2. `hubproto compile`; `hubproto review <id> --as mallory --approve-all`; `hubproto review <id> --as eve --approve-all`; `hubproto release <id> --as mallory`.
Expected: only policy owners of the release **in effect** may decide changes; new members cannot approve their own admission.
Actual: `compile_proposal` computes `reviewers` as the union of policy owners in the **proposed** rules and the old ones (`compile.py:75`), and `decide_change` checks only membership in that list (`compile.py:89`). Two invented principals satisfy the two-approval rule for every widening (member additions, controller permission) and mallory releases.
Evidence: probe `reviewers: ['bob','carol','eve','mallory'] / readiness: [] / released by mallory: 121d56e8... controller roles: ['controller','policy-owner','staff']`.
Suggested fix: reviewers = policy owners of `base` only (fall back to the proposed set only when there is no base, i.e. bootstrap); additionally require that a decision's `by` is not the subject of a `member` change it approves.

### F-code-reviewer-4: Records created or changed from the CLI (or `tick`) are invisible in the shell until the shell itself writes to the kernel
Severity: blocker
Area: shell
Steps:
1. Start the shell, open `/boards?as=alice&type=bill` once (this builds `Kernel._cache`).
2. In another process (`docker compose exec hub hubproto run purchase-to-pay --as alice --record-json ...`, exactly what README tells panelists to do), start a run.
3. In the shell open `/record/rec:bill:BILL-0001?as=alice` and `/boards?as=alice&type=bill`.
Expected: the record is there.
Actual: 404 "No record", the board does not show it, while `/runs` lists the run and dave's inbox names the bill (those read files, not the projection). `Kernel._records` (`kernel.py:103-156`) caches the whole projection and only `Kernel.append` in the **same process** clears it (`kernel.py:84`). The compose file runs `tick` as a separate container against the same volume, so SLA escalations (`record.assigned`) are invisible too, and `tick`'s read-modify-write of `state.json` races with the shell's `decide` on the same run (last writer wins, no lock).
Evidence: probe `shell /record -> 404 | /boards shows it: False | /runs lists run: True | dave's inbox shows BILL-0001: True / after any kernel write in the shell process: /record -> 200`.
Suggested fix: key the cache on `SELECT max(seq) FROM events` (one cheap query per read) or drop the cache and build a proper read model (table per record, updated in `append`); for run state, take a file lock (`fcntl.flock` on the run dir) around load-modify-save in `advance`, `decide`, `tick`.

### F-code-reviewer-5: One malformed POST freezes the whole shell for every user
Severity: major
Area: security
Steps:
1. `printf 'POST /msg HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n' | nc localhost 8080` (keep the connection open), or any POST that sends a large `Content-Length` and then stalls.
2. From another client, `GET /healthz`.
Expected: other requests are served; the request is rejected or times out.
Actual: `do_POST` takes the class-level `RLock` (`shell.py:282,294`) before reading the body; `_read_body` does `int(Content-Length)` with no bound or sign check and `rfile.read(-1)` blocks until the client closes (`shell.py:343-345`). Every other request, including `/healthz`, blocks behind it (the `ThreadingHTTPServer` is effectively single-threaded). The lock is also held while the response is written, so a slow reader has the same effect, and a 2 GB `Content-Length` is read into memory (no upload limit; the whole multipart body is parsed in memory). The same unbounded `int(Content-Length)` and `base64.b64decode` are in the worker (`worker.py:191,157`).
Evidence: probe `F healthz while hung POST open -> ('TimeoutError','timed out') (3.0s) / F healthz after close -> 200`; the Docker `HEALTHCHECK` would mark the container unhealthy.
Suggested fix: reject `Content-Length` < 0 or > a limit (say 32 MB) with 413 before locking; set `timeout` on the handler (`BaseHTTPRequestHandler.timeout`); read the body outside the lock and lock only around hub mutations; do the same in `worker.py`.

### F-code-reviewer-6: The "electronic signature" is an unkeyed checksum that binds a cog payload, not the record, and nothing detects tampering
Severity: major
Area: security
Steps:
1. Start `purchase-to-pay` with the 4,000 USD bill (waits for dave).
2. Before dave decides, change the record: `kernel.set_fields(rec, {"amount": 1.0}, by="dave")` (the shell lets an assignee do this through `post_decide` fields, `shell.py:975`, for any typed field of the record type).
3. Dave approves with the pending `payload_digest`; then edit `state/runs/<id>/state.json` and delete a line of `track.jsonl`.
Expected: a signature that binds what was approved, is verifiable by a third party and cannot be recomputed by anyone; a Track that detects deletion.
Actual: `signature = sha256(by, verdict, payload_digest, at)[:24]` (`runner.py:336`): no key, so anyone with file access can forge or recompute it. `payload_digest` is the digest of the **cog step's payload**, not of the record, so record edits between routing and decision do not change it. `Track.record_digest` (`track.py:26`) is per-line with no chain, so deleting a line is undetectable; `Runner.load` reads `state.json` with no integrity check.
Evidence: probe `record amount now 1.0 pending digest unchanged: True / signature ... recomputed ... equal True / state tampered; load ok: 99 / track line removed; status still: completed; track reads 14 events, no error`.
Suggested fix: bind the decision to a digest of (record fields, attached document SHAs, payload) frozen at `gate.pending`; refuse `decide` if that digest changed; HMAC the signature with a hub secret (or sign with a per-principal key); chain `record_digest` to the previous line and verify on read.

### F-code-reviewer-7: A legitimate approval fails the run permanently when the next state's required fields are missing
Severity: major
Area: workflow
Steps:
1. As carol start `capa` with `{"title": "c"}` (the drafter supplies actions/owner/due); it waits at "Verify the actions".
2. `hubproto decide <run> --as carol --verdict approve` (the CLI has no way to pass fields).
Expected: the Gate refuses the decision and names the fields it needs (as the shell form does), or the next Gate waits with the record flagged.
Actual: `decide` pre-checks `required_missing` only when the Gate is the last manifest step (`runner.py:439-443`). Otherwise the signature and grant are recorded, `effectiveness-scheduler` runs, and the transition to `awaiting-verify-effectiveness` raises `required-fields` (needs `root_cause`), which `_human_gate` turns into `_fail`: the CAPA record and run are `failed` after a valid approval, with a grant issued. Same for `requirements-capture` (terminal `accepted` needs `acceptance_criteria`; `_complete` fails).
Evidence: probe `after approve: status failed ["required-fields: CAPA-2026-0001 cannot enter 'awaiting-verify-effectiveness' without root_cause"] / record state failed`.
Suggested fix: compute the next state from the workflow (`_next_state`) and check `required_missing` for it **before** `_record_decision`, regardless of position; give the CLI `decide --field k=v`; make `_fail` for a `required-fields` guard put the run back to `waiting` on the same Gate instead of terminal failure.

### F-code-reviewer-8: Goods receipt books the same PO twice and accepts negative quantities
Severity: major
Area: records
Steps:
1. Issue a PO for 10 units (via purchase-request).
2. Start `goods-receipt` for it twice, dave confirms both; then once more with `lines: [{"item": ..., "quantity": -100}]`.
Expected: the second receipt is refused or flagged as a duplicate (the PO is already `received`); a negative or over-quantity line is an error, not a warning.
Actual: `receipt-checker` (`procurement.py:159-178`) never looks at `po.state`/`received`; over-receipt is a warning (`:172-173`); negative quantities pass every check; `stock-booker` adds whatever it gets (`:196`). Stock went 10 -> 20 -> -80 with all three runs `completed`. `required_by_state` for `received` needs `po` and `lines` but there is no type check on `lines` (`kernel._check_fields` validates enums only, `kernel.py:239-246`).
Evidence: probe `receipt 1 ... stock 10.0 / receipt 2 ... stock 20.0 / negative receipt status completed stock -80.0`.
Suggested fix: error-severity problems for a PO already received, for quantity <= 0, and for received > ordered unless the buyer overrides at the Gate; validate `int/number/money/list/object` field types in `_check_fields`.

### F-code-reviewer-9: A whitespace edit to a Frame produces a new release and forces re-approval of every process in it
Severity: major
Area: policy
Steps:
1. Release; then insert one blank line before `## Process: purchase-request` in `purchasing-policy.frame.md`; `hubproto compile`.
Expected: `release.py:7-9` and `strip_sources` (`release.py:206-217`) promise "an edit that changes no rendered rule produces no new release".
Actual: the proposal has 4 changes (`remove process purchase-request`, `add process purchase-request`, same for `goods-receipt`), each needing an approval, and the digest differs: process steps carry `line` (`extract.py:189-192`) and `n`, which `strip_sources` does not remove, and `members[...]["sources"]` (plural, `release.py:144`) is likewise in the digest. Since the digest is the identity runs pin to, a comment moves every new run to a "new" policy and the drift report is noise. Conversely, `ReleaseStore.save` re-saving the same digest silently overwrites `effective_from`/`released_by`/`parent` in the index (`release.py:257-259`).
Evidence: probe `changes: [('add','process','purchase-request'),('add','process','goods-receipt'),('remove',...),('remove',...)] / digest same? False / process step keys in bundle: ['cog','id','kind','line','n','role','title']`.
Suggested fix: keep `line` in the rule's `source` (per step: `source.steps[i].line`) and out of the rendered body; exclude `n`; name the plural `sources` so it is stripped; refuse `save` of an existing digest with different metadata (or store metadata per release event).

### F-code-reviewer-10: Any `as` value, including non-members, can read every run's Track, payloads and amounts; `/runs` and `/inbox` are not permission-trimmed
Severity: major
Area: security
Steps:
1. `GET /run/<any run id>?as=mallory` and `GET /runs?as=nobody`.
Expected: the shell docstring says every page is permission-trimmed; `/files` and `/record` do refuse mallory.
Actual: `page_run` (`shell.py:803`) and `page_runs` (`:794`) have no `_require_read`; the run page shows the full Track with routing contexts, bill amounts, suppliers and decision signatures. `page_inbox` (`:575`) shows whatever principal is named in `as`. `post_attach` (`:1020`) lets a user attach an existing document from a folder they cannot read (its number and title then appear on the record page). Error pages print full Python tracebacks (`shell.py:322`), and `_back_link` (`:376`) reflects the Referer header into an `href` (a `javascript:` URL is not blocked by `html.escape`).
Evidence: probe `H /run as mallory -> 200 ... amount in page: True / H /runs as nobody -> 200 True / (compare) /files as mallory -> HTTPError`.
Suggested fix: `_require_read(run.manifest.folder)` on `/run/*`, filter `/runs` by readable folders, require `read` on the document's folder in `post_attach`; hide tracebacks unless a debug flag is set; validate `Referer` scheme.

### F-code-reviewer-11: The drift Guard misses several widenings, so one approval suffices where two were meant
Severity: major
Area: policy
Steps: run `guards.drift_guard` on (old, new) pairs:
1. approval criteria `conditions [match_within_tolerance, supplier_approved]` -> `[risk_low]`;
2. same criteria, `currency USD -> EUR`;
3. threshold `2 percent -> 2 days`;
4. two criteria rules for the same cog and process (different objects): the second is never compared.
Expected: replacing the conditions a cog needs, or changing the currency/unit of a limit, is a widening (two approvals).
Actual: `_widens` (`guards.py:212-236`) flags only a strict subset of conditions (`:227`), ignores currency and unit, returns on the first matching old rule (`:222-229`), and for thresholds treats any numeric increase as widening and any decrease as safe regardless of what the threshold means (a lower "purchase approval threshold" sends more to the controller, a lower "design rule error limit" is stricter, but a lower "cog purchase limit" is also stricter while a higher one is wider: the sign depends on the name). `role` is listed in `_WIDENING_KINDS` but never widens.
Evidence: probe `conditions swapped: [('add', False), ('remove', False)] / currency USD->EUR: [... False ...] / unit percent->days: [... False ...]`.
Suggested fix: any change to an `approval_criteria` or `threshold` rule that is not a pure tightening (same currency/unit, lower or equal max, superset of conditions) is a widening; compare against all old rules with the same key fields; declare per-threshold direction in the sentence grammar ("at most"/"at least").

### F-code-reviewer-12: Every kernel read after a write rebuilds the whole projection from every event (quadratic)
Severity: major
Area: performance
Steps: create N `task` records in a loop and time it.
Expected: roughly constant cost per command.
Actual: `Kernel.append` clears `_cache` (`kernel.py:84`); the next `get`/`query`/`require` replays all `rec:` events (`kernel.py:103-156`) and every command calls `require`/`get`. Measured 0.6 ms per create at 200 events, 1.8 ms at 600, 5.5 ms at 1,400. A `design-release` run performs on the order of 100 kernel writes (every cog `set_fields`, each attachment creates a document with 2-3 events), each followed by a full rebuild, and `links_of`, `documents_of`, `query(linked_to=)` are themselves O(records). The shell's global lock (F-5) serializes all of this.
Evidence: probe `200 creates: 0.11s (0.6 ms each) / 400: 0.71s (1.8 ms) / 800: 4.40s (5.5 ms each), events=1416`.
Suggested fix: apply each appended event incrementally to the cached projection (the same `_records` switch, applied to one record) instead of invalidating; or materialize records and links in tables.

### F-code-reviewer-13: A child process is admitted under any parent run that ever held a grant, including rejected or completed ones, and grants are never revoked
Severity: major
Area: security
Steps: observe `Runner.submit` (`runner.py:100-104`) and `_record_decision` (`:345`, `:356-368`).
Expected: a child run inherits admission only from a parent that is currently executing under a grant for the step that spawns it; a rejection after an earlier approval revokes the grant.
Actual: `parent_run` skips both the `create` permission check and the first-role check whenever `parent["grant"]` is truthy. After a rejection at a second checkpoint (`capa`, `internal-audit`, `change-control` have two) `state["grant"]` from the first approval stays in the state file, and after `_rewind` the stale grant is still what `_ctx` passes to reaching Cogs (`_require_grant` only compares `run_id`). The grant's `steps` list is never checked by anyone. `ShortageRequester` submits purchase requests as the design **engineer** under the design authority's grant (`procurement.py:138-145`), so alice's requests are "admitted by the grant on parent run", not by her own permission, and SoD then treats alice as requester.
Evidence: code reading; probe A shows `grant False` only because the first checkpoint was rejected.
Suggested fix: clear `state["grant"]` on reject and on rewind; in `submit(parent_run=)` require `parent.status == "running"` and that the parent's current step lists the child process; have `_require_grant` check `ctx.step in grant["steps"]`.

### F-code-reviewer-14: `kernel.query(linked_to=<unknown record>)` raises `TypeError`
Severity: minor
Area: records
Steps: `hub.kernel.query(linked_to="rec:task:NOPE")`.
Expected: an empty list or a `KernelGuardError("exists", ...)`.
Actual: `kernel.py:197-198` evaluates `self.get(linked_to)["links"]` before the guarding `if`, so a stale or mistyped link target (the shell's `/boards` does not pass `linked_to`, but the CLI/API do) is a 500.
Evidence: probe `-> TypeError 'NoneType' object is not subscriptable`.
Suggested fix: resolve `other = self.get(linked_to)` once before the loop.

### F-code-reviewer-15: `Kernel.seed()` in January closes the December period it has just opened
Severity: minor
Area: records
Steps: run `hubproto init` on 2027-01-05 (or patch `kernel.date.today`).
Expected: the previous, current and next month open; December of the year before last closed.
Actual: `seed` opens months -1/0/+1 (`kernel.py:380-384`) then closes `{year-1}-12` (`:385`), which in January is the month it just opened; `_latest` takes the last event, so bills dated last month cannot post (`period-open`).
Evidence: probe `{'period:2026-12': False, 'period:2027-01': True, 'period:2027-02': True}`.
Suggested fix: close `{year-2}-12` or compute the closed period relative to the earliest opened one.

### F-code-reviewer-16: Role/object singularisation and `object_of` are guessable heuristics with no Guard when they disagree
Severity: minor
Area: policy
Steps: `singular_role("Sales")`, `singular_role("Data analysis")`, a process whose first step is "Receive goods" with a permission "Staff may create goods in ...".
Expected: either a deterministic mapping or a compile error when a process object has no matching `create`/`approve` permission sentence.
Actual: `extract.py:34-44` produces `sale`, `data-analysi`, and `good` for "goods", while `object_of` (`:103-110`) produces `goods`; `consistency_guard` never checks that the process object has any `create` permission or that its first-step role holds one, so every run of such a process is refused at `submit` with "may not start" and the Frame author gets no finding at compile time. `_match_sentence` (`:210-280`) also applies the permission/threshold/routing/criteria regexes in every rule section, so a permission written under `## Routing` compiles.
Evidence: probe `singular_role('Sales')= sale | 'Data analysis'= data-analysi ... object_of('Receive goods')= goods | perm object 'goods' -> good`.
Suggested fix: add a consistency error "process X: no permission lets any role create <object> in <folder>" and "first-step role R has no create permission"; restrict each regex to its section; use an explicit irregular-plural table.

### F-code-reviewer-17: Two steps with the same title alias each other: duplicate states, one Gate overwrites the other, no Guard
Severity: minor
Area: policy
Steps: a process with `2. [staff] Review the task` and `4. [staff] Review the task`.
Expected: a compile error (step ids must be unique).
Actual: both get id `review-the-task`; `render_workflow` emits `awaiting-review-the-task` twice; `render_manifest` attaches both Gates under the same `gate.step`; `_next_state` (`runner.py:237`) and `_prior_pending` resolve by first match, so approving the second Gate transitions to the first Gate's target; `consistency_guard` reports nothing. Related: the `approve` alias (`release.py:75-87`) silently binds "Approvals in X" to the first checkpoint starting with approv/accept/confirm/authori, so a process with two such checkpoints routes the second one to the step's role with only a warning.
Evidence: probe `step ids: ['file-the-task','review-the-task','render-the-record','review-the-task'] / consistency errors: [] / states: ['open','awaiting-review-the-task','awaiting-review-the-task','closed','rejected']`.
Suggested fix: error on duplicate step ids; error (not alias) when "Approvals in X" is ambiguous.

### F-code-reviewer-18: SLA escalation adds assignees who may not approve, and reports the wrong rule
Severity: minor
Area: workflow
Steps: let a `calibration` item go overdue; `hubproto tick`.
Expected: only people who can decide are added; the Track cites the rule that added them.
Actual: `tick` (`runner.py:453-486`) adds `principals_for` the overdue route with no `authz.check` and no SoD filter (`_human_gate` applies both at routing time); bob is added to a calibration he cannot approve (`Quality managers may approve calibrations` only), gets an inbox card with an Approve button and a 400 on click. `rules[0]["source"]` is cited whatever rule produced the extras.
Evidence: probe `calibration: overdue -> bob (role:management-representative) but bob may NOT approve calibration in Quality/Calibration`.
Suggested fix: reuse the candidate filtering from `_human_gate`; cite the matching rule.

### F-code-reviewer-19: The submitter controls fields that cogs and Gates trust
Severity: minor
Area: security
Steps: `POST /start` with `json` = `{"part": "rec:part:PN-0001"}` on a design, or an `attachments` entry with `"doc_class": "evidence"`.
Expected: `record-filer` writes only the record type's declared fields; evidence documents are produced by the hub, not uploaded as such; the released part is chosen by the design authority.
Actual: `RecordFiler` (`cogs.py:105`) files every submitted key (`risk`, `conditions`, `match`, `approved_by`, `grant`, `journal`, `filed_by`, ...); `_check_fields` keeps unknown fields (`kernel.py:244`); `PartReleaser` (`cad.py:320-331`) revises whichever existing part the submission names; attachments take `doc_class`/`doc_type` from the submitter (`cogs.py:116`), so a user can file immutable "evidence" of class `evidence`. Cogs that compute these overwrite them when present, so the practical exposure today is `part`, document classes and whatever a process without a computing cog routes on.
Evidence: code reading; probe W (`document-control` approved by `doc-reviewer` on a self-declared `change_type: minor` for a rewrite) shows the shape of the problem.
Suggested fix: whitelist fields per record type (`fields` in `record-types.json`) and drop the rest with a problem; force attachment class to `external`/`working` for user uploads; have `part-releaser` look the part up by design link, not by submitted field.

### F-code-reviewer-20: Document check-out is advisory and immutability is a field away
Severity: minor
Area: DMS
Steps: `dms.checkin(ref, bytes, by="alice")` on a document nobody checked out; `kernel.set_fields(doc, {"doc_class": "working"})` on an evidence document, then `add_version`.
Expected: check-in requires a prior check-out by the same person; the class of an evidence/external document cannot change.
Actual: `checkin` (`dms.py:139-147`) only refuses when **someone else** holds the lock, `add_version` clears the lock (`:113`), and the immutability test reads `fields.doc_class` (`:100`), which `set_fields` can change. `next_revision("AZ")` returns `"A["` (`:58-65`). `DMS.content(ref, version)` raises `StopIteration` for an unknown version (`:126`), so `/download?v=99` is a 500.
Evidence: code reading.
Suggested fix: require `checked_out_by == by` in `checkin`; keep `doc_class` out of `set_fields` (or reject class changes for immutable classes); handle `rev[-1] == "Z"` by carrying; raise `KernelGuardError` for an unknown version.

### F-code-reviewer-21: Anyone can make a reviewer cog file records, including from inbound email
Severity: minor
Area: security
Steps: `hubproto msg --from anyone --to ap-reviewer --subject x --body "please file" --attach bill.json`, or `hubproto mail-in` with a crafted `.eml`.
Expected: the sender must be a member and must themselves be allowed to start the process the cog files.
Actual: `Hub.send`/`Inbox.send` (`inbox.py:46-70`) never check the sender; `ReviewerCog.task_inbox` (`cogs.py:573-592`) submits `files_process` as the cog whenever the body contains "file", "please" or "process"; `purchasing-assistant` holds `staff` so it can raise purchase requests. `parse_eml` trusts the `From:` header. Messages to non-members are also accepted and shown to whoever uses that `as`.
Evidence: code reading.
Suggested fix: check the sender is a member and `authz.check(sender, "create", object, folder)` before the cog files; record the sender as `requested_by`/owner.

### F-code-reviewer-22: Worker client and worker error handling gaps
Severity: minor
Area: architecture
Steps: point `HUBPROTO_CAD_WORKER_URL` at a server that closes the connection mid-response.
Expected: `worker-unreachable` warning (the exporter treats that as "checks not run").
Actual: `call_worker` (`cad.py:166`) catches `URLError`, `TimeoutError`, `JSONDecodeError` only; `ConnectionResetError`/`http.client.RemoteDisconnected`/`IncompleteRead` propagate to `Cog.invoke`, which returns `invocation-failed` and the Gate fails the run. The worker has no authentication, no body limit (`worker.py:191,157`), runs tools with `timeout=600` in a thread per request, and `kicad_check` does `json.loads(report.read_text())` without a size cap. The "design rule error limit" sentence is in **percent** (`design-control-sop.frame.md`) but `cad.py:185,261` compares an absolute error count.
Evidence: code reading.
Suggested fix: catch `OSError` and `http.client.HTTPException`; bound request bodies; make the threshold unit match (`limit` as a count, or compute a percentage of checks).

### F-code-reviewer-23: Files are written non-atomically and without cross-process locks
Severity: minor
Area: architecture
Steps: kill the process during `Runner._save`, `ReleaseStore.save`, `ProposalStore.save` or `Inbox._append`; or run `tick` while the shell decides the same run.
Expected: a crash leaves the previous version; concurrent writers do not lose updates.
Actual: `write_text` truncates then writes (`runner.py:71-73`, `release.py:254-259`, `compile.py:149-151`); `Track.append` and `Inbox._append` use `open("a")` with no lock; `Track.append` re-reads the whole file to compute `seq` (`track.py:24`); SQLite is the only durable store. `index.json` is a read-modify-write list.
Evidence: code reading.
Suggested fix: write to a temp file and `os.replace`; `flock` per run dir and per index; keep `seq` from the last line rather than `len(read())`.

### F-code-reviewer-24: `finalize` does not re-guard the rule set it actually releases
Severity: minor
Area: policy
Steps: reject the removal of a permission whose folder was also removed in the same proposal; approve everything else; release.
Expected: the release is checked as a whole.
Actual: `finalize` (`compile.py:125-141`) drops rejected adds and re-adds rejected removals, then renders without running `schema/grounding/consistency` on the resulting list, so the bundle can reference undeclared folders, roles without members, or a routed cog without criteria.
Evidence: code reading.
Suggested fix: run the guards on `rules` in `finalize` and refuse on errors.

### F-code-reviewer-25: Dead code, duplicated logic and fragile internals
Severity: minor
Area: architecture
Steps: grep.
Expected: n/a.
Actual: unused: `authz.readable_folders`/`_objects`, `Kernel.file_bill`/`mark_bill`/`file_document`, `BillFiler`/`DocumentFiler` (registered, never named in a Frame), `Runner._remaining_after` (used only in a `pass` branch, `runner.py:347-351`), `rules.KINDS["process"]` lacks `object`/`record_type` while `validate_rule` never sees extraction's extra keys. Duplicated: `shell.summary_of` vs `cli_records._summary`, `shell.fmt` vs `cli_records._fmt`, the field-assignee table appears in `extract.py:98`, `runner.py:248-249` and `guards.py:166`; `cli_records._replace` edits argparse's private `_choices_actions` for a conflict that does not exist. `ReviewerCog.task_review` (`cogs.py:564`) overwrites `reasons` with the last criterion's reasons, so the escalation message cites the wrong rule when a cog has several. `release.render` sorts routing by `source.line` across Frames (`release.py:172`), so two Frames routing the same process interleave by line number. `extract.py:18` and `inbox.py:7` use `import re` (the host project's AGENTS.md forbids it; this repo has no such rule, noting only).
Evidence: `grep` counts in the review log.
Suggested fix: delete or wire up; one shared `_FIELD_ASSIGNEES` table; one `summary_of`.

### F-code-reviewer-26: Is a self-declared `change_type: minor` meant to let the doc-reviewer cog approve a rewrite?
Severity: question
Area: policy
Steps: as alice start `document-control` with `change_type: minor` and a body that replaces the whole document.
Expected: unclear. The routing "with low risk go to the doc-reviewer cog" and the criteria "when the change is minor" both reduce to the author's own field (`cogs.py:300-302`).
Actual: the cog approves and the document becomes effective with no human review.
Evidence: probe `doc-control status completed decided by [('doc-reviewer','cog')]`.

### F-code-reviewer-27: Should cogs be able to hold people's roles and permissions interchangeably?
Severity: question
Area: policy
Steps: `/admin`: `purchasing-assistant` holds `buyer, staff`; `quality-assistant` holds `quality-manager`.
Expected: unclear. `authz.check` makes no distinction between persons and cogs, so a cog with `staff` may create records (`ReviewerCog.task_inbox`) and a cog with `quality-manager` could in principle be routed any quality-manager approval; only the approval-criteria Guard and `_human_gate`'s person filter keep it in check.
Actual: works as described; worth stating as a design rule either way.
Evidence: code reading (`authz.py:30-55`, `runner.py:266-293`).
