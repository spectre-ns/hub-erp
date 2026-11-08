# 9. Workflow review notes

**These are the author's observations, not facts about how the hub should work.** They list what looked unclear,
inconsistent or surprising while the manual was written, for the product owner to decide on. Each note says what was
seen and how: "ran the code" means it was reproduced on a throwaway copy of the hub with the repository's Frames
(never the live hub); "read the code" means it follows from the code cited but was not run. Notes are grouped by area;
the ones that seem to matter most are marked **[high]**.

## 9.1 All processes

**G1 [high]. A retry after an approval keeps the approval, but the steps after it use the changed fields.** When an
automatic step after a Gate stops, its owner may change the record's fields and retry; every automatic step after the
last signed Gate runs again on the new values, and the earlier signature is kept (hubproto/runner.py, `retry`;
hubproto/cogs.py, `Cog._prior`: "The record is the system of record: its fields win"). Ran the code: dave approved a
purchase request for 10 units with no price; issuing the order stopped ("no purchase order: the request has no
positive quantity and price"); alice, the requester, retried with a unit cost of 1,000 and a quantity of 100, and
PO-0001 for 100,000 USD was issued with "approved by dave". It never went to the controller. The same mechanism applies
to every process with automatic steps after a Gate (bill posting, publishing, CAPA opening). Question: should a change
after a signature send the item back to the Gate?

**G2 [high]. Amount routing only works in USD.** Amount bands in routing sentences and in approval criteria compare
only amounts in the currency the sentence names (hubproto/routing.py, `condition_holds`). Ran the code: a 50,000 EUR
purchase request went to the buyer, not the controller; a 50,000 USD one went to the controller. The same applies to
bills over 10,000 in another currency. The risk scorer and the order issuer, on the other hand, compare the number
without the currency. Question: convert, refuse other currencies, or add sentences per currency?

**G3. One person per role stops items that person starts.** Whoever starts an item may never decide it on a route to a
role. With one person per role, nobody can take it and the run stops with "Nobody can take ...". Ran the code: a
purchase request, an item request and a goods receipt started by dave (the only buyer), and a calibration recorded by
carol (the only person quality manager), all stopped. The nonconformance SOP has a fallback route (the management
representative); the others have none. Question: add fallback routes, or accept the stop as a staffing signal?

**G4. Escalation only happens when someone runs it.** Nothing schedules `hubproto tick` (chapter 7.7). Without it,
overdue items are never escalated.

**G5. "Go to" in an escalation sentence sometimes only informs.** Escalation adds only people who may approve the kind
of item. "Overdue items in calibration go to the management representative" and "Overdue items in change-order go to
the management representative" therefore only inform bob, because management representatives may not approve
calibrations or change orders (ran the code for calibration). The SOP wording suggests a hand-over.

**G6. Many steps have no due date, so they are never escalated.** Goods receipt, item request, journal reversal,
management review, tasks, and the second Gate of CAPA ("Verify effectiveness"), internal audit ("Accept the findings")
and change control ("Verify the change"). Requirements acceptance and supplier evaluation have due dates but no
escalation sentence. "Overdue items in management-review go to the quality manager" can never apply because the
review has no due date.

**G7. A rejection ends the item in all processes but design release.** Elsewhere the record is closed as Rejected and
a new one must be started, or **Back to the start** used instead of rejecting. Question: should documents, CAPAs,
change requests and purchase requests return to their owner for rework like designs do ("Rejections return to ...")?

**G8. Assigning a person does not let them decide.** **Assign** adds a person to an item's "with" list and messages
them, but a waiting step still waits only for the people it was routed to. The page then shows "with bob" while bob has
no Approve button (read the code: hubproto/shell/actions.py, `post_assign`).

**G9. Thresholds and routing amounts are separate.** Routing sentences carry their own amounts ("over 10,000 USD");
the threshold sentences ("The controller approval threshold is 10,000 USD", "The cog purchase limit is 500 USD") feed
only the risk scorer and the order issuer. Changing a threshold in Settings does not move a route. The risk scorer
uses the purchasing "cog purchase limit" (500) as the medium-risk line for bills too. "The design review limit is 10
days." is read by no code.

**G10. Email is not sent.** "Via email" stores the message and writes an `.eml` file into the hub's outbox folder;
nothing is delivered outside the hub.

## 9.2 Purchasing and finance

**P1. A supplier approved by evaluation is approved for purchasing but not for payables.** Ran the code: after an
approved supplier evaluation of NewCo Tools, purchase requests from NewCo passed the supplier check ("approved by
supplier evaluation SUP-0001"), but its bill was declined by the ap-reviewer ("“the supplier is approved” does not
hold"), and the Suppliers page still shows "not approved". Bill matching reads only the finance system's seeded list,
and there is no form to add or approve a supplier there.

**P2. A bill of exactly 2,000 USD goes to the ap-reviewer, which cannot approve it.** The route says "at or under
2,000 USD", the criterion "under 2,000 USD" (ran the code). Harmless (it goes on to dave) but noisy.

**P3. The ap-reviewer cog may file bills and request journal reversals on its own.** It holds the finance-clerk role
and has autonomy "act". Ran the code: as the ap-reviewer, `start_process` opened journal reversal JR-00001, waiting for
the controller. The finance policy only says "Small, clean bills may be approved by the payables reviewer cog".

**P4. A purchase request can be approved without a price.** The card warns but does not require the unit cost; the
order then stops and the requester fixes it (see G1).

**P5. dave cannot get his own purchase requests, item requests or goods receipts approved** (see G3).

## 9.3 Quality

**Q1 [high]. A severity chosen by the reporter overrides the safety keywords, and the cog then closes the
nonconformance.** The classifier raises the severity to high for "safety", "recall", "field", "injury", "fire" or
"shock" only when the reporter leaves **Severity** empty. Ran the code: "safety hazard: exposed live wire on unit 12"
with severity low was dispositioned "rework" by the quality-assistant cog at once, with no CAPA; the same text without
a severity waited for the quality manager at high risk. The work instruction says "Words such as "safety" or "recall"
raise the severity to high" (wi-file-a-nonconformance). The cog's criterion is fixed ("when the risk is low"): no
inspection or model review stands behind its signature.

**Q2 [high]. A CAPA opened from a nonconformance the quality manager reported cannot be closed.** For a child run the
preparer is the parent's starter, so the reporter of the nonconformance may not take the CAPA's role-routed step,
"Verify effectiveness". carol is the only person quality manager. Ran the code: a medium complaint logged by alice and
answered by carol opened a nonconformance reported by carol (high risk), which bob dispositioned as scrap; its CAPA
went to bob for "Verify the actions" and then stopped: "Nobody can take “Verify effectiveness”: quality manager:
carol: segregation of duties: they prepared this item". Every medium or high complaint and every out-of-tolerance
calibration that carol handles leads here.

**Q3. "Verify effectiveness" does not wait for the effectiveness check.** The effectiveness check date is set 90 days
out, but the step waits from the moment the actions are verified, with no due date. The CAPA's owner and the
effectiveness verifier can also be the same quality manager (ran the code: carol did both for alice's nonconformance).

**Q4. The CAPA's "Verify the actions" belongs to the process owner in the SOP but goes to the CAPA's owner.** The
step reads "[process-owner] Verify the actions"; the routing sentence sends it to "the owner", the person who signed
the disposition.

**Q5. Nonconformances from complaints and calibrations always go to the management representative.** The person who
answered the complaint or reviewed the calibration is recorded as the reporter, so the quality manager is excluded
(ran the code).

**Q6 [high for calibration users]. Calibrations cannot be recorded from the web pages.** The form requires an asset
picked from asset records, and nothing in the pages, the policy or the cogs' tools can create an asset (no process and
no permission covers assets). The calibration SOP also still has its placeholder "{{NATIONAL_STANDARDS_BODY}}", and
calibrations are not risk-scored.

**Q7. Training records for courses and inductions cannot be filed from the web pages.** The training SOP lets the
quality manager and document controllers file them; the Start form offers title, description and a JSON box, but the
field the step is routed by (the assignee) cannot be set, so the record stops at once ("Nobody can take “Acknowledge
the document”", ran the code). A rejected acknowledgement notifies the run's owner, the document's approver (which may
be a cog), where the work instruction says "the quality manager will follow up".

**Q8. Management review is hard to fill in and its roles are blurred.** **Inputs** and **Outputs** are JSON objects
typed into text boxes. The chair defaults to the management representative who prepared it (the SOP says top
management chairs). Actions become tasks or change requests only by hand.

**Q9. Internal audit independence is not checked.** "Auditors do not audit processes they own" has no rule behind it.
Findings become nonconformances only by hand. "Notify the auditor" notifies whoever planned the audit.

**Q10. Change control: the management representative can approve an overdue change.** Staff may approve change
requests (so that requesters can sign the verification). When an approval is overdue, escalation adds the management
representative, who as staff may then approve the change itself (ran the code: bob was added to CR-2026-0001). The SOP
says "the approval checkpoint is routed to the quality manager only".

**Q11. A cog's approval criteria cover a whole process, not one step.** The qa-manager's criterion for change-control
applies at "Verify the change" as well as at "Approve the change" (read the code: hubproto/routing.py, `criteria_for`).

## 9.4 Documents

**D1. Minor drawing revisions are approved by the doc-reviewer cog, not the process owner.** The low-risk route comes
before the drawings route (ran the code: a minor revision of an effective drawing was approved by the doc-reviewer at
once). The SOP says "Drawings are approved by the process owner".

**D2. A drawing by the only process owner goes to the reviewers.** alice is the only process owner; her own drawings
fall through to "the reviewer" (ran the code).

**D3. Revision letters do not advance for SOP revisions.** A revision made through document control is a new document
that starts at revision A again (DOC-0002 rev A supersedes DOC-0001 rev A; ran the code). Templates do advance (B, C).
The work instruction asks reviewers to check "the title and revision are right".

**D4. Review dates are recorded but never acted on.** The review date is set to one year after approval; nothing
reminds anyone when it passes. The reviewer is asked to check it is set (wi-review-a-document) but cannot set it at the
Gate.

**D5. Documents are approved from the inbox, not their page.** The document page has no approve form; the work
instruction says "On the record page ... decide approve".

**D6. Uploaded files get different classes depending on where they are uploaded.** Files attached on a Start form
become controlled documents (Draft); files attached later on a record's page become external (unchangeable); an
uploaded Markdown file becomes external.

**D7. Templates are never used.** The Templates page says placeholders "are filled in when a template is used", but
nothing in the hub makes a record or document from a template yet.

## 9.5 Cogs

**C1 [high]. A cog working for someone can start processes that person may not.** When a cog acts for a person (chat,
assigned work, a task), moving, changing and editing records are checked against both the cog's and the person's
rights, but starting a process (`start_process`, or `create_record` for a type that has a process) is checked against
the cog's rights only (hubproto/agent.py; hubproto/agent_records.py). Ran the code: from dave's chat, the
quality-assistant (autonomy act) opened two CAPAs, which dave may not create. The messenger text promises the opposite
for the cog's actions in general.

**C2. The messenger says the cog "will not change anything from here".** Cogs with propose or act autonomy can act
from the chat (chapter 5.5). The README's messenger paragraph says the same as the old text.

**C3. A work instruction forbids what the hub now invites.** wi-respond-to-a-cog-escalation says "Never message the cog
to ask it to approve: it cannot act outside the release". "@qa-manager please approve" is now how the qa-manager is
asked to sign (within the release).

**C4. Cogs' roles carry more than their prompts suggest.** The quality-assistant and the qa-manager hold the
quality-manager role (read all of Quality, open CAPAs, supplier evaluations and training records, approve-permission
on most quality records); the doc-reviewer holds author. Approvals at Gates are still limited by criteria, but starting
processes and reading are not.

**C5. Autonomy does not limit Gate approvals.** The doc-reviewer's autonomy is "propose", yet it signs minor revisions
at Gates without anyone confirming, because Gate decisions follow the approval criteria. This may surprise someone who
reads "propose" as "a person confirms".

**C6. Standing duties only run when the duty service runs.** The quality-assistant's and purchasing-assistant's duties
need the `agents` compose profile or `hubproto agent-duties`; neither runs by default.

## 9.6 Policy, configuration and administration

**X1. Members are declared outside the organization model.** The org model says "Roles, members and folders named
anywhere else must be declared here", but the purchasing policy and the design-control SOP declare members and roles
(dave as buyer, the purchasing-assistant cog, alice as design engineer, bob as design authority).

**X2. Settings, once used, stops the Frames from being compiled.** After the first release from Settings the Frames
are no longer compiled ("the Frames are the SOPs people and cogs read"), so the rule sentences in the Frames and the
rules in force can drift apart unless someone keeps them in step.

**X3. Workflows and routing cannot be changed in Settings** ("edited in the workflow builder (coming next)").

**A1. A backup leaves out some state.** `backup` does not copy `state/judgments` (cogs' failed reviews, which "bind for
that version") or `state/automation` (automation jobs). After a restore a cog may review a version again and
automation history is gone.

**A2. The README's queue worker default is out of date.** It says `HUBPROTO_COG_WORKERS` defaults to 1; the code starts
one worker per cog.

**A3. The command line trusts `--as`.** Document and link verbs check membership only, not folder permissions.

**A4. The live hub is behind the repository.** At the time of writing the hub at `http://localhost:8080` ran policy
version 6 of 6 November (no qa-manager, no change orders, no route for policy documents) on an image built before the
latest commits.

## 9.7 Change orders and SOPs as documents

These notes describe the committed code at `26962fa` (chapter 6.8 to 6.11).

**E1 [high]. A change order prepared by the quality manager cannot be approved.** Change orders are approved by the
quality manager, and the preparer rule keeps the requester out. carol is the only person quality manager and also the
document controller, the natural preparer of SOP changes; Settings changes she proposes become her own change order.
Ran the code: carol's change order stopped with "Nobody can take “Approve the change order”: quality manager: carol:
segregation of duties: they prepared this item".

**E2. Policy owners approve only the widenings in a change order.** Rule changes that are not widenings (removing a
permission, adding a "may not" or a segregation rule, a new route to a person, a lower limit) and every wording change
are approved by document control and by the quality manager's approval of the change order; the release is recorded
as made by "change-order:CO-...". In the Frame-draft path every change needs at least one policy owner, and a wording
change to an SOP a cog follows needs two (hubproto/eco.py, `activate_frame`; hubproto/compile.py). Question: should a
policy owner still sign every rule change?

**E3. A minor SOP revision is approved by a cog even when it changes rules.** A revision declared minor is low risk
(it revises a document in force), so it goes to the doc-reviewer cog, whose criterion is fixed ("when the change is
minor"). Ran the code: an ncr-sop revision that shortened a due date was approved by the doc-reviewer at once. The
cog does not see the rule changes; they show only on the change order's staged Frame.

**E4. Adding people and cogs, and changing a cog's model, stop working after the conversion.** These pages draft a
Frame, which is refused once the SOPs are documents ("the org-model Frame is compiled from DOC-...: change that
document on a change order", ran the code). There is no change-order path for them yet.

**E5. A hub whose policy came from Settings cannot use change orders.** If the release in force came from a Settings
proposal before the conversion, every compile is refused ("the rules are edited in Settings since the release in force
came from there ...", ran the code), so no change order can stage its Frame, and Settings changes, which now become
change orders, cannot either.

**E6. Some converted documents are owned by names that are not members.** A converted document is owned by its Frame's
maintainer; the organization model, the cog profiles, the tasks SOP and the reference guide are maintained by
"operations" and the design-control SOP by "engineering". Only document controllers, policy owners and whoever
converted them may revise those.

**E7. Revision letters depend on the path.** An SOP revision on a change order gets the next letter (B, C ...); a
revision through document control alone starts again at A (D3).

**E8. The repository's Frame files are rewritten by the hub.** When a change order is released, the hub writes the new
text into the `.frame.md` files, which in Docker are the repository's own files mounted into the container.

**E9. Smaller points.** A change order's description is optional on the form but required before it can wait for
approval (the run stops without it). Overdue change orders are only reported to the management representative, who
may not approve them (G5). The Ops row of an open change order also offers "Start change order", a second way to send
it for approval (the compiled-Frame check still applies). The conversion has no button; it is a command (`hubproto sops
convert`).
