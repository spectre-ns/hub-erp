# Rolling out change control v2 on the live hub

The live hub (Docker Compose in `~/source/hub-erp-proto`, container `hub-erp`, state volume `hub-state`) runs the
earlier code: release v9 (`2dcbfc780b93`), the SOPs already documents, 374 runs of which 129 wait for someone (the
backup manifest's "748 runs" counts files: each run is a directory with two). Change control v2 changes how
configuration changes, and the live configuration (its SOP documents and record types) must be brought in line with
it through one change order. `scripts/rollout_cc2.py` makes and releases that change order, and checks the hub before
and after. This page is the runbook; the rehearsals and their findings are in `cc2-rehearsal.md` beside it.

## Status: not ready

The script stops at step 2 (exit 3) on the live data until these code changes are on `change-control-v2`. It writes
nothing when it stops there. Each was found by rehearsing on fresh restores of the live backup.

1. **A change order cannot carry a new SOP document.** `it-sop`, `persona-advisor` and `persona-executor` have no
   document on the live hub. `eco.add_sop` refuses them (hubproto/eco.py:91-92). `FrameStore.with_overrides` also
   refuses a Frame that is not in the store (hubproto/frames.py:194-195), and Hub.compile calls it (app.py:359, 361).
   The release writes each Frame to `self.frames.get(name).path` (app.py:737), and a new Frame has no path. The script
   expects `eco.add_new_sop(hub, co_ref, name, text, *, by, author=None, relpath=None) -> draft`.
2. **A change order cannot retire a document.** The 19 role and cog personas the repository dropped
   (`persona-ap-reviewer` ... `persona-stores`) are documents in force. The new grammar refuses them as errors
   (hubproto/extract.py:666-671), so **every compile on the live hub fails until they are retired**, whatever a
   change order carries. The script expects `eco.retire_sop(hub, co_ref, name, *, by, reason)`: compile without that
   Frame, make the document obsolete at release, and drop the Frame from the release.
3. **Nobody may answer the compile committee's blocking questions under v9.** The round raises 19 blocking
   `unread-rule` questions, one for each prose paragraph of the new texts. Only the committee's chair or a system
   administrator may answer one (changecontrol.py:1878-1879), and an open one stops approval
   (changecontrol.py:2589-2591). Under v9 the committee has no chair and nobody holds `system-administrator`; both
   arrive with this change order. One possible fix is that a policy owner who contributed nothing may answer when the
   release in force names neither. That is a governance decision for the product owner.
4. **After the merge, the Frame files on disk decide which Frames are compiled.** `Hub.released_frames` takes the
   Frame names from the files (app.py:914-929). After `git merge` the bind-mounted `./frames` holds the new Frames, so
   the 19 retired personas are missing from disk, and any compile that does not retire them fails with
   `unknown Frame 'persona-ap-reviewer'`. This was verified on a restore with the new Frames on disk. On a converted
   hub the names should come from the release and the documents.

These should be fixed too. They do not stop the change order.

5. **15 of the 129 waiting runs cannot be decided by the person they wait for**, before or after the rollout: 13
   tasks, 1 training acknowledgement and 1 audit. Each is routed to a named field (assignee, auditor) who is also the
   run's owner. The earlier code saved their pending decision without a `route`, so the new runner uses `role:`
   (runner.py:2403) and keeps the owner out as the item's preparer (runner.py:823-826). Fix: for a pending saved
   without a route, derive it from `pending["route_rule"]`.
6. **58 waiting runs have no `seen_digest` from `runner.load`.** Their pending has no `kind` (runner.py:108), so
   `hubproto decide` without `--seen` is refused (cli.py:855). The shell works: it computes the digest itself.
   Fix: `pending.get("kind", "decision")`.
7. **The change-order and change-request `system_fields` cannot be carried.** `from_file_changes` skips them
   (recordtypes.py:695-698), and activation then adopts the file's hash (recordtypes.py:760-763), so `status --check`
   is clean while `stage`, `rounds`, `contributor`, `state_map`, `rejected` and others stay settable through the
   shell's JSON box (shell/actions.py:51-60). `contributor` is the field that keeps contributors off the approval
   Gate.
8. **The README's container restore probably fails.** `restore` renames `state/` aside (cli.py:1365, and the same in
   the earlier code). In the container `/hub/state` is the volume's mount point, so the rename should fail. This was
   not tried: the rehearsal has no Docker. The rollback below avoids it.

## What the script does

`python scripts/rollout_cc2.py --root ROOT --frames-from FRAMES [--dry-run] [--commit SHA] [--test-result LINE]`

| Step | What it does |
|---|---|
| 1 health | Reports the release in force and the run counts. Reads each waiting run with the new runner: its graph, status, deciders, whether an approval resolves, the fields it needs, and the release it will follow. Draws every decider's inbox (all pages) and a sample's record pages, on 127.0.0.1 with a free port. Decides a sample (one per process and status) on a throwaway copy. |
| 2 co | Prepares `Migration to change control v2` (CO, marked `[rollout:cc2]`) as **alice**. It carries a revision of every document whose text differs from `--frames-from` (header and body; carol, a document controller, opens each draft because alice owns no SOP, eco.py:66-67, and alice is its author and writes its only version). It also adds a new document for each Frame without one, retires each document whose Frame was dropped, and carries `record-types carry --from-file` (12 field additions). There are no form layouts in force and none shipped. It stops before writing anything when the code lacks a path it needs. |
| 3 review | alice requests review, and the round compiles with `HUBPROTO_EXTRACTOR` (default `convention`). The v9 change-control SOP has no "Review of changes", so the suggested panel is empty. alice then adds the reviewers the repository's section names who are members under v9 (qa-manager, quality-assistant). The it-admin and management cogs arrive with this CO. Every question, finding and suggestion is logged verbatim. Blocking committee questions are answered by the first person the code accepts (chair, system administrator, then a policy owner who contributed nothing), with "Answered by Claude at the product owner's instruction: ...". |
| 4 validate | `validation.record_evidence`: a passing test record (the suite on `--commit`) for the high-risk items, and a test note for the medium-risk ones. |
| 5 decide | bob and carol each approve every item, so every widening has two owners, accepting each warning with "Approved by Claude at the user's instruction: migration to change control v2 (rollout rehearsed on a copy of the live data)". carol accepts the CO-level warnings and alice sends the CO for approval. carol (quality manager) signs it, and the release automation runs. |
| 6 verify | Checks that a new release is in force from this CO, that every carried revision is effective and reads as the repository's text (a version line raised by the compile is allowed), that the retired documents are out and the record types activated, and that the waiting runs are still waiting and decidable. The step-1 sample is decided again on a further copy and compared outcome by outcome. Then `verify_chain`, `verify_signatures`, `verify_delta` and `status --check`. |

It is idempotent: a second run finds the CO by its mark and only verifies. Exit codes: 0 done, 1 a check failed,
3 a code change is needed (named in the log), 2 a command mistake. Logs go to `<root>/state/rollout-cc2/<time>.log`,
with a JSON report beside each. The script works its own queue (`HUBPROTO_COG_QUEUE=inline`). In the container the
junior cogs' reviews therefore call Ollama (qwen3:14b) during step 3. The senior cogs are unavailable until hosted
models are switched on.

## The live run

Run these from `~/source/hub-erp-proto` once the four blocking changes are fixed and a rehearsal on the final
`change-control-v2` commit is clean (`cc2-rehearsal.md`).

```bash
# 0. The old commit, for a rollback.
OLD=$(git rev-parse master)                      # 833f20c at the time of writing

# 1. Back up the live state with the old code, and copy it off the volume.
B=pre-cc2-$(date +%Y%m%d-%H%M)
docker compose exec hub hubproto backup /hub/state/backups/$B
docker compose cp hub:/hub/state/backups/$B ~/hub-backups/$B

# 2. Merge, and run the suite on what will run.
git switch master && git merge --no-ff cc/rollout
.venv/bin/python -m pytest -q -n auto            # copy the summary line, e.g. "1029 passed, 2 warnings in 56.45s"
NEW=$(git rev-parse HEAD)

# 3. Stop the hub (and the optional services, if they run). Nobody works while the script runs.
docker compose stop hub
docker compose --profile cog-users --profile agents stop cog-users agents 2>/dev/null || true

# 4. Rebuild the image with the new code.
docker compose build hub

# 5. Dry run: reads everything, writes nothing to the hub. It must exit 0 (exit 3: a code change is missing, so stop
#    here and roll back).
docker compose run --rm --no-deps -v "$PWD/scripts:/hub/scripts:ro" --entrypoint python hub \
  /hub/scripts/rollout_cc2.py --root /hub --frames-from /hub/frames --commit $NEW --dry-run

# 6. The rollout.
docker compose run --rm --no-deps -v "$PWD/scripts:/hub/scripts:ro" --entrypoint python hub \
  /hub/scripts/rollout_cc2.py --root /hub --frames-from /hub/frames --commit $NEW \
  --test-result "<the summary line from step 2>"

# 7. Start the hub. The entrypoint runs init and status --check: it must print no drift.
docker compose up -d hub && docker compose logs --tail 30 hub
```

Checks after the start:

```bash
docker compose exec hub hubproto status --check        # exit 0: v10 in force, Frames and record types match
docker compose exec hub hubproto verify                # 0 problems
docker compose exec hub hubproto releases              # v10 released by change-order:CO-...
docker compose exec hub hubproto release-diff v10      # what the CO changed, by Frame and line
docker compose exec hub ls /hub/state/rollout-cc2      # the logs and reports of steps 5 and 6
git status frames                                       # the release writes the released texts into ./frames (bind mount):
                                                        # 17 Frames carry a version the compile raised; commit them to master
```

Then open http://localhost:8080/inbox?as=carol, ?as=bob and ?as=dave: the waiting items are there and decide as
before. The 15 runs in finding 5 are the exception, until it is fixed. The release also assigns about 97 training
acknowledgements for the new revisions.

The script runs in a one-off container of the hub image with the hub volume, while the shell is stopped, so no
worker thread or user acts while the change order moves. The image does not contain `scripts/`, so it is mounted
read-only.

## Rollback

The backup from step 1 is the earlier code's state, so restore it with the earlier image. Do not use the README's
`restore --force` inside the container (finding 8). Restore into a temporary root, then swap the volume's contents
and keep the rolled-back state on the volume:

```bash
docker compose stop hub
git switch --detach $OLD && docker compose build hub      # the earlier code and Frames (./frames is bind-mounted)
docker compose run --rm --no-deps -v ~/hub-backups/$B:/backup:ro --entrypoint sh hub -c '
  set -e
  mkdir -p /tmp/r/frames && hubproto --root /tmp/r restore /backup
  keep=/hub/state/.rolled-back-$(date +%Y%m%d-%H%M%S); mkdir -p $keep
  find /hub/state -mindepth 1 -maxdepth 1 ! -name ".rolled-back-*" -exec mv {} $keep/ \;
  cp -a /tmp/r/state/. /hub/state/ && touch /hub/state/.bootstrapped
  hubproto --root /hub status'
docker compose up -d hub      # v9 in force, 129 waiting
```

This recipe was not tried in the rehearsal (no Docker there). The `hubproto restore` inside it ran on every rehearsal.
Afterwards, `git status frames` should show no change: remove any file the release added that is not tracked at
`$OLD`. Revert the merge on master (`git revert -m 1 <merge>`) if you stay on the earlier code.

## Hosted models (the compile committee's senior cogs)

The new compile-frame committee is the senior cogs on `claude-opus-5-5`. On the live hub they are unavailable until
hosted models are switched on. Add these two lines under `services.hub.environment` in `docker-compose.yml`
(`ANTHROPIC_API_KEY` is already in `.env`):

```yaml
      HUBPROTO_HOSTED_MODELS: "on"
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
```

The new IT SOP has a system administrator switch them on as a recorded installation change.
