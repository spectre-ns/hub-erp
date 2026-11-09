# Rolling out change control v2 on the live hub

The live hub (Docker Compose in `~/source/hub-erp-proto`, container `hub-erp`, state volume `hub-state`) runs the
earlier code: release v9 (`2dcbfc780b93`), the SOPs already documents, 374 runs of which 129 wait for someone (the
backup manifest's "748 runs" counts files: each run is a directory with two). Change control v2 changes how
configuration changes, and the live configuration (its SOP documents and record types) must be brought in line with
it through one change order. `scripts/rollout_cc2.py` makes and releases that change order, and checks the hub before
and after. This page is the runbook; the rehearsals and their findings are in `cc2-rehearsal.md` beside it.

## Status

The final rehearsal ran with no patch, on fresh restores of the live backup with the new Frames on disk as they will
be after the merge. See `cc2-rehearsal.md` for the commit, exit codes, counts and timings.

- bob opens the 30 drafts, adds the 3 new documents and retires the 19 personas. A policy owner may do this on any
  change order, and it counts as contributing. alice writes every text. carol opens and retires nothing.
- carol, as a policy owner who contributed nothing, answers the 19 blocking `unread-rule` questions. 14 are narrative:
  "Answered by Claude at the product owner's instruction: narrative, no rule". The other five, it-sop lines 37, 62, 76,
  82 and 88, the product owner decided are people's procedures: `category="procedure"`, "Answered by Claude at the
  product owner's instruction: a procedure people follow; the hub does not enforce it". The script's `READ` table keys
  each answer to the hash of its paragraph's words, so a changed text is never answered.
- bob and carol decide every item, so every widening has two owners. carol signs. The release automation checks Frame
  paths and duplicates before anything comes into force.

## What the script does

`python scripts/rollout_cc2.py --root ROOT --frames-from FRAMES [--dry-run] [--commit SHA] [--test-result LINE]`

| Step | What it does |
|---|---|
| 1 health | Reports the release in force and the run counts. Reads each waiting run with the new runner: its graph, status, deciders, whether an approval resolves, the fields it needs, and the release it will follow. Draws every decider's inbox (all pages) and a sample's record pages, on 127.0.0.1 with a free port. Decides a sample (one per process and status) on a throwaway copy. |
| 2 co | Prepares `Migration to change control v2` (CO, marked `[rollout:cc2]`) as **alice**. It carries a revision of every document whose text differs from `--frames-from` (header and body; bob opens each draft because alice owns no SOP, eco.py:67, and alice is its author and writes its only version; carol opens and retires nothing). It also adds a new document for each Frame without one, retires each document whose Frame was dropped (bob), and carries `record-types carry --from-file` (14 changes, system fields included). There are no form layouts in force and none shipped. It stops before writing anything when the code lacks a path it needs. |
| 3 review | alice requests review, and the round compiles with `HUBPROTO_EXTRACTOR` (default `convention`). The v9 change-control SOP has no "Review of changes", so the suggested panel is empty. alice then adds the reviewers the repository's section names who are members under v9 (qa-manager, quality-assistant). The it-admin and management cogs arrive with this CO. Every question, finding and suggestion is logged verbatim. carol (policy owner, no contribution) answers each blocking unread-rule question with "Answered by Claude at the product owner's instruction: narrative, no rule" where `READ` says the paragraph is narrative, and the five people's procedures with `category="procedure"` and "... a procedure people follow; the hub does not enforce it". A paragraph `READ` does not know stays open (exit 4). |
| 4 validate | `validation.record_evidence`: a passing test record (the suite on `--commit`) for the high-risk items, and a test note for the medium-risk ones. |
| 5 decide | bob and carol each approve every item, so every widening has two owners, accepting each warning with "Approved by Claude at the user's instruction: migration to change control v2 (rollout rehearsed on a copy of the live data)". carol accepts the CO-level warnings and alice sends the CO for approval. carol (quality manager) signs it, and the release automation runs. |
| 6 verify | Checks that a new release is in force from this CO, that every carried revision is effective and reads as the repository's text (a version line raised by the compile is allowed), that the retired documents are out and the record types activated, and that the waiting runs are still waiting and decidable. The step-1 sample is decided again on a further copy and compared outcome by outcome. It also checks that the next compile has the new Frames and none of the retired personas, and that the senior cogs are unavailable with hosted models off and available with them on (a fake key: nothing is called). Then `verify_chain`, `verify_signatures`, `verify_delta` and `status --check`. |

It is idempotent: a second run finds the CO by its mark and only verifies. Exit codes: 0 done, 1 a check failed,
3 a code change is needed (named in the log), 4 a question needs the product owner's decision, 2 a command mistake. Logs go to `<root>/state/rollout-cc2/<time>.log`,
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

# 5. Dry run: reads everything, writes nothing to the hub. It must exit 0 (3 or 4: stop here and roll back).
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
before (all 129 are decidable at 769d2e0). The release also assigns about 97 training
acknowledgements for the new revisions.

The script runs in a one-off container of the hub image with the hub volume, while the shell is stopped, so no
worker thread or user acts while the change order moves. The image does not contain `scripts/`, so it is mounted
read-only.

## Rollback

The backup from step 1 is the earlier code's state, so restore it with the earlier image. The earlier code's
`restore --force` renames the volume's mount point, which fails in the container; 769d2e0 fixes this for the new code
only. So restore into a temporary root, then swap the volume's contents and keep the rolled-back state on the volume:

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
