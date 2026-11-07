# Adversarial review, 7 Nov 2026: triage

Five lenses: governance (15 findings), security (13), cogs on qwen3:14b (13), UX (20), reliability (pending).
Owners split by files so fixes run in parallel:

* **GOV**: `runner.py`, `guards.py`, `compile.py`, `release.py`, `track.py`, `kernel.py`, `dms.py`, `app.py`
* **AGENT**: `cogs.py`, `mentions.py`, `agent.py`, `agent_records.py`, `agent_docs.py`, `agent_config.py`, `tools.py`, `wopi.py`, `edits.py`, `office.py`, `review.py`
* **SHELL**: `hubproto/shell/*`

## Blockers

| Finding | Problem | Owner | Fix |
|---|---|---|---|
| governance-1 | Author self-declares "minor", a cog approves, a same-titled human-approved SOP is made obsolete | GOV + AGENT | publish supersedes only the same document's earlier revision (same number or lineage), never by title (GOV); "minor" is checked, not believed: a new document or one with no effective revision is major (AGENT, risk scorer) |
| governance-2 | Dropping a countersignature or deleting a segregation rule is classed as narrowing; one owner releases it | GOV | removing a sod rule, dropping or weakening a countersign, raising caps or removing caps on a criterion are widenings (both pairing and plain removes) |
| governance-3, security-5 | `update_fields` rewrites approved/acknowledged records and in-flight bill amounts; create permission lets a cog update | AGENT | only the "update" permission; only records in a start state (draft, open, returned, failed) and not in a live run past its first Gate; never fields a decision covered |
| governance-4 | After escalation the requester countersigned his own change; escalation and decide don't re-check the countersign role | GOV | a countersign pending keeps its role and exclusions through escalation; decide checks role, owner and author for a countersign |
| governance-8 | Prose-only SOP change releases with zero decisions and rewrites a past release's record in place | GOV | a Frame text change is a reviewable change (one owner); the release digest includes the Frames' content digests so a new text is a new release, never a rewrite |
| security-1 | `start-on` moves any record into a readable folder before any check | GOV | `runner.submit(record=)` checks the record's type matches the process and the starter may read and act on it before anything moves |
| security-2 | Cogs read with their own permissions, not the requester's | AGENT | when acting for a person (mention, task, assign, chat) a cog sees only what both it and that person may read; messages and comments go only to people who may read the item |
| security-3 | Prompt injection through item text steers a cog | AGENT | item text, documents and comments enter the prompt fenced as untrusted data with a standing instruction; with security-2 the exfiltration path is closed |
| security-4 | Forged comment author in a saved .docx becomes a message from that person | AGENT | a new comment is attributed to the person who saved it (the WOPI token's user); a different author in the file is ignored and noted |
| cogs-1 | A reader can get a document edited by @mentioning a cog | AGENT | an edit asked through a cog needs the asker's right to edit that document (same check as the editor) |
| cogs-2 | Questions mentioning "approval"/"release" start processes and make cogs try to sign | AGENT | approve intent needs an explicit request ("please approve", "approve this"), never a question; the reply says when a process was started |
| ux-1 | PR without a supplier fails "no eligible approver" but the status line says it is waiting; Fix-and-retry repeats it | GOV + SHELL | runner reports the failure reason; retry re-runs the checks; the shell says what failed and what to fill in |
| ux-2 | Second policy owner is never told a proposal waits; no proposals list | SHELL | inbox card for each open proposal awaiting my decision; a Proposals page |
| ux-3 | Unrelated drafts merged into one proposal; an approved proposal fails "superseded" | GOV + SHELL | a compile carries forward only the compiler's own draft set (per draft owner); approve and reject per change on the page |

## Majors (by owner)

* **GOV**: governance-5 (SoD compares the named role, not only the run owner), governance-6 (warn-only SoD: processes without a sod sentence get a default "the person who files and the approver differ"), governance-7 (cogs via field routes only for the explicit `assignee` field), governance-10 (`submit(record=)` must not re-approve effective or evidence documents), governance-12 (Track keeps the signed digest; signatures keyed with the hub secret; Track entries hash-chained), governance-13 (a failed judgment review binds for that item version; only people may ask a cog to step in), governance-14, governance-15 (confirm checks who confirms; actions replay as the proposing cog), ux item-request SoD.
* **AGENT**: governance-9 (cogs read the SOP text of the release in force, not the files), governance-11 (edit tools check permission and ownership; versions credited to the cog), cogs-3 (record fields in the cog's context, not only `text`), cogs-4 (reviews cite only what the SOP says; warnings are not failures), cogs-5 (search matches values, not field names), cogs-6 (tool results quote real numbers; finish once), cogs-7 (replace scope: `@@ in section:`), cogs-8 (judgment review judges content, not change size), security zip-bomb (cap uncompressed sizes), security read-only token lock (lock needs write).
* **SHELL**: proposals shown as sentences (`hubproto.settings.sentence`), approve per change; owners told when their item moves; decision cards explain routing the right way round with field labels; cog review findings deduplicated and condition keys in words; messenger not reopening or stealing focus; Ops hotkeys confirm and can be turned off; Ops offer only cogs that follow the item's SOP; phone layout of the form head; CAPA card JSON; Assign without an instruction re-renders the form; edit replies link to the version compare; model picker and new-cog form when no model is servable; privacy: messages, queue titles and agent runs visible only to those involved or who may read the item.

## Minors

Kept in the lens files; fixed opportunistically by the same owners.

## Reliability (15 findings; added when the lens reported)

| Finding | Problem | Owner | Fix |
|---|---|---|---|
| reliability blocker | a cog signs an item that changed while its model was thinking; the decision digest covers fields only | GOV | seen digest taken before the review and checked at decide; decision digest covers documents |
| reliability blocker | paragraph operations remove a whole table, list, section or content control | AGENT | target the paragraph inside the container or refuse with a report |
| crash mid-decision | a second, contradictory signature lands on the same Gate | GOV | decide idempotent and atomic per Gate |
| crash after a version is saved | the cog's edit is applied twice | AGENT | idempotent apply and verify |
| queue restart | finished work repeated; another live worker's task reclaimed; worker thread dies on the first error | AGENT | idempotency keys, leases with heartbeats, a worker that survives and reports errors |
| storage | two processes rewrite messages.jsonl (443/500 lost); a torn line breaks pages; half-written blobs served; one-second WOPI timestamps; backup omits queue.db and agent logs | AGENT (inbox, wopi) + GOV (blobs, backup) | safe concurrent inbox; bad lines skipped; atomic blobs with hash check; version-based conflict checks; complete backup |
| edit engine on real files | formulas turned into constants; ods comments lost; prefixed xlsx unchanged; ods rows moved; non-UTF-8 mangled; "İ" case folding | AGENT | per finding |
| growth | chatter read ~1 s at 100k messages; ~6 s per autosave at 20k events | AGENT (inbox index) + GOV (incremental projection) | indexes and an incremental kernel projection |
