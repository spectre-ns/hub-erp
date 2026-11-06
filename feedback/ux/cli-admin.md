# cli-admin: the `hubproto` CLI, policy-owner pages and operator ergonomics

Method: a scratch hub (`hubproto --root <scratch> init`, the repo's `frames/`
copied in as the compose bind mount would, `seed iso9001`,
`bootstrap-release --as bob --second carol`), every verb run at least once with
good and bad input, the shell served from the same root on port 8099 and read
with curl. Line numbers quoted from the CLI are what the CLI printed.

### F-cli-admin-1: `release` checks no role and re-releasing a digest rewrites its history
Severity: blocker
Heuristic: Error prevention; the product claim "changes are gated by people; widenings need two"
Steps:
1. With a release in force and no Frame edits: `hubproto compile` (prints `changes: 0`).
2. `hubproto release <that proposal> --as dave` (dave holds no `policy-owner` role).
3. `hubproto releases`.
4. Same effect: `hubproto bootstrap-release --as alice --second dave --force`.
Expected: step 2 refused (`dave is not a policy owner`); a release that already exists is never re-stored; the index keeps who released it first.
Actual: step 2 printed `release 82466504ddab...  effective_from=2026-11-06T16:04:39+00:00  parent=82466504ddab...` and exit 0. `releases` then showed the single release as `by=dave  proposal=6ec97ef906e6  parent=82466504ddab04b0` (parent is itself; bob's original entry and the bootstrap proposal id are gone; `effective_from` moved forward). `bootstrap-release --force` by alice did the same and printed `release ... in effect`. `review` of a closed proposal as alice printed `recorded decisions by alice; status=released` with exit 0 although nothing was recorded.
Impact: anyone with container access rewrites the audit trail of the release in force; the "two owners" gate only guards proposals that happen to contain changes. Every operator who runs the README's compile/release loop after a no-op edit hits this.
Suggested fix: `finalize` refuses `by` not in the reviewers set and refuses a proposal whose status is already `released`; `ReleaseStore.store` refuses an existing digest (or records a second `effective_from` without replacing the entry).

### F-cli-admin-2: releasing a stale proposal silently reverts a newer release
Severity: blocker
Heuristic: Visibility of system status; error prevention
Steps:
1. Delete `Payables reviewers may read Finance.` from `frames/finance/finance-policy.frame.md`; `hubproto compile` → proposal A.
2. Also delete `Controllers may read Finance.`; `hubproto compile` → proposal B.
3. `hubproto review B --as bob --approve-all; hubproto release B --as bob`.
4. `hubproto why --as bob --action read --object bill --folder Finance` → `DENIED` (correct).
5. `hubproto review A --as bob --approve-all; hubproto release A --as bob`.
Expected: step 5 refused because A was compiled against an older base than the release in force, or at least a warning naming the newer release.
Actual: step 5 succeeded. `why` now says `ALLOWED  allowed by finance-policy line 16: Controllers may read Finance.` although that sentence no longer exists in any Frame; no WIDENING gate was shown for the re-added permission. `releases` lists two releases with the same `effective=2026-11-06T16:10:13+00:00` and both with `parent=e41e7c48c39fedc0`.
Impact: two policy owners working in parallel (or one who forgot an old proposal) undo each other's work without any message; the release in force then cites prose that is not in the files.
Suggested fix: `finalize` requires `proposal["base"] == releases.current()["digest"]`, otherwise `error: proposal <id> was compiled against release <base>; release <current> is in force; run compile again`.

### F-cli-admin-3: a mistyped sentence or heading silently removes rules and the compile still exits 0
Severity: blocker
Heuristic: Error prevention; help users recognise and diagnose errors
Steps:
1. In `finance-policy.frame.md` change `Finance clerks may create bills in Finance/Payables.` to `Finance clerks can create bills in Finance/Payables.` (or drop the full stop, or write `two percent`).
2. `hubproto compile`.
3. Change the heading `## Permissions` to `## Permisions`; `hubproto compile`.
Expected: an `[error]` finding that names the file and line, says the sentence did not parse and which rule would be lost; non-zero exit; release blocked until fixed.
Actual: step 2 printed only `[note] finance-policy:11: unparsed sentence in the 'permissions' section: ...` and a change `remove:05d9934703434f3c  remove allow role:finance-clerk create bill in folder:Finance/Payables`, exit 0; one approval releases it. Step 3 printed no note at all: nine `remove` changes including `remove:dead42985a952828  WIDENING remove deny role:quality-manager create bill in folder:Finance/Payables`, exit 0. The routing variant (`goes to`) silently removed the controller route for bills over 10,000 USD.
Impact: the most common editing mistake a policy owner can make turns into a silent permission change; the loss of a `may not` sentence is a widening that nobody is told about in words.
Suggested fix: an unparsed sentence inside a grammar section is an `error` finding (with the file path and file line) that blocks release; an unknown `##` heading inside a policy Frame is a warning that names the nearest known heading.

### F-cli-admin-4: one malformed Frame file disables every CLI verb, and the error does not name the file
Severity: blocker
Heuristic: Error recovery; help users diagnose errors
Steps:
1. In any Frame break the frontmatter (replace a `---` with `—-`), or set `inherits: org-modle`.
2. `hubproto records` (or `runs`, `inbox-pending`, `why`, anything).
Expected: verbs that do not need Frames keep working; `compile` says `frames/finance/finance-policy.frame.md: missing frontmatter block`.
Actual: every verb dies with a traceback ending in `hubproto.frames.FrameError: missing frontmatter block` (no path) or `FrameError: unknown Frame 'org-modle'` (no path, no line), because `Hub()` loads all Frames in its constructor. In Docker the Frames are a host bind mount, so a half-saved edit on the host takes out `docker compose exec hub hubproto ...` entirely; the running shell keeps serving its startup copy and shows nothing wrong.
Impact: an operator cannot list runs or records while a colleague's editor has a Frame in a bad state, and cannot tell which of 25 files is at fault from the output.
Suggested fix: load Frames lazily (only `compile`, `frames`, `seed`); catch `FrameError` in `main()` and print `error: <path>:<line>: <message>`.

### F-cli-admin-5: the shell shows Frames as they were when it started, and "Open at line N" lands on the wrong sentence
Severity: major
Heuristic: Visibility of system status; match between system and the real world
Steps:
1. Add `Payables reviewers may create bills in Finance/Payables.` after line 24 of `finance-policy.frame.md`; compile, review with bob and carol, release.
2. `curl 'http://localhost:8080/frame/finance-policy?as=bob'`; also `/frame/org-model` after adding a member.
3. `curl 'http://localhost:8080/why?as=bob&principal=dave&action=create&object=bill&folder=Finance/Payables'` and follow the link.
Expected: the Frame page shows the file as released; the link opens the sentence that allowed the action.
Actual: the Frame page still shows the 54 lines from before the edit (the new sentence is absent; erin is absent from org-model) while `/releases` already lists the new digest and `/admin` lists erin. The `why` answer says `allowed by finance-policy line 15` with link `/frame/finance-policy?as=bob#L15`; line 15 of the stale page reads `Controllers may approve bills in Finance/Payables.` `docker-compose.yml` promises "Edit Frames on the host and recompile from inside the container" but only the CLI sees the edit.
Impact: a policy owner reviewing on the shell reads prose that is not what was released and is sent to the wrong sentence; this happens after every edit until the container restarts.
Suggested fix: re-read the `FrameStore` per request (or when a file's mtime changes); render the Frame from the release's stored copy rather than from disk.

### F-cli-admin-6: every line citation is off by the length of the frontmatter
Severity: major
Heuristic: Match between system and the real world
Steps:
1. `hubproto compile` with a bad sentence at file line 21 of `finance-policy.frame.md` (ten frontmatter lines above the body).
2. `hubproto why --as alice --action create --object bill --folder Finance/Payables`; `hubproto track <run>`; `hubproto inbox --as bob`.
Expected: citations use the file line an editor shows, or say "body line".
Actual: `[note] finance-policy:11`, `allowed by finance-policy line 11`, `Routed by finance-policy line 25: Approvals in purchase-to-pay over 10,000 USD go to the controller.` (file line 34); no citation gives a path (`finance-policy`, not `frames/finance/finance-policy.frame.md`); six warnings are cited as `(:0)`.
Impact: a policy owner opening the cited line in an editor lands ten lines too high in every file; the shell numbers lines the same way, so the two views disagree with every text editor.
Suggested fix: store the file line (body offset + frontmatter length) and the relative path in `source`; print `frames/finance/finance-policy.frame.md:21`.

### F-cli-admin-7: a failed run cannot be repaired from the CLI, and `run`/`retry` do not say why it failed
Severity: major
Heuristic: Error recovery; visibility of system status
Steps:
1. `hubproto run purchase-to-pay --as alice` (empty record).
2. `hubproto retry <run> --as alice`; `hubproto resubmit <run> --as alice --field supplier=acme-metals ...`.
Expected: `run` prints the failure reason and exit 1; a verb lets the owner set the missing fields and retry.
Actual: `run 2a6d5c7815 status=failed release=29f253bee81620b7` exit 0, no reason. `retry` → `run 2a6d5c7815 retried status=failed` exit 0, no reason. `resubmit` → `error: run 2a6d5c7815 is not waiting for a resubmit (status failed)`. The reason exists only in `inbox --as alice` (`invoice has no supplier; ... Fix the record and retry the run, or cancel it.`), but there is no CLI verb that sets a record's fields, so the only CLI exit is `cancel`.
Impact: every run that fails at a cog step is unrecoverable from the command line; a scripted operator sees exit 0 and a `failed` run.
Suggested fix: print the `run.failed` reason from the Track in `run` and `retry` and exit 1; let `resubmit --field` apply to failed runs (or add `set-fields`).

### F-cli-admin-8: no way to list proposals; approvals are lost on every recompile; proposals pile up
Severity: major
Heuristic: Visibility of system status; user control
Steps:
1. Add `- erin (person): finance-clerk, staff` to org-model; `compile`; `review <P1> --as bob --approve-all`.
2. Change dave's roles; `compile` → P2; `review P2 --as bob --approve-all`.
3. Try to find P1 later: `hubproto --help`, `hubproto proposal 5`.
Expected: `hubproto proposals` lists open proposals with base, reviewers and pending changes; a recompile carries forward decisions on unchanged change ids; a proposal superseded by a newer one is marked.
Actual: no `proposals` verb; after fourteen trial compiles `state/proposals/` held 17 files with no listing and no cleanup; P2 re-listed the erin change as `pending` needing bob again; `hubproto proposal 5` → traceback `CompileError: proposal '5': 2 matches`; `compile` prints `reviewers: bob, carol` but never says "run `review <id> --as bob` next".
Impact: policy owners cannot see what is waiting for them; a second owner's edit silently discards the first owner's approvals.
Suggested fix: `proposals` verb (open first, with `needs:` lines); carry decisions forward by change id when the base is unchanged; mark older open proposals `superseded`.

### F-cli-admin-9: `why` says DENIED for the plural noun the policy sentences use, and echoes the wrong noun back
Severity: major
Heuristic: Consistency and standards; match between system and the real world
Steps:
1. After releasing `Payables reviewers may create bills in Finance/Payables.`:
2. `hubproto why --as dave --action create --object bills --folder Finance/Payables`
3. `hubproto why --as dave --action create --object bill --folder Finance/Payables`
4. Same on the shell: `/why?...&object=bills`.
Expected: either noun form is accepted, or the error says "unknown object 'bills'; objects are bill, document, ...".
Actual: step 2 → `DENIED   no rule in the release lets any of ['buyer', 'payables-reviewer', 'staff'] create bills in Finance/Payables`; step 3 → `ALLOWED  allowed by finance-policy line 15: ...`. The DEV-NOTES examples and every Permission sentence use the plural. `why --as nobody ...` and `why --as erin` (before release) both say `X is not a member of the organization` with exit 0, same as a true denial.
Impact: a policy owner concludes the release did not take effect and recompiles; scripts cannot tell "denied" from "you asked the wrong question" (exit 0 in all cases).
Suggested fix: singularise/validate `--object` against the record types and reject unknown ones with the list; exit 1 for DENIED, 2 for a malformed question.

### F-cli-admin-10: `--help` does not say what the verbs do, in what order, or what any flag means
Severity: major
Heuristic: Help and documentation; recognition rather than recall
Steps:
1. `hubproto --help`; `hubproto review --help`; `hubproto why --help`; `hubproto release --help`; `hubproto bootstrap-release --help`.
Expected: a top-level synopsis of the admin path (`init → seed → compile → review (two owners for widenings) → release`), one line per verb, and a help string per flag.
Actual: verbs are listed in registration order (`init, frames, compile, proposal, review, release, releases, why, run, ...`); `releases`, `runs`, `run-show`, `track`, `inbox`, `ledger`, `bills` have no help text at all; `init` is described as "seed the kernel" while `seed` is "install a seed profile" (two different things called seed); no option in any sub-help has a description (`--as AS_USER`, `--decide CHANGE=verdict`, `--effective-from EFFECTIVE_FROM`, `--scope SCOPE`, `--second SECOND`, `--force` are bare); `conformance` takes both `--profile` and a positional `profile_pos` that silently overrides it.
Impact: a new admin cannot learn the release path from the tool; the README is the only source, and it does not mention `proposal`, `frames`, `inbox-pending` or `conformance`.
Suggested fix: `description` and `epilog` on the parser with the ordered path; `help=` on every argument; group verbs (policy, runs, records, documents, messaging).

### F-cli-admin-11: ordinary mistakes produce Python tracebacks
Severity: major
Heuristic: Help users recognise, diagnose and recover from errors
Steps:
1. `hubproto proposal nope`; `hubproto run-show nope`; `hubproto track nope`; `hubproto proposal 5` (ambiguous prefix).
2. `hubproto run purchase-to-pay --as alice --record-json '{oops'`.
3. `hubproto upload --folder Finance/Payables --as alice --file /nope.txt`; `hubproto mail-in /nope.eml`; `hubproto doc-get DOC-0001 --version 9`.
4. `hubproto compile --scope bogus`; `hubproto seed nope`; `hubproto conformance nope`; `hubproto --root /nonexistent/x records`.
Expected: one line `error: ...` and exit 1 (as `decide nope` and `record NCR-2026-9999` already do).
Actual: each prints a 10–40 line traceback (`CompileError: proposal 'nope': 0 matches`, `RunError: run 'nope': 0 matches`, `json.decoder.JSONDecodeError`, `FileNotFoundError`, `StopIteration` for the bad version, `FrameError: unknown Frame 'bogus'`, `ProfileError: no profile 'nope' under ...`, and four chained `FileNotFoundError`/`PermissionError` tracebacks for the bad root). Only `decide`, `review`, `release` and the `cli_records` verbs are guarded.
Impact: every operator, daily; the useful message is on the last line of a stack dump and `0 matches` does not say "no such run".
Suggested fix: one `try/except (CompileError, RunError, FrameError, ProfileError, KernelGuardError, OSError, json.JSONDecodeError)` in `main()`; say `no proposal starts with 'nope'` / `'5' matches 2 proposals: 5bab..., 5fcb...`.

### F-cli-admin-12: a wrong `--root`/`HUBPROTO_ROOT`/cwd silently creates an empty hub and answers "nothing"
Severity: major
Heuristic: Error prevention; visibility of system status
Steps:
1. `cd /tmp/emptydir && hubproto records` (default `--root .`).
2. `HUBPROTO_ROOT=/tmp/typo hubproto why --as alice --action read --object bill --folder Finance`.
Expected: `error: /tmp/emptydir is not a hub (no state/kernel.db); run hubproto init or pass --root`.
Actual: step 1 prints nothing, exit 0, and leaves a new `state/` (kernel.db, blobs/, inbox/, proposals/, releases/, runs/) in the current directory; step 2 prints `DENIED   no policy release is in effect` and creates `/tmp/typo/state`. Nothing says which root was used.
Impact: an operator in the wrong shell or container directory believes the hub is empty or unreleased; stray state directories accumulate.
Suggested fix: only `init` may create `state/`; every other verb fails fast when `state/kernel.db` is missing and prints the root it used.

### F-cli-admin-13: `--effective-from` and `--today` accept any string
Severity: major
Heuristic: Error prevention
Steps:
1. `hubproto release <P> --as bob --effective-from "next monday"`.
2. `hubproto releases`; `hubproto why --as dave ...`.
3. `hubproto tick --today tomorrow`.
Expected: `error: --effective-from must be an ISO date or timestamp`.
Actual: step 1 → `release 29f2...  effective_from=next monday  parent=...` exit 0; the release is stored with that string and, because selection is a string comparison, never takes effect; `releases` does not say a release is scheduled; `why` keeps answering from the old release with no hint that a newer one is pending. `tick --today tomorrow` → `tick done`, exit 0, escalates nothing.
Impact: a policy owner who schedules a release believes it is done; overdue items are never escalated after a typo.
Suggested fix: parse with `datetime.fromisoformat`; `releases` and `why` mention a scheduled release (`next: <digest> effective 2030-01-01`).

### F-cli-admin-14: backup and restore are undocumented and a partial restore is undetectable
Severity: major
Heuristic: Help and documentation; error recovery
Steps:
1. `grep -ri backup README.md DEV-NOTES.md docker-compose.yml Dockerfile` (nothing).
2. Copy `state/` without `runs/`; `hubproto runs`, `hubproto record BILL-0003`, `hubproto inbox-pending --as bob`.
3. Copy `state/` without `releases/index.json`; `hubproto releases`, `hubproto why ...`, `hubproto runs`.
Expected: a documented `hubproto backup <tar>` / `restore`, or at least "stop the container and copy the volume"; an integrity check that reports records pointing at missing runs or runs pinned to an unindexed release.
Actual: no guidance at all (the only words are "State lives in the `hub-state` volume"); the kernel is an open SQLite file with no WAL/journal statement, so a live copy may be torn. After step 2, `runs` lists nothing while `record BILL-0003` still shows `run=3cdfa0c642` and `inbox-pending` says `nothing is waiting for bob` with no warning. After step 3, `releases` silently recreates an empty index, `why` says `no policy release is in effect` although both release bundles are still in `state/releases/`, and `runs` lists runs pinned to `release=29f253bee816` that the hub now says does not exist.
Impact: the first operator who restores from a backup cannot tell whether the restore is complete; a lost 2 KB index file looks like "no policy".
Suggested fix: `hubproto backup`/`restore` (SQLite `.backup` plus tar of files), `hubproto check` that rebuilds `index.json` from the bundle directories and reports dangling run/release references; a README section.

### F-cli-admin-15: `/releases` lets nobody review a change: no diff, no sentences, no approvals, no proposal link
Severity: major
Heuristic: Visibility of system status; recognition rather than recall
Steps:
1. Release the widening from F-cli-admin-5 with two owners, then `curl 'http://localhost:8080/releases?as=bob'`.
Expected: per release: which Frames changed, which sentences were added/removed, who approved each change and when, a link to the proposal and to the parent's diff.
Actual: a table with `Digest | Effective from | Released by | Parent | Frames`; the Frames column is the same 24-item `name@version` list on every row (`finance-policy@1.2.0` before and after the sentence was added, because versions are hand-maintained frontmatter), then the routing/criteria/workflows/thresholds of the current release only. Carol's approval and the `meaning` text appear nowhere; the CLI `proposal` verb is the only place that shows `[bob:approve, carol:approve]` and only if the id is known. `/admin` is served in full to `alice` and to a non-member (`?as=nobody`) with no access rule.
Impact: a policy owner or auditor cannot answer "what changed between these two releases and who signed it" from the shell at all.
Suggested fix: a `/release/<digest>` page listing the proposal's changes with reason, widening flag, decisions (by, at, meaning) and a sentence-level diff against the parent; frames whose sha256 changed highlighted.

### F-cli-admin-16: on the CLI `--as` is a label, not an identity; impossible actors and targets are accepted
Severity: major
Heuristic: Error prevention; consistency with the shell
Steps:
1. `hubproto doc-new --title Note --class controlled --folder Finance --as nobody --file note.txt`.
2. `hubproto doc-new ... --folder Nowhere --as alice --file note.txt`.
3. `hubproto msg --from alice --to zed --subject hi`.
4. `hubproto doc-checkin DOC-0001 --as alice --file v2.txt` with no check-out; `hubproto cancel <alice's run> --as bob --reason dup`.
Expected: `nobody is not a member`, `folder 'Nowhere' is not declared in the release`, `no member or cog 'zed'`, `DOC-0001 is not checked out`.
Actual: `DOC-0002 rec:document:DOC-0002 state=draft rev A.0`; `DOC-0003 ... ` filed in `Nowhere`; `delivered to zed via dm`; check-in without check-out bumps to `rev A.1`; bob cancels alice's run (`record rec:bill:BILL-0001 state=cancelled`). Meanwhile `run`/`decide` do enforce roles (`dave does not hold the role 'finance-clerk' that starts purchase-to-pay`, `alice is not an assignee of this decision`), so the operator cannot predict which verbs check and which do not.
Impact: records and documents end up attributed to non-members and in undeclared folders; `why` on the same hub says those principals do not exist.
Suggested fix: resolve `--as` and `--to` against the release's members for every verb; validate folders against the bundle; `cancel` only by owner or policy owner.

### F-cli-admin-17: exit codes and output do not distinguish success from failure
Severity: major
Heuristic: Consistency and standards (scriptability)
Steps:
1. `hubproto compile` with `[error]` findings; `hubproto run ...` that ends `status=failed`; `hubproto retry` that fails again; `hubproto why` → `DENIED`; `hubproto records --state nonsense`; `hubproto bootstrap-release` when a release exists.
Expected: non-zero exit for errors, denials and failed runs; "unknown state 'nonsense'; states are ..." for the filter.
Actual: all exit 0. `compile` with 23 `[error]` findings exits 0 and still saves the proposal; `bootstrap-release` prints `a release is already in effect; use --force to add another` and exits 0; `records --state nonsense` prints nothing. `init` prints `kernel seeded at ...` on every run even when it did nothing.
Impact: `docker/entrypoint.sh` and any operator script cannot branch on results; CI-style checks of Frames (`compile` as a lint) are impossible.
Suggested fix: exit 1 on error findings, DENIED, failed/cancelled runs; exit 0 with "already seeded"/"already in effect" only when that is what was asked.

### F-cli-admin-18: the change list a reviewer must approve is dominated by noise
Severity: major
Heuristic: Recognition rather than recall; aesthetic and minimalist design
Steps:
1. Change one Permission sentence in `finance-policy.frame.md`; `hubproto compile`.
2. First compile on a fresh hub; `hubproto compile` with a `## Permisions` typo.
Expected: one change per sentence; processes untouched unless their section changed; reviewers told what to run next.
Actual: step 1 shows the one real change plus `add process purchase-to-pay with 6 steps`, `add process journal-reversal with 3 steps`, `remove process purchase-to-pay ...`, `remove process journal-reversal ...` on every edit to that file. The first compile lists 112 changes, 60 of them `WIDENING`, as `add:40565a358d3e62d4     WIDENING add cog:quality-assistant may approve nonconformance up to None  when ['risk_low']` (`up to None`), with `reviewers:` blank; the only practical response is `--approve-all`, which is what `bootstrap-release` and the README do, so per-change review is never exercised. Rules are printed in internal form (`allow role:staff read * in folder:Quality`) not as the sentence that produced them.
Impact: policy owners rubber-stamp; a real widening hides among phantom process re-adds.
Suggested fix: key process rules on content, not position; show the source sentence next to each change; collapse a Frame's unchanged processes; print `next: hubproto review <id> --as bob` after compile.

### F-cli-admin-19: identifiers are inconsistent across verbs and messages
Severity: minor
Heuristic: Consistency and standards
Steps:
1. `hubproto release ...` (64-char digest), `releases` (16), shell `/releases` (20), `record` (12), `runs` (12); `proposal` wants a prefix of a 12-char id, `review` and `release` the same; runs are 10-char.
2. `hubproto record bill-0003`; `hubproto record 3`; `hubproto link BILL-0003 relates-to BILL-0002 --as alice`.
3. `msg` uses `--from`, every other verb `--as`; `doc-new --class` vs `upload` (no class); `records` hides documents unless `--type document` is given.
Expected: one digest width, case-insensitive record numbers, error messages that list what is valid.
Actual: `error: exists: no record 'bill-0003'` (the `exists:` prefix is a guard name); `error: link-vocabulary: unknown link relation 'relates-to'` without the list (`config/links.json`: concerns, caused, fulfils, settles, supersedes, ...); `records` output has no header row and no created time; `runs` has no timestamps or record numbers.
Impact: constant small friction and copy-paste of the wrong width; every relation name is a guess.
Suggested fix: 12-char short digests everywhere with full on `--json`; case-insensitive refs; errors that list valid values; header rows.

### F-cli-admin-20: the Docker first start hides seed failures and loops on a bootstrap failure
Severity: minor
Heuristic: Error recovery; help and documentation
Steps:
1. Read `docker/entrypoint.sh`: `hubproto seed iso9001 || true` then `hubproto bootstrap-release --as bob --second carol` under `set -e`, then `touch state/.bootstrapped`.
2. Reproduce the bootstrap failure locally with an org model that has no members: `hubproto bootstrap-release --as bob --second carol`.
Expected: a failed seed is reported; a failed bootstrap leaves a clear message and a container that still serves `/healthz` so the operator can fix the Frame and rerun one command.
Actual: the seed's exit code is thrown away; the bootstrap prints 23 identical `[error] consistency: role:quality-manager is routed to but no member holds that role; fill the org model  (audit-sop:20)` lines (which org-model file? the message never says `frames/iso9001/org-model.frame.md`), then `bootstrap stopped: fix the findings above`; with `set -e` the container exits before the shell starts and `restart: unless-stopped` restarts it forever, re-running the same bootstrap. The README's "Without Docker" line (`hubproto init && hubproto seed iso9001 && hubproto bootstrap-release ...`) only works from the repo root because `--root` defaults to `.`; from anywhere else it seeds iso9001 into a hub with no `company/org-model` and fails the same way.
Impact: first-run failures on a new site show up as a crash-looping container with the explanation scrolled away in `docker logs`.
Suggested fix: drop `|| true`; on bootstrap failure print the file to edit once (not per SOP), write a `state/.bootstrap-failed` note, and still start the shell with a banner "no release in effect".

## Summary: the three changes with the best payoff

First, make `release` and `bootstrap-release` honest gates: refuse a releaser who is not a policy owner, refuse a proposal whose `base` is not the release in force or whose status is already `released`, and never overwrite an existing digest's index entry (F-1, F-2). Second, make the compiler loud about what it could not read and where: an unparsed sentence or unknown heading inside a grammar section is an error that blocks release, every citation carries the relative path and the file line (frontmatter included), `FrameError` is caught and names the file, and `compile` exits non-zero on errors (F-3, F-4, F-6, F-17). Third, give policy owners a place to see a change: a `proposals` verb plus a `/release/<digest>` page that shows the sentence-level diff against the parent and each change's decisions (who, when, meaning), with the shell re-reading Frames so `/frame` and the "open at line N" links show what was actually released (F-5, F-8, F-15).
