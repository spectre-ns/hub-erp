---
name: deploy-live
description: "Deploy main to the live Docker Compose hub, check it, back it up, and roll it back. Only when the user has asked for a deployment in this task."
---

# Deploy to the live hub

**Deploying needs the user's request.** Do not deploy because a PR merged, a test passed or a demo needs it; ask.
Everything else in this skill touches the live hub, so the same holds for a backup or a rollback. Never act as a
person on the live hub (approve, sign, release as bob or carol) as part of a deployment.

The live hub is the Compose service `hub` (container `hub-erp`, `http://localhost:8080`), built from the clone that
holds `docker-compose.yml` and `.env`. Its state is the `hub-state` volume (`/hub/state`) and its Frames the
`hub-frames` volume (`/hub/frames`): a release writes the released SOP texts into the volume, never into the clone,
and nothing is mounted from the clone (issue #5). The image's Frames only fill a new hub's volume; a change to the
repository's Frames reaches a running hub through a change order, not through a deployment. The first start
bootstraps; later starts run `hubproto init` and `hubproto status --check` (`docker/entrypoint.sh`).

A hub set up before issue #5 still bind-mounts the clone's `./frames`, and its live policy is the uncommitted changes
under `frames/` there. Deploying the commit that brings the volume to such a hub is not this recipe: it is the move in
`docs/rollout/frames-volume.md`, which copies the Frames into the volume first, with its own checks and rollback.
The pre-checks below tell the two apart.
An earlier rollout with its rehearsal and a tried recipe: `docs/rollout/cc2.md`.

## Deploy

Shell variables do not survive from one command (or session) to the next, so this recipe keeps nothing in them:
write the old commit and the backup name down in your notes and in your report to the user, and type them into the
later steps where they appear as `<old commit>` and `<backup name>`.

Run from the clone the live hub is built from, on the branch that tracks `origin/main`:

```bash
docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hub/frames"}}{{.Type}} {{.Name}}{{end}}{{end}}' hub-erp
                                     # "volume <project>_hub-frames"; "bind" means: stop, docs/rollout/frames-volume.md
git status --short                   # must be empty: see "Pitfalls" for changes under frames/
git rev-parse --abbrev-ref '@{upstream}'   # must print origin/main, or `git pull` in step 4 fails: ask the user
git fetch origin
git rev-parse HEAD                   # the old commit: what runs now; write it down for a rollback
git log --oneline HEAD..origin/main  # what the deployment brings; tell the user
```

1. **The suite passes on what will run.** In a worktree at `origin/main`:
   `python -m pytest -q -n 4; echo $?` must print 0 (`-n auto` on a machine of your own).
2. **Wait for the cog queue to be empty**, so no cog is mid-task and no automation (a review round, a release) is
   half done when the hub restarts:

   ```bash
   docker compose exec -T hub python - <<'EOF'
   import sqlite3
   db = sqlite3.connect("file:/hub/state/queue.db?mode=ro", uri=True)
   rows = db.execute("SELECT cog, kind, status, title FROM tasks WHERE status IN ('queued','working') ORDER BY seq").fetchall()
   print(len(rows), "open"); [print(*r, sep="  ") for r in rows]
   EOF
   ```

   Repeat until it prints `0 open`. A task that stays `working` for long is a stuck cog: tell the user rather than
   restarting over it.
3. **Back up**, and copy the backup off the volume, outside any checkout (it holds the signing secret `.secret`).
   Choose the name once (for example `pre-deploy-<date>-<time>`), write it down, and use it in both commands:

   ```bash
   docker compose exec hub hubproto backup /hub/state/backups/<backup name>
   docker compose cp hub:/hub/state/backups/<backup name> <backups dir>/<backup name>
   ls <backups dir>/<backup name>/backup.json     # the copy is there
   ```

   The backup holds the state and the hub's Frames (`frames (N files)` in its output). `<backups dir>/<backup name>`
   must not exist before the `cp`, or the copy lands inside it.

4. **Update and rebuild**, right after one another:

   ```bash
   git pull --ff-only
   docker compose build hub && docker compose up -d hub
   ```

   If the optional services run (`docker compose ps`: `hub-erp-cog-users`, `hub-erp-agents`), they use the same
   image; recreate them too: `docker compose --profile cog-users --profile agents up -d cog-users agents` (only the
   ones that were running).
5. **Check**:

   ```bash
   docker compose ps hub                          # running, then (healthy)
   docker compose logs --tail 40 hub              # the entrypoint: init, then status --check with no drift
   curl -s http://localhost:8080/healthz          # "ok"; "ok; cog queue stalled" means the worker died
   docker compose exec hub hubproto status --check; echo $?   # 0: a release in force, Frames and config match it
   docker compose exec hub hubproto verify; echo $?           # 0: event chain, Tracks, signatures, files intact
   git rev-parse HEAD                                         # the commit now running
   ```

6. **Report** to the user: the commit now running, the old commit, the backup name and where its copy is, and the
   checks with their exit codes. A rollback, possibly in another session, starts from these.

## Roll back

Take the old commit and the backup name from the deployment's report (or ask the user); never from a shell
variable. If you do put them in variables for one command line, guard them, so an empty value stops the command
instead of acting on the current commit: `git switch --detach "${OLD:?set OLD to the old commit}"`.

Code first, state only if needed:

1. **Previous commit**:

   ```bash
   git switch --detach <old commit> && git rev-parse HEAD     # must print the old commit, not the new one
   docker compose build hub && docker compose up -d hub
   ```

   Then the checks above, and `docker compose exec hub hubproto status` to see the release in force. Rolling back
   to a commit from before the frames volume (issue #5) is the rollback in `docs/rollout/frames-volume.md`, not this:
   the older compose file reads the clone's `./frames`, so the volume's Frames must be copied back there first. Tell the user;
   `main` still has the change, so a fix or a revert goes through a PR. The clone is now detached: switch it back to
   its `main` branch before the next deployment.
2. **Restore the backup** only when the new code changed the state in a way the old code cannot use (a check above
   fails on the old commit), and only with the user's decision: it loses everything done on the hub since the
   backup. Every service that mounts the `hub-state` volume must be stopped first, or it keeps writing while the
   state is replaced: `hub`, and `cog-users` and `agents` when they run. Do not run the one-off `tick` service (or
   any other `docker compose run` on the hub image) during a restore.

   ```bash
   docker compose ps --services --status running      # note which of hub, cog-users, agents are running
   docker compose --profile cog-users --profile agents stop hub cog-users agents
   docker compose run --rm --no-deps -v <backups dir>/<backup name>:/backup:ro --entrypoint sh hub \
     -c 'test -f /backup/backup.json && hubproto restore --force /backup && touch /hub/state/.bootstrapped'
   docker compose up -d hub
   docker compose --profile cog-users --profile agents up -d cog-users agents   # only the ones that were running
   ```

   `restore --force` refuses a partial backup and sets the replaced state aside inside the volume
   (`/hub/state/.before-restore-<time>`), because `/hub/state` is a mount point. It also puts the backup's Frames into
   the frames volume (the replaced ones in `/hub/frames/.before-restore-<time>/`) and says whether they match the
   restored release; a backup of a running hub can show drift there (stop the hub for a consistent backup). The `.bootstrapped` marker is not
   in a backup (issue #14); without the `touch` the next start takes the first-start path (harmless, but it tries to
   seed again). Drop the `touch` once #14 is fixed. Then run the checks.

## Pitfalls

- **Changes under `frames/` in the deploy clone**: on a hub with the frames volume, nothing writes there, so they
  are someone's edits; show the user. On a hub not yet moved (the pre-check printed `bind`), they are the live hub's
  own releases: never `git checkout`, `git stash` or `git clean` them away outside the move in
  `docs/rollout/frames-volume.md`, which copies them into the volume first.
- `docker compose down -v` deletes the hub's Frames with its state. Never use `docker-compose.frames-dir.yml` on the
  live hub: it is a separate development hub (`hub-dev`).
- `docker compose up -d` without `build` reuses the old image: always build first.
- Rebuild only the `hub` image. `collabora`, `cad-worker` and `ollama-relay` are not part of a code deployment;
  do not recreate them unless the change is about them.
- `status --check` exiting 1 after a deployment means the Frames or config files on disk differ from the release in
  force (the output says which). On a hub whose SOPs are documents that is a change order's job, not the deploy's:
  report it.
- Never print or copy `.env`; never commit a backup; keep backups outside the repo.
- `docker compose run` for the restore must have `--no-deps` and its own `--entrypoint`, or it starts the other
  services and the entrypoint's start-up steps.
- Stopping a service that is not running is harmless, so stop all three before a restore; but start again only
  the ones that were running (the optional services stay off unless they were on).
- A backup holds the kernel, runs, releases, proposals, blobs, inbox, agents, settings, forms, record types, the
  queue and the signing secret (`BACKUP_PARTS`, `SQLITE_PARTS` in `hubproto/cli.py`), not the automation job history
  (`state/automation`) or the library folders (`state/library`): after a restore they are empty (the earlier ones
  are in the set-aside copy), so tell the user what a restore would lose (issue #14).
