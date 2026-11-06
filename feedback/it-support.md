# IT Support / operations findings

Persona: the person who installs, runs, backs up, upgrades and supports the
hub. Everything below was run against the live stack on this machine
(`docker compose` in `~/source/hub-erp-proto`, hub at
`http://127.0.0.1:8080`) on 2026-11-06, release `b7da4af94d3c571f`,
Docker 29.1.3, Compose 2.40.3. Everything I created is prefixed `[IT]`.
I did not run `docker compose down -v` on the shared stack (other panelists
had 100+ live runs in it); I reproduced the fresh-volume and restore paths on
throwaway volumes instead (`it-fresh`, `it-restore`, both removed).

Summary: 1 blocker, 6 major, 7 minor, 2 questions.

---

### F-it-1: Anyone on the network can act as anyone; the hub is published on every host interface
Severity: blocker
Area: security
Steps:
1. `docker compose ps` and note the port binding.
2. `curl -s "http://127.0.0.1:8080/?as=mallory"` (a name that is not a member).
3. `curl -sI http://127.0.0.1:8080/healthz` and read the `Server` header.
4. Read `hubproto/shell.py` line 304 (`self.user = form "as" or query "as" or DEFAULT_USER`) and any
   `/decide` form (line 600: `hidden_as(user)` is just a hidden `<input name="as">`).
Expected: a prototype without SSO is acknowledged in `README.md`/`feedback/README.md`, but the
default deployment should at least bind to localhost, reject unknown principals, and not let a
POST choose its own signer.
Actual: the shell listens on `0.0.0.0:8080` and `[::]:8080`, so on Docker Desktop / WSL it is reachable
from the LAN. Identity is a free-text query or form parameter; unknown names get a `200` page (the
name is even added to the "Acting as" dropdown) that shows the release digest, every process and
the full routing table. Every approval ("electronic signature") is a POST with `as=bob` in the
body, so any client can sign as bob. The `Server` header advertises the Python patch version.
Evidence:
```
$ docker compose ps
hub-erp   hub-erp-proto:local   Up (healthy)   0.0.0.0:8080->8080/tcp, [::]:8080->8080/tcp
$ curl -s -o /dev/null -w "status=%{http_code}\n" "http://127.0.0.1:8080/?as=mallory"
status=200
(page text) Welcome, mallory not a member  Roles: none | Policy release in effect: b7da4af94d3c571f ...
(dropdown)  Acting as alice ap-reviewer (cog) bob carol dave doc-reviewer (cog) mallory ...
$ curl -si http://127.0.0.1:8080/healthz | head -2
HTTP/1.0 200 OK
Server: hubproto-shell/0.1 Python/3.14.10
```
Suggested fix (optional): bind `127.0.0.1:8080:8080` in `docker-compose.yml` by default and say in
the README that anything else needs a reverse proxy with auth; return 403 for non-members; drop the
Python version from `Server`; document in README under "Security" that `?as=` is impersonation.

---

### F-it-2: One slow CAD export freezes the whole shell for everyone, including /healthz
Severity: major
Area: architecture | performance
Steps:
1. `docker pause hub-erp-cad-worker` (simulates a worker that is up but not answering; connect hangs).
2. In the background, as alice, POST `/start` for `design-release` with `examples/sensor.kicad_sch` attached
   (`json={"kind":"electrical","attachments":[{"name":"sensor.kicad_sch","content_b64":"..."}]}`).
3. Three seconds later, from a second client: `curl --max-time 8 http://127.0.0.1:8080/healthz`.
4. `docker unpause hub-erp-cad-worker`.
Expected: other users and the health check keep working while one run waits on the worker.
Actual: every request (GET and POST, `/healthz` included) is served under one process-wide
`threading.RLock` (`shell.py` 283-296), and `post_start` runs all automatic cog steps
synchronously inside that lock (`shell.py` 955 -> `runner.submit`). `call_worker` has a 120 s
timeout per call (`cad.py` 154) and a KiCad design makes four calls (ERC + three exports), so a
hung worker can stall every user for up to eight minutes per submission. The `/healthz` probe
blocked until my 8 s client timeout; the submit returned only after the worker was unpaused (12 s).
Docker's HEALTHCHECK (3 s timeout) would start failing during any long export.
Evidence:
```
worker paused
== /healthz from a second client while the run is in progress (max 8s):
healthz status=000 time=8.002548s
healthz: curl exit=28 (timed out: blocked by the global lock)
worker unpaused
start: status=303 time=12.049149s
== healthz after:
healthz status=200 time=3.349927s
```
Run created: `[IT] lock probe (worker paused)` (design-release, owner alice).
Suggested fix (optional): serve `/healthz` outside the lock; run cog steps on a worker thread and
redirect to the run page immediately (the Track already records progress); lower the worker timeout.

---

### F-it-3: When the CAD worker is down the run quietly proceeds without ERC/STEP, the user sees a raw Python error, and nothing can re-run the export later
Severity: major
Area: workflow
Steps:
1. `docker compose stop cad-worker`.
2. As alice, start `design-release` through the shell with `examples/sensor.kicad_sch` attached
   (title `[IT] worker-down probe`, kind electrical, two user needs).
3. Open the run page `/run/3de517ad8a?as=alice`; `docker compose exec -T hub hubproto run-show 3de517ad8a`;
   `hubproto record DSN-2026-0011`.
4. `docker compose start cad-worker`; `run-show` again.
Expected: the submission is refused or parked ("the CAD worker is unavailable; try again or ask IT"),
or the export step retries once the worker is back; the user sees a plain-language message; the hub
logs the outage.
Actual: the run completed its automatic steps in 0.9 s and went to bob for approval with
`checks.erc.ran=false`, no STEP/PDF/netlist export (only the BOM, which is computed in-process),
`design_checks_clean=false`. The step shows gate `pass-with-problems` with the message
`ERC not run: <urlopen error [Errno -2] Name or service not known>` (worker-unreachable is mapped
to severity `warning`, `cad.py` 232-235, 248-249). After the worker came back the run is unchanged;
there is no retry/re-run command (see F-it-4), so the only path is for bob to reject and alice to
resubmit. `docker compose logs hub` recorded nothing about the outage. `/healthz` stayed `200 ok`
while the worker was down (see F-it-8).
Evidence:
```
$ docker compose stop cad-worker; docker compose ps -a
hub-erp-cad-worker   Exited (137) Less than a second ago
$ curl ... /start   ->  http://127.0.0.1:8080/run/3de517ad8a?as=alice status=303 time=0.927s
$ hubproto run-show 3de517ad8a
run 3de517ad8a process=design-release owner=alice status=waiting release=b7da4af9...
  export-the-design                    pass-with-problems cog=cad-exporter ok=True problems=2
  pending: Review the design -> bob
(run page, step row) Export the design | cad-exporter 0.1.0 | pass-with-problems |
  erc ERC not run: <urlopen error [Errno -2] Name or service not known> |
  kicad-export DOC-0128: <urlopen error [Errno -2] Name or service not known>
$ hubproto record DSN-2026-0011
  checks     {"erc": {"errors": null, "ran": false, "warnings": null}}
  exports    [{"doc_type": "bom", "document": "rec:document:DOC-0130", ...}]   (no STEP, PDF, netlist)
$ docker compose start cad-worker; hubproto run-show 3de517ad8a   -> identical; export still missing
$ docker compose logs --since 3m hub | tail -3
hub-erp  | workspace shell at http://0.0.0.0:8080/?as=alice        (nothing else)
```
Suggested fix (optional): treat `worker-unreachable` as an error-severity problem that parks the run
in a retryable state; add `hubproto run-retry <id> --step export-the-design`; show "CAD worker
unavailable" instead of the urlopen text; log it.

---

### F-it-4: There is no way to reset, cancel or retry a stuck or failed run; a failed run is still listed as "waiting for bob"
Severity: major
Area: CLI | workflow
Steps:
1. `docker compose exec -T hub hubproto --help` (look for cancel/reset/retry/abort).
2. `grep -rniE 'cancel|abort|reset|retry|resume|requeue|stuck' hubproto/*.py` -> no matches.
3. `hubproto run-show f21db81ca8` (a run another panelist left in `failed`); open `/runs?as=alice`;
   `hubproto record DSN-2026-0004`.
Expected: a support command to cancel a run (closing its record as `cancelled` with a Track entry),
retry a failed step, or re-route a checkpoint; and a failed run should not be shown as waiting for
someone.
Actual: no such verb exists in the CLI or the shell (no hold/cancel on `/run/<id>`; "Put on hold" on
the record needs an approve permission and does not touch the run). Run `f21db81ca8` has
`status=failed` (reason `required-fields: DSN-2026-0004 cannot enter 'awaiting-review-the-design'
without kind`) yet `run-show` prints `pending: Review the design -> bob` and `/runs` shows
`Waiting for bob (due 2026-11-16)`; the record is in state `failed` with no assignee. bob's inbox
cannot act on it; alice cannot withdraw it; the only "fix" is editing files in the volume.
Evidence:
```
$ hubproto run-show f21db81ca8
run f21db81ca8 process=design-release owner=alice status=failed release=b7da4af9...
  score-the-risk                       passed             cog=risk-scorer ok=True problems=0
  release-the-part                     not-reached
  pending: Review the design -> bob
$ hubproto track f21db81ca8 | tail -1
 13 ... run.failed  {"reason": "required-fields: DSN-2026-0004 cannot enter 'awaiting-review-the-design' without kind", "step": "score-the-risk"}
(/runs) f21db81ca8 | design-release | DSN-2026-0004 [QA] Sensor board | alice | failed | b7da4af94d3c | bob (due 2026-11-16)
$ hubproto record DSN-2026-0004 | head -1
DSN-2026-0004 (design) state=failed folder=Engineering/Designs ...
```
Suggested fix (optional): `hubproto run-cancel <id> --as <owner|policy-owner> --meaning ...` and
`run-retry`; clear `pending` when a run fails; `/runs` should show "failed" in the Waiting-for column.

---

### F-it-5: The hub logs nothing: no request log, no error log, no decision log
Severity: major
Area: observability
Steps:
1. `curl "http://127.0.0.1:8080/admin?as=alice&itprobe=1"`, `curl "http://127.0.0.1:8080/nope?as=alice"` (404),
   `curl "http://127.0.0.1:8080/run/zzz?as=alice"` (400), the 20 MB upload of F-it-7, the worker outage of F-it-3.
2. `docker compose logs --no-color --since 10m hub`.
Expected: at least one line per request or per error (method, path, principal, status, duration), and
a line for each run submitted/decided and each worker failure, so support can correlate a user report
with what the hub did.
Actual: `Handler.log_message` is overridden to do nothing (`shell.py` 285-286) and nothing else prints.
After bootstrap the entire log for the container's life is the single line
`workspace shell at http://0.0.0.0:8080/?as=alice`. Errors are rendered to the browser only.
Evidence:
```
$ docker compose logs --no-color --since 1m hub | wc -l
0
$ docker compose logs --no-color hub | grep -v "kept iso9001"
hub-erp  | bootstrapping hub state in /hub/state
hub-erp  | kernel seeded at /hub/state/kernel.db
hub-erp  | org-model exists at company/org-model.frame.md; template not installed
hub-erp  | seeded profile 'iso9001': 0 file(s) installed under /hub/frames/iso9001
hub-erp  | next: hubproto compile, then review and release with two policy owners (or bootstrap-release)
hub-erp  | release b7da4af94d3c571fae7779e0030ed7b19352d1b972735a6cf966b286fba922ef in effect (rules: 197)
hub-erp  | workspace shell at http://0.0.0.0:8080/?as=alice
```
Suggested fix (optional): a one-line access log on stdout (`ts method path as= status ms`), `logging`
at WARNING for ShellError/RunError/500 with the run id, and INFO for run.submitted/gate.decided.

---

### F-it-6: Backup and restore are undocumented, and a hot backup of the running hub is not consistent
Severity: major
Area: docs | records
Steps:
1. Search `README.md` and `DEV-NOTES.md` for backup/restore/upgrade: no section. README only says
   "State lives in the `hub-state` volume."
2. With the hub running: `docker compose exec -T hub tar -C /hub/state -czf - . > /tmp/hubstate.tgz`
   then `tar -tzvf /tmp/hubstate.tgz`.
3. `docker compose exec -T hub python -c "import sqlite3; c=sqlite3.connect('/hub/state/kernel.db'); print(c.execute('pragma journal_mode').fetchone())"`.
4. Restore into a throwaway volume: `docker run --rm -v it-restore:/hub/state -v /tmp/hubstate.tgz:/b.tgz:ro --entrypoint sh hub-erp-proto:local -c "tar -C /hub/state -xzf /b.tgz"` then
   `docker run --rm -v it-restore:/hub/state hub-erp-proto:local runs | wc -l`.
Expected: README says what to back up (the `hub-state` volume: `kernel.db`, `blobs/`, `runs/`,
`releases/`, `proposals/`, `inbox/`, the `.bootstrapped` marker; plus `./frames`, `./profiles`,
`./config` which are the policy source and live outside the volume), how to take a consistent copy
(stop the hub or use SQLite's online backup), and how to restore (volume name, uid 1000 ownership,
keep the dotfile).
Actual: nothing is documented and there is no `hubproto backup` command. The tar of the live hub
warned that `kernel.db` changed while being read and captured a `kernel.db-journal` (rollback
journal; `journal_mode=delete`, not WAL), i.e. a transaction in flight. The restore did work
(SQLite rolled the journal back; `integrity_check` ok; 61 runs; same release digest) but whatever
was mid-write at backup time is gone, and nobody would know. The procedure also relies on knowing
that `.bootstrapped` must be in the archive (see F-it-16) and that files must be owned by uid 1000.
Evidence:
```
$ docker compose exec -T hub tar -C /hub/state -czf - . > /tmp/hubstate.tgz
tar: ./kernel.db: file changed as we read it
tar: .: file changed as we read it
$ ls -la /tmp/hubstate.tgz        -> 318531 bytes, 240 entries
$ tar -tzvf /tmp/hubstate.tgz | grep -E '\./[^/]+/?$'
./.bootstrapped  ./kernel.db-journal (12824)  ./kernel.db (327680)  ./runs/  ./inbox/  ./proposals/  ./blobs/  ./releases/
$ pragma journal_mode -> ('delete',)
== restore on it-restore ==
* b7da4af94d3c571f  effective=2026-11-06T14:38:17+00:00  by=bob  proposal=e22ebcd9e84f  parent=-
61 runs; pragma integrity_check -> ('ok',)
```
Suggested fix (optional): README "Backup and restore" section with the volume list above, a
`hubproto backup <file>` that uses `sqlite3.Connection.backup()` plus a tar of the directories, and a
matching `restore` note (stop hub, `docker run --entrypoint sh ... tar -x`, `up`).

---

### F-it-7: No upload size limit and no container memory limit: a 20 MB upload is accepted in 1.5 s and kept forever
Severity: major
Area: security | performance
Steps:
1. `head -c 20971520 /dev/zero > /tmp/it-20mb.bin`.
2. `curl -F as=alice -F folder=Tasks -F "title=[IT] 20MB upload limit probe" -F file=@/tmp/it-20mb.bin http://127.0.0.1:8080/upload`.
3. `docker stats --no-stream hub-erp` before and after; `docker compose exec -T hub du -sh /hub/state/blobs`.
4. `docker inspect hub-erp --format 'mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}}'`.
Expected: a configurable maximum body size (e.g. 25 MB) returning 413, and `mem_limit`/`pids_limit`
in the compose file, so one client cannot exhaust the host.
Actual: `_dispatch` reads the whole `Content-Length` into memory (`shell.py` 344-345) with no cap,
then the multipart parser and the blob store copy it; RSS went from 44 MiB to 109 MiB for one 20 MB
file and the blob is stored under `/hub/state/blobs` with no delete path. No memory, CPU or pid
limit is set on either container. The hub also holds the global lock while reading the body
(F-it-2), so a slow upload stalls everyone.
Evidence:
```
==MEM-BEFORE   hub-erp 43.95MiB / 23.47GiB
==UPLOAD       status=303 size_up=20972071 time=1.461667
==MEM-AFTER    hub-erp 109.4MiB / 23.47GiB
$ du -sh /hub/state/blobs -> 22M ; blobs/cd52d81e...  20971520 bytes
$ docker inspect hub-erp -> restart=unless-stopped mem=0 cpus=0 pids=<no value> ro=false
```
Document created: `[IT] 20MB upload limit probe` in folder `Tasks` (cannot be deleted from the shell or CLI).

---

### F-it-8: /healthz says only "ok"; no version string, no live release digest, no worker health anywhere in the hub
Severity: minor
Area: observability
Steps:
1. `curl -s http://127.0.0.1:8080/healthz` with the worker up, then with `docker compose stop cad-worker`.
2. `docker compose exec -T hub hubproto --version`.
3. `docker compose restart` and read `docker compose logs hub` (non-bootstrap start).
4. Look for the worker on `/admin` and `/releases`.
Expected: `/healthz` (or `/version`) reports package version, image build id, the release digest in
effect, SQLite reachability and whether `HUBPROTO_CAD_WORKER_URL` answers; `hubproto --version`
exists; every start logs version and release.
Actual: `/healthz` returns the literal `ok` from inside the request handler (so it also proves nothing
about the kernel, and blocks under the global lock, F-it-2); it stayed `200 ok` with the worker
stopped. There is no `--version` (argparse error); the only version is `hubproto.__version__ = 0.1.0`
/ pyproject `0.1.0` and the image tag is `local` with no build SHA. On a normal (non-bootstrap)
start the log shows only the URL, not which release is live. Which release is live is visible on
`/releases` (the `*` row), on the home page, and via `hubproto releases`, which is good, but the
worker appears nowhere in the UI: `/admin` lists the cog catalog and members only; worker health is
only visible with `docker compose ps` or a manual `curl http://cad-worker:9000/healthz` from
inside the hub container.
Evidence:
```
$ curl -s -w " status=%{http_code}\n" http://127.0.0.1:8080/healthz      (worker Exited 137)
ok status=200
$ docker compose exec -T hub hubproto --version
hubproto: error: the following arguments are required: cmd
$ docker compose logs hub | tail -1          (after down/up)
hub-erp  | workspace shell at http://0.0.0.0:8080/?as=alice
$ docker compose exec -T hub python -c "import urllib.request;print(urllib.request.urlopen('http://cad-worker:9000/healthz').read())"
b'{"ok": true, "binding": {"freecad": true, "kicad-cli": true, "fake": false}}'
```
Suggested fix (optional): `/healthz` -> JSON `{version, release, kernel: ok, worker: ok|unreachable}`
served outside the lock; `hubproto --version`; log `hubproto 0.1.0 release <digest> in effect` on every start;
an "System" card on `/admin` with the worker's `/healthz` result.

---

### F-it-9: Containers ignore SIGTERM; every stop waits 10 s and ends in SIGKILL
Severity: minor
Area: architecture
Steps:
1. `time docker compose stop cad-worker`; `docker compose ps -a`.
2. `time docker compose down` (two containers).
3. `docker compose exec -T hub sh -c 'cat /proc/1/cmdline | tr "\0" " "'`.
Expected: a clean stop in well under a second, with in-flight SQLite transactions finished.
Actual: both containers run Python as PID 1 (`exec hubproto shell` / `python3 -m hubproto.worker`),
which does not install a SIGTERM handler, so Docker waits the 10 s grace period and kills them
(exit 137). `down` took 21 s. A kill during a write is exactly how the rollback journal in F-it-6
appears; with the shell's long synchronous requests (F-it-2) it is likely in practice.
Evidence:
```
$ time docker compose stop cad-worker   -> real 0m10.486s ; hub-erp-cad-worker Exited (137)
$ time docker compose down              -> real 0m21.206s
$ cat /proc/1/cmdline -> /usr/local/bin/python3.14 /usr/local/bin/hubproto shell --host 0.0.0.0 --port 8080
```
Suggested fix (optional): handle SIGTERM in `serve()` (call `server.shutdown()`), and in the worker;
or `STOPSIGNAL SIGINT` in both Dockerfiles since `KeyboardInterrupt` is already caught.

---

### F-it-10: Fresh-install notes: README omits the 3.4 GB default worker build and `CAD_TOOLS=none`; first start prints 20 misleading "kept ... use --force" lines; `seed` writes into the host checkout
Severity: minor
Area: docs
Steps:
1. Follow README: `docker compose up --build`. Then `docker image ls`.
2. `docker compose logs hub | head -25`.
3. Check what the entrypoint does: `docker/entrypoint.sh` runs `hubproto seed iso9001 || true` against
   `/hub/frames`, which is the bind mount of `./frames` (compose `volumes`).
4. State-survival checks: `docker compose down && docker compose up -d`; second `bootstrap-release`;
   fresh volume.
Expected: README states the worker image size/build time and the `CAD_TOOLS=none` alternative
(only a comment in `docker-compose.yml` mentions it), a first start that reads clean, and no writes
to the git checkout from a container.
Actual: `hub-erp-cad-worker:local` is 3.39 GB (`hub-erp-proto:local` 182 MB). Every first start
prints `kept iso9001/<frame> (exists; use --force to overwrite)` twenty times, because
`frames/iso9001` is already committed; in a checkout where it is absent, the container would write
the profile frames into the host working tree through the bind mount (and the `frames/.replaced/`
directory is also tracked, which is odd). What does work: `down`/`up` keeps state (103 runs before,
108 after, `.bootstrapped` mtime unchanged, no "bootstrapping" line; healthy 2 s after `up`); a second
`bootstrap-release` is a safe no-op; a fresh volume bootstraps in about 2 s to the same digest
`b7da4af94d3c571f` (deterministic, as claimed); `docker compose run --rm tick` prints `tick done`.
README does not warn that `down -v` deletes every record, run and release.
Evidence:
```
$ docker image ls
hub-erp-proto        local   182MB
hub-erp-cad-worker   local   3.39GB
$ docker compose logs hub | grep -c "kept iso9001"   -> 20
hub-erp | seeded profile 'iso9001': 0 file(s) installed under /hub/frames/iso9001
== down/up ==  BEFORE 103 runs ; healthz ok after 2s ; AFTER 108 runs ; .bootstrapped 2026-11-06 14:38:17 (unchanged)
$ docker compose exec -T hub hubproto bootstrap-release --as bob --second carol
a release is already in effect; use --force to add another        (exit 0)
== fresh volume ==  docker run --rm -v it-fresh:/hub/state hub-erp-proto:local releases  (real 0m2.065s)
* b7da4af94d3c571f  effective=2026-11-06T14:46:28+00:00  by=bob  proposal=c4deccdd9b2b  parent=-
$ docker compose run --rm tick -> tick done (1.2 s)
```
Suggested fix (optional): README "Requirements" (disk, build time, `CAD_TOOLS=none`), make the
entrypoint's seed quiet when nothing is installed, and a "Danger: `down -v`" note.

---

### F-it-11: No upgrade path: the kernel database has no schema version, no migrations, and the README has no upgrade section
Severity: minor
Area: docs | architecture
Steps:
1. `grep -nE 'schema|migrat|user_version|CREATE TABLE' hubproto/kernel.py`.
2. Read README for "upgrade": none. `docker compose up --build` after a code change reuses the
   volume unconditionally (`entrypoint.sh` only checks `.bootstrapped`).
3. `docker compose exec -T hub hubproto compile` four times; `ls /hub/state/proposals | wc -l`.
Expected: a `user_version` or schema table checked at start, a documented "pull, `up --build`,
check `/releases`" procedure with a backup first, and a note that releases in the volume pin frame
versions that may differ from the frames in a newer image.
Actual: only `CREATE TABLE IF NOT EXISTS events ...`; nothing records which code version wrote the
volume; a future incompatible change would open old data silently. Each `compile` also writes a new
proposal file even with 0 changes (grew from 1 to 4 during this session) with no list or cleanup
command; `hubproto proposal <id>` requires knowing the id from the compile output.
Evidence:
```
hubproto/kernel.py:64: "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ...
$ hubproto compile | head -1
proposal 6624520de8fb  base=b7da4af9...  extractor=convention
$ ls /hub/state/proposals | wc -l  -> 4
```

---

### F-it-12: Adding a user needs a host file edit, four CLI commands and two owners; the shell cannot review or release
Severity: minor
Area: policy | shell
Steps (what a support tech would do today; I did not edit repo files):
1. On the host, edit `frames/company/org-model.frame.md` under `## Members` (lines 32-38), e.g. add
   `- erin (person): finance-clerk, staff`. Bump `version` in the frontmatter. The file is bind-mounted,
   so the container sees it at `/hub/frames/company/org-model.frame.md`.
2. `docker compose exec -T hub hubproto compile` -> first line prints `proposal <id>`; read the
   findings (a routed role with no member is an error; a new member with roles is a widening).
3. `docker compose exec -T hub hubproto review <id> --as bob --approve-all --meaning "add erin"`
4. `docker compose exec -T hub hubproto review <id> --as carol --approve-all --meaning "add erin"`
   (widenings need two policy owners; bob and carol are the only ones).
5. `docker compose exec -T hub hubproto release <id> --as bob`
6. Check `/releases?as=bob` (new `*` row, parent = old digest) and `/admin` (Members table).
   Runs already started keep their old digest and finish on it.
Expected: at least the review/release step available to policy owners in the shell (they are
"gated by people"), a `hubproto proposals` list, and this procedure in README under "Adding a
member".
Actual: `/releases` and `/frames` are read-only (the only forms are the "Acting as" selector); there
is no `proposals` command; the README's four-line recipe is correct but does not say which file to
edit, that the version must change, or what the findings mean. Positive: `compile` on unchanged
frames reports `changes: 0`, and `review --decide CHANGE=verdict` exists for per-change decisions.
Evidence:
```
$ grep -n -A7 '^## Members' frames/company/org-model.frame.md
32:- alice (person): finance-clerk, author, process-owner, staff
...
38:- quality-assistant (cog): quality-manager, staff
$ hubproto compile
proposal 6624520de8fb  base=b7da4af9...  extractor=convention
rules: 197  changes: 0  findings: 5  notes: 0
reviewers: bob, carol
$ hubproto review --help -> --as, --approve-all, --decide CHANGE=verdict, --meaning
$ curl -s "http://127.0.0.1:8080/releases?as=bob" | grep -oE "<form[^>]*>"  -> only <form method="get" action="/releases">
```

---

### F-it-13: Tracebacks are shown to any user; secrets are plain environment variables
Severity: minor
Area: security
Steps:
1. Read `shell.py` 320-322: any unexpected exception renders `traceback.format_exc()` into the page
   (status 500) for whoever made the request.
2. Read `docker-compose.yml`: the only secret handling is the comment "set `ANTHROPIC_API_KEY`" in
   `environment:`; `extract.py` 367 reads `HUBPROTO_EXTRACTOR` from the environment.
3. `docker compose exec -T hub env | sort` shows the full environment to anyone with exec.
Expected: a generic error page with an error id that is in the log (F-it-5); `env_file`/docker
secrets for the API key and a README note that it must not be committed.
Actual: as described; no 500 was reachable from the pages I tried (`/run/zzz` -> 400, `/record/zzz`
-> 404, both handled), so this is from code, not reproduced. Positive: both containers run as uid
1000 (`hub`, `worker`), the worker port 9000 is not published, and no key was present in the
running environment.
Evidence:
```
hubproto/shell.py:320:  except Exception as e:  # readable, never a blank 500
hubproto/shell.py:321:      tb = traceback.format_exc()
hubproto/shell.py:322:      return self._page("Error", f'... <pre>{esc(tb)}</pre>', status=500)
$ docker compose exec -T hub id -> uid=1000(hub) gid=1000(hub)
$ docker inspect hub-erp-cad-worker --format '{{.NetworkSettings.Ports}}' -> map[9000/tcp:[]]
```

---

### F-it-14: `hubproto conformance iso9001` fails; the profile argument is inconsistent with `seed`
Severity: minor
Area: CLI
Steps:
1. `docker compose exec -T hub hubproto seed --help` (positional `profile`).
2. `docker compose exec -T hub hubproto conformance iso9001`.
3. `docker compose exec -T hub hubproto conformance` (works, defaults to iso9001).
Expected: the same spelling for the same thing, or at least an error that says `--profile`.
Actual: `conformance` takes `--profile`; the positional form prints the whole top-level usage block
and `unrecognized arguments: iso9001`. `/conformance` in the shell works but renders the clause map
as raw JSON blobs in a two-column table (`covered`, `uncovered`, `titles`), which is unreadable; the
CLI table is fine.
Evidence:
```
$ hubproto conformance iso9001
hubproto: error: unrecognized arguments: iso9001
$ hubproto conformance | head -3
iso9001 clause conformance from release b7da4af94d3c571f
clause   status     title                                                                frames
4.1      covered    Understanding the organization and its context                       quality-manual
(/conformance page) covered | {"10.1": ["quality-manual"], "10.2": ["capa-sop", "ncr-sop"], ... }
```

---

### F-it-15: "Why can't carol create a bill" is answerable, but only by impersonating carol
Severity: question
Area: shell | CLI
Steps:
1. `/why?as=carol&action=create&object=bill&folder=Finance/Payables`.
2. `docker compose exec -T hub hubproto why --as carol --action create --object bill --folder Finance/Payables`.
Expected: a support tech asks about another principal from their own session.
Actual: both answer correctly without reading code:
`denied by finance-policy line 15: Quality managers may not create bills in Finance/Payables.`
(the page links to the frame line). But the `/why` form has no "about whom" field; the acting
principal is the subject, so the tech must switch `?as=carol`. That is fine while identity is a
query parameter and will stop working the day there is SSO. Is a `user=` field planned?
Evidence:
```
$ hubproto why --as carol --action create --object bill --folder Finance/Payables
DENIED   denied by finance-policy line 15: Quality managers may not create bills in Finance/Payables.
$ curl -s "http://127.0.0.1:8080/why?as=alice" | grep -oE 'name="[a-z_]+"' | sort -u -> action as folder object
```

---

### F-it-16: What happens when `.bootstrapped` is missing but state exists, and is `tick` safe to run beside the live hub?
Severity: question
Area: architecture
Steps:
1. Read `docker/entrypoint.sh`: the only guard is `[ ! -f state/.bootstrapped ]`; a restore made with
   `tar -c *` (no dotfiles) or a copied directory would re-run `init`, `seed`, `bootstrap-release`.
2. Read `cli.py` 25-28 (`cmd_init` -> `kernel.seed()`), `cli.py` 228-230 (`bootstrap-release` is a no-op
   if a release exists), `kernel.py` 370 (`seed` checks for existing supplier events).
3. `docker compose run --rm tick` while the hub is serving, with `journal_mode=delete`.
Expected: a documented statement that re-bootstrap over existing state is idempotent (it looks so
from code: seed is skipped when suppliers exist, release is skipped when one exists), and that `tick`
from a second container is safe against the single SQLite file (or run it in-process on a timer).
Actual: not documented; not reproduced because it would mean restoring over the shared volume. The
marker is a zero-byte dotfile that a backup can easily miss (F-it-6). `tick` ran in 1.2 s and printed
only `tick done` with no count of what it escalated.
Evidence:
```
docker/entrypoint.sh: if [ ! -f state/.bootstrapped ]; then hubproto init; hubproto seed iso9001 || true; hubproto bootstrap-release --as bob --second carol; touch state/.bootstrapped; fi
$ docker compose run --rm tick -> tick done
```

---

## Resource use (for reference, not a finding)

```
$ docker image ls
hub-erp-proto        local   182MB     (python:3.14-slim + package)
hub-erp-cad-worker   local   3.39GB    (debian:trixie-slim + freecad + kicad + kicad-libraries)
$ docker stats --no-stream
hub-erp              0.30%   30.1MiB  (idle) -> 109 MiB after one 20 MB upload; 1 PID
hub-erp-cad-worker   0.01%   18.5MiB  (idle); 1 PID
state volume after ~110 runs: kernel.db 332 KB, blobs 22 MB (20 MB of it is my probe), releases 184 KB, proposals 280 KB
```

## Things I created in the shared hub

- Run `3de517ad8a` / record `DSN-2026-0011` `[IT] worker-down probe` (design-release, waiting for bob; no ERC/STEP).
- Run for `[IT] lock probe (worker paused)` (design-release, waiting for bob).
- Document `[IT] 20MB upload limit probe` in folder `Tasks` (20 MB blob).
- Three empty proposals from `hubproto compile` (`1d94c00c1455`, `66adba1c6aeb`, `6624520de8fb`).
- One `down`/`up` of the stack (about 25 s outage), one 10 s stop of the worker, one 12 s pause of the worker.
