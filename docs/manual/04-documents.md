# 4. Documents

A document is a record of type `document` with a file behind it. Its content is kept in a store keyed by the file's
hash, with a history of versions; the record carries the title, class, revision, state and who owns it. A document can
be attached to any number of other records (hubproto/dms.py). This chapter covers the library, the five document
classes, editing and versions, and the approval of controlled documents.

## 4.1 The library

**Documents** in the rail (or the **Documents** app) opens the library (hubproto/shell/pages.py, `page_files`).

* **The folder tree** on the left lists every folder of the policy release. Folders you may not read are greyed and
  marked "(no access)".
* **The top level** lists the root folders with **Access** (readable or no access) and **Why** (the permission
  sentence), then the 24 most recently changed documents you may read.
* **A folder** shows a toolbar (**New document** and **Upload** when you may file documents there; a search box; **Sort
  by** newest first, name or revision), its subfolders, its documents as tiles, and the other items filed in it
  (number, type, state, title, owner, who it is with, due date, last change).

Each **tile** shows a text preview (Markdown and text files) or a file-type icon, the title, the number, the revision
(for example "rev A.2"), the state, a **checked out** chip, the owner, when it changed, and links **Open**, **Edit
online** or **View**, and **Download**.

**Controlled documents** (Documents app) lists every controlled document you may read. **Templates** is described in
4.12.

Who may read a folder comes from the policy. In the example company staff may read Quality, Policy, Purchasing,
Inventory and Tasks; only finance clerks, payables reviewers and controllers may read Finance; only design engineers,
design authorities and quality managers may read Engineering.

## 4.2 Document classes

| Class | Used for | Starts as | Can it change? |
|---|---|---|---|
| **controlled** | SOPs, work instructions, forms, drawings; anything that goes through document control | Draft (revision A) | While Draft, Open, Returned or Failed. Once in review or effective, only through a new revision (4.10) |
| **template** | controlled forms and outlines (4.12) | Draft | As controlled |
| **working** | drafts and everyday files | Open (revision 1) | Yes, by whoever may change it (4.5) |
| **evidence** | snapshots, cog reviews, CAD exports, rendered records | Filed | Never. File a new one instead |
| **external** | files received from outside, kept exactly as received, with where they came from (channel, sender, when) | Received | Never |

Where documents come from:

| You do | Class it gets |
|---|---|
| **New document** in a folder or on a record | working |
| **Upload** in a folder, for an office or text file (docx, xlsx, pptx, odt, ods, odp, doc, xls, txt, csv, rtf) | working, unless you tick **Keep as received (evidence; not editable)** |
| **Upload** in a folder, for any other file (PDF, images, Markdown, CAD files) | external |
| **Attach** a file on a record's page | external |
| **Files to attach** on a Start form | controlled (typed schematic, board, model or drawing by extension) |
| **Make a working copy** of a document | working (linked to the original, attached to the same items) |
| **Render snapshot**, a cog review, a CAD export, a process closing | evidence |
| The document-control process (**New controlled document**) | controlled |

## 4.3 Creating and uploading

You may file documents in a folder when the policy lets you create documents there, or create any kind of record
there (hubproto/shell/pages.py, `may_file`).

**How to create a new document** (anyone who may file documents in the folder)

1. Open the folder (or the record the document belongs to) and press **New document**.
2. Give a **Title\*** and choose the **Kind**: Word document (.docx), Spreadsheet (.xlsx), OpenDocument text (.odt),
   OpenDocument spreadsheet (.ods), Markdown (.md) or Plain text (.txt).
3. Press **Create and open**. A blank working document is created (attached to the record, if you started there) and
   opens in the online editor. A Markdown document opens on its page instead, to be edited with **Edit the text**
   (4.5); so does any document when no online editor is configured ("Download it to edit, then check it in").

**How to upload a file** (anyone who may file documents in the folder)

1. Open the folder and press **Upload**.
2. Choose the **File\*** (up to 25 MB by default) and optionally a **Title** (defaults to the file name).
3. Tick **Keep as received** to file it as an unchangeable external document.
4. Press **Upload**. The status line says what it became: "Uploaded *file* as DOC-0123; it is a working document you
   can edit online" or "kept as received".

**How to make a working copy** of a document you cannot change (an external or evidence file, or a controlled
document in force): on its page press **Make a working copy**. You need to be allowed to file documents in its folder
and the file must be a kind the editor edits.

## 4.4 The document page

A document's page (`/document/DOC-0123`) has:

* **Buttons**: **Edit online** (or **Join the editing** when someone else is editing, or **Open in the viewer** when
  you may only read it), **Edit the text** (Markdown, text and CSV files), **Make a working copy**, **Why not me?**,
  **Assign**, **Back to the start**, **Download**, **Check-out and check-in**, **Print**. Policy documents and SOP
  documents add their own buttons (chapter 6).
* The **status bar**, the **Ops** row (**Send for approval**, **Ask *cog* to review**; chapter 2.8) and smart buttons
  for **Versions**, **Attached to** and **Links**.
* **The sheet**: title, class, type, revision, state, folder, owner, author, who has it checked out, file name and
  format, labels, provenance (for received files), effective from, review date, approved by.
* **Versions**: every version with when, who, file, size and note, a **Download version *n*** link and **what
  changed** (4.8).
* **Text**: the text the hub extracted from the latest version (this is what reviews and cogs read).
* **Check-out and check-in** (4.6), **Attached to**, **Links**, **History** and the **Chatter**.

A document waiting for approval has no approve form on its page: approvers decide it from their **Inbox** (or from
the run's page).

## 4.5 Editing online (Collabora)

Documents open in Collabora Online inside the hub; the hub is the file host the editor reads from and saves to
(hubproto/wopi.py, hubproto/shell/editor.py). Office files (docx, xlsx, pptx, odt, ods, odp, doc, xls, txt, csv, rtf)
can be edited; PDFs open read-only in the viewer. Markdown is the hub's own text format and is edited with the hub's
text editor, not in Collabora.

**Who may change a document**: its author or owner, a document controller, or anyone who may create documents in its
folder. A draft of a policy Frame may only be changed by its author or a policy owner (hubproto/shell/pages.py,
`_may_edit_document`).

**How to edit a document online** (anyone who may change it)

1. On the document's page press **Edit online**. The editor opens inside the hub with a bar: the document's number and
   title, an **editing** or **read-only** chip with the reason, and **Finish editing**.
2. Opening the editor checks the document out to you as an editing session. Others who may change it can press **Join
   the editing**; everyone's changes merge in the editor.
3. Work as in any office editor. The page asks the editor to save about two seconds after each change. Every save
   becomes a new version noted "saved in the editor".
4. Press **Finish editing**. The status line says "Finished editing DOC-0123: your changes are version *n*", or
   "nothing was changed". The last person to finish releases the document; while others are still in it, it stays
   checked out to one of them.

The editor opens **read-only** when you may not change the document, when it is evidence or external, when it is a
controlled document or template that is not Draft, Open, Returned or Failed (a controlled document in force changes
only through a new revision), or when someone has it checked out by hand.

If no editor is configured, the editor page says how to configure it (chapter 7.3); documents can still be
downloaded, changed and checked in.

**How to edit a Markdown or text document in the hub** (anyone who may change it)

1. On its page press **Edit the text**.
2. Change the text in the box and press **Save**. A new version is added only if the text changed.

## 4.6 Check-out and check-in

Without the online editor, you change a document by checking it out, editing a copy and checking the new version in
(hubproto/dms.py).

**How to change a document by check-out** (anyone who may change it)

1. On its page, under **Check-out and check-in**, press **Check out**. Nobody else can check in a version while you
   hold it.
2. **Download** the file and edit it on your computer.
3. Back on the page, choose the **New version\*** file, write **What changed** and press **Check in**. The status line
   says "Checked in *file* as version *n*". Checking in releases the check-out.
4. To give the document back unchanged, press **Undo the check-out**.

Only the person who holds a check-out can check in or undo it ("checked out by alice; only alice can check it in or
undo the check-out"). An editing session started in the online editor is shared instead (4.5). **Back to the start**
also releases any check-out.

## 4.7 Comments in documents, and cog replies

You can talk to a cog inside a document, the way you would with a colleague (hubproto/wopi.py, `index`;
hubproto/mentions.py).

**How to ask a cog in a document comment** (anyone editing the document)

1. In the online editor, insert a comment and type `@`. The editor suggests members and roles of the organization;
   pick the cog (for example `@qa-manager`) and write your request: a question ("is the scope clear?"), a review ("please
   review this procedure"), or a change ("delete the Records section", "say must, not shall").
2. The comment is picked up when the document is saved. It becomes a message on the document's chatter from you,
   and goes into the cog's queue; saving does not wait for the cog. The editor bar shows which cog is working on it.
3. The cog answers in the comment thread:
   * a **question** gets an answer in a few sentences, from its procedures and the document;
   * a **review** gets its findings, filed as an evidence document;
   * a **change** gets a short plan ("Working on it: ..."), and the change is made in the document.
4. A reply in a thread where a cog has spoken goes to that cog, even without a new @mention.

**How the change is made.** When someone has the document open in the editor, the cog's own hidden editor session
makes the simple changes live (find and replace, deleting text, inserting at the very start or end; in a spreadsheet,
setting cells and appending rows). Everything else (inserting after a paragraph, replacing or deleting paragraphs and
sections, tables, rewrites) is done by the hub in the file once the session has saved, and the editor reloads. After
a live change the hub checks each change in the saved file and redoes any that landed in the wrong place. When nobody
has the editor open, the hub writes the answer and the changes into the file at once, as a new version. Anything the
cog could not do (text not found, no section with that heading) is said on the chatter (README.md, "Targeted edits").

A cog changes a document only if the person asking may change it: "The cog writes as the asker's pen" (hubproto/
mentions.py). Formulas are not adjusted when spreadsheet rows move, merged cells and charts are not edited, and
spreadsheet changes apply to the first sheet only.

## 4.8 Comparing versions

Each version after the first has a **what changed** link in the **Versions** table. It shows the differences between
that version and the one before it, line by line, as text. Formatting is not compared. A cog's reply on the chatter
also links to the change it made ("see what changed (version 4)").

## 4.9 Downloading

**Download** gives the latest version; **Download version *n*** in the versions table gives any earlier one. You need
to be allowed to read the document's folder.

## 4.10 Controlled-document approval (document control)

Controlled documents are drafted, filed, risk-scored, approved and published. When a document becomes effective, the
revision it replaces is withdrawn and everyone in its training roles is asked to acknowledge it (document-control-sop;
wi-review-a-document; hubproto/cogs.py, `DocumentDrafter`, `DocumentPublisher`).

**Who may start it:** anyone who may create documents in Quality/Controlled Documents: authors and document
controllers in the example company (alice, carol, and the doc-reviewer and qa-manager cogs, which hold the author
role).

**How to send a new controlled document for approval** (an author or document controller)

1. **Home** > **New controlled document** (or **Documents** > **+ New controlled document**).
2. Give the **Title\***, the **Document type** (sop, wi, drawing, policy, ...), the **Change type** (minor or major),
   the **Roles that must read it** (comma-separated roles, for example `staff`) and the **Text** (Markdown). Without a
   text the draft is "# *title* (draft body)".
3. Press **New controlled document**. The draft becomes version 1 of a new document.

**How to send an existing document for approval** (its owner or author, someone it is assigned to, or a document
controller): on its page choose **minor or major?** in the **Ops** row and press **Send for approval**. Its content is
kept as it is. Evidence and external documents cannot be sent.

| # | Step | Who | What happens |
|---|---|---|---|
| 1 | Draft the document | the author (automatic step: document-drafter) | title required; change type defaults to major; document type defaults to sop; the text becomes the draft version |
| 2 | File the document | automatic | DOC-*n*, filed in Quality/Controlled Documents; a document of type `policy` is filed in Policy instead |
| 3 | Score the risk | automatic | **low** only when the change is declared minor **and** the document revises a document in force; otherwise **medium** ("a new document is a major change") |
| 4 | Approve the document | Gate (table below) | the approver may also set the roles that must read it |
| 5 | Publish the document | automatic | sets approved by, effective from (today) and the review date (one year later); makes the revision it replaces **Obsolete** and links the two; opens a training sign-off for each person in the training roles (3.14). Closes as **Effective** |

**Routing** (document-control-sop, in this order):

| Condition | Goes to |
|---|---|
| document type policy | the policy owner (bob, carol) |
| risk low (a minor revision of a document in force) | the doc-reviewer cog |
| document type drawing | the process owner (alice) |
| any document | the reviewer (bob, carol) |

**Approval criteria** (document-control-sop):

* "The doc-reviewer cog may approve document-control documents when the change is minor." A fixed criterion: no
  model review.
* "The qa-manager cog may approve document-control documents when 3 adversarial reviews pass and the change is minor."
* "The qa-manager cog may approve document-control documents when 3 adversarial reviews pass, countersigned by a
  quality manager."

The qa-manager is on no route; it signs only when asked (chapter 5.6).

**Segregation of duties:** "The author and the approver of a document must be different people." The hub also
refuses to publish a document approved by its own author.
**Due date and escalation:** approval within 10 days; overdue items go to the document controller (carol), who is
added as an approver.
**On reject:** the document ends as Rejected. To send it back for rework instead, use **Back to the start**, which
returns it to **Draft** (3.1).

Points the routing table leads to (checked by running the code):

* A minor revision of a drawing goes to the doc-reviewer cog, not the process owner, because the low-risk route comes
  first.
* A drawing whose author is the process owner (alice) cannot go to her; it goes to the reviewers.
* A brand-new document is always a major change, whatever the author declares, so a person approves it.

**How to review a document** (the reviewer, process owner or policy owner it is routed to)

1. Open "Approve the document: DOC-..." in your inbox. The card names the document, the author, the change type and
   revision, and the routing sentence.
2. Open the document from the card and read it. Check that the title and revision are right, that the change type
   matches the content, that the training roles list who must read it, and that the content is fit to use
   (wi-review-a-document). The review date is set automatically on approval.
3. Approve with the meaning of your signature, or reject with the reason.

### Revisions and obsolete versions

A controlled document in force cannot be edited. A change is a new document with **the same title**, sent through
document control. The hub treats it as a revision of the one in force (it records which document it revises) when you
are that document's owner or author, or a document controller; anyone else is refused ("DOC-0040 “WI-9 Soldering” is
in force; only its owner (alice) or a document controller may revise it. Give the new document another title, or ask
them"). When the revision becomes effective, the earlier one becomes **Obsolete** and the new one is linked as
superseding it (hubproto/runner.py, `_revision_of`; hubproto/kernel.py, `publish_document`).

Each revision is a new document number. Its revision field starts at A again: the revision letter is not advanced for
SOP revisions made this way (checked by running the code; templates are different, 4.12).

A cog's review of a document flags a reference to an obsolete controlled document as an error.

### Review dates

On approval the review date is set to one year after the approval date, unless one was given. It shows on the
document's page. Nothing in the hub reminds anyone when a review date passes (chapter 9).

### Documents carried by a change order

A revision of an SOP or policy that a change order carries is approved through the same process, but on approval it
is **held**: it waits in state Approved and becomes effective only when its change order is released, together with
the other revisions and the compiled Frame (chapter 6.9).

## 4.11 Policy documents and Frames as documents

Every policy Frame also has a controlled document in Policy/Frames that carries its text, and policy owners approve
documents in Policy. Editing a Frame through its document, and SOPs as documents, are covered in chapter 6.

## 4.12 Templates

Templates are controlled forms and outlines (an NCR form, a CAPA form, a change request form, an audit plan) kept in
the library beside the SOPs (hubproto/templates.py; hubproto/shell/templates_ui.py).

**The Templates page** (Documents app > **Templates**) lists each template with: its title and the kind of record it
is for, the revision **In force** (number, revision, since when), any **Open revision** (with **Edit online** while it
is a draft), and the number of **Placeholders** such as `{{number}}`. With no templates yet it says "An administrator
brings the profile's templates in with `hubproto templates import --as PERSON`."

**How templates get into the library.** An administrator runs `hubproto templates import --as carol` (chapter 7.8).
Each Markdown file under `frames/<profile>/templates/` becomes a .docx document of class template, keyed by its source
file. Headings, lists, tables and placeholders are kept as plain text. Importing again changes nothing unless a source
changed: then the template's open draft gets a new version, or, when the template is in force, a new revision is
created. With `--submit` each new or changed template is sent for approval at once. Seeding a profile with an author
also imports its templates.

**Lifecycle.** A template draft is edited online and sent for approval through document control like an SOP (4.10).
Once effective it is read-only.

**How to revise a template in force** (its owner or author, or a document controller, who may also file documents in
its folder)

1. Open the template in force (from the Templates page).
2. Press **Start a new revision**. A new draft is created from its file, with the next revision letter (B, C, ...),
   revising the one in force. If an open revision already exists, you are taken to it instead.
3. Edit it online, then send it for approval (**Send for approval** in the Ops row).
4. When it becomes effective it supersedes the earlier revision of the same template only.

Nothing in the hub creates a record or document from a template yet, so placeholders are never filled in
automatically (chapter 9).
