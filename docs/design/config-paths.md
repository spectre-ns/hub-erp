# Paths that change configuration (change control v2, workstream 4: closure)

Principle 3 of `change-control.md`: **no configuration change without a change order (CO)** once the SOPs are
documents (`sopdocs.converted`). This note lists every path in the hub that can change the configuration in force,
what it changes, and what happens on it after conversion. The guards live in `hubproto/closure.py` and are called from
`Hub.compile`, `Hub.review`, `Hub.release` and `Hub.frame_draft`, so the command line, the shell, MCP and the cogs' tools
all get the same answer. A refusal is a `closure.ChangeOrderRequired` (both a `CompileError` and a `ValueError`) whose
text ends with the sentence:

> Configuration changes only through a change order: open one under Quality > Change orders.

**The first release converts.** A hub's first release (however it is made: `bootstrap-release`, the entrypoint's
first start, CLI `release`, the shell) is followed at once by the conversion (`closure.after_release` ->
`sopdocs.convert_after_release`), so on a new hub the first release is the only configuration released without a CO.
When the conversion is blocked (no change-order process in the release, or SOPs that would not read back exactly)
`bootstrap-release` exits 1 with why, `status --check` exits 1 ("the SOPs are not documents yet, so changes are released
without change orders"), and running `bootstrap-release` again finishes it.

Exempt everywhere: **the first release** (bootstrapping), **a hub that keeps the Frame files** — an explicit opt-out
recorded before conversion with who and why (`bootstrap-release --keep-frame-files REASON`,
`sopdocs.keep_frame_files`; `status` shows it on every run; `sops convert` ends it), used for work on the Frames
themselves and by the test suites through `tests/conftest.py`'s `release_all` — and **the release automation acting for
an approved CO** (`by == "change-order:<CO>"`, CO record state `approved` or `released`). A hub released before this
rule that never converted and did not opt out is reported by `status --check` (exit 1); it is not converted without
someone asking (`bootstrap-release` or `sops convert`).

## The policy (rules, workflows, members, roles, folders, cog profiles, personas)

| Path | What it changes | After conversion |
|---|---|---|
| `Hub.compile(save=True)` without `sop_drafts` (CLI `compile`, `bootstrap-release --force`, shell `/frame-propose`, `/text-save` "propose", `_recompile_drafts`) | makes a proposal meant for release | **refused** (`closure.check_compile`). A preview (`save=False`: `/frame-check`, `eco.preview`, `sops convert`'s check) is allowed: it changes nothing |
| `Hub.compile(save=True, sop_drafts=...)` | compiles the SOP revisions a CO carries | **goes through a CO**: this is how `eco.stage` (compile-frame automation) stages the CO's Frame. A proposal compiled this way but not marked by `eco.stage` can never be reviewed or released |
| `Hub.review` (CLI `review`, shell `/proposal-review`, `bootstrap-release`) | records policy-owner decisions | **refused** for a proposal no CO staged (`closure.check_review`); **allowed** on a CO's staged proposal (the policy owners sign its widenings) |
| `Hub.release` (CLI `release`, shell `/proposal-release`, `eco.activate_frame`) | brings a proposal into force, writes Frame files | **refused** unless the proposal was staged by a CO, `by` is `change-order:<that CO>` and the CO is approved (`closure.check_release`). A policy owner cannot release a CO's staged proposal on its own: it comes into force with the CO's revisions |
| Proposals open at the moment of conversion | could still be decided and released | **marked stale** at conversion with the CO sentence (`closure.on_conversion`) |
| Open proposals after a release (`Hub._rebase_open_proposals`) | were recompiled against the new release | a proposal no CO staged is **marked stale** with the CO sentence; a CO's staged proposal is **left to its CO** (the release automation restages every open CO), so no stray, unreleasable proposal is made |
| `Hub.frame_draft` (shell `/frame-draft`, a cog's `propose_policy_change`) | starts an edit of a Frame | **refused** with the CO sentence (the Frame is derived from the SOP document; shell answers 409) |
| `Hub.settings_propose` (Settings page, a cog's `propose_setting_change`) | rule changes as data | **goes through a CO** (`settings_to_change_order`: SOP revisions on the person's settings CO) |
| `Hub.propose_cog_model`, `propose_new_person`, `propose_new_cog` (People page, cog profiles page, a cog's `propose_cog_model` / `propose_member`) | members, roles, cog profiles | **goes through a CO** (`rules_to_change_order`) |
| `eco.add_sop`, `eco.remove_sop`, revisions edited in Collabora | SOP, policy, cog-profile, persona prose | **goes through a CO** (the CO carries the revision; approved with it) |
| CLI `seed <profile>` | copies a profile's Frame files into `frames/` | **refused**, exit 1 (the files would not be used; a new SOP comes in on a CO) |
| First release (`Hub.release` with no release before; `bootstrap-release`; entrypoint first start) | the first policy in force | **released, then converted at once** (`closure.after_release`), unless the hub keeps the Frame files on the record |
| `bootstrap-release --keep-frame-files REASON`, `sopdocs.keep_frame_files` | keeps the Frame files as the source (no conversion) | **refused** once converted; before, recorded with who and why and shown by `status` |
| CLI `sops convert` | makes the documents the source | the switch itself; now **refuses** (exit 1) what the shell already refused: a conversion that would change rules (round-trip problems) or leave no change-order process. Running it again says "already" (exit 0) |
| Frame files on disk (`frames/**`) | were the source | **not used** (the documents are the source); `status --check` reports a changed, missing or new file, says it is not used, gives the CO sentence and exits 1 |

## Data model and presentation

| Path | What it changes | After conversion |
|---|---|---|
| `config/record-types.json` on disk (read by `Kernel` at start) | record types, fields, states, retention, sensitivity | **not used**: the record types in force are `state/record-types/active.json` (the file as of the release in force, plus record-type changes released by COs). A file that differs is **reported** by `status --check` (exit 1) and `record-types status`, not used. What it adds can be carried on a CO (`record-types carry CO --from-file`) |
| `recordtypes.carry` / `uncarry` (CLI `record-types carry`) | record-type changes on a CO | **goes through a CO**; destructive changes refused (rename, retype, removing enum options, removing a field or type with data) |
| `recordtypes.activate(hub, co, digest)` | brings a CO's record-type changes into force | **exempt** for an approved CO (refused otherwise); idempotent |
| Form layouts (`formlayout.save_draft`, `carry`, `uncarry`, `activate`) | form layouts and custom fields | already **through a CO** (drafts are personal; `activate` is a release step, and now refuses a CO that is not approved) |
| `config/links.json`, `config/retention.json` | link vocabulary, retention classes | **reported only**: `status --check` says when either differs from what the release in force recorded (exit 1), but they are still used. Not under change control yet (left undone) |
| Controlled templates (`Hub.import_templates`, CLI `templates import`, shell `/template-revise`) | template documents | **through document control** (each revision is approved by a person), not yet carried on a CO. Left for workstream 1 (a CO carrying revisions of any controlled document) |
| Library folders (`LibraryFolders.add`, shell) | subfolders, top-level folders | **exempt**: a subfolder takes its parent's rights and a top-level library folder takes the rights of an existing policy folder, so nobody's rights change; a folder with rights of its own is a policy folder, added in the org model on a CO |

## Installation and operations (outside the release: IT change control)

| Path | What it changes | After conversion |
|---|---|---|
| Environment (`HUBPROTO_*`: models, extractor model, cog queue mode, agents kill switch, CAD worker, ports, secrets) | installation | **exempt** (IT SOP), as section 2 of `change-control.md` says |
| CLI `init`, `kernel.seed()` | sample suppliers, periods, stock locations | **exempt**: data, not configuration |
| CLI `backup` / `restore` | the whole state | **exempt** (IT backup and restore): it restores the configuration in force, not a change. `backup` now also copies `state/forms` and `state/record-types`, without which a restore silently dropped the form layouts and record types in force |
| Code (`hubproto/**`, cog registry, automations, the sentence grammar) | software | **exempt**: a software change (IT change control); a code update that ships a new record-types file is reported and carried as above |
