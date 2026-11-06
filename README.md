# hubproto

A from-scratch prototype of the design in the Intelligence Hub ERP plan:
prose policy documents (Frames) compile into an immutable **policy release**;
business processes run as Ops under that release with **Guards**, **Gates** and
**Tracks**; **Cogs** are team members that get routed work and messages, file
records, and approve within fixed criteria. A small event-sourced kernel is the
system of record.

Standard library only. PyYAML and the Anthropic SDK are optional.

## What it demonstrates

| Claim in the plan | Where it is proven |
|---|---|
| Permissions, routing and processes come from prose | `frames/*.frame.md` are the only policy input. `hubproto compile` turns them into rules with sentence-level citations. |
| A release is deterministic and content-addressed | `release.py`: SHA-256 of the rendered bundle without citations. A no-op edit yields the same digest (`test_no_op_edit_makes_no_new_release`). |
| Changes are gated by people; widenings need two | `compile.py`: every change is approved per change; a widening (new allow, cog route, raised threshold) needs two policy owners. |
| Runs pin the release they started on | `runner.py`: a run pins the digest at submission and finishes on it even after a cutover (`test_run_keeps_its_release_across_a_cutover`). |
| Cogs are team members | `org-model.frame.md` lists `ap-reviewer (cog)` and `doc-reviewer (cog)` with roles. They receive routed approvals, DMs and email, file records from attachments, and approve only within `## Approval criteria`. |
| Segregation of duties binds cogs too | A cog that filed a bill is skipped as its approver and a person decides (`test_segregation_of_duties_binds_people_and_cogs`). |
| Only code Cogs write, only under a grant | `ledger-poster` and `document-publisher` refuse without a grant; the kernel re-checks balance, open period, SoD and grant. |
| Every decision is evidence | The Track records release digest, manifest digest, Cog versions, Guard problems, Gate verdicts, the delegating policy sentence for a cog decision, and a signature digest for every decision. |
| "Why?" points at the sentence | `hubproto why` and the shell's Why page quote the Frame line that allowed or denied an action. |

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
hubproto init                      # seed suppliers, purchase orders, periods
hubproto compile                   # Frames -> proposal (rules, Guard findings, changes)
hubproto review <proposal> --as bob --approve-all
hubproto review <proposal> --as carol --approve-all     # widenings need a second policy owner
hubproto release <proposal> --as bob
hubproto why --as alice --action create --object bill --folder Finance/Payables
hubproto run purchase-to-pay --as alice --record-json \
  '{"supplier":"acme-metals","amount":1500,"currency":"USD","date":"2026-11-05","po_ref":"PO-1001","description":"Aluminium stock"}'
hubproto runs
hubproto track <run>
hubproto ledger
hubproto msg --from alice --to ap-reviewer --subject status --body "what is the status?"
hubproto msg --from alice --to ap-reviewer --subject invoice --body "please file this" --attach examples/acme-small.json --channel email
hubproto inbox --as dave
hubproto decide <run> --as dave --verdict approve --meaning "approved for posting"
hubproto shell                     # http://127.0.0.1:8080/?as=alice
```

Then edit a sentence in `frames/finance/finance-policy.frame.md` (say, the match
tolerance), bump its `version`, and run `compile` again: the proposal shows one
removed and one added rule, flags the widening, and after two approvals a child
release appears with the previous digest as its parent.

## Layout

```
frames/            policy Frames (Frame Spec v0.2). org-model is the scope root.
hubproto/
  frames.py        load Frames, resolve inheritance (child wins, later parent wins)
  extract.py       ConventionExtractor (deterministic) and ClaudeExtractor (optional)
  rules.py         rule shapes, canonical JSON, rule keys
  guards.py        schema, source-grounding, consistency, drift
  compile.py       proposal, per-change decisions, finalize
  release.py       render bundle + Op manifests, digest, ReleaseStore with effective_from
  authz.py         permission check with folder inheritance; deny wins; returns the sentence
  routing.py       routing candidates, approval criteria evaluation, thresholds
  envelope.py      result envelope v1 and the default Gate
  cogs.py          Cog catalog: code Cogs, context stand-ins, reviewer Cogs (team members)
  runner.py        Op runner: steps, Gates, cog review, pending decisions, grants, Track
  track.py         append-only run record
  kernel.py        SQLite event store: bills, journals, documents, with kernel Guards
  inbox.py         DMs and email to people and cogs; .eml outbox; mail-in parser
  shell.py         minimal workspace shell (stdlib http.server)
  cli.py           command line
tests/             pytest suite
```

## Using Claude for extraction

The default extractor is deterministic and reads the sentence shapes in
`frames/reference/writing-policy-frames.frame.md`. To extract from freer prose,
set `HUBPROTO_EXTRACTOR=claude` with the Anthropic SDK installed and credentials
available. The model's output goes through the same Guards: a rule whose cited
sentence is not in the Frame is refused before any person reviews it.

## Deviations from the published specs, on purpose

* Op manifests are JSON generated from `## Process:` sections under a local
  profile (`hubproto/op-manifest [0.1]`), not `openteams/op-manifest [0.2]`.
  The Op Spec allows a profile-specific manifest; aligning to the OpenTeams
  profile is a rendering change.
* Gates decided by a cog are an extension. The published profile has a default
  Gate and a human Gate. Here a cog may decide only under an approval-criteria
  rule that a policy owner approved at compile time, and the Track records that
  delegating sentence with the decision.
* Everything runs in one process with files and SQLite. The plan's production
  shape (Collab Hub run API, Harbor digests, OpenFGA, Postgres, Keycloak) keeps
  the same seams.
