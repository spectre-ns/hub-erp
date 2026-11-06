# hubproto: a governed ERP on Frames, Cogs, Ops, Guards, Gates and Tracks

A from-scratch prototype of the Intelligence Hub ERP plan. Prose policy
documents (Frames) compile into an immutable **policy release**; every record
type's workflow is derived from its SOP; business processes run as Ops under
that release with **Guards**, **Gates** and **Tracks**; **Cogs** are team members
that get routed work and messages, file records, and approve within fixed
criteria. One record system and one document management system (DMS) span
every module. Standard-library Python, SQLite and files, packaged in a
single Docker image.

## Run it

```bash
docker compose up --build        # http://localhost:8080/ asks who you are; e.g. /?as=alice
```

First start seeds the kernel, installs the ISO 9001 profile, and compiles and
releases the first policy release with the two seeded policy owners (bob and
carol). If that first release fails (for example an org model with nobody in a
routed role) the container prints the Frame to fix, notes it in
`state/.bootstrap-failed`, and still serves the shell with no release in
effect; fix the Frame and run
`docker compose exec hub hubproto bootstrap-release --as bob --second carol`.
State lives in the `hub-state` volume. Frames are bind-mounted from `./frames`.

Without Docker (`--root` is the directory holding `frames/` and `state/`;
set `HUBPROTO_ROOT` or pass `--root` when you are not in it):

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
hubproto init && hubproto seed iso9001 && hubproto bootstrap-release --as bob --second carol
hubproto shell
```

`hubproto --help` lists every verb in the order an operator uses them, and
`hubproto <verb> --help` explains each flag. Exit codes: 0 done; 1 the hub
refused or the outcome is a failure (error findings, `DENIED`, a failed run);
2 a mistake in the command (unknown id, bad JSON, missing file, wrong root).
A wrong `--root` is refused ("no hub at ..."), never filled with an empty hub.

## After changing Frames

Edit a sentence in `frames/` on the host, then compile, have it reviewed and
release it. Until the release, the shell and every run keep applying the
release in force (the Frames as they were released), and on its next start
the container says so: `hubproto status --check` lists the files that differ
and prints these commands.

```bash
docker compose exec hub hubproto compile                                  # exit 1 and no release while there are error findings
docker compose exec hub hubproto proposals                                # what is waiting, by number
docker compose exec hub hubproto proposal 3                               # each change with its sentence, file:line and decisions
docker compose exec hub hubproto review 3 --as bob --approve-all --meaning "..."
docker compose exec hub hubproto review 3 --as carol --approve-all        # a widening (a new allow, member, cog route) needs two owners
docker compose exec hub hubproto release 3 --as bob                       # bob or carol: a policy owner of the release in force
docker compose exec hub hubproto releases                                 # versions v1, v2, ... with the digest after
docker compose exec hub hubproto release-diff v2                          # sentences added, removed, changed against v1, and who signed
docker compose run --rm tick                                              # escalate overdue items
```

`compile` refuses to release a heading or a rule-like sentence it could not
read (a `grammar` error with the file and line), a stale proposal (compiled
against an older release than the one in force) is refused with "compile
again", and a release that only re-renders a process is reported as
"re-rendered, no semantic change" instead of a pair of add/remove changes.

## Who may do what

`hubproto why --as <person> --action create|read|approve --object <bills|documents|...> --folder <Folder>`
answers `ALLOWED` or `DENIED` with the policy sentence and its line (exit 1 on
`DENIED`); the shell's "Why?" link asks the same question. Members, roles and
folders come from the org model in the release in force: `--as`, `--second`,
`--to` and a message's sender must be members (an unknown name is refused
with the member list); only a person may send a message or sign a Gate,
cogs decide only within their `## Approval criteria`. Only a policy owner
may review or release; starting a process needs the first step's role and a
`create` permission; deciding a Gate needs to be an assignee with an
`approve` permission, a meaning for the signature, and the item as it was
seen (`inbox-pending` prints the seen digest and the fields the Gate
accepts). A failed run is the owner's to repair:
`hubproto retry <run> --field k=v --file path`, or `cancel`.

## Cogs are users

A cog is a member of the organization like a person: it holds roles from the org model, is routed work by the
same sentences, is assigned and notified, and signs its decisions through the same path (seen digest, meaning,
signature) with the approval-criteria sentence that let it decide recorded as the delegating rule. The Track of
a bill under the cog's limit reads: assigned to ap-reviewer, reviewed, decided by ap-reviewer (cog); one outside
its criteria reads: assigned to ap-reviewer, reviewed, declined, assigned to dave. Segregation of duties applies
to cogs; a cog supplies no fields at a Gate and may not take an item whose next state needs a person's input.

By default the cog takes its turn at once, in the same process (deterministic for tests and the shell). With
`HUBPROTO_COG_USERS=service` the item stays waiting for the cog and `hubproto cog-users --loop 5` (the
`cog-users` compose profile) takes it, which is the production shape: cogs run as their own service accounts
against the same API as people. `hubproto inbox-pending --as ap-reviewer` shows a cog's queue; the shell shows
each cog's profile, queue and decisions under People.

## Cog profiles, @mentions, committees and swarms

Each cog has a profile in a Frame (`frames/company/cogs.frame.md`): a `## Cog: <name>` section with
`Model:`, `Prompt:` and `Follows:` (the SOPs it follows). The profile is a reviewed rule in the release;
changing it is a widening (two policy owners). A cog is routed work only from processes defined in the SOPs
it follows, as a person is only given work they are trained on; a context cog drafts with its model and prompt
and the text of those SOPs as its only context (`hubproto/model.py`). Which model answers: a profile model `ollama/<name>` goes to a local Ollama at
`HUBPROTO_OLLAMA_URL` (compose points it at the host's `host.docker.internal:11434`; `ollama pull llama3.1` on
the host first); a `claude-*` model goes to Anthropic when `ANTHROPIC_API_KEY` is set, or to Ollama's
`HUBPROTO_OLLAMA_MODEL` when `HUBPROTO_OLLAMA_FALLBACK=1` (the compose default); otherwise a deterministic
stand-in answers, and a model that does not answer never blocks the work. Models draft and answer; they never sign.

A message about an item (`hubproto msg --record NCR-0001 ...`, or the item's chatter in the shell) acts on its
@mentions: a mentioned **cog** reviews the item against its SOPs, answers in the chatter, files the review as
evidence, and takes its turn at the Gate if the item is waiting for it and the review passed; a mentioned
**person** is asked in their inbox; a mentioned **role** (a committee, people and cogs) fans out to its members;
"please approve" with nothing in flight starts the item's own process, if the sender may. A process step that
names several cogs, `[system] Review the design package (quality-assistant, purchasing-assistant)`, is a swarm:
each reviews from its own SOPs, the findings merge onto the record and into one evidence document, and the
Gate that follows sees them.

## Frames and cog settings as documents

Every Frame (the policy prose the release is compiled from) is also a controlled document in **Policy/Frames**.
"Edit this policy" on it makes a draft, a working Markdown document written in the hub's text editor.
"Check the change" compiles the draft in place of the Frame and shows the rule changes, the findings and a diff;
"Propose for release" makes it a proposal, which the policy owners review and release on the proposal page.
Releasing writes the draft into the Frame file (raising its version) and makes it the effective version of the
Frame's document. A recompile from the CLI carries an open draft forward instead of dropping it.

Cog settings are Frames too: a cog's model, prompt and the SOPs it follows are its `## Cog:` section in
`cogs.frame.md`; its roles and the permissions that come with them are in the organization model. A profile may
also link documents: `Prompt from: DOC-0123` takes the prompt from a document, `References: DOC-0200, DOC-0201`
adds documents the cog works from. Each release pins the version it compiled, so a cog reads exactly what was
reviewed; a newer version of a linked document is a change the policy owners approve when they next release.
The cog's profile page shows the pinned documents and links to its profile and the organization model.

The messenger in the bottom-right corner of every page is an informal chat with a cog: it knows the page you
are on, the procedures it follows and its queue, remembers the conversation, and changes nothing; reviews,
edits and approvals go through the item's chatter, comments or Ops.

## The agent harness: cogs working on their own

A cog can be handed a task ("find nonconformances older than a week and remind their owners") from its profile
page or `hubproto agent --cog quality-assistant --as carol --goal "..."`, and takes up the `Duty:` lines in its
profile once a day (`hubproto agent-duties`, or the `agents` compose profile). Its model plans and calls tools:
the record tools to look (search, read records and documents, queues, history, counts) and action tools to act.
It acts **as itself** and only through governed paths: commenting on an item, messaging a person, starting a
process (the runner checks it may), taking its own turn at a Gate (bound by its approval criteria), reviewing a
document (filed as evidence) and editing a working document. It never writes record fields directly; when the
system refuses, the run says so.

- **Autonomy** is a line in the cog's profile: `read` (look and report), `propose` (actions wait for the person
  who gave the task, or a policy owner, to confirm them on the run page) or `act`. Changing it is a widening.
- **Budget**: at most 5 actions and 10 steps a run (`HUBPROTO_AGENT_ACTIONS`, `HUBPROTO_AGENT_ROUNDS`).
- **Audit**: every step of a run is on its Track (`state/agents/<run>.jsonl`, the run page); the person who gave
  the task is told what was done.
- **Kill switch**: `HUBPROTO_AGENTS=off`.

The same record tools are served over MCP at `POST /mcp?as=<member>` (JSON-RPC: initialize, tools/list,
tools/call), trimmed to what that member may read, so any MCP client can look at the record space.

**New cogs and people** are added from the People page (or `hub.propose_new_cog` / `propose_new_person`): the
form drafts the change to the organization model (and, for a cog, its profile) as one policy proposal for the
policy owners. A cog needs no code: a profile is enough; on release the hub installs it as a working cog.

## The integrated editor

Documents open in Collabora Online inside the hub: the hub is a WOPI host (`hubproto/wopi.py`; the
endpoints are under `/wopi/files/<number>`), so SharePoint's Office Online or any other WOPI client
could stand in for Collabora in production. `docker compose up` starts Collabora beside the hub.

- "New document" on a folder page or an item creates a blank .docx, .xlsx, .odt, .ods, .md or .txt
  (a *working* document) and opens it. "Edit online" on a document page opens an existing one.
- Opening the editor checks the document out to you; every save is a minor version noted "saved in
  the editor"; "Finish editing" releases it. Someone else's check-out, an *effective* controlled
  document, and evidence or external files open read-only (a controlled document changes only
  through a new revision via document-control).
- Settings: `HUBPROTO_COLLABORA_URL` (the editor as the browser reaches it), `HUBPROTO_WOPI_BASE`
  (this hub as the editor reaches it), `HUBPROTO_COLLABORA_DISCOVERY` (where the hub reads the
  editor's discovery document), `HUBPROTO_PUBLIC_URL` (this hub's origin, for the editor's frame
  policy). Without them the editor page says how to configure it and documents stay downloadable
  and check-in-able. Set `HUBPROTO_SECRET` to keep editor sessions valid across hub restarts.

## Backup and restore

The state is one SQLite file plus directories under `state/`: `kernel.db`,
`runs/` (one directory per run with its Track), `releases/` (bundles and
`index.json`), `proposals/`, `blobs/` and `inbox/`. They reference each other
(a record names its run and release, a run is pinned to a release digest), so
a backup is only usable as a whole.

```bash
docker compose exec hub hubproto backup /hub/state/backups/2026-11-06   # a new or empty directory
docker compose cp hub:/hub/state/backups/2026-11-06 ./backup-2026-11-06
```

`backup` copies `kernel.db` through the SQLite backup API (a consistent
snapshot even while the shell is running) and the directories as files. For
a copy that is consistent across all of them, stop the shell first
(`docker compose stop hub`) or run the backup when nobody is working.

```bash
docker compose stop hub
docker compose run --rm --entrypoint hubproto hub restore /hub/state/backups/2026-11-06 --force
docker compose start hub
```

`restore` refuses a partial set (every part must be present) and refuses to
overwrite a live state without `--force`; with it the previous state is moved
beside the new one (`state.before-restore-<time>`). `hubproto status` then
shows the release in force and the run counts.

## What it demonstrates

| Claim in the plan | Where it is proven |
|---|---|
| Permissions, routing, processes and workflows come from prose | `frames/**` and `profiles/iso9001/frames/**` are the only policy input; `hubproto compile` turns sentences into rules with citations |
| A release is deterministic and content-addressed | `release.py`: SHA-256 of the rendered bundle without citations; a no-op edit yields the same digest |
| Changes are gated by people; widenings need two | `compile.py`: per-change decisions; widenings need two policy owners |
| Every record type has a workflow derived from its SOP | `release.render_workflow`: states, human and automatic transitions, SLAs, rejection returns from the process section |
| Runs pin the release they started on | `runner.py`: a run pins the digest at submission and finishes on it after a cutover |
| One record system for all modules | `kernel.py`: types, numbering, states, typed links, labels, holds, history; `config/record-types.json` |
| A DMS with classes, versions, check-out and attachments | `dms.py`: blob store, versions, controlled/evidence/external/working/template classes, snapshots with signature pages, email and upload intake |
| Cogs are team members | The org model lists `ap-reviewer`, `doc-reviewer` and `quality-assistant` as members with roles; they receive routed approvals, DMs and email, file records, and approve only within `## Approval criteria` |
| Segregation of duties binds cogs too | A cog that filed an item is skipped as its approver and a person decides |
| Only code Cogs write, only under a grant | `ledger-poster`, `document-publisher`, `capa-opener`, `ncr-opener` refuse without a grant; child processes inherit admission from the parent's grant |
| SLAs and escalation in prose | "Disposition the nonconformance in nonconformance is due within 5 days." "Overdue items in nonconformance go to the management representative." `hubproto tick` escalates |
| Every decision is evidence | Track entries carry release digest, manifest digest, Cog versions, Guard problems, Gate verdicts, the delegating sentence for a cog decision, and a signature digest |
| An ISO 9001 seed profile | `profiles/iso9001/`: quality manual, SOPs, work instructions, templates, clause map; `hubproto conformance` prints the coverage |
| Policy owners can see what a release changed | `hubproto release-diff v<n>`: sentences added, removed and changed against the parent by Frame and file line, with each owner's decisions; `hubproto proposal <n>`: every change with its sentence and decisions |

## Layout

```
frames/              the example company's Frames (org-model is the scope root)
profiles/iso9001/    the seed profile: frames, templates, clause map
config/              record types, link vocabulary, retention classes (versioned configuration)
hubproto/            see DEV-NOTES.md for the module map and APIs
tests/               pytest suites
docker/, Dockerfile, docker-compose.yml
```

## Deviations from the published specs, on purpose

* Op manifests are JSON generated from `## Process:` sections under a local
  profile (`hubproto/op-manifest [0.1]`), not `openteams/op-manifest [0.2]`.
* Gates decided by a cog under approval criteria are an extension of the
  published profile; the Track records the delegating sentence with every
  cog decision.
* Everything runs in one process with files and SQLite. The production shape
  (Collab Hub run API, Harbor digests, OpenFGA, Postgres, Keycloak, Collabora,
  Tika, Gotenberg) keeps the same seams.
