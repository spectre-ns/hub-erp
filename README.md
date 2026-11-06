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
docker compose up --build        # http://localhost:8080/?as=alice
```

First start seeds the kernel, installs the ISO 9001 profile, and compiles and
releases the first policy release with the two seeded policy owners (bob and
carol). State lives in the `hub-state` volume. Frames are bind-mounted from
`./frames`, so you can edit a policy sentence on the host and recompile:

```bash
docker compose exec hub hubproto compile
docker compose exec hub hubproto review <proposal> --as bob --approve-all
docker compose exec hub hubproto review <proposal> --as carol --approve-all   # widenings need two owners
docker compose exec hub hubproto release <proposal> --as bob
docker compose run --rm tick                                                  # escalate overdue items
```

Without Docker:

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
hubproto init && hubproto seed iso9001 && hubproto bootstrap-release --as bob --second carol
hubproto shell
```

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
