# Acceptance-testing panel

Each panelist exercises the hub as one kind of user, writes findings to
`feedback/<persona>.md`, and the maintainer triages them in
`feedback/TRIAGE.md`. Findings are the only output that counts; praise is
noise.

## Where the hub is

- Shell: `http://localhost:8080/?as=<principal>` (identity is the `as`
  parameter; no SSO in the prototype). Members and their roles are on
  `/admin`. People: alice (finance clerk, author, design engineer), bob
  (controller, reviewer, policy owner, management representative, internal
  auditor, design authority), carol (quality manager, reviewer, policy owner,
  document controller), dave (payables reviewer). Cogs: ap-reviewer,
  doc-reviewer, quality-assistant.
- CLI inside the container: `docker compose exec hub hubproto <command>`
  (`hubproto --help`), or locally `. .venv/bin/activate && hubproto --root . <command>`
  against a local state directory.
- Frames (the policy prose) are under `frames/` and `profiles/iso9001/frames/`;
  the grammar is in `frames/reference/writing-policy-frames.frame.md` and
  `DEV-NOTES.md`.
- The plan the product is built from is the "Intelligence Hub ERP Plan"
  artifact; `README.md` summarizes the claims.

## Finding format

```
### F-<persona>-<n>: <one-line title>
Severity: blocker | major | minor | question
Area: shell | CLI | workflow | records | DMS | policy | docs | security | performance | architecture
Steps: numbered, exact (URLs, principals, inputs)
Expected:
Actual:
Evidence: paths, output, record numbers, run ids
Suggested fix (optional):
```

Rules for panelists: act in character for your role and only use what a
person in that role would use; do not fix anything; do not edit files outside
`feedback/`; prefer real reproduction over speculation; when a feature is
missing rather than broken, say "missing" and what you expected instead.
