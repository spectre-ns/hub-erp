# 8. Administration

This chapter is for whoever installs and runs the hub. It covers the Docker setup, settings, the models the cogs use,
backup, restore and verification, the cogs' queues and the scheduled jobs, the command line, and the development hubs
used to build and test the hub (8.10). Under the IT SOP the installation is the environment: it changes under that SOP
and outside the policy release (chapter 7.16).

## 8.1 What runs where

The hub is standard-library Python with SQLite and files, packaged as one Docker image (Dockerfile;
docker-compose.yml).

| Service | Started by `docker compose up` | What it does |
|---|---|---|
| **hub** | yes | the web shell on `127.0.0.1:8080`, and by default the worker threads that work the cogs' queues. Its state lives in the `hub-state` volume (`/hub/state`) and its Frames in the `hub-frames` volume (`/hub/frames`), which the first start fills with the Frames the image ships and every release writes the released texts into; once the SOPs are documents, which happens at the first start, a Frame file that differs from the release is reported and not used (chapter 7.5) |
| **ollama-relay** | yes | forwards the host's Ollama (which listens on 127.0.0.1 only) to the Docker bridge, so the containers can reach the local model. Remove it if Ollama listens on all interfaces |
| **collabora** | yes | Collabora Online, the office editor, on `127.0.0.1:9980` |
| **cad-worker** | yes | FreeCAD and KiCad behind an `/invoke` interface, for design exports and checks. Built with `CAD_TOOLS=none`, or with `HUBPROTO_CAD_FAKE=1`, it answers with synthetic exports |
| **cog-users** | only with `--profile cog-users` | works the cogs' queues in a process of its own; use it with `HUBPROTO_COG_QUEUE=service` for the hub |
| **agents** | only with `--profile agents` | takes up the cogs' standing duties (`hubproto agent-duties --loop 900`) |
| **tick** | only on demand (`docker compose run --rm tick`) | escalates overdue items once (8.7) |

The hub's program code, the seed profiles, `config/` and the repository's Frames are copied into the image when it is
built; nothing from the checkout is mounted, so the hub never writes into it. After a code change, rebuild: `docker
compose up --build`. The repository's Frames only fill a new hub's `hub-frames` volume: a newer image's Frames reach a
running hub through a change order. For a development hub that reads and writes the checkout's `frames/` and
`profiles/`, use `docker-compose.frames-dir.yml`: a separate hub with its own Compose project (`hub-dev`), containers,
image tag and ports, never the live hub (see its header). A hub set up before the
Frames had a volume (they were in the checkout) is moved with `docs/rollout/frames-volume.md`.

## 8.2 Installing and first start

**How to start the hub with Docker** (an administrator)

1. Make sure Ollama runs on the host with the cogs' model (`ollama pull gemma4:12b-it-qat`; see 8.4).
2. In the repository, run `docker compose up --build`.
3. On the first start the container (docker/entrypoint.sh): creates the state (`hubproto init`), installs the ISO 9001
   profile (`hubproto seed iso9001`; its SOPs are the same as the repository's), compiles and releases the first
   policy release with the two seeded policy owners (`hubproto bootstrap-release --as bob --second carol
   --if-needed`), and with it converts the SOPs to documents, so every later configuration change goes through a change
   order (chapter 7.5).
4. Open `http://localhost:8080/` and choose who you are (chapter 1.1).

If the first release or the conversion fails (for example an org model with nobody in a routed role), the container
prints "the bootstrap stopped (no release in effect, or the SOPs not yet documents): fix what is named above, then run:
docker compose exec hub hubproto bootstrap-release --as bob --second carol", writes it to `state/.bootstrap-failed`,
and still serves the shell; with no release in force every page says "No policy release is in effect".

On every later start the container keeps the existing state, runs `hubproto init`, which adds only what a newer
version needs and the state lacks (the sample stock locations and part categories, chapter 4.3), and runs `hubproto
status --check`. It exits 1, and the container says so, when the hub is released but not converted, when Frame files
on disk differ from the documents ("they are not used: the Frame is compiled from the SOP documents"), or when
`config/record-types.json`, `links.json` or `retention.json` differ; the hub keeps applying the release in force. The
answer to a difference in Frames or record types is a change order, not a command.

The first release is compiled from the sentence shapes only: the committee's model reading (chapter 7.4) is done by
background work, not by `bootstrap-release` (read the code: hubproto/cli.py, `cmd_bootstrap_release`).

**Without Docker:**

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
hubproto init && hubproto seed iso9001 && hubproto bootstrap-release --as bob --second carol   # releases and converts
hubproto shell                      # http://127.0.0.1:8080/
```

`--root` (or `HUBPROTO_ROOT`) names the directory holding `frames/` and `state/`. A wrong root is refused ("no hub at
..."), never filled with an empty hub.

## 8.3 Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `HUBPROTO_ROOT` | current directory | the hub directory (`/hub` in the image) |
| `HUBPROTO_MAX_UPLOAD` | 25 MB | the largest form or upload the shell accepts; larger ones get a readable "too big" page |
| `HUBPROTO_SECRET` | a secret kept in `state/.secret` | the key for signatures and editor sessions; set it to keep editor sessions valid across restarts on another host |
| `HUBPROTO_COLLABORA_URL` | `http://localhost:9980` in compose | the editor's address as the browser reaches it |
| `HUBPROTO_WOPI_BASE` | `http://hub:8080` in compose | the hub's address as the editor reaches it |
| `HUBPROTO_COLLABORA_DISCOVERY` | `http://collabora:9980` in compose | where the hub reads the editor's discovery document |
| `HUBPROTO_PUBLIC_URL` | `http://localhost:8080` in compose | the hub's own origin, for the editor's frame policy |
| `HUBPROTO_OLLAMA_URL` | `http://host.docker.internal:11434` in compose; none outside it | where the local models answer. Without it no cog is available, and SOP prose is not read by a model |
| `HUBPROTO_HOSTED_MODELS` | off | `on` (or 1, yes, true) lets the hub use hosted models: Claude, with `ANTHROPIC_API_KEY`, and Ollama's cloud models (a tag ending in `:cloud` or `-cloud`, or one Ollama reports runs remotely), which run on Ollama's servers, not this machine. Off, a cog whose profile names a `claude-*` model, the two senior cogs included, or one of Ollama's cloud models is not available, and the model lists offer only the models Ollama runs on this machine. Under the IT SOP only a system administrator switches it, as a recorded installation change (8.4) |
| `ANTHROPIC_API_KEY` | none (compose does not pass it) | used only when `HUBPROTO_HOSTED_MODELS=on` |
| `HUBPROTO_EXTRACTOR` | `hybrid` when `HUBPROTO_OLLAMA_URL` is set, or hosted models are on with a key; else `convention` | how SOPs are read: `convention` (the sentence shapes only) or `hybrid` (those, then the compile-frame committee's senior cogs read the prose, each with its bundled model, chapter 7.4). The former `claude` value is read as unset; there is no global model override |
| `HUBPROTO_EXTRACTOR_MODEL` | the organization cog's model, else the largest local model | used only when the release has no compile-frame committee (so not in the example company); a `claude-*` value only with hosted models on |
| `HUBPROTO_COG_QUEUE` | `worker` in compose and `hubproto shell`; `inline` for the CLI | who works the cogs' queues (8.7) |
| `HUBPROTO_COG_WORKERS` | one worker per cog | how many queue tasks run at once |
| `HUBPROTO_MODEL_PARALLEL` | 1 | how many model calls run at once at the shared model gate |
| `HUBPROTO_MODEL_GATE` | shared | `per-model` gives each model its own gate; `off` lets every call through |
| `HUBPROTO_MODEL_TIMEOUT` | 180 s (240 s with tools) | how long a call to Ollama may take; calls to Anthropic are fixed at 60 s (120 s with tools) |
| `HUBPROTO_MODEL_CTX`, `HUBPROTO_MODEL_CTX_MAX` | the model's window; 65,536 | the context window a prompt is sized to |
| `HUBPROTO_MODEL_MAX_CHARS`, `HUBPROTO_SOP_CHARS`, `HUBPROTO_DOC_CHARS` | from the window | how much text a compile reading, a cog's procedures and an item's document may take |
| `HUBPROTO_COG_LEASE` | 900 s | how long a queue task may stay "working" before it goes back in the queue |
| `HUBPROTO_AGENTS` | on | `off` stops every agent run before it starts (the kill switch) |
| `HUBPROTO_AGENT_ACTIONS`, `HUBPROTO_AGENT_ROUNDS` | 5, 10 | the budget of an agent run |
| `HUBPROTO_CHAT_ACTIONS` | 5 | the action budget of one chat turn |
| `HUBPROTO_CAD_WORKER_URL`, `HUBPROTO_CAD_FAKE` | compose: `http://cad-worker:9000`, 0 | the CAD worker, and synthetic exports |
| `HUBPROTO_ALLOW_STAND_IN` | off | development and tests only: a deterministic stand-in answers for every cog. Never in a real hub |
| `HUBPROTO_DEV_HOME` | `~/.cache/hubproto-dev` | where `hubproto dev` keeps its hubs and registry (8.10) |
| `HUBPROTO_DEBUG` | off | shows the error trace on "Something went wrong" pages (otherwise only in the log) |

The code also reads `HUBPROTO_COLLABORA_URLSRC`, `HUBPROTO_COLLABORA_HOST`, `HUBPROTO_MAX_INFLATED` (200 MB),
`HUBPROTO_MODEL_THINK`, `HUBPROTO_MODEL_FAKE` and the older `HUBPROTO_COG_USERS`. `HUBPROTO_COMPILE_HOSTED`, which
for a while let compiling use a hosted model for every member, is gone: the seniors' own model decides. The README
describes `HUBPROTO_COG_WORKERS` as defaulting to 1; the code starts one worker per cog of the release in force unless
it is set (hubproto/workqueue.py, `CogWorker`).

## 8.4 Models (Ollama) and a cog's model

Each cog is bundled with the model its profile names. Nine of the eleven cogs use `ollama/gemma4:12b-it-qat` (Gemma 4
12B, quantization-aware; it reads pictures), served by Ollama at `HUBPROTO_OLLAMA_URL`; the two senior cogs, the
compile-frame committee, use `claude-opus-5-5`, a hosted model (chapter 6.12). Hosted models are off unless
`HUBPROTO_HOSTED_MODELS=on` and an Anthropic key is passed, and the compose file passes neither (its comment says
where to add them). A cog whose model the hub cannot reach is not available, and nothing stands in for it
(hubproto/model.py, `backend_for`).

**Ollama's cloud models are hosted models.** Ollama's library has cloud tags, such as `gemma4:cloud` and
`gemma4:31b-cloud`: the local Ollama passes the call on to Ollama's servers, so the prompt and the records in it leave
this machine. The hub treats such a model like Claude. With hosted models off, a cog bundled with one is not available
("ollama/gemma4:31b-cloud runs on Ollama's servers, not this machine, and hosted models are switched off here ..."), the
model lists show it as not available, and proposing it for a cog is refused. With hosted models on it can be used, and
`hubproto models`, the cog's profile and the model pickers say it "runs on Ollama's servers, not this machine"; each call's
record names its backend, `ollama-cloud`; a proposal that gives a cog one tells every policy owner that the cog's prompts
and the records in them would leave this machine. The hub knows a cloud model by what Ollama reports for it (`/api/show`
names a remote host), and otherwise by its tag: a name ending in `:cloud` or `-cloud` is never treated as local
(hubproto/model.py, `ollama_cloud`). Before each call it asks Ollama again, so a model name re-pointed at a cloud model
is not sent as local; when Ollama does not say where a model runs, nothing is sent ("Ollama did not say whether ... runs
on this machine or on Ollama's servers, so nothing was sent to it"; `where_now`). The prompt each cog gets is sized to its model's context window as Ollama reports
it, capped at 65,536 tokens unless `HUBPROTO_MODEL_CTX_MAX` or `HUBPROTO_MODEL_CTX` says otherwise (hubproto/model.py,
`num_ctx_for`).

So on the default installation **no model reads SOP prose**: the juniors do not read in the seniors' place, a change
order's compile reads only the sentence shapes, and every changed rule-like sentence the grammar cannot read becomes a
blocking question for the committee's chair (chapter 7.4).

**How to switch hosted models on** (a system administrator, as a recorded installation change under the IT SOP, with
the provider approved under the supplier evaluation SOP; chapter 7.16): set `HUBPROTO_HOSTED_MODELS=on` and
`ANTHROPIC_API_KEY` for the hub's service in `docker-compose.yml`, and restart the hub. The IT SOP limits hosted use to
the senior cogs and to compiling, and limits what may leave the hub. The hub does not enforce or record any of that:
the switch is global, so any cog bundled with a `claude-*` model may then use it, and who switched it, when and why is
not recorded in the hub (read the code: hubproto/model.py, `backend_for`; chapter 10.9). Each decision, review and
drafted text still records the model that produced it (chapter 6.13).

**How to see which models are available** (an administrator): `hubproto models` (in Docker: `docker compose exec hub
hubproto models`). It lists the models installed in Ollama with size and quantization, a line on Claude ("no
Anthropic key: a cog bundled with a claude-* model is not available here" unless hosted models are on and a key is
set; it says so also when the key is set and the switch is off), and for each cog its model and who serves it now, or
"NOT AVAILABLE: *why*". One of Ollama's cloud models is listed as "cloud: runs on Ollama's servers, not this machine"
(with "not available: hosted models are off" while they are), and a cog served by one as "(ollama-cloud: runs on
Ollama's servers, not this machine)".

The same is visible in the web pages: each cog's profile shows its model with "answers" or "not available"; the
People page shows unavailable cogs; the messenger lists them as not available.

**How to change a cog's model.** A cog's model is part of its profile, which is configuration: changing it is a
direct change on a change order, a widening (two policy owners) and high risk (a test record). Use **Change model** on
the cog's profile (chapter 6.10), or Settings > **Cog rails** > the cog's profile (chapter 7.7). When a change names a
model this hub cannot reach, the compiler adds a warning ("the cog will be unavailable until it can").

## 8.5 Backup and restore

The state is one directory with SQLite files and subdirectories that refer to each other (a record names its run and
release, a run is pinned to a release digest), so a backup is only usable as a whole (README.md; hubproto/cli.py).

**How to back up** (an administrator)

1. Run `docker compose exec hub hubproto backup /hub/state/backups/2026-11-07` (a new or empty directory).
2. Copy it out: `docker compose cp hub:/hub/state/backups/2026-11-07 ./backup-2026-11-07`.

What `backup` copies: `kernel.db` and `queue.db` through the SQLite backup API (a consistent snapshot even while the
hub runs); the directories `runs`, `releases`, `proposals`, `blobs`, `inbox`, `agents`, `settings`, `forms` and
`record-types`; `frame-files-kept.json`; the signing secret `.secret`; `proposal-numbers.json`; the hub's Frames
(`frames/`, the `hub-frames` volume); and a `backup.json` manifest (hubproto/cli.py, `BACKUP_PARTS`). For a copy
consistent across all parts, stop the hub first (`docker compose stop hub`) or back up when nobody is working. The
Frames in particular are copied after the kernel's snapshot, with no lock: a release during the backup of a running hub
can leave them newer than the state, and a restore of that backup then shows drift (`restore` prints it, as `status
--check` would).

Not copied: the automation jobs (`state/automation`), the folders people added to the library (`state/library`),
what the committee read from the SOPs (`state/extract-cache`) and **the workflow layouts in force and the editor's
drafts (`state/workflows`)**, and any other directory added since (chapter 10, A1). After a restore every workflow is
drawn with the hub's own layout again. The cogs keep no review results of their own any more (chapter 6.11). The SOPs
themselves are documents in the kernel and the blobs, so they are in the backup.

**How to restore** (an administrator)

1. `docker compose stop hub`
2. `docker compose run --rm --entrypoint hubproto hub restore /hub/state/backups/2026-11-07 --force`
3. `docker compose start hub`, then `docker compose exec hub hubproto status`.

`restore` refuses a partial set (every part the manifest lists must be present) and refuses to overwrite a live state
without `--force`. With it, the previous state is moved beside the new one (`state.before-restore-<time>`). The Frames
in the backup replace the hub's, which are kept in `frames/.before-restore-<time>/`; a backup made before backups held
the Frames, or one whose `frames/` holds no Frame files (it says so with a `WARNING`), leaves them as they are. Then
`restore` says whether the Frames match the release it restored and lists any that differ, as `status --check` would.

## 8.6 Verifying the evidence

`hubproto verify` checks that nothing was changed after it was written:

* the record system's event chain (each event is chained to the one before it);
* every run's Track chain and every signature on it (signed with the hub's secret);
* every stored file against its hash.

It prints `PROBLEM:` lines and exits 1 when anything was changed; events and Tracks from before chaining was
introduced are reported as notes, not problems. A restore without the original `.secret` makes every signature fail
to verify.

## 8.7 Queue health and the background jobs

**The cogs' queues** (chapter 6.3) are a SQLite table (`state/queue.db`). `HUBPROTO_COG_QUEUE` says who works them:

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
| Cogs' duties | `hubproto agent-duties` (once), `--loop SECONDS` (the `agents` profile uses 900) | each cog takes up each of its `## Duties` sentences every day, week or month, as the sentence says |
| Cogs' queues as a service | `hubproto cog-users --loop 3` | only when `HUBPROTO_COG_QUEUE=service` |

## 8.8 The command line

`hubproto --help` lists every verb in the order an operator uses them; `hubproto <verb> --help` explains each flag. In
Docker, prefix commands with `docker compose exec hub`. Exit codes: **0** done; **1** the hub refused or the outcome is
a failure (error findings, DENIED, a failed run); **2** a mistake in the command (unknown id, bad JSON, missing file,
wrong root). Records are named by number (any case), runs by id prefix, proposals by number (`#3`) or id prefix,
releases by version (`v2`) or digest prefix.

| Group | Verbs |
|---|---|
| Setting up | `init` (create the state; on an existing hub, add only what it lacks), `seed <profile>` (refused once the SOPs are documents), `bootstrap-release --as P --second Q` (first release on a fresh hub, which converts the SOPs; `--if-needed`; `--keep-frame-files REASON` to opt out; `--force` refused once converted), `shell` (`--host`, `--port`) |
| Policy | `proposals` (`--open`), `proposal <n>`, `review <n> --as P` (only a proposal a change order staged; it comes into force with that change order), `releases`, `release-diff v<n>`, `status` (`--check`), `frames`, `conformance` (`--profile`), `why --as P --action A --object O --folder F`. `compile` and `release <n>` are refused once the SOPs are documents (chapter 7.5) |
| Record types | `record-types status`, `record-types show TYPE`, `record-types carry CO --as P` (`--changes JSON`, `--changes-file PATH`, `--from-file`, `--reason`): the only way to change a record type is to carry it on a change order (chapter 7.14) |
| Runs and decisions | `run <process> --as P --record-json JSON`, `runs`, `run-show <run>`, `track <run>`, `inbox-pending --as P` (what waits for a person, with the seen digest and the fields each Gate accepts), `decide <run> --as P --verdict approve\|reject --meaning TEXT` (`--field K=V`, `--seen DIGEST`), `retry <run> --as P` (`--field`, `--file`, `--note`), `resubmit <run> --as P`, `cancel <run> --as P --reason TEXT`, `tick` |
| Records and documents | `records` (`--type`, `--state`, `--folder`, `--assignee`, `--q`), `record <n>`, `link`, `snapshot`, `docs`, `doc-new`, `doc-versions`, `doc-get`, `doc-checkout`, `doc-checkin`, `attach`, `upload` |
| Messages | `inbox --as P`, `msg --as P --to M --subject S` (`--body`, `--channel dm\|email`, `--attach`, `--record N`), `mail-in <file.eml>` (deliver an .eml as email to its To: member) |
| Finance | `ledger` (posted journals with their lines), `bills` |
| Cogs | `models`, `agent --cog C --as P --goal TEXT` (`--actions N`; `--confirm RUN` confirms a run's proposed actions), `agent-duties` (`--cog`, `--loop`), `cog-users` (`--as COG`, `--loop`) |
| Templates | `templates import --as P` (`--submit`), `templates list` |
| SOPs as documents | `sops convert --as P` (a policy owner or document controller; once; without the checks of the Policies page's button, chapter 7.8), `sops status` (each SOP's document in force and any revision under way) |
| Evidence and state | `verify`, `backup <dir>`, `restore <dir>` (`--force`) |
| Development | `dev serve`, `dev list`, `dev stop`, `dev clean` (8.10) |

There is no command to open, review, send, decide or sign a change order: that is done in the web pages (chapter
7.9).

The command line acts as whoever `--as` names. Starting runs, deciding, reviewing and releasing go through the same
checks as the web pages, but the document and link verbs (`doc-new`, `upload`, `attach`, `link`, `doc-checkout`)
check only that the name is a member, not folder permissions (hubproto/cli_records.py). Treat shell access to the hub
as administrator access.

## 8.9 Policy commands an administrator runs

On a hub whose SOPs are documents an administrator changes no policy from the command line. What remains is reading
and checking:

```bash
docker compose exec hub hubproto status --check                          # exit 1 if files on disk differ from what is in force, or the hub is not converted
docker compose exec hub hubproto sops status                              # each SOP, its document in force, any revision under way and its change order
docker compose exec hub hubproto releases                                 # every policy version, and which change order released it
docker compose exec hub hubproto release-diff v2                          # what a version changed
docker compose exec hub hubproto record-types status                      # the record types in force, and the changes carried on change orders
docker compose exec hub hubproto models                                   # which cogs can answer here
```

On a hub that kept its Frame files (chapter 7.5), the older cycle applies: `compile`, `proposals`, `review <n> --as
bob --approve-all`, `review <n> --as carol --approve-all` for a widening, and `release <n> --as bob`.

## 8.10 Development hubs and tests

Development and agents never use the hub at `http://localhost:8080`, and never bind 8080, 9980 or 11434. Each piece of
work gets its own git worktree and its own hub (DEV-NOTES.md, "Running hubs for development and for agents";
hubproto/cli_dev.py):

```bash
python -m hubproto dev serve --worktree DIR --seed demo --detach --name NAME   # prints the URL, e.g. http://127.0.0.1:8100/
python -m hubproto dev list                    # name, port, pid, branch, commit, started, url, worktree, state dir (--json)
python -m hubproto dev stop NAME               # state kept; dev stop --all stops every instance
python -m hubproto dev clean NAME              # removes a stopped instance's state
```

* `dev serve` builds a **fresh** hub from the worktree's code and Frames, as the container's first start does: `init`,
  `seed iso9001`, the first release by bob and carol (the SOPs become documents), then with `--seed demo` (the
  default) an open nonconformance waiting for carol, a 1,500 USD bill the ap-reviewer cog approves and the ledger posts,
  and an open change order alice prepared carrying a revision of the finance policy that carol drafted
  (hubproto/dev_demo.py). That demo change order can be reviewed but not approved, because carol, the only quality
  manager, wrote its revision (chapter 7.9). `--seed empty` skips the demo data; `--keep` reuses the hub already in the
  state directory; `--port N` (default: the first free port from 8100) and `--state-dir DIR` place it; `--models
  ollama` uses the local Ollama instead of the stand-in. It takes a few seconds.
* Cogs answer with the deterministic stand-in (`HUBPROTO_ALLOW_STAND_IN=1`).
* The state is `~/.cache/hubproto-dev/<name>/` (`HUBPROTO_DEV_HOME` moves it). The command-line verbs work on it with
  `hubproto --root ~/.cache/hubproto-dev/<name> <verb>`. `<url>?as=carol` opens the shell as carol; `<url>healthz`
  answers `ok` once it is up. The registry is written under a lock, so agents starting hubs at the same time never get
  the same name or port.

**Tests.** `pip install -r requirements-dev.txt`, then `python -m pytest -q -n auto` (or `-n 8` on a shared machine)
runs the suite on every core; the serial run must pass too. Each test may run on any worker in any order, so a test
never depends on another having run, a server binds port 0, and files go under `tmp_path`. No test calls a model. At
`2c99c4d` the suite has 1,029 tests and all pass (`-n 8`, 55 s; checked by running the code).
