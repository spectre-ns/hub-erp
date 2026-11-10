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

First start seeds the kernel, installs the ISO 9001 profile, compiles and
releases the first policy release with the two seeded policy owners (bob and
carol), and then makes the SOPs documents: from then on **every configuration
change goes through a change order** (Quality > Change orders); see "Once the
SOPs are documents" below. If that first release fails (for example an org
model with nobody in a routed role) the container prints the Frame to fix,
notes it in `state/.bootstrap-failed`, and still serves the shell with no
release in effect; fix the Frame and run
`docker compose exec hub hubproto bootstrap-release --as bob --second carol`
(run again on a released hub whose SOPs are not documents yet, it converts them).
State lives in the `hub-state` volume and the hub's Frames in the `hub-frames`
volume: the first start fills it with the Frames the image ships, and every
release writes the released texts into it, never into the checkout (the
repository's `frames/` are the example Frames, not a hub's policy). A newer
image's Frames reach a running hub only through a change order. To work on the
checkout's `frames/` and `profiles/` folders from Docker (a development hub,
never the live one), add `docker-compose.frames-dir.yml`; see its header. A hub
whose Frames were bind-mounted from the checkout before (issue #5) is moved with
`docs/rollout/frames-volume.md`.

Without Docker (`--root` is the directory holding `frames/` and `state/`;
set `HUBPROTO_ROOT` or pass `--root` when you are not in it):

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
hubproto init && hubproto seed iso9001 && hubproto bootstrap-release --as bob --second carol
hubproto shell
```

For development, run as many hubs as you like beside the Docker one, each from a
worktree's code, released and with a little demo data, on its own port from 8100
(never 8080, 9980 or 11434), with stand-in models; see DEV-NOTES.md, "Running hubs
for development and for agents" (in a worktree, `python -m hubproto dev ...` runs
that worktree's command line):

```bash
hubproto dev serve --detach --name ws12   # prints http://127.0.0.1:81xx/; open it with ?as=carol
hubproto dev list                         # what runs: name, port, pid, branch, commit, url
hubproto dev stop ws12 && hubproto dev clean ws12
```

Tests: `pip install -r requirements-dev.txt` (pytest and pytest-xdist, development
only), then `python -m pytest -q -n auto` (parallel; without `-n auto` the suite runs
serially and passes that way too).

`hubproto --help` lists every verb in the order an operator uses them, and
`hubproto <verb> --help` explains each flag. Exit codes: 0 done; 1 the hub
refused or the outcome is a failure (error findings, `DENIED`, a failed run);
2 a mistake in the command (unknown id, bad JSON, missing file, wrong root).
A wrong `--root` is refused ("no hub at ..."), never filled with an empty hub.

## After changing Frames (a hub that keeps the Frame files)

A hub bootstrapped with `hubproto bootstrap-release --as bob --second carol
--keep-frame-files "<why>"` (or `sopdocs.keep_frame_files`) skips the
conversion: the Frame files stay the source and the policy owners release
changes without change orders. It is recorded and `status` shows it on every
run; it is meant for work on the Frames themselves and for the test suites
(`tests/conftest.py`'s `release_all` opts out this way), and it ends with
`hubproto sops convert`. On such a hub, edit a sentence in `frames/` on the
host (in Docker, with `docker-compose.frames-dir.yml` added, or in the
`hub-frames` volume), then compile, have it reviewed and release it. Until the release, the
shell and every run keep applying the release in force (the Frames as they
were released), and on its next start the container says so: `hubproto status
--check` lists the files that differ and prints these commands.

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

Prose that is not in the sentence shapes is read by the compile-frame committee, **as a diff**: an SOP whose text
is the one in force keeps the rules the release read from it, with no model call; a changed SOP's readers get the
unified diff (with line numbers) and the rules in force from it, and answer with rules to add, remove or change and
with **questions** where the changed prose is ambiguous (`proposal["questions"]`; members asking the same thing about
a line make one question; a question the next compile no longer raises is closed). A large diff is read in parts that
fit the model's window. Every change on a proposal says where it came from (`origin`: the SOP, line, words and
reader) and, for a rule a model read, what it means in the hub's own words (`origin.explained`); a reading that drops
or adds a "not" carries a `negation` warning. A rule that only moved in its file (a process below inserted lines) is
listed as moved, not as a change to decide. Direct changes on a change order are put into prose by the qa-manager cog
and compiled back to check they give exactly that rule and nothing else (`hubproto/prose_drafting.py`); the lines it
drafts are credited to it. A reading may touch only the lines that changed: anything else is refused as an `outside-diff`
question (the SOP text is fenced as data in the readers' prompts, so words aimed at the readers are ignored and reported).
A changed sentence that reads like a rule and from which nothing was read, by the grammar or the committee, is a
blocking `unread-rule` question for the committee's chair, also when no model reads here (`convention`).

**The committee reads by consensus.** It is defined on the `senior-cogs` group (organization model, `## Groups`):
the **senior** cogs `senior-organization` and `senior-qa-manager`, bundled with Claude Opus (`claude-opus-5-5`); the
**junior** cogs (`organization`, `qa-manager` and the others, group `junior-cogs`) stay on the local model for
day-to-day work. Each member reads with the model its profile bundles it with,
from its own perspective (its About text, its persona's behaviour and the roles it holds); the diff, the rules in force
and the answer format are the same for every member, so their readings stay comparable.
A rule goes in only when every member reads it the same way; where members read a line differently, the proposal gets
a `disagreement` question quoting the line and each member's reading, for a person to decide. A member whose model is
not available here (not installed, Ollama down, or a `claude-*` model while hosted models are off) is a
`member-unavailable` question naming the committee's chair (`Chair: carol`), who intervenes: the prose of a changed SOP
is not read until it is. **The senior cogs are available only where the administrator has switched hosted models on**
(`HUBPROTO_HOSTED_MODELS=on` with `ANTHROPIC_API_KEY`, under the IT SOP); the call goes through the hub's model gateway
to the Anthropic Messages API. Without them the compiler reads the sentence shapes only and asks about changed prose.
The committee's members change through a change order (the `senior-cogs` group, or a cog's `Model:` line).

**Groups and committees** (change control 11) are also used in processes and on change orders: a process step written
`the design-review committee decides` waits for every member of the committee (people and cogs) and moves only when all
agree; an objection with its reason returns the item; when a cog member's criteria do not hold or a member is late, the
committee's chair intervenes (decide in that member's place, extend the due date, or return the item). A change-control
review sentence may name a committee as reviewers. See DEV-NOTES.md, "Groups and committees".

| Compiler setting | Default | Effect |
|---|---|---|
| `HUBPROTO_EXTRACTOR` | `hybrid` with an Ollama (or with hosted models on and a key), else `convention` | `convention` reads the sentence shapes only (tests, offline use); `hybrid` has the compile-frame committee's cogs read the prose, each with its bundled model. There is no global model override (the old `claude` value is gone and is read as unset) |
| `HUBPROTO_MODEL_MAX_CHARS` | the model's window | the most characters one reading may send; a larger diff is read in parts |

## Once the SOPs are documents: change orders only

The first release converts the SOPs into documents (a hub that kept the Frame
files converts with `hubproto sops convert --as <policy owner>` or Policy >
Convert in the shell). The Frame is then compiled from the SOP documents, and
configuration changes only through a change order (Quality > Change orders);
`status --check` exits 1 on a released hub whose SOPs are not documents and
that did not say it keeps the Frame files. `compile`, `review` and
`release` of a proposal no change order staged, `bootstrap-release --force` and
`seed` are refused (exit 1) with that sentence, and so are the shell's Frame
drafts and proposal actions. The record types in force are kept by the hub from
then on (`state/record-types`): an edited or newly shipped
`config/record-types.json` is reported by `status --check` and not used until a
change order carries what it adds:

```bash
docker compose exec hub hubproto record-types status                       # version in force, history, what the file says differently
docker compose exec hub hubproto record-types carry CO-2026-0004 --from-file --as carol
docker compose exec hub hubproto record-types carry CO-2026-0004 --as carol \
    --changes '[{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}]'
```

`docs/design/config-paths.md` lists every path that changes configuration and
what happens on it.

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

### Cogs work from queues

Everything asked of a cog lands in its queue, like a task in a person's tray: a comment or chatter message that
mentions it, a message sent to it, an item waiting for it at a Gate, a task given on its profile, a standing duty.
Asking never waits for the cog: the page comes back at once, saying where the request is ("next in its queue",
"3rd in line"), and the chatter shows a "working on it" entry that turns into the answer when it lands, without
a reload (the page polls `/queue-status` and swaps in the parts that changed, so a half-typed message stays put).
A comment saved in the editor is queued the same way, so the save returns at once, and the editor shows which
cog is on it until its reply is written into the comment thread.

Each cog takes one task at a time, oldest first; different cogs work side by side. The queue is a SQLite table
(`state/queue.db`); its modes are set by `HUBPROTO_COG_QUEUE`:

* `worker` (what `hubproto shell` and the compose hub use): a thread in the hub works the queues. It holds the
  hub lock while it reads and writes and gives it up while the model thinks, so pages keep loading during a
  70-second qwen answer. `HUBPROTO_COG_WORKERS` (default 1) sets how many tasks run at once across cogs.
* `service`: the hub only queues; `hubproto cog-users --loop 3` (the `cog-users` compose profile) works the
  queues in its own process, the production shape of cogs as service accounts.
* `inline` (library, CLI verbs and tests): a task is worked the moment it is asked, so callers see the result.

A task left "working" by a worker that stopped goes back in the queue when a worker starts (failed after three
tries). A failed task tells the person who asked why. The person who asked, or a policy owner, may withdraw a
task still waiting. Each cog's profile shows its queue (working on, waiting with positions, recently finished);
`/queue` shows every cog's queue on one page, and People shows each cog as free or working.

### The QA manager cog, judgment approvals and assigning work to cogs

`qa-manager` is a cog that works like a QA manager. It holds the quality-manager, reviewer and author roles and
follows the document-control, change-control, NCR and CAPA procedures. It is not on any route, so people stay
first in line. It acts when it is asked:

* **Approving.** "@qa-manager please approve" on an item waiting for people (or `take_turn` in an agent run)
  makes it step in where its approval criteria allow. Its criteria are judgment criteria: `when its review passes`
  makes its model review the item against its procedures and files the review as evidence. In the document-control
  SOP, a minor change is final on its signature. A major change, and any change request, is
  `countersigned by a quality manager`: its signature is recorded and the Gate then waits for a quality manager
  (a person who did not prepare the item). If its review does not pass, the item stays with the people it was
  waiting for. Removing a countersignature from a criterion is a widening, which needs two policy owners.
* **Drafting.** Its `draft_document` tool creates a controlled document with itself as author, written in the
  writing markup, and can send it for approval. Segregation of duties means it never signs its own drafts.
* **Assigned work.** Any item can be assigned to a cog from **Assign** on its page, with an instruction. The cog
  takes it from its queue, works it within its autonomy (looking things up, commenting, reviewing, drafting,
  stepping in to approve) and reports on the chatter. A task filed with a cog as its assignee is the cog's own
  step: it completes it with a summary (`complete_step`) or hands it back to whoever filed it with why
  (`hand_back`). If it does neither, the step goes back to a person automatically.

## Cog profiles, @mentions, committees and swarms

Each cog has a profile in a Frame (`frames/company/cogs.frame.md`): a `## Cog: <name>` section with
`Model:`, `Prompt:` and `Follows:` (the SOPs it follows). The profile is a reviewed rule in the release;
changing it is a widening (two policy owners). A cog is routed work only from processes defined in the SOPs
it follows, as a person is only given work they are trained on; a context cog drafts with its model and prompt
and the text of those SOPs as its only context (`hubproto/model.py`). **A cog is bundled with its model**: `Model: ollama/<name>` is served by that model
in a local Ollama at `HUBPROTO_OLLAMA_URL` (compose reaches the host's Ollama through the `ollama-relay` service),
`Model: claude-*` by Anthropic with `ANTHROPIC_API_KEY`. Nothing stands in: if the model cannot be reached the cog
is not available, so Gates route past it, and chat, mentions, tasks and reviews tell you so. `hubproto models` and
the cog's profile page list the installed models and propose a different one as a policy change; the compiler
warns when a profile names a model this hub cannot use. (Tests and development may set `HUBPROTO_ALLOW_STAND_IN=1`
for a deterministic stand-in.) Models draft and answer; they never sign.

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

## Targeted edits to documents

When a comment or a chatter message asks a cog to change a document ("delete the Records section", "say must,
not shall"), the cog answers with a short list of operations rather than a whole new text
(`hubproto/edits.py`; the cog's edit task is in `hubproto/cogs.py`). An operation is a line starting `@@`; the
lines after it, up to the next `@@`, are its content in the writing markup (headings, bullets, `| a | b |` tables).

Text documents (docx, odt, md, txt):

    @@ insert at start          @@ insert at end
    @@ insert after: <text>     @@ insert before: <text>     @@ append to section: <heading>
    @@ replace: <exact text>    @@ with: <new text>          (every occurrence, inside its paragraph)
    @@ replace paragraph: <text>    @@ replace section: <heading>    @@ rewrite
    @@ delete: <exact text>     @@ delete paragraph: <text>  @@ delete section: <heading>

Spreadsheets (xlsx, ods):

    @@ append rows   @@ insert rows at: <n>   @@ set: B3: <value>   @@ clear: B2:D4
    @@ delete rows: 4-6   @@ replace: <text>  @@ with: <text>

- Applied in the file, the operations keep the document's formatting. A replacement touches only the part that
  differs ("suppliers shall be" to "suppliers must be" rewrites "shall" alone), so the runs around it keep their
  bold or italic. Comment anchors in removed paragraphs move to the nearest paragraph that stays, so no comment
  is lost. Text is matched exactly first, then ignoring case, curly quotes and non-breaking spaces. Deleting the
  whole text of a paragraph removes the paragraph.
- Each operation is reported: the cog's reply on the chatter lists the changes, and anything that could not be
  done (text not found, no section with that heading) is said in a follow-up from the cog.
- **Live or in the file.** When someone has the document open in the editor, the cog's seat (its own editor
  session) does live only what is safe from outside the editor: find-and-replace and deleting text, and inserting
  headings and paragraphs at the very start or end of the document; in a spreadsheet, setting cells and appending
  rows. Everything else (inserting after or before a paragraph, deleting or replacing paragraphs and sections,
  tables, lists, row moves, rewrites) is done by the hub in the file once the session has saved, and the editor
  reloads. Placing the cursor at a paragraph found by search is not reliable from outside the editor: in a live
  test, text typed after the search replaced the found paragraph instead of following it.
- **The check after a live edit.** Once the seat is done and the session has saved, the editor page posts to
  `/verify-edit`. The hub checks each operation in the saved file, including that an insert landed right after
  or before its target. An insert that landed in the wrong place is taken out and done again in the right place;
  any operation that did not take is finished in the file (a new version by the hub, and the editor reloads). The
  report is kept on the cog's reply (`edit_report`), and failures are said on the chatter.
- With nobody in the editor, the hub applies the operations in the file at once (`WopiHost.apply_replies`).
  The agent harness's `edit_document` tool uses the same operations.
- Limits: formulas are not adjusted when rows move; merged cells and charts are not edited; spreadsheet
  operations apply to the first sheet only.

## Backup and restore

The state is one SQLite file plus directories under `state/`: `kernel.db`,
`runs/` (one directory per run with its Track), `releases/` (bundles and
`index.json`), `proposals/`, `blobs/` and `inbox/`. They reference each other
(a record names its run and release, a run is pinned to a release digest), so
a backup is only usable as a whole. The backup also holds the hub's Frames
(`frames/`, in Docker the `hub-frames` volume), which the releases write.

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
beside the new one (`state.before-restore-<time>`), or, when `state/` cannot be
renamed (in the container it is the volume's mount point) or holds the backup
being restored (`/hub/state/backups/...` above), into
`state/.before-restore-<time>/`, leaving the backups folder where it is; the
command above works in the container as written. The Frames in the backup
replace the hub's (the ones replaced are kept in `frames/.before-restore-<time>/`);
a backup made before backups held the Frames restores the state and leaves the
Frames as they are. `hubproto status` then shows the release in force and the
run counts.

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
frames/              the example company's Frames (org-model is the scope root); a new hub starts from them
profiles/iso9001/    the seed profile: frames, templates, clause map
config/              record types, link vocabulary, retention classes (versioned configuration)
hubproto/            see DEV-NOTES.md for the module map and APIs
tests/               pytest suites
docker/, Dockerfile, docker-compose.yml, docker-compose.frames-dir.yml (development: Frames from the checkout)
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
