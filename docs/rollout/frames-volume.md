# Moving an existing hub's Frames into the frames volume (issue #5)

Until issue #5, `docker-compose.yml` bind-mounted the checkout's `./frames` and `./profiles` into the hub (and into
the `cog-users`, `agents` and `tick` services). Every release writes the released Frame texts into `/hub/frames`,
so the live hub's policy ended up as uncommitted edits in the git working tree: the tests run from that checkout read
it, and it could be committed by accident.

Now the hub keeps its Frames in a volume of its own, `hub-frames` (`<project>_hub-frames`, for example
`hub-erp-proto_hub-frames`), mounted at `/hub/frames` in every service that reads them. The image ships the
repository's Frames in `/usr/local/share/hubproto/frames`; the entrypoint copies them into `/hub/frames` only on a
hub's first start (the folder empty and no state yet). The profiles and `config/` come from the image. To work on
Frames in the checkout's folders instead, add `docker-compose.frames-dir.yml` (a development hub only; see its header).

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
release and the SOP documents are in the state volume), but do the copy.

## The move

Run these in the checkout the hub runs from. The hub is stopped from step 2 to step 8.

```bash
B=frames-volume-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p ~/hub-backups

# 1. With the code the hub runs now: how it stands (the same must hold after the move), and a backup of the state.
docker compose exec hub hubproto status --check; echo "status exit $?"
docker compose exec hub hubproto backup /hub/state/backups/$B
docker compose cp hub:/hub/state/backups/$B ~/hub-backups/$B

# 2. Stop everything that writes the Frames: the hub, and the optional services if they run.
docker compose stop hub
docker compose --profile cog-users --profile agents stop cog-users agents 2>/dev/null || true

# 3. Copy the hub's Frames out of the checkout exactly as they are (the dot-directories .replaced and .retired too).
cp -a frames ~/hub-backups/$B-frames
diff -r frames ~/hub-backups/$B-frames && echo "the copy is complete"

# 4. Put the checkout's frames/ back to the repository's, then update the code. What `git status` and `git clean -n`
#    list are the live hub's released texts and the Frames its releases added: they are in the copy from step 3.
git status --short frames
git clean -n -d frames
git restore --staged --worktree -- frames
git clean -f -d frames
git pull --ff-only                                   # the commit with this change (or git switch to it)

# 5. Build the image with the new code.
docker compose build hub

# 6. Create the frames volume and copy the hub's Frames into it, in a one-off container of the new image (it runs as
#    the hub's user, so the files belong to it). The volume must be empty: on a second attempt, empty it first with
#    `find /hub/frames -mindepth 1 -delete` in the same command.
docker compose run --rm --no-deps -v "$HOME/hub-backups/$B-frames:/old-frames:ro" --entrypoint sh hub -c \
  'set -e; test -z "$(ls -A /hub/frames)"; cp -R /old-frames/. /hub/frames/; diff -r /old-frames /hub/frames; echo copied'

# 7. Check before starting: status as in step 1, and the checkout clean.
docker compose run --rm --no-deps --entrypoint hubproto hub status --check; echo "status exit $?"
git status --short frames                            # nothing

# 8. Start (and the optional services, if they ran before).
docker compose up -d hub && docker compose logs --tail 30 hub
```

The log of step 8 must not show the `WARNING ... frames is empty` line, and its `status --check` part must say what
step 1 said. From now on a release leaves `git status` clean. Keep `~/hub-backups/$B` and `~/hub-backups/$B-frames`
until the hub has run a release on the new code.

To read the hub's Frames: `docker compose exec hub cat /hub/frames/<path>`, or copy them out of the repository
(`docker compose cp hub:/hub/frames /some/folder`). To bring a released text into the repository on purpose (the
example Frames follow the live policy), copy that file into `frames/` and commit it like any other change. To bring a
change in the repository's Frames into a running hub, carry it on a change order (as `scripts/rollout_cc2.py`
does, with the repository's Frames mounted read-only beside the hub's: `-v "$PWD/frames:/repo-frames:ro"` and
`--frames-from /repo-frames`).

`docker compose down -v` now deletes the Frames with the state. A `hubproto backup` made on the new code holds both.

## Rollback

Before any release on the new code, the copy from step 3 is the hub's Frames. After one, copy the volume's Frames out
first (`docker compose cp hub:/hub/frames ~/hub-backups/$B-frames-after`) and use that instead.

```bash
docker compose stop hub
git switch --detach <the earlier commit>
rm -rf frames && cp -a ~/hub-backups/$B-frames frames   # the bind mount of the earlier compose file reads these
docker compose build hub && docker compose up -d hub
```

The state volume is not touched by the move, so no restore is needed. The `hub-frames` volume stays until removed
(`docker volume rm <project>_hub-frames`).
