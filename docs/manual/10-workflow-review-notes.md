# 10. Workflow review notes

**These are the author's observations, not facts about how the hub should work.** They list what looked unclear,
inconsistent or surprising while the manual was written and brought up to date, for the product owner to decide on.
Each note says what was seen and how: "ran the code" means it was reproduced on a throwaway copy of the hub with the
repository's Frames (never the live hub); "read the code" means it follows from the code cited but was not run; "seen
on the live hub" means a page of `http://localhost:8080`, read only. Notes carried over from the first edition were
run at `26962fa` and not run again; the code they rest on has not changed since in a way that affects them, unless the
note says so. Notes are grouped by area; the ones that seem to matter most are marked **[high]**.

**Resolved since the first edition, and dropped:** D5 (documents are now approved from their own page too, chapter
5.4), C2 (the chat window no longer says the cog changes nothing; the repository's README still does), A4 (the live
hub runs the repository's HEAD) and, from E9, the conversion of the SOPs, which is now a button (chapter 7.8). G1, C1
and E4 are partly resolved; they are kept below with what is left.

## 10.1 All processes

**G1 [high]. A retry after an approval now voids it, but a purchase request is approved again on its old amount.**
Since the first edition, a change to a record's fields after a signature voids the signature: the run goes back to
the first signed Gate and the item is approved again (hubproto/runner.py, `retry`). The automatic steps before that
Gate do not run again, so what they worked out is not worked out again. Ran the code: dave approved a purchase request
for 10 units with no price; issuing the order stopped ("no purchase order: the request has no positive quantity and
price"); alice retried with a unit cost of 1,000 and a quantity of 100. dave's approval was voided, but the request's
amount stayed 0, so it was routed as before: the purchasing-assistant declined ("amount is not positive") and it went
to dave, not to the controller. dave approved it again and PO-0001 was issued for 100,000 USD, "approved by dave". The
history line reads "dave's approval at score the risk no longer matches", naming the step before the Gate. Question:
should a retry that voids an approval also run the checks before the Gate again?

**G2 [high]. Amount routing only works in USD.** Amount bands in routing sentences and in approval criteria compare
only amounts in the currency the sentence names (hubproto/routing.py, `condition_holds`). Ran the code: a 50,000 EUR
purchase request went to the buyer, not the controller; a 50,000 USD one went to the controller. The same applies to
bills over 10,000 in another currency, and to stock adjustments of lots costed in another currency (read the code).
The risk scorer and the order issuer, on the other hand, compare the number without the currency. Question: convert,
refuse other currencies, or add sentences per currency?

**G3. One person per role stops items that person starts.** Whoever starts an item may never decide it on a route to a
role. With one person per role, nobody can take it and the run stops with "Nobody can take ...". Ran the code: a
purchase request, a part request and a goods receipt started by dave (the only buyer), a calibration recorded by carol
and a release from quarantine carol asked for (she is the only person quality manager) all stopped. The
nonconformance SOP has a fallback route (the management representative); the others have none. Stock adjustments have
a variant of their own (I2). Question: add fallback routes, or accept the stop as a staffing signal?

**G4. Escalation only happens when someone runs it.** Nothing schedules `hubproto tick` (chapter 8.7). Without it,
overdue items are never escalated.

**G5. "Go to" in an escalation sentence sometimes only informs.** Escalation adds only people who may approve the kind
of item. "Overdue items in calibration go to the management representative" and "Overdue items in change-order go to
the management representative" therefore only inform bob, because management representatives may not approve
calibrations or change orders (ran the code for calibration). The SOP wording suggests a hand-over.

**G6. Many steps have no due date, so they are never escalated.** Goods receipt, part request, journal reversal,
management review, tasks, and the second Gate of CAPA ("Verify effectiveness"), internal audit ("Accept the findings")
and change control ("Verify the change"). Requirements acceptance, supplier evaluation, stock adjustments (3 days) and
releases from quarantine (5 days) have due dates but no escalation sentence. "Overdue items in management-review go
to the quality manager" can never apply because the review has no due date.

**G7. A rejection ends the item in all processes but design release.** Elsewhere the record is closed as Rejected and
a new one must be started, or **Back to the start** used instead of rejecting. Question: should documents, CAPAs,
change requests and purchase requests return to their owner for rework like designs do ("Rejections return to ...")?

**G8. Assigning a person does not let them decide.** **Assign** adds a person to an item's "with" list and messages
them, but a waiting step still waits only for the people it was routed to. The page then shows "with bob" while bob has
no Approve button (read the code: hubproto/shell/actions.py, `post_assign`).

**G9. Thresholds and routing amounts are separate.** Routing sentences carry their own amounts ("over 10,000 USD");
the threshold sentences ("The controller approval threshold is 10,000 USD", "The cog purchase limit is 500 USD", "The
adjustment approval threshold is 500 USD") feed only the risk scorer and the order issuer. Changing a threshold in
Settings does not move a route. The risk scorer uses the purchasing "cog purchase limit" (500) as the medium-risk line
for bills too. "The design review limit is 10 days." is read by no code.

**G10. Email is not sent.** "Via email" stores the message and writes an `.eml` file into the hub's outbox folder;
nothing is delivered outside the hub.

## 10.2 Purchasing and finance

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
order then stops and the requester fixes it, and the second approval is routed on the old amount (G1).

**P5. dave cannot get his own purchase requests, part requests or goods receipts approved** (see G3).

## 10.3 Inventory

**I1 [high]. Back to the start takes a lot out of stock without a ledger event.** The record page offers **Back to
the start** on a lot to whoever created it. A lot booked by a goods receipt was created by the stock-booker, so nobody
may; but a lot a person split off (by a split, a partial move or a partial quarantine) was created by that person.
Ran the code: dave split 30 off a lot and could send the new lot back to the start; alice quarantined 10 of a lot and
could send that one back. The lot then goes to state Open: it is no longer in stock, counts as nothing available,
cannot be moved, counted or released ("LOT-... is open: it cannot be moved"), and the ledger has no event for it. The
inventory policy says every movement "is written to the ledger and cannot be undone or switched off".

**I2 [high]. Stock the controller ordered cannot be adjusted by 100 USD or more.** "The ordered-by and the approver of
a stock-adjustment must be different people", and orders over 10,000 USD are approved by the controller, who is the
only controller and the only person adjustments of 100 USD or more go to. Ran the code: for 150 boards bob had
ordered, adjustments to 140 (1,000 USD) and to 149 (100 USD) both stopped: "Nobody can take “Approve the stock
adjustment”: controller: bob: segregation of duties: they are the ordered by of this item". Only adjustments under 100
USD within the tolerance, which the purchasing assistant may sign, get through.

**I3. Small adjustments signed by the cog move the baseline, so they add up.** The purchasing assistant signs an
adjustment under 100 USD when it is within the count tolerance of the lot's baseline, and an applied adjustment
becomes the new baseline. Ran the code: a lot of 1,000 washers at 0.60 USD was adjusted to 980 and then to 960.4, each
signed by the purchasing assistant at once; the next 2% would be signed too. Nothing adds the cog's adjustments up:
its rail has no daily or monthly limit. Its rail also covers only changes a storekeeper could book as a free count,
except that the adjustment moves the baseline and the count does not. The policy's aim, "a run of small counts shares
one tolerance", holds for counts but not for adjustments.

**I4 [high]. A goods receipt can book more than was ordered.** The receipt's check compares each line with what is
outstanding on its own (hubproto/procurement.py, `ReceiptChecker`). Ran the code: a receipt with two lines of 100 of
one part, against an order of 100, passed its check, the buyer confirmed it, and 200 were booked. The order's own
**Receiving** card adds the lines up and would have refused it; the Home form ("Record a goods receipt") and the cogs'
tools do not. The buyer may also change the lines and the location when confirming, and what the buyer sets is booked
without being checked again: ran the code, dave confirmed a receipt of 100 with its line changed to 300, and 300 were
booked.

**I5. Goods can be received straight into Scrap or Quarantine.** The Home receipt form lists every location, and the
check refuses only a structural one. Ran the code: goods received into Scrap and into Quarantine were booked as OK
lots there, not available. The order's **Receiving** card leaves those locations out, and a transfer may not put OK
stock into a hold location ("quarantine it instead").

**I6. Some inventory records can only be made by a cog.** There is no page to create a supplier part, a manufacturer
part, a part category or a stock location, though the policy lets buyers, design engineers and storekeepers create
them; only a cog's `create_record` tool can (read the code). A part requested through part-request therefore has no
supplier part, and its page says "No supplier part: the part cannot be ordered until one is added" (a purchase request
naming the supplier and a unit cost still works).

**I7. The stores cog cannot do all its prompt says.** Its prompt says "you move lots within a site, count them, split
and merge them, and quarantine anything suspect"; its tools move, count and quarantine lots and ask for adjustments
and releases, but none splits (except as part of a partial move), merges or marks a lot (read the code:
hubproto/agent_inventory.py).

**I8. Orders placed before inventory cannot be received.** Their lines name items, which are archived; the
**Receiving** card says so, and a receipt against such an order stops at its check (tests/test_inventory_ui.py).
Their bills can therefore never match "goods received" and always go to a person (read the code). On the live hub the
older orders are of this kind (seen on the live hub: PO-0002, PO-0030).

**I9. Smaller points.** The Count form says "Within 2% of *the current quantity* it is booked at once", but a count is
measured from the baseline, so a count the form describes as within 2% can be refused (ran the code: 97 after a count
of 98 from 100). Marking a lot OK is refused with "a lot is marked OK only through its process (an adjustment, a
disposition)", though it is a release from quarantine that does it. The statuses REJECTED and LOST exist, but nothing
sets them. Nobody may approve a stock lot, so no lot can be put on hold.

## 10.4 Quality

**Q1 [high]. A severity chosen by the reporter overrides the safety keywords, and the cog then closes the
nonconformance and moves the stock.** The classifier raises the severity to high for "safety", "recall", "field",
"injury", "fire" or "shock" only when the reporter leaves **Severity** empty. Ran the code: "safety hazard: exposed
live wire on unit 12" with severity low was dispositioned "rework" by the quality-assistant cog at once, with no CAPA;
the same text without a severity waited for the quality manager at high risk. Ran it again at `6af5a46` with a lot of 5
housings named under **Stock affected**: the 5 were quarantined and, on the cog's signature, moved to Production as
ATTENTION. The work instruction says "Words such as "safety" or "recall" raise the severity to high"
(wi-file-a-nonconformance). The cog's criterion is fixed ("when the risk is low"): no inspection or model review
stands behind its signature.

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

**Q12. The nonconformance texts do not say the hub holds the stock.** The hub quarantines the lots a nonconformance
names when it is filed and applies the disposition to them (the SOP's process steps "Quarantine the stock" and "Apply
the disposition"), but the SOP's own text and the work instructions do not say so: wi-file-a-nonconformance still
tells the reporter to "Quarantine the affected items as the containment text says", and wi-disposition-stock does not
say what each disposition does to the lots.

## 10.5 Documents

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

**D6. Uploaded files get different classes depending on where they are uploaded.** Files attached on a Start form
become controlled documents (Draft); files attached later on a record's page become external (unchangeable); an
uploaded Markdown file becomes external.

**D7. Templates are never used.** The Templates page says placeholders "are filled in when a template is used", but
nothing in the hub makes a record or document from a template yet.

**D8. The New folder menu and the rule behind it differ.** The menu offers every folder where you may file any kind of
record, but adding a folder needs the right to create documents there. Ran the code: dave may file purchase requests in
Purchasing/Requests and is offered it, but adding a folder there was refused ("only people who may file documents in
Purchasing/Requests, a document controller or a policy owner add folders there"). The other way round, a document
controller may add a folder anywhere, even where she may not read: carol added "Finance/Payables/Carol in finance"
and may not open it.

## 10.6 Cogs

**C1 [high]. A cog working for someone can still start processes that person may not.** Since the first edition the
`start_process` tool checks the person too ("dave, for whom you work, may not: ..."), but `create_record` for a type
that has a process still starts it on the cog's rights only (hubproto/agent_records.py, `create_record`). Ran the
code: for dave, `start_process` of a CAPA was refused, and `create_record` of a CAPA opened CAPA-2026-0001. The chat
window says the cog does "only what both it and you may do".

**C3. A work instruction forbids what the hub now invites.** wi-respond-to-a-cog-escalation says "Never message the cog
to ask it to approve: it cannot act outside the release". "@qa-manager please approve" is now how the qa-manager is
asked to sign (within the release).

**C4. Cogs' roles carry more than their prompts suggest.** The quality-assistant and the qa-manager hold the
quality-manager role (read all of Quality, open CAPAs, supplier evaluations and training records, approve-permission
on most quality records); the quality-assistant now also holds author and reviewer, and the doc-reviewer holds author.
Every cog on the staff may quarantine stock. Approvals at Gates are still limited by criteria, but starting processes
and reading are not.

**C5. Autonomy does not limit Gate approvals.** The doc-reviewer's autonomy is "propose", yet it signs minor revisions
at Gates without anyone confirming, because Gate decisions follow the approval criteria. This may surprise someone who
reads "propose" as "a person confirms".

**C6. Standing duties only run when the duty service runs.** The quality-assistant's and purchasing-assistant's duties
need the `agents` compose profile or `hubproto agent-duties`; neither runs by default.

**C7. Every cog is offered on every task.** Every persona starts with the company scope, which includes the tasks SOP,
so each of the seven cogs, the organization cog included (autonomy read, signs nothing), is offered as **Ask *cog* to
review** on a task (ran the code). Elsewhere the Ops row offers only the cogs whose personas include the item's SOP.

**C8. Follows lines live on where personas replaced them.** A new cog proposed from the People page gets a **Follows**
line for the procedures ticked, and no persona; and Settings > **Cog rails** still offers **Follows** boxes, which
write a line that is ignored for a cog with a persona (the compiler warns "its persona decides what it follows")
(read the code). The new-cog form also still says to "set an Anthropic key" when no model can be reached, though
hosted models are off.

## 10.7 Policy, configuration and administration

**X1. Members are declared outside the organization model.** The org model says "Roles, members and folders named
anywhere else must be declared here", but the purchasing policy, the inventory policy and the design-control SOP
declare members, roles and folders (dave as buyer and storekeeper, the purchasing-assistant as buyer and stock
reviewer, the stores cog, alice as design engineer, bob as design authority, the Inventory folders).

**X2. Settings, once used, stops the Frames from being compiled.** After the first release from Settings the Frames
are no longer compiled ("the Frames are the SOPs people and cogs read"), so the rule sentences in the Frames and the
rules in force can drift apart unless someone keeps them in step.

**X3. Workflows and routing cannot be changed in Settings** ("edited in the workflow builder (coming next)").

**X4 [high]. The committee's reading can block a release, and reads narrative Frames too.** Two findings, both ran the
code with a stand-in reading in place of the model:

* A rule whose quoted words are not exactly the text of the line it cites is a grounding error ("cited text does not
  match the Frame at that line"), and errors block the release; rejecting the change does not clear it. Readings are
  kept by the exact text, so compiling again gives the same error until the SOP's text changes or model reading is
  switched off. The prose is wrapped, so a rule stated across two lines cannot be quoted as one.
* The quality manual and the work instructions are `compile: none`, but the committee reads them like any SOP: a rule
  read from wi-review-a-document was proposed as a change. "Narrative only" no longer means "no rules".

**X5. After the conversion, some pages still name the Frame files.** The scopes page and the **Depends and depended
on** cards show the source the release in force recorded, so on the live hub, whose SOPs are documents, they say
"the Frame file (the SOPs are not documents yet)" until a release is compiled from the documents (seen on the live
hub, policy version 9). The Policies page says "The rules are compiled from 49 SOP and policy documents." and then
"The policy and procedure documents (Frames) the release is compiled from, as they are on disk now."

**X6 [high]. Form conditions for "required" and "read-only" are not enforced.** The Forms page says fields "shown,
required or read-only only when a condition holds" are "checked by the hub when a record is saved or moves on", and the
editor lets document controllers set **Required** and **Read-only** conditions. Only **Shown** works: the record page
hides the row. A read-only field gets a chip, but nothing calls the check that would refuse a change or a missing
required field (read the code: hubproto/formlayout.py, `check_change`, `required_now`, `readonly_now` have no caller
outside the module).

**X7. Personas say more than the hub does.** The scopes Frame says later layers "may only narrow what earlier ones
allow"; nothing checks that, and every rule of every Frame applies to everyone whatever the personas (read the code).
Roles with no persona have no required reading: buyer, design engineer, design authority and stock reviewer (ran the
code), so the design-control SOP is required reading only through the design review committee.

**X8. What changed on a version's page can name the wrong cog.** The page pairs a removed and an added sentence when
they sit on the same line or read alike (hubproto/cli.py, `release_diff`). Seen on the live hub's page for policy
version 9: "cog-profiles line 37" changed from "## Cog: ap-reviewer" to "## Cog: doc-reviewer", and the ap-reviewer's
profile is listed again as added. The proposal's own changes table (chapter 7.6) words each change by its rule.

**X9. The action "quarantine" is half supported.** The inventory policy grants "Staff may quarantine stock items in
Inventory/Stock.", but the **Why?** page and Settings offer only create, read, approve, update and delete, so nobody
can ask Why? about it or add such a permission in Settings (read the code).

**A1. A backup leaves out some state.** `backup` does not copy `state/judgments` (cogs' failed reviews, which "bind for
that version"), `state/automation` (automation jobs), `state/forms` (the form layouts in force and the drafts),
`state/library` (the folders people added) or `state/extract-cache` (what the committee read). After a restore a cog
may review a version again, automation history is gone, every form is the hub's own again, added folders vanish from
the tree, and the next compile reads the SOP prose again, possibly not the same way (read the code: hubproto/cli.py,
`BACKUP_PARTS`).

**A2. The README's queue worker default is out of date.** It says `HUBPROTO_COG_WORKERS` defaults to 1; the code starts
one worker per cog.

**A3. The command line trusts `--as`.** Document and link verbs check membership only, not folder permissions.

## 10.8 Change orders and SOPs as documents

**E1 [high]. A change order prepared by the quality manager cannot be approved.** Change orders are approved by the
quality manager, and the preparer rule keeps the requester out. carol is the only person quality manager and also the
document controller, the natural preparer of SOP changes; Settings changes she proposes become her own change order.
Ran the code: carol's change order stopped with "Nobody can take “Approve the change order”: quality manager: carol:
segregation of duties: they prepared this item".

**E2. Policy owners approve only the widenings in a change order.** Rule changes that are not widenings (removing a
permission, adding a "may not" or a segregation rule, a new route to a person, a lower limit) and every wording change
are approved by document control and by the quality manager's approval of the change order; the release is recorded
as made by "change-order:CO-...". In the Frame-draft path every change needs at least one policy owner, and a wording
change to an SOP a cog follows needs two (hubproto/eco.py, `activate_frame`; hubproto/compile.py). Form layouts a
change order carries are approved the same way, by the change order's approval alone, though they decide which fields
people see. Question: should a policy owner still sign every rule change?

**E3. A minor SOP revision is approved by a cog even when it changes rules.** A revision declared minor is low risk
(it revises a document in force), so it goes to the doc-reviewer cog, whose criterion is fixed ("when the change is
minor"). Ran the code: an ncr-sop revision that shortened a due date was approved by the doc-reviewer at once. The
cog does not see the rule changes; they show only on the change order's staged Frame.

**E4. After the conversion, only two people can add members or change a cog's model.** Since the first edition these
pages write the change into the organization model and cog-profiles documents on a change order instead of refusing.
But only someone who may revise those documents can: their maintainer, "operations", is not a member, so only the
document controller and the policy owners (carol and bob) may. Ran the code: dave's model change and new person were
refused ("only operations, its author, a document controller or a policy owner revises DOC-..."), bob's were written
onto one change order. The profile page and the People page still offer the forms to everyone (seen on the live hub:
**Change model** on the stores cog's page, as dave).

**E5. A hub whose policy came from Settings cannot use change orders.** If the release in force came from a Settings
proposal before the conversion, every compile is refused ("the rules are edited in Settings since the release in force
came from there ...", ran the code), so no change order can stage its Frame, and Settings changes, which now become
change orders, cannot either.

**E6. Some converted documents are owned by names that are not members.** A converted document is owned by its Frame's
maintainer: the organization model, the cog profiles, the tasks SOP and the reference guide by "operations", the
design-control SOP by "engineering", and the quality manual by the placeholder "{{MANAGEMENT_REPRESENTATIVE}}" (ran
the code). Only document controllers, policy owners and whoever converted them may revise those.

**E7. Revision letters depend on the path.** An SOP revision on a change order gets the next letter (B, C ...); a
revision through document control alone starts again at A (D3).

**E8. The repository's Frame files are rewritten by the hub.** When a change order is released, and when a draft is
released from the web pages, the hub writes the released text into the `.frame.md` files, which in Docker are the
repository's own files mounted into the container. A release from a draft written on an older text replaces edits on
disk that no release carries; they are kept only by hash in the hub's state, and the policy owners are told (chapter
7.6).

**E9. Smaller points.** A change order's description is optional on the form but required before it can wait for
approval (the run stops without it). Overdue change orders are only reported to the management representative, who
may not approve them (G5). The Ops row of an open change order also offers "Start change order", a second way to send
it for approval (the compiled-Frame check still applies). Converting makes a document of the reference guide too,
which no release carries, though otherwise only released Frames have documents (ran the code: 49 documents for 48
released Frames).
