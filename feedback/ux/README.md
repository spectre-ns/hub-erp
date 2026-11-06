# Adversarial UX panel

Eight reviewers, each with one lens, attack the hub's user experience and
write findings to `feedback/ux/<lens>.md`. The maintainer triages them in
`feedback/ux/TRIAGE.md`. Findings are the only output that counts; praise is
noise. The product claim under test: a Nextcloud/SharePoint-like workspace
where people of every role start work, find their items, decide at Gates with
a clear signature, and recover from mistakes, without reading the code or the
policy grammar.

## Where the hub is

- Shell: `http://localhost:8080/?as=<principal>` (identity is the `as` query
  parameter; no SSO in the prototype). Pages: `/` (home: processes you can
  start, with Start forms), `/inbox`, `/boards?type=<record type>`, `/files`,
  `/runs`, `/run/<id>`, `/record/<locator>`, `/doc/<locator>`, `/releases`,
  `/frame/<name>`, `/why`, `/admin`, `/healthz`. POST endpoints: `/start`
  (multipart), `/decide`, `/resubmit`, `/retry`, `/cancel`, `/msg`,
  `/attach`, `/hold`.
- People: alice (finance clerk, author, process owner, design engineer),
  bob (controller, reviewer, policy owner, management representative,
  internal auditor, design authority), carol (quality manager, reviewer,
  policy owner, document controller), dave (payables reviewer, buyer).
  Cogs: ap-reviewer, doc-reviewer, quality-assistant, purchasing-assistant.
  `/admin?as=bob` lists every member and role.
- CLI inside the container: `wsl.exe -d Ubuntu -u dahubley -- bash -lc 'cd ~/source/hub-erp-proto && docker compose exec -T hub hubproto --help'`
  or locally `cd ~/source/hub-erp-proto && . .venv/bin/activate && hubproto --root /tmp/some-dir init` for a scratch state.
- Source for inspection only: `hubproto/shell.py` (HTML, CSS and routing),
  `hubproto/cli.py`, `hubproto/cli_records.py`. Frames (policy prose) are
  under `frames/` and `profiles/iso9001/frames/`.
- Use `curl` (and Python's urllib if you need multipart) to fetch pages and
  submit forms. Read the HTML you get back; no browser is available. You may
  create records and runs in the live state; do not restart the containers,
  do not touch files under the Docker volume, and do not edit any file
  outside `feedback/ux/<your lens>.md`.

## Finding format

```
### F-<lens>-<n>: <one-line title>
Severity: blocker | major | minor | question
Heuristic: <which usability principle or WCAG criterion it violates>
Steps: numbered, exact (URLs, principals, inputs)
Expected:
Actual: (quote the HTML or text you saw)
Impact: who is hurt and how often
Suggested fix (optional):
```

Rules: stay in your lens; be adversarial (try to get lost, confused, stuck,
or misled); rank blockers first; aim for the twenty findings that matter most,
not for volume; do not fix anything.
