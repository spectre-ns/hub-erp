# UX review, 2026-11-07: real people doing real jobs

Lens: four people with day jobs, walking complete jobs end to end in a sandbox hub
(`hubproto init` + `bootstrap-release`, stand-in cogs, shell on :8093), plus read-only GETs
against the live hub on :8080 to see real qwen3:14b answers. The personas: dave the buyer,
carol the quality manager, alice the finance clerk and author, bob the controller and policy
owner. The jobs: raise a purchase request; file and disposition a nonconformance (NCR); draft
a controlled document, have a cog edit and approve it, countersign it; change a policy and
release it; give a cog a task, assign an item to a cog, follow its queue; ask /why.

Note: other committee members shared the same sandbox port, so some records (DOC-0006,
DSN-2026-0001, proposal #3) are theirs. Where a finding rests on one of them it says so.

**What held from the last triage:** every POST now ends on a page with a status line; the
inbox is a compact queue with one open card; Reject is a separate button with its own
reason; a refused decision keeps the typed rationale; pickers replaced most locators on start
forms; money and dates are mostly formatted.

**What regressed or never landed:** the phrase "Fix the record and retry the run" is back in
notifications for failures that no field can fix (finding 5). Locators (`rec:ncr:NCR-2026-0002`),
state slugs (`awaiting-verify-the-actions`), raw field names (`disposition_rationale`,
`review_passes`), Python lists and floats (`['supplier_approved', 'item_known']`, `1000.0 USD`)
and ISO dates (`Due 2026-11-17`) show up in user-facing text again (findings 4, 9, 17).

---

## 1. Blocker: a purchase request without a supplier ends in a dead end, and the hub says it worked

**Persona and task:** dave, raising a purchase request.

**Steps:** `/?as=dave` > Raise a purchase request > Title, Item ITEM-00001, Quantity 500,
Unit cost 0.02, leave Supplier on "choose…" (it is not marked required) > submit.

**What happened:**
- The redirect's status line says *"Filed PR-00001; waiting for purchasing-assistant at
  Approve the purchase."* The same page's header says **Failed**: *"Stopped at score the risk:
  no eligible approver: no person or cog holds any route for this item"*. In the Fix and retry
  form that message sits in red under the **Item** field, which has nothing to do with it.
- The real cause is two steps away and nowhere on the page in plain words. The cog declined
  because no supplier was named (`supplier_approved` not met). Dave, the only person who holds
  the buyer role, was skipped because the requester and the approver must differ.
- Dave sets Supplier = Acme Metals and presses **Fix and retry**. The status line says
  *"Retried PR-00001 with supplier changed; it now waits for purchasing-assistant"*. It does
  not: the retry resumes at "score the risk" and never re-runs "check the supplier", so the
  record still shows *Supplier approved: no / Supplier note: no supplier named* and fails again
  with the same message. History reads *"dave retried from ?: added supplier"* (a literal `?`).
- The Reason text he typed ("Prototype build of 10 boards") is gone from the retry form.
- The only way out is to find **Back to the start**, give a reason, and then press the Ops
  button **Request the purchase** (`o 1`). After that the PR was ordered at once. No ordinary
  user would find that path.
- The Frame itself says *"A purchase request names an item, a quantity and a supplier"*
  (purchasing-policy line 14), yet the form treats supplier as optional.
- In a fresh hub the Item picker is empty, with no hint to use "Request an item" first. The
  refusal reads *"A purchase request needs item before it can be filed."*

**Why it hurts:** this is the buyer's most common job. Here it fails silently, the hub tells
him it succeeded (twice), and the obvious fix makes no difference.

**Suggested fix:** make Supplier required at start when the policy says the request names
one. Retry should re-run every step whose inputs changed (or simply restart from the first
automatic step). Write the stop reason as the real chain: "purchasing-assistant could not
approve: no approved supplier named. You cannot approve your own request, and nobody else
holds the buyer role. Name an approved supplier and retry, or ask bob to add a buyer." Never
put a routing error on a field. Make the status line read the run's actual state after the
POST. Keep every field on the retry form. Give an empty picker a "none yet: Request an item"
link.

## 2. Blocker: the second policy owner is never told that a proposal waits for her

**Persona and task:** bob proposes a policy change; carol must approve it (a widening needs
two owners).

**Steps:** bob: Policies > purchasing-policy > Open as a document > Edit this policy >
raise "cog purchase limit" to 1,000 USD > Save and check the change > Propose for release >
Approve all. Then carol: Home, Inbox, Policy versions, Policies, the Frame page, the Frame's
document page, People and roles.

**What happened:** carol sees nothing on any of those pages: no inbox card, no notification,
no "1 proposal open" on Policy versions, no banner on the Frame page. The shell has no
proposals list at all (only `/proposal/<id>`; the CLI has `hubproto proposals`). The proposal
page says it is *"reviewed by the policy owners bob, carol"*, but carol can only reach it if
bob sends her the URL. Dave, the purchasing-policy maintainer, is not told either.

**Why it hurts:** the policy change job cannot be finished inside the product. It depends on
someone pasting a link into another chat tool.

**Suggested fix:** put an inbox card ("Review policy proposal #2: purchasing-policy, 3
widenings") for every policy owner whose decision is still needed. Add a Proposals section
to Policy versions (open, superseded, released). Show a banner on the Frame page and its
document ("a change to this policy is proposed: #2"). Notify the Frame's maintainer.

## 3. Blocker: unrelated drafts are merged into one proposal, and the reviewed one cannot be released

**Persona and task:** bob releasing the change that he and carol both approved.

**Steps:** after both approvals, proposal #2 shows *"Every change has the approvals it
needs"* and a **Release** button. Meanwhile someone (another reviewer, acting as bob) proposed
an org-model draft, DOC-0015. bob presses Release.

**What happened:**
- The release is refused with an error page: *"proposal 4b98050dc706 is superseded; compile a
  new one"*. That is a raw hash, an instruction ("compile") that only the CLI can carry out,
  and no link to the proposal that replaced it.
- The replacement, #3, bundles bob's six approved changes with an unrelated widening:
  *"change member dave (person) roles ['payables-reviewer', 'staff', 'policy-owner',
  'controller']"*.
- The review form offers only **Approve all 2 change(s)** and **Reject all**. bob cannot
  release his approved threshold change without also approving a buyer becoming a policy
  owner and controller, and "Reject all" does not say whether it also throws away his own
  approved changes.

**Why it hurts:** two owners working on different Frames on the same afternoon (a normal day)
block each other. The design pushes a reviewer toward approving a privilege escalation just
to ship his own change.

**Suggested fix:** one proposal per draft (or per Frame), released independently when they
do not conflict. When one really supersedes another, the old page says "superseded by #3"
with a link and carries the decisions forward. Allow a decision per change, with a clear
choice between "release only what is approved" and "hold back the rest". Never show a
"Release" button that the server will refuse.

## 4. Major: what a policy change does is shown in compiler output, not in sentences

**Persona and task:** bob (controller, not a developer) checking and reviewing a change.

**Steps:** Save and check the change (`/frame-check/...`), then the proposal page.

**What happened:** the change table reads *"change cog:purchasing-assistant may approve
purchase-request up to 1000.0 USD when ['supplier_approved', 'item_known'] (max_amount: 500.0
-> 1000.0)"*, *"add route purchase-request/approve when amount < 1000.0 to
cog:purchasing-assistant"*, *"superseded by the change above: remove threshold
cog-purchase-limit = 500.0 USD"*. The approvals box lists *"change add:6c2e7223f7c47416 needs
2 approval(s)"*. One sentence edited in three places becomes six rows. The Check page also
lists six release-wide warnings that have nothing to do with this edit (SoD notes on audits,
CAPAs and tasks, and *"required_by_state key 'in-review' is not a state any process reaches"*).

**Why it hurts:** the people who sign a policy release cannot tell what they are signing. The
text diff underneath is clearer than the "changes" table, which is the part that carries the
signatures.

**Suggested fix:** one row per edited sentence: "purchasing-assistant may now approve purchase
requests under **1,000 USD** (was 500 USD) when the supplier is approved and the item is known.
Widening: two owners." Fold the "superseded by the change above" rows into their pair. No ids
in the UI. Show only the warnings this draft introduces, with the rest behind "other warnings
in the release".

## 5. Major: when the only quality manager authors a document, the countersign step is a dead end

**Persona and task:** carol (the only person with the quality-manager role) authoring a
document that qa-manager reviews. Observed on DOC-0006 in the shared sandbox (filed by
another reviewer as carol); it follows from the org model.

**Steps:** `/inbox?as=carol`, the "Returned or stopped, mine" section.

**What happened:** *"Stopped at score the risk: qa-manager approved it, but no quality-manager
who may approve it (and did not prepare it) can countersign"*, followed by a full **Fix and
retry** form (title, type, change type, text, JSON). No field change can fix a missing person.
The notification says *"Fix the record and retry the run, or cancel it"*, the exact phrase the
last triage removed (first-time-user-1).

**Why it hurts:** the person who most often writes quality documents cannot get her own
documents released. The page tells her to edit fields, which wastes her time.

**Suggested fix:** recognise "no eligible signer" as an organisational stop, not a data
stop. Say who could fix it ("a policy owner can add a second quality manager, or the
document-control SOP can name the management representative as countersigner") and hide
the field form. The compiler could also warn at release time when a countersign role has
only one holder who is also an author.

## 6. Major: a buyer approves his own item request; /why says "allowed" when the hub will refuse

**Persona and task:** dave requests a new item, then asks /why about approving his own PR.

**Steps:** Request an item (10k resistor) > the redirect lands on *"Approve the item: waiting
for you"* > dave approves his own request. Then `/why?principal=dave&action=approve&object=purchase-request&folder=Purchasing/Requests`.

**What happened:**
- Item request has no segregation of duties: the requester is routed his own approval and
  signs it (ITEM-00001 is active). The purchase request, two clicks later, enforces SoD and
  skips him. The two inconsistent rules sit next to each other in the same Frame.
- /why answers **allowed** (*"Buyers may approve purchase requests"*). It does not mention
  line 70 (*"The requester and the approver of a purchase-request must be different
  people"*), which is exactly why his PR stalled in finding 1.
- /why is not in dave's navigation at all (only policy owners see the Policy rail). Dave
  reaches it only through the "Why can I do this?" disclosures, which explain what he *can*
  do.

**Why it hurts:** "why can't I approve this?" is the question people actually ask. /why
gives a confident wrong answer and is hard to find.

**Suggested fix:** let /why take an item ("why can't I approve PR-00001?") and evaluate SoD,
routing and approval criteria, not only the permission sentence. Put a "Why not me?" link on
any item that is waiting on someone else. Put /why in everyone's rail. Add SoD to
item-request, or have the compiler warn about a route whose only holder may also file.

## 7. Major: drafting a controlled document has no draft stage, and cog edits land with no diff or undo

**Persona and task:** alice drafting a new procedure and getting a cog to tighten the wording.

**Steps:**
1. Path A: Home > **Propose a document change** (the only "new controlled document" entry)
   with text in a plain textarea > submit.
2. Path B: Documents > folder > New document (.md) > Edit > Save, then chatter *"@quality-assistant
   please change 'shall' to 'must' and delete the paragraph 'Old line to delete.'"*, then
   **Send for approval** (`o 1`).

**What happened:**
- Path A routes the document to bob and carol for approval the moment it is filed (DOC-0004,
  revision A.0). There is no "draft, review with a cog, then send" step. The page then says
  *"a controlled document changes only through a new revision"*, so asking a cog to edit it
  is pointless, but the chatter and Ops still invite exactly that.
- Path B: the cog's change is written straight into the document as a new version. The chatter
  shows *"the hub: version added version 3"* and the reply *"Changes: insert 3 line(s) at the
  start"* (live hub: *"Changes: replace "shall" with "must"; insert 1 line(s) at the end."*).
  There is no diff, no "show me where", no undo, and no accept or reject step.
- "Send for approval" never asks minor or major. The document went to approval as **major**
  (which decides whether qa-manager's signature is final) without alice choosing.
- Revision schemes differ between the two paths (A.0 against 1.2) for the same class of
  document.

**Why it hurts:** authors need to see and approve what a cog did to their text before it goes
to signers. In a controlled document system that is the job itself.

**Suggested fix:** name the entry "New controlled document" and file it as a draft with a
"Send for approval" step. Show each cog edit as a proposed change (diff, Accept and Undo) or
at least link "version 3" to a compare view. Ask for the change type when sending for
approval. Use one revision scheme.

## 8. Major: people are not told when their own item moves, and a cog says "approved" when it has not

**Persona and task:** alice (author) and carol (NCR owner) waiting on others.

**Steps:** `@qa-manager please approve` on DOC-0008, then carol countersigns. Separately, bob
dispositions carol's NCR-2026-0001 (scrap) and a CAPA opens.

**What happened:**
- qa-manager's reply opens with *"I reviewed DOC-0008 and approved it: approved on its review,
  under its approval criteria: its review passed (evidence DOC-0010): Stand-in review…"*.
  The document is still **Awaiting**. Only the last clause says it waits for carol.
- Alice's notification reads *"Decided automatically: qa-manager approved [DOC-0008]"*, cut off
  mid-word (*"Segregation of duties: checked against"*).
- When carol countersigns and DOC-0008 becomes **Effective**, alice gets nothing.
- When bob closes carol's NCR with "scrap" and a CAPA is opened, carol's inbox shows
  *Notifications 0*.

**Why it hurts:** owners have to poll their items. "Approved" from a cog reads as done, when
the item actually needs a person's countersignature.

**Suggested fix:** notify the owner on every state change made by someone else ("carol
countersigned DOC-0008; it is now effective"). Have the cog lead with the outcome ("Signed;
waiting for carol to countersign") and put the review behind a link. Never cut a notification
off mid-sentence.

## 9. Major: decision cards explain routing backwards and in field names

**Persona and task:** bob dispositioning an NCR; carol countersigning a document.

**Steps:** `/inbox?as=bob&open=<NCR run>`; `/inbox?as=carol&open=<DOC-0008 run>`.

**What happened:**
- *"the quality-assistant cog may approve when the risk is low ([ncr-sop line 40]); here, not
  met: the risk is low"*. The card states the condition where it should give the fact (the
  risk is **medium**).
- On the countersign card: *"the doc-reviewer cog may approve when the change is minor; here,
  not met: the change is minor; the qa-manager cog may approve when review_passes and the
  change is minor; here, not met: the change is minor; …"*. That is three clauses saying the
  opposite of the truth, plus a raw identifier.
- The countersign card never says that qa-manager has already signed, or what it signed.
- *"To approve, also give: disposition, disposition_rationale."* and *"To approve, also give:
  root_cause."* use raw field names.
- Policy sentences are quoted with their bad grammar: *"At Disposition the nonconformance in
  nonconformance go to the management representative."*
- The disposition is signed with a button labelled **Approve NCR-2026-0001**, even when the
  disposition is "scrap". The Reject text says *"closes it as rejected; nothing more can be
  done with it"* on documents and CAPAs, where an author would expect the item to come back
  for revision.

**Suggested fix:** use "Why you: the cog could not decide because the risk is **medium** (it
may only when low)" and "qa-manager signed on 7 Nov (review DOC-0010); you countersign".
Use labels, not keys. Label the button "Sign disposition: scrap". For documents and CAPAs,
offer "Send back to the author" next to a final Reject.

## 10. Major: cog reviews read as "fail" with every finding twice and internal condition names

**Persona and task:** bob asking cogs to review a purchase request (live hub, PR-00069, qwen3:14b).

**Steps:** `http://localhost:8080/record/rec:purchase-request:PR-00069?as=bob`, the chatter.

**What happened:** every review prints *"Review of PR-00069 by quality-assistant: fail."*,
then the findings list, then the model's paragraph, then **the same findings list again**,
then *"Verdict: fail"*. The findings are *"condition risk_low does not hold"* and *"condition
risk_not_high does not hold"*. "Fail" is shown for a request that is simply high value and
belongs with a person. DOC-0444 shows the same duplication from doc-reviewer, with five
warnings and the verdict "fail". The Ops offer "Ask ap-reviewer / doc-reviewer /
quality-assistant to review" on purchase requests and NCRs, so cogs that do not follow
purchasing-policy review purchases.

**Suggested fix:** list the findings once. Use words ("the risk is high, so a cog may not
approve this; it needs a person"). Separate "not mine to approve" from "something is wrong"
(three outcomes: looks right / needs a person / problems found). Offer only the cogs whose
SOPs cover the item type.

## 11. Major: after the messenger is opened once, it reopens on every page and takes the keyboard focus

**Persona and task:** carol, a keyboard user, chats with a cog once and then works normally.

**Steps:** open "Chat with a cog", then go to any other page.

**What happened:** `messenger.py` stores `mx.open=1` and calls `open(true)` on every load,
which focuses the messenger textarea after 30 ms. From then on every page starts with focus
in the chat box. The skip link, the page heading and the `o` hotkeys (which ignore key presses
inside text fields) do nothing until the user clicks away, and screen readers start every page
in the chat. Once a model is configured, a message to it blocks the panel for the full model
time (around 70 s for qwen3:14b) while the dots spin. If the user navigates away, the answer
only shows up when the panel is reopened.

**Why it hurts:** this breaks keyboard and screen-reader use on every page, for anyone who
has used the feature once.

**Suggested fix:** remember the panel as open but do not move the focus on page load (focus
only on a click). Queue chat like mentions, so the answer arrives in the panel or as a
notification.

## 12. Major: Ops hotkeys fire a POST at once, and Ops show cogs that do not belong

**Persona and task:** anyone on a record page.

**Steps:** on NCR-2026-0001 (Closed), press `o` then `1`, or `o` then `q`.

**What happened:** `o 1` **starts a new process** on the item at once (the primary-coloured
button on a *closed* NCR is "Open the nonconformance"). `o q`/`w`/`e`/`r`/`t` send a review
request to ap-reviewer, doc-reviewer, purchasing-assistant, qa-manager or quality-assistant in
that order: letters with no mnemonic, assigned by position. There is no confirmation and no
way to turn the shortcuts off (WCAG 2.1.4 character key shortcuts). The record header carries
eight buttons and six Ops before the title.

**Suggested fix:** make the hotkeys move focus to the button (Enter confirms), or confirm before
posting. Add a setting to turn them off. Show only the Ops that make sense in the item's state
and only relevant cogs. Hide the primary "start" Op on closed items.

## 13. Major: at phone width the item is about 1,000 px down, and the status bar is cut off

**Persona and task:** carol checking an NCR on her phone on the shop floor.

**Steps:** a 390 px wide screenshot of `/record/rec:ncr:NCR-2026-0001?as=carol`.

**What happened:** the app sub-navigation (12 links), the "+" start links, the rail (11 links)
and the eight header buttons stack above the content. The NCR number appears only about
1,000 px down. The workflow status bar is clipped at the right edge, so the current state
("Closed") is not visible. Hotkey hints (`o 1`, "press o then the key") are shown on a device
with no keyboard.

**Suggested fix:** under 600 px, collapse the app menu and rail behind the grid button, put the
number, title and state chip first, wrap or scroll the status bar so the current state is
always in view, and hide hotkey hints on touch devices.

## 14. Major: the CAPA card prefills raw JSON and skips straight to "Verify the actions"

**Persona and task:** bob, owner of the CAPA that was opened automatically from carol's NCR.

**Steps:** `/inbox?as=bob&open=<CAPA run>`.

**What happened:**
- The new CAPA goes straight to **Verify the actions**, before any action has been planned
  or done. There is no "plan" or "implement" step, and nobody is asked to do the work.
- The "Actions (comma-separated)" field is prefilled with
  `[{"action": "Contain and investigate", "owner": "bob", "status": "open"}]`. Editing it as
  comma-separated text will mangle it.
- "Root cause method" shows *5-whys* in the item panel, but the select on the same card says
  "choose…".
- The header says *"Corrective action (CAPA) started by bob"* and the history says *"bob
  created the corrective action"*. bob did neither; capa-opener did, from carol's NCR.
- *"It is stored with your signature and shown to bob"*: bob is shown his own signature.

**Suggested fix:** an actions editor with one row per action (action, owner, due, status). A
plan or implement step before verification. Preselect stored values. Credit the automatic
opener ("opened automatically from NCR-2026-0001"), not the person who triggered it.

## 15. Minor: live refresh updates the chatter but not the header, and announces nothing

**Persona and task:** alice waiting for a cog's answer on DOC-0008.

**What happened:** only `#statusbar`, `#chatter-count` and `#chatter-log` carry `data-live`.
When a cog approves or edits, the status bar changes but the header chip ("Document Open"),
the Details panel, the text preview and the Versions table stay stale. The page then shows
two different states until it is reloaded. The swapped chatter log has no `aria-live`, so
screen-reader users are not told that the answer has arrived. The polling itself works:
2.5 s while a task is open, 12 s otherwise, 20 s in a background tab.

**Suggested fix:** mark the header, details, text and versions as live too, or reload them
when the record's state or version changes. Add a polite live region that says "qa-manager
answered".

## 16. Minor: a missing Assign instruction ends on an error page, and Assign also changes the Gate

**Persona and task:** carol assigning NCR-2026-0001 / CAPA-2026-0001 to qa-manager.

**What happened:** with the instruction empty, the hub answers with a full error page (*"Not
done: Say what qa-manager should do with NCR-2026-0001"*). The form is not re-rendered, so the
choice is lost. The textarea is not marked required, and says only "(a cog needs this)". With
an instruction, assigning the CAPA also adds qa-manager as a **co-assignee at the Gate**
(history: *"carol assigned it to bob, qa-manager"*; the cog reports *"waiting for bob,
qa-manager"*), which the form never mentions. The cog's report uses the raw state
`awaiting-verify-the-actions`.

**Suggested fix:** re-render the Assign form with the error next to the field, and mark the
field required as soon as a cog is chosen. Say on the form what assigning does to the Gate
("qa-manager may also decide the pending step within its criteria").

## 17. Minor: the model picker and new-cog form give no usable choice and no explanation; the profile is developer-facing

**Persona and task:** bob or carol changing a cog's model, or adding a new cog.

**What happened:**
- Sandbox (no Ollama): on `/user/qa-manager`, the current model (ollama/qwen3:14b) is not in
  the list. Every option is disabled ("needs an Anthropic key"), yet **Propose the change** is
  shown. On People, the new-cog form's required Model select has only disabled options, so it
  can never be submitted, and the page does not say so. On the live hub the picker is good:
  the current model is checked, unavailable ones are disabled, sizes are shown.
- The new-cog form's "Procedures it follows" offers `org-model`, `cog-profiles` and eleven
  `wi-*` work instructions with no descriptions. The roles have no descriptions either.
- The cog profile shows Kind *context*, Version *0.1.0*, Reaches *kernel:records,
  kernel:documents*, Tasks *review, inbox, review-document…*, Files records through *-*,
  Reviews *-*, and a raw org-model line under the role chips
  (*"- qa-manager (cog): quality-manager, reviewer, author, staff"*). "Recent runs" titles a
  run by its internal prompt (*"You are assigned CAPA-2026-0001 (capa: …"*). The agent-run page
  shows raw JSON tool calls.

**Suggested fix:** when no model is usable, replace the form with one sentence ("No model is
reachable from this hub; ask an admin to configure Ollama or an Anthropic key") and list the
current model. Give SOPs and roles their one-line descriptions. Move technical fields behind
"Technical details". Title runs by what was asked.

## 18. Minor: one thing has many names, and machine text leaks into the UI

Examples, all from the sandbox:
- A cog is "cog" (People, Acting as), "automatic reviewer" (the inbox message form's To
  list), and "Agent-run: qa-manager finished" (notifications). The Steps table's "Automatic
  reviewer" column lists code workers (record-filer, risk-scorer). "Gate", "Track" and
  "digest" still appear in README-driven text. The triage settled on one name per thing.
- The message form's "Via" select offers **dm, dm, email** (a blank value labelled "dm").
- *"You are buyer payables reviewer staff."* (home, no commas). *"1 decisions in 30 days"*
  (People).
- Notifications use *"Due 2026-11-17"* while cards use "17 Nov 2026".
- The queue shows *"take its turn at a Gate on NCR-2026-0002: Disposition the nonconformance:
  rec:ncr:NCR-2026-0002"*.
- The Policies page shows `{{MANAGEMENT_REPRESENTATIVE}}` as the maintainer and *"Quality
  manual of {{COMPANY_NAME}}"*.
- Record details show "Mpn", "Lead time days", "Stock 0.0", "Provenance {}"; an item shows
  "Approved manufacturer: yes" before anyone approved it.
- Run history says *"retried from ?"*.

**Suggested fix:** one glossary applied everywhere ("cog", "approval", "policy version"). No
blank option labelled like a real one. Format lists, counts and dates through one helper.
Render templates in Frames at install time, or reject them at compile time.

## 19. Minor: the Steps table puts the approval on the wrong step

**What happened:** on every run page the approval shows against the step before it: *"Score
the risk: waiting for approval"* while the header says "Waiting at **Approve the purchase**" /
"**Disposition the nonconformance**". Failures are reported as *"Stopped at score the risk"*
when the risk score passed and the approval routing failed.

**Suggested fix:** give the Gate its own row ("Approve the purchase: waiting for
purchasing-assistant") and name the Gate in stop messages.

## 20. Minor: the document library's "Recent documents" shows raw markdown and digests, and the home page is slow

**What happened:**
- `/files` previews snapshot documents as raw source: *"# NCR-2026-0001 (ncr) State:
  **closed** … Created 2026-11-07T20:54:07+00:00 … Policy release: `e3fb5b99aad2c079` …
  - **capa**: rec:capa:CAPA-2026-…"*.
- On the live hub, `/` takes about 1.8 s and `/inbox` about 0.9 s. Record pages are 0.4 to
  0.9 s.
- Every page carries 60 to 175 KB, much of it the repeated app menu, rail and messenger.
- The home chart "Decisions per day" says "peak 1" over a row of zeros.

**Suggested fix:** render previews as text (title, state, date), not markdown source. Cache
the home counters and charts per user for a few seconds, and load the charts after the page.
