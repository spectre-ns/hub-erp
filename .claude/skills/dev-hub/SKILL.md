---
name: dev-hub
description: "Run a development hub on localhost (fresh, or from a copy of another hub's state), choose its models, wait for its cog queue, snapshot and restore it, then stop and clean it. Use for any development, demo or bug reproduction instead of the live hub."
---

# Run a development hub

Use this whenever you need a running hub: to try a change in the browser, reproduce a bug, record a demo, or drive a
change order end to end. Never use the live hub (`http://localhost:8080`, the Docker Compose stack) for this, and
never bind 8080, 9980 or 11434. Background and the full option list: DEV-NOTES.md, "Running hubs for development
and for agents".

Run every command from your own worktree. `python -m hubproto` runs the code in the current directory; the served
hub runs the worktree's code and Frames (child processes with `PYTHONPATH=<worktree>`).

## A fresh hub

```bash
python -m hubproto dev serve --detach --name <slug>   # about 3 s; prints the URL, e.g. http://127.0.0.1:8100/
python -m hubproto dev list                           # name, port, pid, branch, commit, url, worktree, state dir
curl -s http://127.0.0.1:<port>/healthz               # "ok" ("ok; cog queue stalled" is a problem)
```

- `--seed demo` (the default) adds an open NCR waiting for carol, a bill the ap-reviewer cog approved and an open
  change order (requested by bob) carrying a revision of the finance policy (`hubproto/dev_demo.py`). `--seed empty`
  gives the released example company only. `--port N` picks a port; the default is the first free one from 8100.
- Open `<url>?as=carol` (or bob, alice, dave): every link carries `?as=`, so the session stays that person.
- CLI verbs on the hub: `python -m hubproto --root <state dir> <verb>` (the state dir is in `dev list`, or
  `dev list --json`). Set `HUBPROTO_ALLOW_STAND_IN=1` for verbs that ask a cog (`run`, `agent`, `models`): the served
  hub has it, your shell does not.
- The hub does not reload code. After a code change: `dev stop <slug>`, then `dev serve --detach --name <slug>`
  (fresh) or add `--keep` (same state, new code).

## Models

| Mode | How | When |
|---|---|---|
| Stand-in (default) | nothing to add (`HUBPROTO_ALLOW_STAND_IN=1` is set for the hub) | almost always: deterministic, instant |
| Local Ollama | `--models ollama` (uses `$HUBPROTO_OLLAMA_URL`, default `http://localhost:11434`) | only when the work is about model behaviour; slow, and it shares the machine's Ollama |
| Hosted (Claude) | `HUBPROTO_HOSTED_MODELS=on` and `ANTHROPIC_API_KEY` in the environment of `dev serve` | only when the user asked for it: it spends money |

- The compile-frame committee (the senior cogs on `claude-opus-5-5`) never counts a stand-in as a reader
  (`Hub.compile_committee`). On a stand-in or Ollama hub a review round says "sentence shapes only: the compile-frame
  committee cannot reach consensus without ...": sentences the grammar reads are compiled, and prose it cannot read
  becomes a blocking question for the committee's chair. That is expected, not a bug.
- For hosted models load the key only into that one command, from the `.env` beside the live hub's
  `docker-compose.yml`, never printing it: `(set -a; . <that dir>/.env; set +a; HUBPROTO_HOSTED_MODELS=on python -m
  hubproto dev serve --detach --name <slug>)`.
- With hosted models on, the `claude-*` cogs call Claude and the other cogs keep the stand-in; add `--models ollama`
  for local models on those too.
- `python -m hubproto --root <state dir> models` (with the same environment) says who answers for each cog.

## Wait for the cog queue

Cogs and automations (compile-frame, release-change-order) work from the queue in `<state dir>/state/queue.db`; a
page returns at once and the work lands later. Before you read a result, wait until nothing is queued or working:

```bash
URL=http://127.0.0.1:<port>/
until [ "$(curl -s "${URL}queue-status?as=bob" | python -c 'import json,sys; print(json.load(sys.stdin)["open"])')" = 0 ]; do sleep 2; done
```

To see what is still open (read-only; the automations are the cog `hub-automation`):

```bash
python - <state dir>/state/queue.db <<'EOF'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for row in db.execute("SELECT cog, kind, status, title FROM tasks WHERE status IN ('queued','working') ORDER BY seq"):
    print(*row, sep="  ")
EOF
```

## Snapshot, restore, and a hub from a copy of another hub's state

`hubproto backup` copies a consistent snapshot even while the hub serves (SQLite backup API). A backup holds the
hub's signing secret (`.secret`): keep it outside the checkout and never commit it. It holds the hub's Frames
(`frames/`), and a restore puts them in place. It does not hold the
automation job history (`state/automation`) or the library folders (`state/library`); copy the whole root
(`dev stop` first, then `cp -a`) when you need those too.

```bash
python -m hubproto --root <state dir> backup <backup dir>              # a new or empty directory
# a new hub from that backup: restore creates <new root>/frames from it, then serve it as it is
mkdir -p <new root>
python -m hubproto --root <new root> restore <backup dir>     # prints whether the Frames match the restored release
python -m hubproto dev serve --detach --name <slug>-copy --state-dir <new root> --keep
```

- To put a dev hub back to a snapshot: `dev stop <slug>`, `python -m hubproto --root <state dir> restore --force
  <backup dir>` (the old state is set aside beside it), then `dev serve --detach --name <slug> --state-dir <state dir>
  --keep`.
- A copy of the live hub's state is the same recipe with a backup of the live hub, and only when the user asked
  for it: the `backup` and `docker compose cp` steps of the `deploy-live` skill; the backup holds the live hub's Frames
  (its `hub-frames` volume). Work on the copy, never on the live volumes.

## Stop and clean

```bash
python -m hubproto dev stop <slug>     # its state is kept
python -m hubproto dev clean <slug>    # removes its state directory
```

## Pitfalls

- `dev clean` deletes the state directory, including one you gave with `--state-dir` (serving writes the
  `dev-instance.json` marker into it). Only ever pass `--state-dir` a directory you made for this hub.
- `--state-dir` without `--keep` rebuilds the hub: a non-empty directory without the marker is refused, one with the
  marker is wiped. With `--keep` and a `state/kernel.db` there, the hub is served as it is.
- Never `dev stop --all` or `dev clean --all`: they act on other agents' hubs too.
- `HUBPROTO_DEV_HOME` moves the registry and the default state directories; `dev list` shows only the hubs of the
  registry in effect, so use the same value for serve, list, stop and clean.
- A foreground `dev serve` (no `--detach`) stops on Ctrl-C; an agent should always use `--detach`.
- Do not pipe `dev serve` into `head` or anything that closes early: the broken pipe stops the hub it just started.
  Redirect to a file if you need the output.
- Each `dev serve` picks the port again (the first free one from 8100), so the URL can change when you restart a
  hub; read it from the output or `dev list`, or pass `--port N`.
- If `dev serve` fails, it prints the end of `<state dir>/hub.log`; read that log before retrying.
