# Moving an existing hub's Frames into the frames volume (issue #5)

Until issue #5, `docker-compose.yml` bind-mounted the checkout's `./frames` and `./profiles` into the hub (and into
the `cog-users`, `agents` and `tick` services). Every release writes the released Frame texts into `/hub/frames`,
so the live hub's policy ended up as uncommitted edits in the git working tree: the tests run from that checkout read
it, and it could be committed by accident.

Now the hub keeps its Frames in a volume of its own, `hub-frames` (`<project>_hub-frames`), mounted at `/hub/frames`
in every service that reads them. The image ships the repository's Frames in `/usr/local/share/hubproto/frames`; the
entrypoint copies them into `/hub/frames` only on a hub's first start (the folder empty and no state yet). The
profiles and `config/` come from the image. To work on Frames in the checkout's folders instead, use
`docker-compose.frames-dir.yml`: a separate development hub (see its header), never the live one.

| | Before | After |
|---|---|---|
| Where the hub's Frames live | `./frames` in the checkout (bind mount) | the `hub-frames` volume |
| What a release writes into | the checkout | the volume |
| A newer image's Frames | in force on disk at once (`git pull`), reported by `status --check` | used only to fill a new hub; reach a running hub only through a change order |
| `status --check`, the entrypoint's drift warning | the checkout's Frames against the release in force | the volume's Frames against the release in force |
| `hubproto backup` / `restore` | state only | state and Frames (`frames/` in the backup; an older backup restores the state and leaves the Frames alone) |

A hub whose Frames are still in the checkout must have them copied into the new volume **before** it starts on the
new code. Otherwise it starts with an empty `/hub/frames` (the image's Frames are never copied over a hub that has
state): the entrypoint prints `WARNING: /hub/frames is empty but this hub has state`, `status --check` lists every
Frame as missing, and the next release writes only the Frames it changes. Nothing in force is lost that way (the
release and the SOP documents are in the state volume), but do the move below.

## Before you start

- **What is where.** Until step 3, the only copy of the live Frames is the checkout's `frames/`: the state backup of
  step 1 (made by the old code) does not hold them. From step 3 on there is a second copy outside the checkout, and
  step 4 keeps a third in a git stash.
- **The Compose project.** The volumes are named after the Compose project: `<project>_hub-state` holds the hub's
  state, and the move creates `<project>_hub-frames`. Run every step from the checkout the hub was started from, in
  a shell with the same `-p`, `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` (normally none of them). Under another project
  name, `docker compose` would create a new, empty state volume and start a new hub instead of the live one. Step 0
  records the project the running hub belongs to, and every step checks it.
- **Nothing else runs on the hub image during the move.** From step 2 until step 8 has finished, do not run `tick`,
  `cog-users`, `agents`, `docker compose up` or any other `docker compose run`. A container started on the new
  compose file creates the frames volume and fills it with its image's `/hub/frames` (Docker copies an image's folder
  into a new, empty volume), and a hub started early writes released texts into the wrong place.
- **How to run the steps.** Use `bash`. Fill in the placeholders once: `<deploy checkout>` (the checkout the hub
  runs from), `<backups dir>` (a folder outside any checkout: the state backup holds the hub's signing secret) and,
  in step 0, `<repository>` (the repository's URL, or the name of a remote that points to it). Paste each step's
  block whole. Each block is a subshell (`( ... )`), so a failure ends the step, not your shell. It prints
  `step N: done` or a line starting `STOP:`; never go on after a `STOP`. Step 0 writes what the later steps need
  into `<backups dir>/frames-volume.env`, and every later step reads it back, so a new shell, or running a step
  again, is safe. Steps 1 to 7 refuse to repeat what they have already done, or say how to go on.
- **If a step fails** after step 2, either fix what it names and run that step again, or roll back (the last
  section). The rollback works from any step.

## The move

The hub is stopped from step 2 to step 8.

```bash
# 0. Before anything is stopped: the Compose project, the code to move to, and the file the later steps read.
( set -eu
  cd '<deploy checkout>'
  D='<backups dir>'
  ENVFILE="$D/frames-volume.env"
  test ! -e "$ENVFILE" || { echo "STOP: $ENVFILE exists: a move has started; go on from the step it stopped at, or roll back"; exit 1; }
  P=$(docker compose ps --status running --format '{{.Project}}' hub | sort -u)
  test -n "$P" || { echo "STOP: no running hub in this shell's Compose project: run from the folder the hub was started from, with its -p, COMPOSE_PROJECT_NAME and COMPOSE_FILE (docker compose ls lists the projects)"; exit 1; }
  docker compose ls
  KIND=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hub/frames"}}{{.Type}}{{end}}{{end}}' "$(docker compose ps -q hub)")
  test "$KIND" = bind || { echo "STOP: the hub's /hub/frames is a '$KIND' mount, not the checkout's bind mount: there is nothing to move"; exit 1; }
  docker volume inspect "${P}_hub-state" > /dev/null
  if docker volume inspect "${P}_hub-frames" > /dev/null 2>&1; then
    echo "STOP: the volume ${P}_hub-frames exists already (an earlier attempt, or a container of the new compose file ran); if an earlier attempt was rolled back, the rollback said how to remove it"; exit 1
  fi
  test -z "$(git status --porcelain --untracked-files=no -- . ':(exclude)frames')" || { git status --short; echo "STOP: the checkout has changes outside frames/: commit them or set them aside first"; exit 1; }
  git fetch '<repository>' main
  T=$(git rev-parse FETCH_HEAD)
  git cat-file -e "$T:docs/rollout/frames-volume.md" || { echo "STOP: $T does not hold this move"; exit 1; }
  OLD=$(git rev-parse HEAD)
  OLDREF=$(git symbolic-ref -q --short HEAD || echo "$OLD")
  B="$D/frames-volume-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$D" && mkdir "$B"
  printf "ENVFILE='%s'\nCHECKOUT='%s'\nP='%s'\nT='%s'\nOLD='%s'\nOLDREF='%s'\nB='%s'\n" \
    "$ENVFILE" "$PWD" "$P" "$T" "$OLD" "$OLDREF" "$B" > "$ENVFILE"
  cat "$ENVFILE"; echo "step 0: done" )

# 1. With the code the hub runs now: how it stands (the same must hold after the move), and a backup of the state.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test ! -e "$B/state-backup" || { echo "STOP: step 1 has run ($B/state-backup exists): go on with step 2"; exit 1; }
  rc=0; docker compose exec -T hub hubproto status --check > "$B/status-before.txt" || rc=$?
  echo "$rc" > "$B/status-before.rc"; cat "$B/status-before.txt"; echo "status exit $rc"
  N="$(basename "$B")-$(date -u +%H%M%S)"
  docker compose exec -T hub hubproto backup "/hub/state/backups/$N"
  docker compose cp "hub:/hub/state/backups/$N" "$B/state-backup"
  test -f "$B/state-backup/backup.json"; echo "step 1: done" )

# 2. Stop everything that writes the Frames: the hub, and the optional services if they run (noted, to restart them).
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  running() { docker ps --filter "label=com.docker.compose.project=$P" --format '{{.Label "com.docker.compose.service"}}' | grep -x -E 'hub|cog-users|agents|tick' || true; }
  test -e "$B/services-running.txt" || running > "$B/services-running.txt"
  docker compose --profile cog-users --profile agents stop hub cog-users agents
  test -z "$(running)" || { echo "STOP: still running: $(running)"; exit 1; }
  echo "step 2: done" )

# 3. Copy the hub's Frames out of the checkout exactly as they are (the dot-directories .replaced and .retired too).
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test -z "$(docker compose ps --status running -q hub)" || { echo "STOP: the hub runs: step 2 first"; exit 1; }
  if [ -e "$B/frames" ]; then
    if [ -e "$B/frames.copied" ]; then echo "STOP: step 3 has run: go on with step 4"; exit 1; fi
    echo "STOP: $B/frames is a copy that stopped partway. Step 4 has not run (it needs $B/frames.copied), so the checkout still holds the hub's Frames: remove $B/frames and run step 3 again"; exit 1
  fi
  cp -a frames "$B/frames"
  diff -r frames "$B/frames"
  echo "$(find "$B/frames" -type f | wc -l) files copied"
  touch "$B/frames.copied"; echo "step 3: done" )

# 4. Set the checkout's frames/ aside (git stash: a third copy, which `git stash pop` puts back), then move the
#    checkout to the commit step 0 recorded. Only after a complete copy, and only if nothing changed frames/ since.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test -z "$(docker compose ps --status running -q hub)" || { echo "STOP: the hub runs: step 2 first"; exit 1; }
  test -e "$B/frames.copied" || { echo "STOP: step 3 has not finished"; exit 1; }
  if [ ! -e "$B/checkout-reset" ]; then
    diff -r frames "$B/frames" > /dev/null || { echo "STOP: the checkout's frames/ differ from the copy in $B/frames: something wrote them after step 3; do not go on: roll back"; exit 1; }
    git status --short -- frames
    git stash push --include-untracked -m "live frames $(basename "$B")" -- frames
    test -z "$(git status --porcelain --untracked-files=all -- frames)" || { echo "STOP: frames/ is not clean after the stash"; exit 1; }
    touch "$B/checkout-reset"
  fi
  test "$(git rev-parse HEAD)" = "$T" || git switch --detach "$T"
  test "$(git rev-parse HEAD)" = "$T"; echo "step 4: done" )
#    If `git switch` fails, it names what is in the way: fix that and run step 4 again, or roll back. Either way the
#    hub's Frames are safe in $B/frames and in the stash.

# 5. Build the image with the new code.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test "$(git rev-parse HEAD)" = "$T" || { echo "STOP: the checkout is not at $T: step 4 first"; exit 1; }
  docker compose build hub; echo "step 5: done" )

# 6. Create the frames volume and copy the hub's Frames into it, in a one-off container of the new image (it runs as
#    the hub's user, so the files belong to it). The volume must be new and empty.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test -z "$(docker compose ps --status running -q hub)" || { echo "STOP: the hub runs: stop it (step 2)"; exit 1; }
  test "$(git rev-parse HEAD)" = "$T" && test -e "$B/frames.copied" || { echo "STOP: steps 3 to 5 first"; exit 1; }
  test ! -e "$B/volume.filled" || { echo "STOP: step 6 has run: go on with step 7"; exit 1; }
  rc=0
  docker compose run --rm --no-deps -T -v "$B/frames:/old-frames:ro" --entrypoint sh hub -c '
    set -e
    if [ -n "$(ls -A /hub/frames)" ]; then ls -A /hub/frames; exit 3; fi
    cp -R /old-frames/. /hub/frames/
    diff -r /old-frames /hub/frames
    echo "$(find /hub/frames -type f | wc -l) files in the frames volume"' || rc=$?
  if [ "$rc" = 3 ]; then
    now=$(docker compose run --rm --no-deps -T --entrypoint hubproto hub status | grep -E '^(release in force|scheduled):' || true)
    before=$(grep -E '^(release in force|scheduled):' "$B/status-before.txt" || true)
    echo "STOP: the volume ${P}_hub-frames is not empty (listed above). A container of the image ran on it before this"
    echo "  step (Docker filled the new volume with that image's /hub/frames), or this step stopped partway before."
    echo "  At step 1: $before"
    echo "  Now:       $now"
    echo "  The same: nothing was released since, so what the volume holds is not the hub's. Empty it, then run step 6 again:"
    echo "    docker compose run --rm --no-deps --entrypoint sh hub -c 'find /hub/frames -mindepth 1 -delete'"
    echo "  Different: a release has run on the volume since, and it holds released texts the copy lacks. Do not empty"
    echo "  it. Copy in only the files it is missing (cp -n keeps every file already there), then mark the step done:"
    echo "    docker compose run --rm --no-deps -v \"$B/frames:/old-frames:ro\" --entrypoint sh hub -c 'cp -Rn /old-frames/. /hub/frames/'"
    echo "    touch \"$B/volume.filled\""
    echo "  If an image's Frames were copied in first, the files no release wrote since are the image's, not the hub's:"
    echo "  put those back from $B/frames by hand (hubproto release-diff between the two versions above names the Frames"
    echo "  the releases changed). Step 7 checks the result against step 1."
    exit 1
  fi
  test "$rc" = 0
  touch "$B/volume.filled"; echo "step 6: done" )

# 7. Check before starting: the status as at step 1, and the checkout clean.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test -e "$B/volume.filled" || { echo "STOP: step 6 has not finished"; exit 1; }
  rc=0; docker compose run --rm --no-deps -T --entrypoint hubproto hub status --check > "$B/status-after.txt" || rc=$?
  cat "$B/status-after.txt"; echo "status exit $rc (at step 1: $(cat "$B/status-before.rc"))"
  key() { grep -E '^(release in force|scheduled):|^  (changed|missing|new):' "$1" || true; }
  diff <(key "$B/status-before.txt") <(key "$B/status-after.txt") || { echo "STOP: the release in force or the drift differs from step 1 (above)"; exit 1; }
  test "$rc" = "$(cat "$B/status-before.rc")" || { echo "STOP: status exits $rc, at step 1 it exited $(cat "$B/status-before.rc")"; exit 1; }
  test -z "$(git status --porcelain --untracked-files=all -- frames)" || { echo "STOP: the checkout's frames/ is not clean"; exit 1; }
  touch "$B/checked"; echo "step 7: done" )

# 8. Start the hub, and the optional services that ran before; check it uses the volume and starts without a warning.
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  test -e "$B/checked" || { echo "STOP: step 7 has not passed"; exit 1; }
  docker compose up -d hub
  for s in cog-users agents; do
    if grep -qx "$s" "$B/services-running.txt"; then docker compose --profile "$s" up -d "$s"; fi
  done
  HUB=$(docker compose ps -q hub)
  test "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hub/frames"}}{{.Type}} {{.Name}}{{end}}{{end}}' "$HUB")" = "volume ${P}_hub-frames" \
    || { echo "STOP: the hub's /hub/frames is not the volume ${P}_hub-frames"; exit 1; }
  i=0; until [ "$(docker inspect -f '{{.State.Health.Status}}' "$HUB")" = healthy ]; do
    i=$((i + 1)); [ "$i" -lt 90 ] || { docker compose logs --tail 50 hub; echo "STOP: not healthy after 3 minutes"; exit 1; }; sleep 2
  done
  docker compose logs --tail 30 hub
  if docker compose logs hub | grep -q -F 'frames is empty'; then echo "STOP: the hub started with an empty frames folder"; exit 1; fi
  rc=0; docker compose exec -T hub hubproto status --check || rc=$?
  echo "status exit $rc (at step 1: $(cat "$B/status-before.rc"))"; echo "step 8: done" )
```

From now on a release leaves `git status` clean. Keep `$B` (the state backup, the copy of the Frames, the step
markers), the stash `live frames <name>` (`git stash list`) and `<backups dir>/frames-volume.env` until the hub has
run a release on the new code and a new `hubproto backup` (which now holds the Frames) has been copied off the volume.
Then `git stash drop` that stash and rename the env file (for example to `frames-volume.env.done`), so that a later
move starts afresh.

To read the hub's Frames: `docker compose exec hub cat /hub/frames/<path>`, or copy them out of the volume with
`docker compose cp hub:/hub/frames <a new folder>` (the folder must not exist yet, or the copy lands inside it). To
bring a released text into the repository on purpose (the example Frames follow the live policy), copy that file into
`frames/` and commit it like any other change. To bring a change in the repository's Frames into a running hub, carry
it on a change order (as `scripts/rollout_cc2.py` does, with the repository's Frames mounted read-only beside the
hub's: `-v "$PWD/frames:/repo-frames:ro"` and `--frames-from /repo-frames`).

`docker compose down -v` now deletes the Frames with the state. A `hubproto backup` made on the new code holds both;
stop the hub first for a backup consistent across the state and the Frames (manual 8.5).

## Rollback

From any step after step 0. It stops the hub, puts the hub's Frames back into the checkout if step 4 had taken them
out, moves the checkout back to the commit step 0 recorded, and starts the old code. Which copy of the Frames it puts
back:

- the hub has run on the frames volume (step 8): the volume's, which hold every release made since (copied out of
  the stopped hub container into `$B/frames-after`);
- otherwise: the copy of step 3, `$B/frames` (the volume, if step 6 filled it, holds the same files).

```bash
( set -eu; . '<backups dir>/frames-volume.env'; cd "$CHECKOUT"
  test "$(docker compose ps -a --format '{{.Project}}' hub | sort -u)" = "$P" || { echo "STOP: not the Compose project $P"; exit 1; }
  docker compose --profile cog-users --profile agents stop hub cog-users agents
  back() { if git show-ref -q --verify "refs/heads/$OLDREF"; then git switch "$OLDREF"; else git switch --detach "$OLD"; fi
           test "$(git rev-parse HEAD)" = "$OLD" || { echo "STOP: the checkout is not at $OLD"; exit 1; }; }
  if [ -e "$B/checkout-reset" ]; then
    HUB=$(docker compose ps -aq hub | head -n 1)
    KIND=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hub/frames"}}{{.Type}}{{end}}{{end}}' "$HUB")
    if [ "$KIND" = volume ]; then
      if [ ! -e "$B/frames-after.copied" ]; then
        rm -rf "$B/frames-after"                    # a copy that stopped partway, on an earlier run of this block
        docker cp "$HUB:/hub/frames" "$B/frames-after"
        touch "$B/frames-after.copied"
      fi
      SRC="$B/frames-after"
    else
      SRC="$B/frames"
    fi
    back
    rm -rf frames && cp -a "$SRC" frames && diff -r "$SRC" frames
    echo "the checkout's frames/ are the hub's again, from $SRC"
  else
    echo "step 4 had not run: the checkout's frames/ are still the hub's"
    back
  fi
  docker compose build hub && docker compose up -d hub
  for s in cog-users agents; do
    if grep -qx "$s" "$B/services-running.txt" 2> /dev/null; then docker compose --profile "$s" up -d "$s"; fi
  done
  HUB=$(docker compose ps -q hub)
  test "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hub/frames"}}{{.Type}}{{end}}{{end}}' "$HUB")" = bind
  i=0; until [ "$(docker inspect -f '{{.State.Health.Status}}' "$HUB")" = healthy ]; do
    i=$((i + 1)); [ "$i" -lt 90 ] || { docker compose logs --tail 50 hub; echo "STOP: not healthy after 3 minutes"; exit 1; }; sleep 2
  done
  rc=0; docker compose exec -T hub hubproto status --check || rc=$?
  echo "status exit $rc (at step 1: $(cat "$B/status-before.rc" 2> /dev/null || echo '?'))"
  mv "$ENVFILE" "$ENVFILE.rolled-back-$(date -u +%Y%m%dT%H%M%SZ)"
  echo "rolled back. Before another attempt, once the hub runs as before: docker volume rm ${P}_hub-frames"
  echo "(if it exists), and git stash drop the stash 'live frames $(basename "$B")' when you no longer need it" )
```

The state volume is not touched by the move, so no restore is needed: the old code reads the state as it is,
including any release made on the new code (its texts are in the Frames the rollback put back). Keep `$B` until the
hub has been checked on the old code.
