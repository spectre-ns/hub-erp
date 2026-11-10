---
name: deploy-live
description: "Deploy main to the live Docker Compose hub, check it, back it up, and roll it back. Only when the user has asked for a deployment in this task."
---

# Deploy to the live hub

**Deploying needs the user's request.** Do not deploy because a PR merged, a test passed or a demo needs it; ask.
Everything else in this skill touches the live hub, so the same holds for a backup or a rollback. Never act as a
person on the live hub (approve, sign, release as bob or carol) as part of a deployment.

The live hub is the Compose service `hub` (container `hub-erp`, `http://localhost:8080`), built from the clone that
holds `docker-compose.yml` and `.env`. Its state is the `hub-state` volume (`/hub/state`); `./frames` and
`./profiles` are bind-mounted from that clone, and a release writes the released SOP texts into `./frames`. The
first start bootstraps; later starts run `hubproto init` and `hubproto status --check` (`docker/entrypoint.sh`).
An earlier rollout with its rehearsal and a tried recipe: `docs/rollout/cc2.md`.

## Deploy

Run from the clone the live hub is built from, on the branch that tracks `origin/main`:

```bash
git status --short                 # must be empty: see "Pitfalls" for changes under frames/
git fetch origin
OLD=$(git rev-parse HEAD)          # what runs now, for a rollback
git log --oneline HEAD..origin/main  # what the deployment brings; tell the user
```

1. **The suite passes on what will run.** In a worktree at `origin/main`:
   `python -m pytest -q -n auto; echo $?` must print 0.
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
3. **Back up**, and copy the backup off the volume, outside any checkout (it holds the signing secret `.secret`):

   ```bash
   B=pre-deploy-$(date +%Y%m%d-%H%M)
   docker compose exec hub hubproto backup /hub/state/backups/$B
   docker compose cp hub:/hub/state/backups/$B <backups dir>/$B
   ```

4. **Update and rebuild**, right after one another (the bind-mounted `./frames` changes with the checkout):

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
   ```

6. **Report** to the user: the commit now running, the checks and their exit codes, and the backup name.

## Roll back

Code first, state only if needed:

1. **Previous commit**: `git switch --detach $OLD && docker compose build hub && docker compose up -d hub`, then the
   checks above. Tell the user; `main` still has the change, so a fix or a revert goes through a PR. The clone is
   now detached: switch it back to its `main` branch (`git switch -`) before the next deployment.
2. **Restore the backup** only when the new code changed the state in a way the old code cannot use (a check above
   fails on the old commit), and only with the user's decision: it loses everything done on the hub since the
   backup.

   ```bash
   docker compose stop hub
   docker compose run --rm --no-deps -v <backups dir>/$B:/backup:ro --entrypoint sh hub \
     -c 'hubproto restore --force /backup && touch /hub/state/.bootstrapped'
   docker compose up -d hub
   ```

   `restore --force` refuses a partial backup and sets the replaced state aside inside the volume
   (`/hub/state/.before-restore-<time>`), because `/hub/state` is a mount point. The `.bootstrapped` marker is not
   in a backup; without the `touch` the next start takes the first-start path (harmless, but it tries to seed again).
   Then run the checks.

## Pitfalls

- **Changes under `frames/` in the deploy clone** are usually the live hub's own releases (it writes released texts
  into the bind mount). Never `git checkout`, `git stash` or `git clean` them away; show the user and let them decide
  (commit them to `main` through a PR, as `docs/rollout/cc2.md` did).
- `docker compose up -d` without `build` reuses the old image: always build first.
- Rebuild only the `hub` image. `collabora`, `cad-worker` and `ollama-relay` are not part of a code deployment;
  do not recreate them unless the change is about them.
- `status --check` exiting 1 after a deployment means the Frames or config files on disk differ from the release in
  force (the output says which). On a hub whose SOPs are documents that is a change order's job, not the deploy's:
  report it.
- Never print or copy `.env`; never commit a backup; keep backups outside the repo.
- `docker compose run` for the restore must have `--no-deps` and its own `--entrypoint`, or it starts the other
  services and the entrypoint's start-up steps.
- A backup holds the kernel, runs, releases, proposals, blobs, inbox, agents, settings, forms, record types, the
  queue and the signing secret (`BACKUP_PARTS`, `SQLITE_PARTS` in `hubproto/cli.py`), not the automation job history
  (`state/automation`) or the library folders (`state/library`): after a restore they are empty (the earlier ones
  are in the set-aside copy), so tell the user what a restore would lose.
