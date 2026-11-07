# Reliability and data integrity: adversarial review (2026-11-07)

**Lens:** a senior engineer on call. What loses or corrupts data, what runs twice, what stops without telling anyone,
and what gets slower as the hub fills up.

**Setup:** sandbox hubs copied from one base. Every repro below starts from it:

```bash
D=$(mktemp -d); cp -r ~/source/hub-erp-proto/frames $D/frames; cd ~/source/hub-erp-proto; export HUBPROTO_ALLOW_STAND_IN=1
.venv/bin/hubproto --root $D init; .venv/bin/hubproto --root $D bootstrap-release --as bob --second carol
# python:  from hubproto.app import Hub; hub = Hub(D)
```

How I tested:
- Real threads: a `CogWorker` on an `RLock`, as in `tests/test_cog_queue.py`.
- A fake Ollama on localhost. It sleeps before it answers, so the worker really lets go of the hub lock.
- Real processes for the races between processes.
- Monkeypatched crash points. A crash is a `SystemExit` raised between two writes. A full disk is a `write_bytes`
  that writes half the bytes and then raises `ENOSPC`.
- Office files I built by hand (zipfile) or with python-docx and openpyxl in a separate venv. I checked the output
  with ElementTree, python-docx, openpyxl and `office.extract_text`. LibreOffice and Docker were not available here,
  so I did not open any output in an office suite.

Every finding below is reproduced unless it says **unverified**. Line numbers are from the tree as of this review.
Other people were editing the tree at the same time.

**What held up:**
- `Runner._save` writes `state.json` with tmp+replace.
- Seat tokens are checked.
- `verify_edit` refuses a second run with `applied_in`.
- `/msg` and other forms use one-use tokens, so a double click is absorbed.
- Comment anchors survive deleting a paragraph or a section.
- Every docx and odt I produced stayed well-formed: about 30 operation and file combinations, including textboxes,
  nested tables, fields, footnotes, tracked changes and content controls.
- A 4,000-heading docx is edited in under 0.3 s.

---

## 1. [blocker] A cog signs changes to the item made while its model was thinking, and never read them

**Where:** `runner.py:474-486` (`cog_decide`), `runner.py:637` and `runner.py:838` (what the decision records).

**Repro:**
1. Start a fake Ollama that sleeps 2 s and answers `Verdict: pass`. Set `HUBPROTO_OLLAMA_URL`.
2. Start a `CogWorker(hub, lock)`.
3. Run the following:

```python
rid = hub.runner.submit("document-control", {"record": {"title": "SOP-RACE", "change_type": "major", "body": "# SOP-RACE\n1. Purpose"}}, owner="alice")
rec = hub.runner.load(rid)["record"]                       # waits for bob, carol
hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=rec)
fake.thinking.wait()                                       # qa-manager's judgment review is at the model; hub lock released
with lock:                                                 # an ordinary /attach request served meanwhile
    hub.dms.intake(folder="Quality/Documents", by="alice", content=b"ANNEX-TOKEN-XYZ: raise the torque limit, skip inspection",
                   filename="annex.txt", channel="shell", sender="alice", attach_to=rec)
```

**Observed:**
- The track has `gate.signed` by qa-manager: "approved on its review, under its approval criteria: its review
  passed…".
- 0 of 1 model prompts contained the annex.
- The item now carries the annex. The qa-manager's decision was accepted.

**Why:**
- `cog_decide` passes `payload_digest=self.seen_digest(state)`, which is evaluated **after**
  `cog.invoke("review")` returns.
- That review includes the model call, and the worker releases the lock during it.
- So the digest describes the item as it is after the model call. The review covered the item as it was before.
- The decision does not keep the seen digest either. `_record_decision` stores `pending["payload_digest"]` and
  `sha256(rec["fields"])`, which cover the fields only, not the documents or links.
- So nothing afterwards shows that the annex was never reviewed.
- For a minor change, the qa-manager's signature is final.

**Impact:** the electronic signature does not bind what the signer saw. A person (or a second cog task) can change
the item during the up-to-180 s window. The hub then records an approval of content no reviewer read.

**Fix:**
- Compute the seen digest before the review starts and pass it to `decide`.
- Exclude the cog's own evidence document from the digest (identify it by locator), or take the digest again
  right after filing it.
- Store the quoted seen digest in the decision and in the signature hash.

## 2. [major] A crash in the middle of `decide` lets a second, contradictory signature land on the same Gate

**Where:**
- `runner.py:633-660`: `_record_decision` writes the Track and the kernel decision.
- `runner.py:840`: `_save` comes afterwards.
- `runner.py:86`: `load` trusts the stored `status` in `state.json`.

**Repro:**

```python
rid = hub.runner.submit("document-control", {"record": {"title": "SOP-CRASH", "change_type": "major", "body": "# SOP"}}, owner="alice")
st = hub.runner.load(rid)
real = Runner._save
Runner._save = lambda self, s: (_ for _ in ()).throw(SystemExit("killed")) if s.get("pending") is None else real(self, s)
try: hub.runner.decide(rid, "bob", "approve", meaning="fit for release", payload_digest=st["seen_digest"])
except SystemExit: pass
Runner._save = real; hub2 = Hub(D); st2 = hub2.runner.load(rid)
hub2.runner.decide(rid, "carol", "reject", meaning="not fit", payload_digest=st2["seen_digest"])
```

**Observed:**
- After the restart, `state.json` still says `waiting`, pending for bob and carol.
- The Track ends `gate.decided, grant.issued`.
- carol's reject is accepted. The record carries `[('bob','approve'), ('carol','reject')]` and ends `rejected`.

**Impact:** two opposite signatures on one Gate, and a record state that depends on which signer clicked after
the restart. The same window exists everywhere the Track, the kernel and `state.json` are written as three
separate steps. That includes `advance`, which re-runs a step whose `step.started` was written but whose result
was not saved. For `ledger-poster` that could post a journal twice. That last case is **unverified**.

**Fix:**
- Treat the Track as the source of truth, as `track.py` says. `load()` should take `status` from
  `Track.status()`, and `decide` should refuse when the Track already has a `gate.decided` for the pending step.
- On startup, reconcile runs whose `state.json` disagrees with their Track.
- Make automatic steps idempotent: a step key in the envelope, and the kernel refuses a duplicate.

## 3. [blocker] "Delete/replace/insert at a paragraph" acts on the whole list, table, section or content control, and reports success

**Where:**
- `edits.py:440-450`: `find_block` matches top-level blocks with `want in t`.
- `edits.py:333`: a `text:list` is one block.
- `edits.py:302` and `edits.py:340`: `text:section` and `w:sdt` are one "other" block.
- `w:tbl` is a block of its own, and its kind is in the default kinds.

**Repro:**

```python
from hubproto import edits, office, wopi
blank, _ = wopi.blank_document("docx")
src, _ = edits.apply_ops(blank, "docx", [{"op": "insert", "at": "end", "lines": ["# Approved suppliers",
         "| Supplier | Status |", "|---|---|", "| Acme | approved |", "| Bolt Co | suspended |", "| Nut Ltd | approved |", "End of list."]}])
out, rep = edits.apply_ops(src, "docx", [{"op": "delete_paragraph", "target": "Bolt Co"}])
print(rep, office.extract_text(out, "docx").splitlines())
```

**Observed (every row reproduced):**

| Format | Operation | Result |
|---|---|---|
| docx table | `delete paragraph: Bolt Co` | "deleted the paragraph"; text is now `['Approved suppliers', 'End of list.']`: the whole table is gone |
| docx table | `insert after: Acme` | Lands after the whole table |
| odt list | `delete paragraph: Bolt Co (suspended)` | Deletes all three list items |
| odt `text:section` | `replace paragraph: Applies to Plant B.` | Replaces all three paragraphs of the section with one |
| docx content control (`w:sdt`) with two paragraphs | `delete paragraph: Intro line two.` | Deletes both |

Also seen on the same files:
- `replace paragraph` on a paragraph with a pending tracked change (`w:del`/`w:ins` by bob) and a footnote
  reference writes a plain paragraph. The pending change is silently accepted and the footnote reference is
  dropped (the footnote is orphaned).
- `replace: shall → must` reports "replaced 2 occurrence(s)" but leaves the "shall" that follows a textbox in the
  same paragraph. The pattern `<w:p\b…>.*?</w:p>` ends at the textbox's inner `</w:p>`.

**Impact:**
- An ordinary request to a cog ("remove Bolt Co from the approved supplier list") destroys the list.
- The cog's reply says it was done.
- `op_applied` agrees: the target text is gone, so `verify_edit` reports it done.
- The previous version can still be recovered, but nobody is told to look.

**Fix:**
- Descend into containers (`w:tbl/w:tr/w:tc`, `text:list/text:list-item`, `text:section`, `w:sdtContent`) and
  operate on the innermost paragraph.
- Refuse (report not ok) when the match is a container and the operation is paragraph-level.
- Never drop `w:del`/`w:ins`, `footnoteReference` or field runs when replacing a paragraph. Refuse instead.
- Add these shapes to `tests/test_edits.py`.

## 4. [major] Spreadsheet edits destroy formulas and cell comments

**Where:**
- `edits.py:1005-1013`: `replace` rewrites a cell from its displayed or cached value.
- `edits.py:886-902`: `set` rebuilds the cell.
- `Sheet.grid` for ods uses `_odt_text(cell)`, which includes the annotation text.

**Repro (xlsx):** a sheet as Excel writes it, with `D2 = <f t="shared" ref="D2:D6" si="0">B2*C2</f><v>40</v>`,
`D3:D6 = <f t="shared" si="0"/>` and `G1 = SUM(D2:D6)`.

```python
out, rep = edits.apply_ops(src, "xlsx", [{"op": "replace", "target": "40", "with": "45"}])  # also: set D2, delete rows 2
```

**Observed (xlsx):**
- The report says "changed 2 cell(s)".
- D2 is now the constant `45`. The formula and the shared-formula master are gone.
- D3:D6 are left as orphaned `<f t="shared" si="0"/>`. openpyxl reads them as `'='`, so the formulas are lost.
- `set D2` and `delete rows 2` orphan the dependents the same way.
- After `delete rows 2`, the merge `A8:D8` stays put while its content moves to A7.

**Repro (ods):** a cell with a comment
(`<office:annotation><dc:creator>carol</dc:creator><text:p>Checked against drawing rev C</text:p></office:annotation><text:p>Torque 40 Nm</text:p>`),
then `replace: 40 Nm → 45 Nm`.

**Observed (ods):**
- The annotation is gone.
- The cell's value becomes `carolChecked against drawing rev C\nTorque 45 Nm`.
- The cell's style is dropped too, because `_ods_cell` writes a bare cell.

**Impact:** silent loss of calculations and reviewer comments in controlled spreadsheets (limits, BOMs, calibration
sheets). The cog reports success. Excel will show a repair prompt for the orphaned shared formulas. That is
**unverified** (no Excel here); openpyxl's reading is verified.

**Fix:**
- Never `replace` or `set` a cell that holds an `<f>` or `table:formula` unless the operation is a formula. Refuse
  and report it.
- Expand a shared-formula master into its dependents before touching any of them, or refuse.
- For ods, keep `office:annotation` and the cell attributes, and replace only the `text:p` value.
- Shift or refuse `mergeCell` ranges when rows move.

## 5. [major] The sheet engine assumes one serialization: valid files are silently left unchanged, crash, or get rows moved

**Where:** `edits.py:775`, `edits.py:782`, `edits.py:786` and `edits.py:805-817`.

**Repros and observations:**
- **xlsx with a prefixed main namespace** (`<x:worksheet xmlns:x=…><x:sheetData><x:row>…`, as the .NET OpenXML SDK
  writes it):
  - `append rows` and `set B2` both report `ok: True` ("appended 1 row(s) from row 1", "set B2").
  - The sheet XML is byte-identical afterwards.
  - The grid shown to the cog says "(the sheet is empty)", while `extract_text` reads the data fine.
- **xlsx rows without `r`** (optional in ECMA-376; streaming writers omit it): `apply_ops` raises
  `AttributeError: 'NoneType' object has no attribute 'group'`. It does not return a report, which breaks the
  "one bad operation never spoils the others" contract. `verify_edit` then fails with a 500 page instead of JSON.
- **ods with a large empty gap:** data in row 1, `<table:table-row table:number-rows-repeated="3000">`, then
  "Bottom total". After `set A1`, "Bottom total" has moved from row 3002 to row 2. Rows that come after a repeat of
  more than 2000 go into `ods_rows`, ahead of the gap that went into `tail`.
- **ods with `table:table-header-rows`** (rows to repeat): the header rows are skipped by the grid. `set A2=Washer`
  overwrote "Nut", which is the third row on screen.
- **xlsx "first sheet":** the engine always edits `xl/worksheets/sheet1.xml`, whatever the workbook's sheet order
  says. This one is reasoned from the code and **unverified** against a reordered workbook.

**Impact:** "done" reported for edits that did not happen, and row data silently moved in sheets the hub then
saves as a new version.

**Fix:**
- Parse sheets with a namespace-aware XML parser (ElementTree with explicit namespaces) instead of prefix regexes.
- Derive the row number when `r` is missing.
- Keep the order of rows and repeats exactly as read: treat a repeat as a run-length entry, not an expansion.
- Count header rows.
- Resolve the first sheet from `workbook.xml` and its rels.
- Make `apply_ops` catch any exception during parsing and turn it into a report.

## 6. [major] Text edits corrupt non-UTF-8 files and shift replacements next to some characters

**Where:**
- `edits.py:387`: `content.decode("utf-8", "replace")`, then `to_bytes` writes UTF-8.
- `edits.py:233-243`: `_find` assumes `fold()` keeps string lengths.

**Repros and observations:**
- **A cp1252 .txt with CRLF line endings,** then `insert at end`:
  - Every non-ASCII character is replaced by U+FFFD in the new version.
  - "Prüfanweisung – Lieferant Müller" becomes "Pr�fanweisung � Lieferant M�ller": 6 characters
    lost for good.
  - The line endings come out mixed: 3 CRLF and 2 LF.
- **A docx paragraph "İzmir plant: suppliers shall be approved."** with `replace: Suppliers Shall → suppliers must`.
  The case differs, so the folded path is taken. The result is "İzmir plant: **ssuppliers mustbe** approved.":
  `"İ".lower()` is two characters long, so every offset after it is off by one.
- **A UTF-8 .md that starts with a BOM:** `append to section: Scope` fails with "no section headed Scope", because
  the first heading is not recognized.

**Impact:**
- Any edit to a legacy-encoded text document mangles the whole file.
- The case-insensitive match writes into the wrong characters, silently, and reports success.

**Fix:**
- Detect the encoding, and refuse when the file is not valid UTF-8.
- Keep the newline style and the BOM.
- In `_find`, fold character by character with an index map, or fall back to `re.search(re.escape(t), s, re.I)`
  on the original string. Do not use offsets from a lowered copy.

## 7. [major] Inbox writes from two processes lose messages

**Where:** `inbox.py:29-37` (append and rewrite) and `inbox.py:94-103` (`mark`). Nothing locks between processes.

**Repro:** process A appends 500 notifications. Process B calls `mark()` repeatedly for 3 s, as the shell does
for `applied_in`, `edit` or `chat_clear`. Both run on a `messages.jsonl` with 2,000 lines.

```python
# A: for i in range(500): Inbox(P).notify("alice", kind="t", subject=f"append-{i}", body="x")
# B: while t < 3: Inbox(P).mark(first_id, seen=n)
```

**Observed:** only 57 of 500 appended messages are present (54 on a second run).

**Why:**
- `mark` reads the whole file, writes `messages.jsonl.tmp` and renames it over the original.
- Any append made in between goes to the old inode and is gone.
- Both processes also use the same fixed tmp name.

**Impact:**
- `service` mode (`hubproto cog-users`, the `cog-users` compose profile) runs exactly this pair: the cogs' replies
  are appended from one process while the shell marks replies from the other.
- People's messages, cog replies and Gate notifications vanish with no error.
- The in-process paths are safe only because everything runs under the hub lock.

**Fix:** move the inbox into SQLite (`kernel.db` or `queue.db`), with an `UPDATE` for `mark`. At minimum, take an
`fcntl.flock` on a sidecar lock file around read-modify-write and append, and use a unique tmp name.

## 8. [major] One torn line in `messages.jsonl` takes down every item page and the inbox, and swallows the next message

**Where:** `inbox.py:39-42`. `all()` runs `json.loads` on every line and handles no errors.

**Repro:**

```python
hub.inbox.notify("alice", kind="info", subject="hello", body="first")
with hub.inbox.path.open("a") as fh: fh.write('{"id": "deadbeef", "at": "2026-11-07T1')   # disk full / kill -9 mid-append
hub.inbox.notify("bob", kind="x", subject="s", body="b")
```

**Observed:**
- `for_principal` and `find` raise `JSONDecodeError`.
- The next `notify` appends onto the torn line, so bob's message is glued into it and lost.
- Over HTTP, `GET /document/<loc>` and `GET /inbox` return **500**. `/` and `/files` still return 200.
- Every cog task that calls `inbox.find` then fails.

**Impact:** a single partial write turns into an outage of every chatter, the inbox, editor comment routing and the
cog queue, until someone edits the file by hand.

**Fix:**
- Skip and log an unparsable line, and quarantine it to `messages.bad.jsonl`.
- Before appending, make sure the file ends with `\n`.
- Better still, use SQLite as in finding 7.

## 9. [major] The blob store keeps a half-written blob forever and serves it as the version

**Where:** `dms.py:34-39`. `put` writes in place, skips the write when the path exists, and never checks the hash.

**Repro:**

```python
pathlib.Path.write_bytes = half_then_ENOSPC          # writes len//2 bytes, then raises OSError(ENOSPC)
try: hub.dms.add_version(doc, new, by="alice", filename="b.docx")
except OSError: pass
pathlib.Path.write_bytes = real
hub.dms.add_version(doc, new, by="alice", filename="b.docx")   # the user retries after space is freed
got, v = hub.dms.content(doc)
```

**Observed:**
- Version 2 is recorded with sha `4ed19b00a26b` and size 101,812.
- `content()` returns 50,906 bytes whose sha is `0cc8f43d14e0`. The content is not intact.
- The retry "succeeded" because the file already existed.
- A `kill -9` during the write leaves the same torn file.

**Impact:** a controlled document or evidence version that cannot be opened. Its record claims a sha that does not
match. Because the store is content-addressed, every later save of the same bytes reuses the torn file.

**Fix:**
- Write to `sha.tmp`, `fsync`, then `os.replace`.
- In `put`, when the file exists, check its size (or hash), and rewrite it on a mismatch.
- Check the sha in `get`, or at least in `backup`.

## 10. [major] Side effects run twice after a crash: edits applied twice, duplicate replies and evidence

**Where:**
- `wopi.py:300` (`add_version`) and `wopi.py:308` (`inbox.mark`): two writes with no idempotency key.
- `workqueue.py:219-221` and `197-206`: the queue is at-least-once, with no record of finished side effects.

**Repro A (apply_replies):** a cog reply carries `edit.ops = [insert after "Scope": "Purpose: control suppliers."]`.
Make `inbox.mark` raise once, as if the process were killed after the version was saved. Restart, and the next
mention or `/apply-replies` calls `apply_replies` again.

**Observed A:** versions `['initial', 'edits and replies … written into the document', 'edits and replies …']`.
The document now has "Purpose: control suppliers." **twice**.

**Repro B (recover):** queue a mention task. Make `CogQueue._set(status="done")` raise, as if the process were
killed after the work finished. Start a new `Hub` with a `CogWorker`; `recover()` puts the task back in the queue.

**Observed B:** the task ends `done` with `attempts=2`. There are **2** cog replies on the chatter, and **2**
review evidence documents (DOC-0002 and DOC-0003) are attached.

**Impact:**
- Every deploy or OOM kill during a 70 s model answer can duplicate document edits, chatter replies, evidence and
  notifications.
- Duplicated inserts in a controlled document are silent.

**Fix:**
- Mark the reply applied in the same transaction as the version, or store the source reply id in the version
  event, and have `pending_replies` skip replies already referenced by a version.
- Give each queue task a result ledger: write "replied with msg X" or "filed evidence Y" before acting, and check it
  on a retry.

## 11. [major] `recover()` takes back tasks that another live worker is still running

**Where:** `workqueue.py:197-206`. It requeues every `working` task, whatever its `worker` is and however long
ago it started. It is called from `CogWorker.start` (`workqueue.py:348-350`) and from `cog-users --loop`
(`cli.py:1018`).

**Repro:**

```python
svc = CogQueue(db, mode="service"); svc.worker_id = "cog-users:4242"
t = svc.submit("quality-assistant", "task", by="alice", title="long agent task", payload={"goal": "x"}); svc.claim()
shell = CogQueue(db, mode="worker"); shell.worker_id = "hub:1"
shell.recover(); again = shell.claim()
```

**Observed:** `recover()` moved 1 task back to the queue, and the shell claimed the same task (`attempts 2`)
while the service was still working on it. When either finishes, it overwrites the other's result.

**Impact:**
- Restarting the hub while `cog-users` runs makes the same task run twice concurrently: duplicate edits, Gate
  decisions and messages.
- Two `cog-users --loop` replicas do the same to each other.
- After three restarts a live task is marked `failed`.

**Fix:**
- Requeue only tasks whose `worker` is this host and PID (which is dead), or tasks whose heartbeat or lease has
  expired. Add a `lease_until` column that the worker renews.
- Make the final `_set` conditional: `WHERE id=? AND worker=?`.

## 12. [major] The queue worker thread dies on its first unexpected error and nobody notices

**Where:** `workqueue.py:360-372`. `_loop` has no `try`. `work_one` guards only `execute`; `claim()` and
`_set()` run outside the guard.

**Repro:** hold `BEGIN EXCLUSIVE` on `queue.db` from another connection for longer than the 10 s busy timeout
(another process mid-write, a backup tool, a slow disk), then wake the worker.

**Observed:**
- `sqlite3.OperationalError: database is locked` is raised in `claim`.
- The worker threads alive afterwards are `[False]`.
- A task submitted afterwards stays `queued` and nobody works it.
- `/healthz` still answers `ok`.

**Impact:** every cog (mentions, Gates waiting for a cog, assigned work) silently stops until the hub restarts.
Pages keep promising "next in its queue".

**Fix:**
- Wrap the body of the loop in `try/except Exception`, log it, back off and continue.
- Have `/healthz` report a dead worker, or the age of the oldest queued task.

## 13. [major] A stale editor session overwrites the hub's fix when both saves fall in the same second

**Where:**
- `editor.py:355-359`: the conflict check compares timestamps at one-second resolution.
- `release.py:25`: `utcnow` drops microseconds.
- `wopi.py:79-86`: `wopi_time` writes `.000000`.
- `put_file` never checks `X-WOPI-Lock` either.

**Repro (HTTP):**
1. alice opens the editor (`checkout via="editor"`).
2. PutFile returns `LastModifiedTime = T`.
3. The editor page posts `/verify-edit` straight away (`verify_edit` → hub version 3, at second T).
4. alice's session saves again (autosave, or the reload's save on close) with `X-COOL-WOPI-Timestamp: T`.

**Observed:**
- The second PutFile returns **200**, where a 409 was expected.
- The latest version is 4, by alice. It does **not** contain the cog's edit.
- The reply stays marked `applied_in: 3`, so the edit is never retried.

**Impact:** the documented "the hub finishes the edit in the file and the editor reloads" loses the edit exactly
in its normal timing, because `verify-edit` runs right after the seat's save. A WOPI client that sends no
timestamp (Office Online) is never protected.

**Fix:**
- Use a version counter or a content hash as the conflict token instead of a timestamp, or keep microseconds in
  version events.
- Refuse a PutFile whose base is not the latest version.
- Check `X-WOPI-Lock` against `wopi_lock` on PutFile, as WOPI requires.

## 14. [major] Unbounded growth turns into seconds of work per request under the single hub lock

**Where:**
- `inbox.py:39-45`: every read parses the whole file.
- `shell/queue.py:89`: polled every 2.5 s or 12 s per open page.
- `kernel.py:96`: any append invalidates the projection.
- `kernel.py:116-172`: the projection is rebuilt from all `rec:` events.
- `dms.py:109-131`: every save stores the full text twice.
- `model.py:240` and `model.py:325`: `self.calls` grows forever.

**Measured:**

| Measurement | Result |
|---|---|
| Inbox, 20,000 messages (11 MB) | Chatter read 202 ms; `mark()` 322 ms |
| Inbox, 100,000 messages (53 MB) | Chatter read 952 ms; `mark()` 1,735 ms |
| Inbox reads per request | Document page parses the whole inbox 3 times, `/inbox` 2, `/queue-status` 1 (each poll) |
| One editor save (PutFile) | 8 full projection rebuilds |
| Projection rebuild, 5,000 events (102 MB `kernel.db`) | 166 ms |
| Projection rebuild, 20,000 events (410 MB) | 761 ms |

- At 20,000 events, one autosave holds the hub lock for about 6 s.
- Each version adds about 22 KB of text to the event store: `text_excerpt`, plus the full `text` in
  `fields_set`.

**Not measured but true by construction:**
- `queue.db` is never pruned.
- Blobs are never collected; every autosave is a full copy.
- `ModelGateway.calls` is an in-memory list with no limit.

**Impact:** the hub gets slower on every page as history grows, and every request waits behind any other.

**Fix:**
- Move the inbox to SQLite with indexes on `record` and `to`.
- Apply the projection incrementally: fold only the events after the last `seq` into the cache instead of
  rebuilding it.
- Store `text` once, per blob, rather than in events.
- Keep a bounded deque for `calls`.
- Have a retention job for finished queue tasks.

## 15. [major] `backup`/`restore` drops the cog queue and the agent runs' audit Tracks

**Where:** `cli.py:44`, `BACKUP_PARTS = ("runs", "releases", "proposals", "blobs", "inbox")`.

**Repro:**

```bash
hubproto --root $D agent --cog quality-assistant --as carol --goal "find nonconformances older than a week"
hubproto --root $D backup $B && ls -a $B          # backup.json blobs inbox kernel.db proposals releases runs
hubproto --root $D restore $B --force && ls $D/state/agents   # No such file or directory
```

**Observed:**
- `state/agents/` (each agent run's step Track, which the README calls the **Audit**, plus `duties.json`) is not in
  the backup, and it is gone after a restore.
- `queue.db` is not backed up. After the restore it is recreated empty, so queued and in-flight cog work and its
  results are lost.
- `settings/` and `.secret` are not backed up either.
- `restore` accepts the set as "complete".

**Impact:**
- The audit trail of what cogs did on their own is lost on the documented restore path.
- Standing duties run again, because their dates are lost.

**Fix:**
- Back up `queue.db` through the SQLite backup API, and copy `agents/` and `settings/`.
- List every part in the manifest. Have `restore` refuse a set that lacks one.
- Add a test that diffs the `state/` tree against what the backup contains.

---

### Also noted (minor, verified)

- **Impossible review dates on 29 February.** `kernel.publish_document` builds the review date as
  `f"{year+1}-{month}-{day}"` (`kernel.py:511`). On 2028-02-29 it stores `review_date: 2029-02-29`, which
  `date.fromisoformat` rejects. The `_DATE_RE` field check lets it through. Fix: use
  `today + relativedelta(years=1)`, or clamp to the 28th.
- **Duplicate text in docx extraction.** `office.extract_text` for docx walks every nested `w:p`, so text in
  textboxes and nested tables appears twice in the record's `text`. Cogs read that field, and `op_applied` matches
  against it.
- **Read access is enough to make the hub write.** `/apply-replies` and `/verify-edit` need only read access, so any
  reader can make the hub write a version. Raised here for reliability (unexpected versions); the security review
  owns it.
