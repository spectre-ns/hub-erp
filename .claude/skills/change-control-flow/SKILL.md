---
name: change-control-flow
description: "How a change order moves through compile, guards, review rounds, decisions, the signed Gate and the release automation, with the function that does each step and the pitfalls. Use before changing or debugging anything in the change-order loop."
---

# The change-order loop

Every configuration change on a hub whose SOPs are documents goes through a change order (CO); `closure.py` refuses
anything else (`ChangeOrderRequired`). The design is `docs/design/change-control.md` (sections 3 to 8, 11, 15); the
user's view is `docs/manual/07-policy-and-configuration.md` (7.9 to 7.11). The module docstring of
`hubproto/changecontrol.py` lists its whole API: read it first.

## Stages

`changecontrol.STAGES = ("preparing", "in-review", "revising", "awaiting-approval", "approved", "released")`, kept in
the CO's `stage` field; `stage_of(co)` reads it (the record state wins once approved or released). `EDITABLE` is the
first three.

| Stage | What happens | Where |
|---|---|---|
| preparing | carry SOP revisions, new or retired SOPs; direct changes from Settings and the editors; form, record-type, workflow, link and retention changes; or a whole JSON document (`configjson.propose`), or a configuration script's output (`scripts.propose`: checked, run once in the script worker, its output carried by `configjson.propose`, the run kept in `config_scripts`) | `eco.add_sop`, `eco.add_new_sop`, `eco.retire_sop`; `changecontrol.add_direct_change`, `rules_to_change_order` (Settings); `configdocs.carry`; pages in `shell/change_order_ui.py` |
| in-review | a **review round**: `request_review` starts the `compile-frame` automation job | `changecontrol.request_review`, `automation.start` |
| revising | answer questions, accept or reject suggestions, request review again | `answer_question`, `accept_suggestion`, `reject_suggestion` |
| awaiting-approval | `send_for_approval` (requester or a document controller) once `send_reasons` is empty: the CO is frozen and sealed, and the change-order process waits at the quality manager's Gate; approvers decide each delta item | `send_for_approval`, `decide_item`, `accept_warning` |
| approved | the Gate is signed (`runner.decide`) only when `approval_reasons` is empty; `automation.on_run_completed` records what was signed (`on_approved`) and starts `release-change-order` | `approval_reasons`, `on_approved` |
| released | the release automation applied exactly what was signed | `automation.STEPS["release-change-order"]` |

## A review round: the compile-frame job

`automation.AUTOMATIONS["compile-frame"]["steps"]` names the steps; `automation.STEPS["compile-frame"]` holds the
functions, in the same order. Each step's work is in `changecontrol` (`round_*`):

1. `c_collect`: the carried drafts as they stand and the direct changes.
2. `c_compile` -> `round_compile` -> `compile_delta` -> `eco.stage` -> `compile.compile_proposal` with
   `hub.extractor(calls=True)`. When the compile-frame committee is available this is
   `llm_extract.CommitteeExtractor`: each member cog (`hub.compile_committee()`, the `senior-cogs` group) reads the
   diff with its own model; a rule goes in only when every member reads it the same way (consensus). Disagreement,
   an unavailable member and a rule-like sentence nobody read become blocking questions for the committee's chair
   (`changecontrol.COMMITTEE_CODES`: `member-unavailable`, `disagreement`, `outside-diff`, `unread-rule`).
3. `c_draft` -> `round_draft`: the qa-manager cog drafts prose for direct changes (`prose_drafting`), checked by a
   round trip (the prose must read back as the change; otherwise a `round-trip` question).
4. `c_guards` -> `round_check`: compile errors stop the round. The guards themselves ran in
   `compile.compile_proposal`: `schema_guard`, `grounding_guard`, `consistency_guard` (with
   `responsibilities_guard` and `change_order_signer_guard`), `scope_guard` (with `committee_guard`), and `drift_guard`,
   which diffs against the release in force and flags each change's `widening` (`_widens`, `_widens_over`).
5. `c_stage`: the delta is staged on the CO (`staged_proposal`).
6. `c_panel` -> `round_panel` -> `suggest_panel`: the quality-assistant cog reads the change-control SOP's "Review
   of changes" section (`review_section`; without a model its sentences are applied literally to `touches`). Members
   whose authority the delta widens (`widened_principals`) are left off; contributors are on it but not asked.
7. `c_queue` -> `round_queue`: cogs get a `co-review` task in the cog queue (`cog_review`); people are told and
   answer with `submit_review`. Committees on the panel: `committee_reviews`, `intervene_review`.
8. `c_tell` -> `round_tell`.

When another release comes into force every open CO is compiled again with no round (`r_restage`; no prose drafted,
no panel).

## Decisions, widenings and segregation of duties

- `decide_item(hub, co, item_id, verdict, by, meaning, accept_warnings=(), override=None)`: approve or reject one
  delta item with a meaning; every warning on it must be accepted; an `override` edits the value and may leave a
  **divergence** between configuration and prose.
- `item_state`: whoever made a change is never its only approver. For an item that is not a widening, a decision
  by one of its `makers` does not count. A widening needs two policy owners, at least one of whom contributed
  nothing to the CO; a maker who is a policy owner may be the other one. Decisions made before the baseline changed
  are `stale` and do not count.
- A removal that a change supersedes is that change's other half: `half_of` points at the add, `halves()` finds
  them, `state_in` gives the half its change's state. Never decide a half on its own.
- Who may not sign: `may_not_sign` = `revision_authors` always, plus every `contributors` name unless
  `independent_approval` holds (every decided item approved by someone who contributed nothing). The change-control
  SOP's `SOD_SENTENCE` keeps them off the Gate; the release re-checks it.
- `approval_reasons` (empty means the Gate may be signed) adds to `send_reasons`: frozen and unchanged since sent,
  `verify_delta`, every item decided, warnings accepted, no two approved items on one rule slot (`UNIQUE_KINDS`),
  blocking questions answered, the state map, and `validation.requirements` (the IT SOP's risk classes and the test
  evidence they need: `validation.classify`, `record_evidence`).

## The release: release-change-order

Steps (`automation.STEPS["release-change-order"]`): `r_check` (approved, signed by a person, `eco.check_approval`,
`eco.release_problems` before anything is in force), `r_revisions` (`approve_revisions`), `r_frame` (`apply_delta`,
`eco.activate_frame`, `record_release`), `r_state_maps` (`runner.apply_state_map`), `r_publish`
(`eco.publish_revisions`), `r_forms` (`formlayout.activate`), `r_config_docs` (`configdocs.activate`: links and retention), `r_record_types` (`recordtypes.activate`), `r_training`
(`eco.assign_revision_training`, `training.publish_matrix`), `r_divergences` (`raise_divergence_requests`),
`r_related` (`find_related`), `r_restage` (every other open CO compiled again), `r_tell`.

`automation.MODEL_STEPS` names the only steps in which a model takes part (reading prose, drafting prose,
suggesting the panel, finding related changes by meaning). Every step that changes the system is deterministic and
applies what was signed.

## Pitfalls

- **Pages never wait on a model.** Model work runs in automation jobs and the cog queue. In tests, the CLI and the
  library the queue is `inline` (worked at once); in a served hub it is a worker thread, so wait for the queue
  (skill `dev-hub`) before reading a round's result.
- A step refuses with a `ValueError` carrying a sentence a person can act on; the `_cc` wrapper turns it into a
  `StepError`, stops the job (`some-errors` or `failed`, retryable with `automation.retry`) and marks the round
  failed. Keep refusals as sentences, never codes.
- `AUTOMATIONS[...]["steps"]` (titles) and `STEPS[...]` (functions) are parallel lists, and `MODEL_STEPS` is keyed
  by automation name, then step title (`MODEL_STEPS["compile-frame"]["Compile the Frame"]`): renaming or reordering a step without the other list breaks the job or silently drops its model note.
  If a step starts calling a model, add it to `MODEL_STEPS`.
- Anything that changes what a frozen CO carries sends it back to revising with its decisions void (`sync`,
  `changed_since_sent`, `_back_to_revising`). A test that edits after sending must expect that.
- A stand-in hub has no compile-frame committee (`Hub.compile_committee` never counts a stand-in): rounds read
  sentence shapes only. Test the committee with `call=` fakes (skill `write-hub-test`), not a served hub.
- A cog never signs a change order and never reviews a change that widens its own authority; keep both true in
  any new path (`r_check`, `round_panel`).
- A configuration script (issue #34) is never run by the release: the release applies what its output carried. Keep
  every release step free of `hubproto.scripts`; `config_scripts` is part of `content_digest` (key `scripts`, one of
  `OPTIONAL_KEYS`), so a run recorded after a round or after sending is caught. Only a person submits one
  (`scripts.refuse_cog`); tests: `tests/test_config_scripts.py`.
- Tests for this loop: `tests/test_change_control.py`, `test_change_control_integrity.py`,
  `test_change_control_documents.py`, `test_change_order_pages.py`, `test_widening.py`, `test_automation.py`,
  `test_committees.py`, `test_committee_pages.py`, `test_validation.py`, `test_eco.py`.
