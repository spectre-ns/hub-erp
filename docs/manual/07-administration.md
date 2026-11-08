# 7. Administration

This chapter is for whoever installs and runs the hub. It covers the Docker setup, settings, the models the cogs use,
backup, restore and verification, the cogs' queues and the scheduled jobs, and the command line.

## 7.1 What runs where

The hub is standard-library Python with SQLite and files, packaged as one Docker image (Dockerfile;
docker-compose.yml).

| Service | Started by `docker compose up` | What it does |
|---|---|---|
| **hub** | yes | the web shell on `127.0.0.1:8080`, and by default the worker threads that work the cogs' queues. Its state lives in the `hub-state` volume (`/hub/state`). `./frames` and `./profiles` from the repository are mounted into it, so Frames edited on the host are seen at once (but take effect only when released, chapter 6) |
| **ollama-relay** | yes | forwards the host's Ollama (which listens on 127.0.0.1 only) to the Docker bridge, so the containers can reach the local model. Remove it if Ollama listens on all interfaces |
| **collabora** | yes | Collabora Online, the office editor, on `127.0.0.1:9980` |
| **cad-worker** | yes | FreeCAD and KiCad behind an `/invoke` interface, for design exports and checks. Built with `CAD_TOOLS=none`, or with `HUBPROTO_CAD_FAKE=1`, it answers with synthetic exports |
| **cog-users** | only with `--profile cog-users` | works the cogs' queues in a process of its own; use it with `HUBPROTO_COG_QUEUE=service` for the hub |
| **agents** | only with `--profile agents` | takes up the cogs' standing duties (`hubproto agent-duties --loop 900`) |
| **tick** | only on demand (`docker compose run --rm tick`) | escalates overdue items once (7.7) |

The hub's program code is copied into the image when it is built; only `frames/` and `profiles/` are mounted. After a
code change, rebuild: `docker compose up --build`.

## 7.2 Installing and first start

**How to start the hub with Docker** (an administrator)

1. Make sure Ollama runs on the host with the cogs' model (`ollama pull qwen3:14b`; see 7.4).
2. In the repository, run `docker compose up --build`.
3. On the first start the container (docker/entrypoint.sh): creates the state (`hubproto init`), installs the ISO 9001
   profile (`hubproto seed iso9001`), and compiles and releases the first policy release with the two seeded policy
   owners (`hubproto bootstrap-release --as bob --second carol --if-needed`).
4. Open `http://localhost:8080/` and choose who you are (chapter 1.1).

If the first release fails (for example an org model with nobody in a routed role), the container prints the Frame
to fix, writes the note to `state/.bootstrap-failed`, and still serves the shell with no release in force; every page
then says "No policy release is in effect". Fix the Frame and run
`docker compose exec hub hubproto bootstrap-release --as bob --second carol`.

On every later start the container keeps the existing state and checks whether the Frames on disk differ from the
release in force (`hubproto status --check`). If they do, it prints the files that differ and the commands to compile,
review and release them; until then the hub keeps applying the release in force.

**Without Docker:**

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
hubproto init && hubproto seed iso9001 && hubproto bootstrap-release --as bob --second carol
hubproto shell                      # http://127.0.0.1:8080/
```

`--root` (or `HUBPROTO_ROOT`) names the directory holding `frames/` and `state/`. A wrong root is refused ("no hub at
..."), never filled with an empty hub.

## 7.3 Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `HUBPROTO_ROOT` | current directory | the hub directory (`/hub` in the image) |
| `HUBPROTO_MAX_UPLOAD` | 25 MB | the largest form or upload the shell accepts; larger ones get a readable "too big" page |
| `HUBPROTO_SECRET` | a secret kept in `state/.secret` | the key for signatures and editor sessions; set it to keep editor sessions valid across restarts on another host |
| `HUBPROTO_COLLABORA_URL` | `http://localhost:9980` in compose | the editor's address as the browser reaches it |
| `HUBPROTO_WOPI_BASE` | `http://hub:8080` in compose | the hub's address as the editor reaches it |
| `HUBPROTO_COLLABORA_DISCOVERY` | `http://collabora:9980` in compose | where the hub reads the editor's discovery document |
| `HUBPROTO_PUBLIC_URL` | `http://localhost:8080` in compose | the hub's own origin, for the editor's frame policy |
| `HUBPROTO_OLLAMA_URL` | `http://host.docker.internal:11434` in compose | where the local models answer |
| `ANTHROPIC_API_KEY` | none | lets cogs whose profile names a `claude-*` model use it |
| `HUBPROTO_COG_QUEUE` | `worker` in compose and `hubproto shell`; `inline` for the CLI | who works the cogs' queues (7.7) |
| `HUBPROTO_COG_WORKERS` | one worker per cog | how many queue tasks run at once |
| `HUBPROTO_MODEL_PARALLEL` | 1 | how many model calls run at once at the shared model gate |
| `HUBPROTO_MODEL_GATE` | shared | `per-model` gives each model its own gate; `off` lets every call through |
| `HUBPROTO_MODEL_TIMEOUT` | 180 s (240 s with tools) | how long a model call may take |
| `HUBPROTO_AGENTS` | on | `off` stops every agent run before it starts (the kill switch) |
| `HUBPROTO_AGENT_ACTIONS`, `HUBPROTO_AGENT_ROUNDS` | 5, 10 | the budget of an agent run |
| `HUBPROTO_CHAT_ACTIONS` | 5 | the action budget of one chat turn |
| `HUBPROTO_CAD_WORKER_URL`, `HUBPROTO_CAD_FAKE` | compose: `http://cad-worker:9000`, 0 | the CAD worker, and synthetic exports |
| `HUBPROTO_ALLOW_STAND_IN` | off | development and tests only: a deterministic stand-in answers for every cog. Never in a real hub |
| `HUBPROTO_DEBUG` | off | shows the error trace on "Something went wrong" pages (otherwise only in the log) |

The README describes `HUBPROTO_COG_WORKERS` as defaulting to 1; the code starts one worker per cog of the release in
force unless it is set (hubproto/workqueue.py, `CogWorker`).

## 7.4 Models (Ollama) and a cog's model

Each cog is bundled with the model its profile names (`Model: ollama/qwen3:14b`, or `Model: claude-...`). Local
models are served by Ollama at `HUBPROTO_OLLAMA_URL`; Claude models need `ANTHROPIC_API_KEY`. If a cog's model cannot
be reached, the cog is not available and nothing stands in for it (chapter 5). The prompt each cog gets is sized to
its model's context window (qwen3:14b: 40,960 tokens).

**How to see which models are available** (an administrator): `hubproto models` (in Docker: `docker compose exec hub
hubproto models`). It lists the models installed in Ollama with size and quantization, whether an Anthropic key is
set, and for each cog its model and who serves it now, or "NOT AVAILABLE: *why*".

The same is visible in the web pages: each cog's profile shows its model with "answers" or "not available"; the
People page shows unavailable cogs; the messenger lists them as not available.

**How to change a cog's model.** A cog's model is part of its profile, which is policy: changing it is a proposal for
the policy owners, and a widening (two owners). Use **Change model** on the cog's profile (chapter 5.10), or Settings >
**Cog rails** > the cog's profile (chapter 6.7). The lists offer what Ollama serves now, largest first. A new cog
defaults to the model the other cogs already use. When a proposal names a model this hub cannot reach, the compiler
adds a warning ("the cog will be unavailable until it can").

## 7.5 Backup and restore

The state is one directory with SQLite files and subdirectories that refer to each other (a record names its run and
release, a run is pinned to a release digest), so a backup is only usable as a whole (README.md; hubproto/cli.py).

**How to back up** (an administrator)

1. Run `docker compose exec hub hubproto backup /hub/state/backups/2026-11-07` (a new or empty directory).
2. Copy it out: `docker compose cp hub:/hub/state/backups/2026-11-07 ./backup-2026-11-07`.

What `backup` copies: `kernel.db` and `queue.db` through the SQLite backup API (a consistent snapshot even while the
hub runs); the directories `runs`, `releases`, `proposals`, `blobs`, `inbox`, `agents` and `settings`; the signing
secret `.secret`; `proposal-numbers.json`; and a `backup.json` manifest. For a copy consistent across all parts, stop
the hub first (`docker compose stop hub`) or back up when nobody is working.

Not copied: the cogs' stored review results (`state/judgments`), the automation jobs (`state/automation`) and any
other directory added since (chapter 9). The Frames themselves live in the repository (`frames/`), not in the state;
keep them under version control.

**How to restore** (an administrator)

1. `docker compose stop hub`
2. `docker compose run --rm --entrypoint hubproto hub restore /hub/state/backups/2026-11-07 --force`
3. `docker compose start hub`, then `docker compose exec hub hubproto status`.

`restore` refuses a partial set (every part the manifest lists must be present) and refuses to overwrite a live state
without `--force`. With it, the previous state is moved beside the new one (`state.before-restore-<time>`).

## 7.6 Verifying the evidence

`hubproto verify` checks that nothing was changed after it was written:

* the record system's event chain (each event is chained to the one before it);
* every run's Track chain and every signature on it (signed with the hub's secret);
* every stored file against its hash.

It prints `PROBLEM:` lines and exits 1 when anything was changed; events and Tracks from before chaining was
introduced are reported as notes, not problems. A restore without the original `.secret` makes every signature fail
to verify.

## 7.7 Queue health and the background jobs

**The cogs' queues** (chapter 5.3) are a SQLite table (`state/queue.db`). `HUBPROTO_COG_QUEUE` says who works them:

| Mode | Used by | Behaviour |
|---|---|---|
| `worker` | the compose hub, `hubproto shell` | threads in the hub work the queues; a thread holds the hub's lock while it reads and writes and gives it up while the model thinks, so pages keep loading during a long answer |
| `service` | production shape | the hub only queues; `hubproto cog-users --loop 3` (the `cog-users` compose profile) works the queues in its own process |
| `inline` | the command line, tests | a task is worked the moment it is asked |

A task left "working" by a worker that stopped goes back in the queue when a worker starts again (after its lease
runs out, or at once if its process on this host is gone), and fails after three tries.

**How to check the queues' health** (anyone)

* `GET /healthz` answers `ok`, or `ok; cog queue stalled` when the queue runs in worker mode, no worker thread is
  alive and the oldest task has waited more than a minute. The Docker health check uses `/healthz`.
* The **Cog queues** page (`/queue`) shows the same alert in red ("The cogs' queue is stalled ... Restart the hub, or
  ask an administrator") with what each cog is working on and how many tasks wait.
* The log has one line per request, per decision and per cog task (`cog task <id> <cog> <kind> done|failed`).

**Scheduled jobs.** Nothing in the hub schedules these; run them by hand or from a scheduler:

| Job | Command | What it does |
|---|---|---|
| Escalation | `hubproto tick` (or `docker compose run --rm tick`); `--today YYYY-MM-DD` to test | escalates every overdue item once, as the routing sentences say (chapter 3.1) |
| Cogs' duties | `hubproto agent-duties` (once), `--loop SECONDS` (the `agents` profile uses 900) | each cog takes up each "Duty:" line once a day |
| Cogs' queues as a service | `hubproto cog-users --loop 3` | only when `HUBPROTO_COG_QUEUE=service` |

## 7.8 The command line

`hubproto --help` lists every verb in the order an operator uses them; `hubproto <verb> --help` explains each flag. In
Docker, prefix commands with `docker compose exec hub`. Exit codes: **0** done; **1** the hub refused or the outcome is
a failure (error findings, DENIED, a failed run); **2** a mistake in the command (unknown id, bad JSON, missing file,
wrong root). Records are named by number (any case), runs by id prefix, proposals by number (`#3`) or id prefix,
releases by version (`v2`) or digest prefix.

| Group | Verbs |
|---|---|
| Setting up | `init` (create the state), `seed <profile>` (copy a profile's Frames in; `--as PERSON` also imports its templates), `bootstrap-release --as P --second Q` (first release on a fresh hub; `--if-needed`, `--force`), `shell` (`--host`, `--port`) |
| Policy | `compile` (`--scope FRAME`; exit 1 on error findings), `proposals` (`--open`), `proposal <n>`, `review <n> --as P` (`--approve-all`, `--decide CHANGE=approve\|reject\|defer`, `--meaning`), `release <n> --as P` (`--effective-from` to schedule), `releases`, `release-diff v<n>`, `withdraw-draft <DOC> --as P`, `status` (`--check`), `frames`, `conformance` (`--profile`), `why --as P --action A --object O --folder F` |
| Runs and decisions | `run <process> --as P --record-json JSON`, `runs`, `run-show <run>`, `track <run>`, `inbox-pending --as P` (what waits for a person, with the seen digest and the fields each Gate accepts), `decide <run> --as P --verdict approve\|reject --meaning TEXT` (`--field K=V`, `--seen DIGEST`), `retry <run> --as P` (`--field`, `--file`, `--note`), `resubmit <run> --as P`, `cancel <run> --as P --reason TEXT`, `tick` |
| Records and documents | `records` (`--type`, `--state`, `--folder`, `--assignee`, `--q`), `record <n>`, `link`, `snapshot`, `docs`, `doc-new`, `doc-versions`, `doc-get`, `doc-checkout`, `doc-checkin`, `attach`, `upload` |
| Messages | `inbox --as P`, `msg --as P --to M --subject S` (`--body`, `--channel dm\|email`, `--attach`, `--record N`), `mail-in <file.eml>` (deliver an .eml as email to its To: member) |
| Finance | `ledger` (posted journals with their lines), `bills` |
| Cogs | `models`, `agent --cog C --as P --goal TEXT` (`--actions N`; `--confirm RUN` confirms a run's proposed actions), `agent-duties` (`--cog`, `--loop`), `cog-users` (`--as COG`, `--loop`) |
| Templates | `templates import --as P` (`--submit`), `templates list` |
| SOPs as documents | `sops convert --as P` (a policy owner or document controller; once; chapter 6.8), `sops status` (each SOP's document in force and any revision under way) |
| Evidence and state | `verify`, `backup <dir>`, `restore <dir>` (`--force`) |

The command line acts as whoever `--as` names. Starting runs, deciding, reviewing and releasing go through the same
checks as the web pages, but the document and link verbs (`doc-new`, `upload`, `attach`, `link`, `doc-checkout`)
check only that the name is a member, not folder permissions (hubproto/cli_records.py). Treat shell access to the hub
as administrator access.

## 7.9 Policy commands an administrator runs

After Frames are edited on the host (chapter 6), the commands are:

```bash
docker compose exec hub hubproto compile                                  # exit 1 and no proposal to release while there are error findings
docker compose exec hub hubproto proposals                                # what is waiting, by number
docker compose exec hub hubproto proposal 3                               # each change with its sentence, file and line, and decisions
docker compose exec hub hubproto review 3 --as bob --approve-all --meaning "..."
docker compose exec hub hubproto review 3 --as carol --approve-all        # a widening needs two owners
docker compose exec hub hubproto release 3 --as bob                       # a policy owner of the release in force
docker compose exec hub hubproto releases
docker compose exec hub hubproto release-diff v2
```

Two later modes change this (chapter 6): once a release has come from Settings, `compile` is refused and rules are
changed in Settings; once the SOPs have been converted to documents, the Frames are compiled from those documents, not
from the files on disk.
