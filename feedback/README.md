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

## The scenario every panelist must drive end to end

Design documentation to an order from an approved vendor, then receipt and
payment. Use the shell (`/` Start forms, `/inbox`, `/record/...`, `/boards`)
and, where your role would, the CLI.

1. As alice (design engineer): start `design-release` with a title, kind
   `electrical`, two user needs (free text), and the two example files
   `examples/sensor.kicad_sch` and `examples/box.FCStd` attached. Expect:
   two requirement records waiting for bob, a requirements trace, a BOM,
   an ERC report and a STEP export on the design record, and the design
   waiting for bob with risk `high` (requirements not yet accepted).
2. As bob (design authority): accept both requirements from the inbox,
   supplying acceptance criteria and a verification method. Then review the
   design. Expect: after approval the part is released, an inventory item is
   created for the unknown BOM line, and purchase requests appear for the
   shortages.
3. Purchasing: the small request for the known item from the approved
   supplier is approved by the purchasing-assistant cog and a PO is issued;
   the request with no supplier waits for dave (buyer), who approves it
   naming `acme-metals` and a unit cost. A request over 10,000 USD must go to
   bob (controller). A request naming `newco-tools` (not approved) must never
   be approved by the cog.
4. As alice or dave: start `goods-receipt` for the PO; dave confirms; the
   item's stock rises and the PO is marked received.
5. As alice: start `purchase-to-pay` with a bill against that PO number.
   Expect a matched bill, cog approval within criteria, and a balanced
   journal in `/files?folder=Finance`.
6. Check the Track of every run, the Why links, the snapshot documents, and
   the boards for `design`, `requirement`, `purchase-request`, `item`.
