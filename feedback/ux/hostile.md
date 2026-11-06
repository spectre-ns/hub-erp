# Hostile-user lens (hostile)

Method: curl-equivalent raw HTTP and Python `http.client` against
`http://localhost:8080` as alice, bob, carol and dave; multipart bodies built by
hand; CLI checked in a scratch root (`hubproto --root <scratch> init && seed
iso9001 && bootstrap-release --as bob --second carol`). Every record, run,
message and document named below exists in the live state (titles start with
`HOSTILE` where I chose them). Blockers first.

### F-hostile-1: Double-clicking Start files the item twice
Severity: blocker
Heuristic: Error prevention (Nielsen 5); idempotent form submission
Steps:
1. As alice, POST `/start` twice within the same millisecond (two threads) with
   identical multipart bodies: `process=task`, `title=HOSTILE dup-click task`,
   `description=double click`, `assignee=bob`.
2. GET `/inbox?as=bob`.
Expected: one task, or the second submit told "already started" and sent to
the same run.
Actual: two 303s to two runs: `Location: /run/03d309864c?as=alice` and
`/run/e479580cbd?as=alice`; two records `rec:task:TASK-0005` and
`rec:task:TASK-0006`, both titled `HOSTILE dup-click task`, both in bob's
inbox with their own Approve/Reject forms. Nothing on either page says the
other exists. The same holds for `/msg` (two identical `HOSTILE double send`
cards in bob's inbox) and `/upload`.
Impact: every person who double-clicks a Start button (common on a slow
connection: the button gives no feedback) creates duplicate work for a
reviewer and a duplicate record in the audit trail that cannot be deleted.
Suggested fix: a per-form token (hidden `once=<uuid>`) that the server
remembers for a few minutes and answers with a redirect to the first run;
disable the button on submit.

### F-hostile-2: A validation error throws away everything the user typed
Severity: blocker
Heuristic: Help users recover from errors (Nielsen 9); WCAG 3.3.1, 3.3.3
Steps:
1. As alice on `/?as=alice`, fill the purchase-to-pay form with a long
   description and `amount=$1,000`, submit.
2. Or start nonconformance with a 5,000-character description and a
   whitespace title.
Expected: the form comes back with the typed values and the bad field marked.
Actual: a bare page with no form: `<div class="card crit"><b>Not done.</b>
amount must be a number, not &#x27;$1,000&#x27;.</div>` and, only when the
browser sent a Referer, `<p><a href="http://localhost:8080/?as=alice">Back</a></p>`.
That link is a plain GET of the home page, which renders every Start form
empty (`<textarea name="description" rows="2" cols="50"></textarea>`). Without a
Referer (privacy settings, a bookmark) there is no link at all. The same shape
is used for every POST error (`/decide`, `/resubmit`, `/msg`, `/upload`). On the
blank-title case the message is `nonconformance needs title, description before
it can start<div class="why">RunError</div>`; the 5,000-character description
is gone unless the browser's back-forward cache restores it.
Impact: anyone who makes one typo in a money or date field on a long form;
most often clerks entering bills.
Suggested fix: re-render the form on error with the submitted values and the
message next to the offending field; at minimum echo the values in the error
card.

### F-hostile-3: An oversize or stalled upload ends in a connection reset, never the limit message
Severity: blocker
Heuristic: Visibility of system status (Nielsen 1); Help users recognise errors
Steps:
1. As alice, POST `/upload` to `Quality/Nonconformances` with a 30 MB file;
   repeat with 25 MB + 1 byte.
2. Open a socket, send a POST to `/msg` with `Content-Length: 100` and only 28
   body bytes (a dropped connection mid-upload).
Expected: a 413 page saying the limit is 25 MB; a quick 400 for the short body.
Actual: step 1 gives `BrokenPipeError: [Errno 32] Broken pipe` on the client
before any response is read: the server sends the 413 and closes
(`self.close_connection = True`) while the client is still writing, so a
browser shows its own "connection was reset" page and the limit text
`The request body (31,457,280 bytes) is over the limit of 26,214,400 bytes
(HUBPROTO_MAX_UPLOAD).` is never seen. Step 2 hangs for exactly 120.1 s and
then the socket closes with 0 bytes: no page at all. The server survives
(`/healthz` -> `ok`), the user does not learn what happened.
Impact: anyone attaching a scan or a CAD export over a flaky link; they will
retry, and retries of an accepted earlier attempt create duplicates (F-1).
Suggested fix: state the limit next to the file input and check
`files[0].size` client-side; on 413 drain the body before closing (or answer
`Expect: 100-continue`); shorten the body-read timeout and answer 408 with a
page.

### F-hostile-4: A missing or blank identity silently becomes alice
Severity: blocker
Heuristic: Match between system and real world; visibility of who you are
Steps:
1. GET `/inbox?as=` (a bookmark that lost its value) or `/inbox?as=%20%20`
   or `/inbox` with no query.
2. Approve a card on that page.
Expected: a "who are you?" page, or a chooser.
Actual: 200, alice's inbox; the acting-as select shows `<option value="alice"
selected>alice</option>` and every decision form carries
`<input type="hidden" name="as" value="alice">`, so a bob or dave who lands
here signs as alice. `/?as=ALICE` by contrast is "not a member" and
`/?as=%00` renders `Welcome, \x00`. (`DEFAULT_USER = "alice"` in
`shell.py`; `self.user = ... or DEFAULT_USER`.)
Impact: any person with a stale link; the signature in the Decisions table
then names the wrong person and cannot be undone.
Suggested fix: no default principal; a blank `as` renders a chooser and POSTs
refuse (the POST path already does).

### F-hostile-5: The due date you type on the Start form is stored but not the one the hub shows
Severity: blocker
Heuristic: Consistency and standards (Nielsen 4); the user's model of state
Steps:
1. As alice, start 30 tasks for carol with `due=2026-11-01` (five days ago)
   and `priority=high`.
2. GET `/record/rec:task:TASK-0015?as=carol`, `/boards?type=task&as=carol`,
   `/inbox?as=carol`.
Expected: "due 2026-11-01" in the header, an `overdue` chip on the board and
inbox cards, the overdue items sorted first.
Actual: the Details table has `<tr><td>due</td><td>2026-11-01</td></tr>` but
the header card says `&middot; due - &middot;`, the task board shows 0
`overdue` chips and 0 "due" lines across 44 cards, and the inbox puts the
30 overdue tasks after the one failed CAPA. The record's `due` column is only
ever set from an assignment SLA (`kernel.py` lines 146-151, 321), never from
the field the form asked for.
Impact: everyone who sets a due date on a task; the board's overdue signal is
wrong for all of them, and a manager scanning the board sees nothing late.
Suggested fix: either the Start form sets the record's due (and the board
reads `fields.due` when the column is empty) or the form does not offer the
field.

### F-hostile-6: Unhandled 500 pages from ordinary bad input
Severity: major
Heuristic: Error messages in plain language (Nielsen 9)
Steps:
1. GET `/download?doc=rec:document:DOC-0287&v=abc&as=alice` (a mistyped
   version), `&v=99`, `&v=-1`.
2. As alice, start a task with JSON box `{"assignee": ["bob","carol"]}`.
Expected: "no version 99 of DOC-0287" / "assignee must be one person".
Actual: step 1: `<div class="card crit"><b>ValueError:</b> invalid literal for
int() with base 10: &#x27;abc&#x27;<div class="why">error id 4a60312e (in the
server log)</div></div>` and for 99 and -1 `<b>StopIteration:</b> <div
class="why">error id ef4020a2 (in the server log)</div>` (no message at all).
Step 2: `<b>TypeError:</b> cannot use &#x27;list&#x27; as a dict key (unhashable
type: &#x27;list&#x27;)`. HTTP 500 each time; no Back link without a Referer.
Impact: whoever edits a download link or follows the "other fields (JSON
object)" hint without knowing the field types.
Suggested fix: validate `v` as a positive int within the version list;
type-check JSON values against the field spec the same way form values are
coerced.

### F-hostile-7: inf, nan, negative money and quantities are accepted; a negative amount reaches a Gate
Severity: major
Heuristic: Error prevention; constraints that match the domain
Steps:
1. As alice, POST `/start` purchase-to-pay with `amount=1e400`, then `nan`,
   then `-5` (the `type="number"` input is the only guard; curl skips it).
2. POST `/start` purchase-request with `quantity=1.5e3`, `unit_cost=-1`.
3. POST `/start` purchase-request with `quantity=0` and `-3`.
Expected: "amount must be a positive number" at submission.
Actual: `BILL-0057` is filed and its header reads `<b>ACME inf USD</b>`,
`BILL-0058` reads `ACME nan USD`; both fail three steps later at
`match-the-bill-to-purchase-orders` with messages about the missing PO, not
the amount. `BILL-0059` (-5) does say `a bill must carry a positive amount,
not -5` but only in the failed run, after the record exists. `PR-00058` goes
to the Gate: state `awaiting-approve-the-purchase`, Details
`amount -1500.0 ... unit_cost -1.0`, with no guard finding about the sign.
Quantity 0 and -3 fail at `check-the-supplier` ("quantity must be positive")
rather than at the form.
Impact: clerks and buyers, and the reviewer who gets a -1,500 USD request to
approve. The same strings via the CLI (`--record-json '{"amount": NaN}'`)
produce `BILL-0001 ... ACME nan USD` in `hubproto records`.
Suggested fix: `coerce` rejects non-finite floats and, for amount, quantity,
unit_cost, non-positive values; the type spec should say `money>0`.

### F-hostile-8: dd/mm/yyyy and free-text dates are accepted, then blamed on the "period"
Severity: major
Heuristic: Match between system and real world; error messages that name the cause
Steps:
1. As alice, POST `/start` purchase-to-pay with `date=06/11/2026`; then
   `date=yesterday`; purchase-request with `needed_by=31/12/2026`; task with
   JSON `{"due": "soon"}`.
Expected: "date must be YYYY-MM-DD" at submission.
Actual: all four are stored as typed. The bills fail with `period-open period
06/11/2 is not open for posting` and `period 06/11/2 is not open for posting`
for `yesterd`: the message cuts the string to seven characters and talks about
an accounting period, so the user looks for a closed period instead of a
typo. `needed_by 31/12/2026` and `due soon` sit on records that are now
waiting at a Gate; any date comparison on them is a string comparison.
Impact: anyone who pastes a date from an email or spreadsheet; the date input
helps in a browser but not from the CLI, an email intake or a pasted value.
Suggested fix: validate `spec == "date"` with `date.fromisoformat` in
`coerce`; have the period guard say "date 06/11/2026 is not a date".

### F-hostile-9: The page says only the owner may retry or cancel; the server lets anyone
Severity: major
Heuristic: Consistency between what is shown and what is enforced
Steps:
1. As bob (not the owner), GET `/record/rec:bill:BILL-0058?as=bob`: the failed
   card says `Its owner alice may retry or cancel it.` and shows no buttons.
2. POST `/cancel` with `as=bob`, `run=28c9dd5dc0`, `reason=bob cancels alice's
   failed bill`.
3. As dave, POST `/retry` with `run=a5203032f0`.
Expected: 403 "only alice may cancel this run".
Actual: 303 to `/record/rec:bill:BILL-0058?as=bob`; the bill is `cancelled`
by bob. Dave's retry: 303 to `/record/rec:bill:BILL-0059?as=dave`; the run is
re-executed under dave's name. Earlier, alice cancelled a run that was
waiting for dave's decision (`PR-00005`) with the same ease. By contrast
`/decide` with a tampered run id answers `alice is not an assignee of this
decision (assigned: dave, bob)`.
Impact: anyone who edits a hidden `run` value or replays a form; the owner
finds their run cancelled with no notification.
Suggested fix: enforce owner-or-approver on `/retry` and `/cancel` the way
`/decide` checks assignees, and notify the owner.

### F-hostile-10: Double-clicking Retry executes the failed step twice
Severity: major
Heuristic: Error prevention; idempotent actions
Steps:
1. As alice, POST `/retry` twice concurrently for the failed run
   `0a2674f98a` (BILL-0057).
2. GET `/record/rec:bill:BILL-0057?as=alice#track`.
Expected: one retry; the second request told "already retried".
Actual: both answer 303; the Track shows `run.retried, step.started,
step.result, run.failed, run.retried, step.started, step.result, run.failed`:
the cog ran twice and the record now carries two failures for one click. A
double-clicked `/cancel` is safe (second: `run 0a2674f98a is cancelled and
cannot be cancelled`) and a double-clicked `/decide` is safe (first wins,
second: `run 03d309864c is not waiting for a decision (status completed)`).
Impact: owners of failed runs on a slow connection; for steps with side
effects (a posting, a message to a supplier) the effect is doubled.
Suggested fix: refuse a retry while a retry of the same run is in flight;
the lock already serialises, so a `retried_at` within the last few seconds
is enough.

### F-hostile-11: The inbox and runs pages have no paging, grouping or filter
Severity: major
Heuristic: Flexibility and efficiency; recognition over recall
Steps:
1. As alice, start 30 tasks assigned to carol (4 s).
2. GET `/inbox?as=carol`; GET `/inbox?as=bob`; GET `/runs?as=alice`.
Expected: paging or at least grouping by process, a count per group, and a
way to jump.
Actual: carol's inbox grows from 50 KB to 153 KB with 47 full decision cards
and 47 `<form method="post" action="/decide">` on one page, each with its
brief, routing sentence, a `meaning of your signature` input and two buttons;
bob's is 136 KB; alice's `/runs` is 123 KB with 332 table rows. There is no
`page=`, no per-process heading, no "select all", and the "Send a message"
form sits below the 47th card. The 30 tasks are indistinguishable except by
their number.
Impact: any approver after a week; reviewers scrolling 150 KB to find the
one item that is theirs.
Suggested fix: group cards by process with collapsed sections and counts, a
type/owner filter like the board's, and paging at 20.

### F-hostile-12: After deciding, Back shows a live Approve form; using it is a dead end
Severity: major
Heuristic: Visibility of system status; help users recover
Steps:
1. As bob, approve `TASK-0006` in tab 1 (`/decide`, run `e479580cbd`).
2. In tab 2 (the inbox loaded earlier, or the inbox reached with the Back
   button), click Reject on the same card.
Expected: the stale card is refreshed, or the error says "already approved
by you at 16:08; open TASK-0006".
Actual: no page carries `Cache-Control` (response headers: only `Server`,
`Content-Type`, `Content-Length`), so browsers serve the old inbox from
cache or bfcache with the form still there. The reject answers HTTP 400:
`<div class="card crit"><b>Not done.</b> run e479580cbd is not waiting for a
decision (status completed)<div class="why">RunError</div></div>` with no
link to the record or the run, and no Back link unless a Referer was sent.
The record is correctly `done`; the user has to retype the URL to see that.
Impact: every approver who keeps two tabs or uses Back; the same text answers
a double-clicked Approve, so the user cannot tell "I already did it" from
"someone else did it".
Suggested fix: `Cache-Control: no-store` on every HTML page; the "not
waiting" error links to the record and names who decided and when; a
`digest` mismatch should say the same.

### F-hostile-13: Downloaded files get percent-encoded names
Severity: major
Heuristic: Consistency and standards (RFC 6266 `filename*`)
Steps:
1. As alice, upload `my file (1).pdf` and `résumé 日本語 🚀.pdf` to
   `Quality/Nonconformances`.
2. GET `/download?as=alice&doc=rec:document:DOC-0296&v=1` and `...DOC-0297`.
Expected: the file saves as `my file (1).pdf` / `résumé 日本語 🚀.pdf`.
Actual: `Content-Disposition: attachment; filename="my%20file%20%281%29.pdf"`
and `filename="r%C3%A9sum%C3%A9%20%E6%97%A5%E6%9C%AC%E8%AA%9E%20%F0%9F%9A%80.pdf"`.
Browsers do not decode `filename=`, so the saved names are the encoded
strings. `a"b.txt` becomes `a%22b.txt`. The files page shows the right title.
Impact: everyone who downloads evidence with a space in its name, i.e. most
scans.
Suggested fix: `filename="<ascii fallback>"; filename*=UTF-8''<quoted>`.

### F-hostile-14: The CLI claims delivery to a person who does not exist, and dies with tracebacks on bad JSON
Severity: major
Heuristic: Error messages in plain language; honest status
Steps (scratch root):
1. `hubproto msg --from alice --to nobody --subject x`
2. `hubproto run task --as alice --record-json '{bad json'`
3. `hubproto run task --as alice --record-json '[1,2]'`
Expected: "nobody is not a member" (the shell says exactly that, 404); "the
record JSON does not parse at line 1 column 2".
Actual: `delivered to nobody via dm` (a message to a non-member is filed and
read by no one). Step 2 prints a Python traceback ending
`json.decoder.JSONDecodeError: Expecting property name enclosed in double
quotes: line 1 column 2 (char 1)`; step 3 prints `TypeError: object is not
iterable` and `Cannot convert dictionary update sequence element #0 to a
sequence`. `--record-json '{"amount": NaN}'` and `"$1,000"` are accepted
(`BILL-0001 ... ACME nan USD`, `BILL-0003 ... ACME $1,000 USD`), unlike the
shell which refuses `$1,000`.
Impact: operators scripting intake; a wrong `--to` is a silent black hole.
Suggested fix: `cmd_msg` checks membership like `post_msg`; wrap
`json.loads` and require a dict; route `--record-json` values through
`coerce` as `--field` already is.

### F-hostile-15: Control characters in a title corrupt the CLI listings
Severity: major
Heuristic: Output must stay readable whatever the input (robustness)
Steps (scratch root):
1. `hubproto run task --as alice --record-json '{"title":
   "<b>x</b>\u001b[31mRED\u001b[0m\r\nnewline\ttab", "assignee": "bob"}'`
2. `hubproto records`; `hubproto inbox-pending --as bob`; `hubproto record TASK-0001`.
Expected: one line per record, escapes shown as `\x1b` or stripped.
Actual (`cat -A`): `TASK-0001 task awaiting-complete-the-task Tasks
<b>x</b>^[[31mRED^[[0m^M$` then `newline^Itab assignees=bob$`: the row is
split across lines, the terminal is switched to red, and the carriage
return overwrites the start of the row. `inbox-pending` prints the full
5,000-character title of TASK-0002 on one line (`records` cuts at 120). The
shell escapes every one of these correctly (`&lt;b&gt;bold&lt;/b&gt;...` on
inbox card, record page, board, files, snapshot text, `<title>`).
Impact: anyone using the CLI listings on records that came in by email or
from a pasted document.
Suggested fix: a `clean()` for CLI output that replaces C0 controls and
newlines and truncates at the column width.

### F-hostile-16: A long hold reason or a long chip breaks the page width
Severity: major
Heuristic: Readability; WCAG 1.4.10 reflow
Steps:
1. As bob, POST `/hold` on `rec:task:TASK-0006` with a 3,000-character
   reason (or any reason of a few sentences).
2. GET `/record/rec:task:TASK-0006?as=bob`.
Expected: the reason wrapped under the heading.
Actual: `<h1>TASK-0006 <span class="chip">task</span> <span class="chip
ok">done</span> <span class="chip crit">on hold: &lt;b&gt;HOLD&lt;/b&gt;...HHHH
(3,000 chars)</span></h1>` inside `.chip{...white-space:nowrap}`, so the
heading becomes one 3,000-character line and `main` scrolls sideways; the
same chip repeats on every inbox card for the record (`on hold: ...; lift the
hold first`). The hold form has no limit (`<input name="reason" size="24">`).
Decision meanings of 5,000 characters are likewise shown whole in the
Decisions table (`REQ-2026-0038`).
Impact: anyone viewing a record someone put on hold with a pasted email as
the reason.
Suggested fix: `short()` on chips, `white-space:normal` for text chips, and
a length limit on reason and meaning.

### F-hostile-17: A signature with a blank meaning, or no digest, goes through
Severity: minor
Heuristic: Error prevention for the one field the product calls a signature
Steps:
1. As dave, POST `/decide` for a waiting run with `meaning=   ` (three
   spaces; the input is `required` only in the browser).
2. As bob, POST `/decide` with `digest=` (empty) for run `6b79d141ad`.
Expected: "say what your signature means"; "the item changed, reload".
Actual: step 1: 303; the Decisions table of `PR-00005` shows
`approve | approved approve the purchase` (a meaning the server invented).
Step 2: 303, run completed; only a wrong digest (`0`*32) is refused with `the
payload you saw differs from the pending payload; the item changed`.
Impact: an auditor reading the Decisions table; the "meaning" column is the
product's own stated purpose.
Suggested fix: refuse a blank `meaning` and a missing `digest` server-side.

### F-hostile-18: Error text leaks class names and internal grammar
Severity: minor
Heuristic: Error messages in plain language (Nielsen 9)
Steps: POST `/attach` with `record=` and no file; GET `/run/0?as=alice`;
start a task with JSON `{"assignee": "zed"}`; any RunError.
Expected: "Choose a record"; "no run starts with 0"; "zed is not a member".
Actual: `exists: no record &#x27;&#x27;<div class="why">KernelGuardError</div>`;
`run &#x27;0&#x27;: 26 matches<div class="why">RunError</div>`; the task is
filed, then fails with `Failed at file-the-task: no eligible approver: every
route was skipped or declined` (same text for `"assignee": "ap-reviewer"`);
`link-vocabulary: unknown link relation &#x27;relates-to&#x27;`. `OPTIONS`
and `PUT` answer Python's stock `Error code: 501` page outside the shell's
styling.
Impact: every person who hits an error; the `why` line looks like a hint but
is a Python class.
Suggested fix: drop `type(e).__name__` from the card; map guard ids to
sentences; validate assignee membership at the form.

### F-hostile-19: The message form accepts anything and the inbox shows it as-is
Severity: minor
Heuristic: Error prevention; readability
Steps: As alice POST `/msg` with `to=ap-reviewer`; `to=alice`;
`channel=carrier-pigeon`; `subject=` 5,000 × `S`; `body=line1\n\n\n\nline5`.
Expected: "cogs do not read messages"; "channel must be dm or email";
subject truncated; paragraphs kept.
Actual: all 303. The cog's inbox holds `dm/message HOSTILE to cog` that
nothing reads; alice receives her own message; bob's inbox shows `<span
class='chip'>carrier-pigeon/message</span>`; the 5,000-character subject is
printed whole in `<b>` (the body is cut at 600 with `…`) and is one
unbreakable word; newlines are collapsed because the card has no
`white-space: pre-line`. HTML and Markdown in subject and body are escaped
and shown literally, which is correct.
Impact: whoever reads a long inbox; a message sent to a cog by mistake
looks delivered.
Suggested fix: validate `channel` against the select, warn on cog
recipients, `short()` the subject, `pre-line` the body.

### F-hostile-20: Silent renames and truncation glitches
Severity: minor
Heuristic: Visibility of system status; consistency
Steps: upload `<script>alert(1)</script>.txt`, `empty.txt` (0 bytes), a
600-character name and `evil.exe`; start a task with form `title=FORM title`
and JSON `{"title": "JSON title"}`; JSON `{"title": ["a","b"]}`;
change-control with `affected_documents={"a":1}`; view Activity on any record
with JSON event data.
Actual: the first file is filed as `script&gt;.txt` (the name is rewritten
with no notice); the 0-byte file is `received` evidence with size `0`; the
600-character name and `.exe` are accepted and widen the Documents table;
`FORM title` wins and `JSON title` vanishes without a word; `TASK-0046`'s
headline is `[&quot;a&quot;, &quot;b&quot;]`; `affected_documents` becomes
`[&quot;{\&quot;a\&quot;:1}&quot;]` (a one-element list holding the literal
text); Activity cells are cut at 300 characters after escaping
(`esc(fmt(...))[:300]`) so cells end in fragments such as `...&quot;, &` with
no ellipsis.
Impact: minor confusion each; together they make the JSON box and the file
intake feel untrustworthy.
Suggested fix: reject names with `<>"\` and empty files with a message;
refuse a JSON key that is also a form field; `short()` after escaping, not
before.

## Summary

Three fixes pay for most of the above. First, make every POST idempotent and
every error page a form: a per-form token that turns a double-click, a Back-
and-resubmit or a retry race into a redirect to the first result (F-1, F-10,
F-12), and re-rendering the Start and decide forms with the submitted values
and the message beside the field instead of a bare "Not done" card (F-2,
F-6, F-7, F-8). Second, close the gap between what pages show and what the
server accepts: no default principal when `as` is blank (F-4), owner checks
on `/retry` and `/cancel` that match the page's own sentence (F-9), server-side
rejection of blank meanings, non-finite or negative money, and non-ISO dates
(F-7, F-8, F-17), and a record `due` that reflects the date the user typed
(F-5). Third, treat output as untrusted the way input already is: `short()`
and wrapping on chips, subjects and meanings, `Cache-Control: no-store` on
HTML, `filename*=` on downloads, C0-control stripping in the CLI, a 413 that
the client can actually read, and paging on the inbox (F-3, F-11, F-13,
F-15, F-16).
