"""POST handlers: every action lands on a page that says what happened, and every refusal re-renders the form."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote

from .. import authz
from ..runner import RunError
from ..kernel import KernelGuardError
from .forms import RESERVED_KEYS, ShellError, coerce, doc_type_for
from .handler import hidden_as, href, log_line
from .icons import icon
from .pages import may_file
from .text import esc, day, field_label, join_words, type_name


class ActionsMixin:
    def _f(self, name: str, default: str = "") -> str:
        return (self.form.get(name) or default).strip()

    def _file(self) -> tuple[str, bytes]:
        for _, filename, content in self.files:
            if filename:
                return filename, content
        return "", b""

    def _attachments(self) -> list[dict]:
        """Every uploaded file as a submission attachment: a controlled document typed by its extension."""
        return [{"name": filename, "content_b64": base64.b64encode(content).decode("ascii"), "doc_class": "controlled",
                 "doc_type": doc_type_for(filename)} for _, filename, content in self.files if filename]

    def _json_fields(self) -> dict:
        raw = self._f("json")
        if not raw:
            return {}
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ShellError(f"The JSON in 'Other fields' does not parse: {e}.", field="json") from None
        if not isinstance(record, dict):
            raise ShellError("The JSON in 'Other fields' must be an object, such as {\"lot\": \"L-42\"}.", field="json")
        return record

    def _collect_fields(self, rt: str, current: dict | None = None) -> dict:
        """The record fields from a Start, resubmit or retry form: the type's inputs, any other declared field, the JSON box.
        Unknown and system-owned fields are refused with the list of what is accepted."""
        td = self.hub.kernel.types.get(rt, {})
        specs = td.get("fields", {})
        system = set(td.get("system_fields") or [])
        declared = {name: (kind, spec) for name, kind, spec in self._start_spec(rt)}
        settable = [f for f in specs if f not in system]
        record: dict = {}
        for k, v in self._json_fields().items():
            if k == "attachments":
                record[k] = v
                continue
            if k in system and k not in declared:
                raise ShellError(f"'{k}' is set by the system, not at submission; leave it out of the JSON.", field="json")
            if k not in specs and k not in declared:
                raise ShellError(f"A {type_name(rt).lower()} has no field '{k}'; you may set {', '.join(settable)}.", field="json")
            if isinstance(v, (dict, list)) and not specs.get(k, "text").startswith(("list", "object")):
                raise ShellError(f"'{k}' must be a single value in the JSON, not a list or an object.", field="json")
            record[k] = v
        for k, v in self.form.items():
            if (k in RESERVED_KEYS and k not in declared) or not v.strip():  # a start input may be named like a form key (reason)
                continue
            if k in declared:
                kind, spec = declared[k]
            elif k in specs and k not in system:
                kind, spec = None, specs[k]
            elif k in system:
                raise ShellError(f"'{k}' is set by the system, not at submission.", field=k)
            else:
                raise ShellError(f"A {type_name(rt).lower()} has no field '{k}'; its fields are {', '.join(settable)}.")
            value = coerce(k, spec, v, kind)
            if value is None:
                continue
            if current is not None and current.get(k) == value:
                continue  # unchanged on a rework
            record[k] = value
        return record

    def _check_required(self, process: str, rt: str, record: dict, current: dict | None = None) -> None:
        have = {**(current or {}), **record}
        missing = [f for f in self._required_at_start(process, rt) if have.get(f) in (None, "", [], {})]
        if missing:
            labels = join_words(field_label(f).lower() for f in missing)
            raise ShellError(f"A {type_name(rt).lower()} needs {labels} before it can be filed.", field=missing[0])

    # ------------------------------------------------------------------ start
    def post_start(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        process = self._f("process")
        if process not in bundle["ops"]:
            raise ShellError(f"There is no process '{process}' in the release.", 404)
        rt = bundle["ops"][process].get("record_type", "")
        record = self._collect_fields(rt)
        self._check_required(process, rt, record)
        attachments = list(record.get("attachments") or []) + self._attachments()
        if attachments:
            record["attachments"] = attachments
        run_id = self.hub.runner.submit(process, {"record": record}, owner=user)
        state = self.hub.runner.load(run_id)
        log_line(f"run started run={run_id} process={process} by={user} record={state.get('record')} status={state['status']}")
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        number = rec["number"] if rec else self._process_name(process)
        if state["status"] == "waiting" and state.get("pending"):
            p = state["pending"]
            cog = p.get("assignee_type") == "cog"
            msg = (f"Filed {number}; waiting for {', '.join(p['assignees'])} at {p['title']}" + (f" (approval due {day(p['due'])})" if p.get("due") else "")
                   + (". It is a cog and takes its turn from its queue; this page shows what it decides" if cog else "") + ".")
        elif state["status"] == "failed":
            ev = self._failure_of(run_id)
            if self._org_stop(ev):
                headline, why = self._stop_reasons(run_id, ev)
                msg = (f"Filed {number}, but nobody may take it now: " + ("; ".join(why) if why else headline).rstrip(".")
                       + ". See below for who can fix it.")
            else:
                msg = f"Filed {number}, but it stopped at {ev.get('gate') or (ev.get('step') or '?').replace('-', ' ')}: {ev.get('reason', '')} Change what it names below and retry."
        elif state["status"] == "completed":
            msg = f"Filed {number}; it completed" + (f" and is now {rec['state']}" if rec else "") + "."
        else:
            msg = f"Filed {number}; the run is {state['status']}."
        self._redirect(self._once_finish(href("/run/" + run_id, user, done=msg)))

    # ----------------------------------------------------------------- decide
    def post_decide(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        verdict = self._f("verdict")
        if not run_id:
            raise ShellError("No item was named. Go back to your inbox and open the item again.")
        state = self.hub.runner.load(run_id)
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        number = rec["number"] if rec else f"run {run_id[:8]}"
        if verdict == "reject":
            meaning = self._f("reason") or self._f("meaning")
            if not meaning:
                raise ShellError(f"Say why you are rejecting {number}; the reason goes to {state['owner']}.", field="reason")
            fields: dict = {}
        else:
            verdict = "approve"
            meaning = self._f("meaning")
            fields = {}
            reserved = {"as", "run", "verdict", "meaning", "digest", "confirm", "reason", "once"}
            given = {k: v for k, v in self.form.items() if k not in reserved and v.strip()}
            if given and state.get("pending") and state["pending"].get("kind", "decision") == "decision" and rec:
                td = self.hub.kernel.types.get(rec["type"], {}).get("fields", {})
                allowed = self.hub.runner.allowed_decision_fields(state)
                refused = sorted(k for k in given if k not in allowed)
                if refused:
                    raise ShellError(f"A decision may not set {', '.join(refused)}; this approval accepts {', '.join(allowed) or 'no fields'}.", field=refused[0])
                for k, v in given.items():
                    value = coerce(k, td.get(k), v)
                    if value is not None:
                        fields[k] = value
            if not meaning:
                raise ShellError("Say what your signature means: in a few words, what you are confirming.", field="meaning")
            if self._f("confirm") not in ("1", "on", "true", "yes"):
                raise ShellError(f"Tick “I have read the item and the checks above” to approve {number}.", field="confirm")
        queue = [r["id"] for r in self._pending(user, "decision")]
        following = [i for i in queue[queue.index(run_id) + 1:]] if run_id in queue else []
        result = self.hub.runner.decide(run_id, user, verdict, meaning=meaning, payload_digest=self._f("digest") or None, fields=fields or None)
        log_line(f"decision run={run_id} record={state.get('record')} by={user} verdict={verdict} fields={sorted(fields)} meaning={meaning!r} status={result['status']}")
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        if verdict == "reject":
            p = result.get("pending") or {}
            msg = f"Rejected {number}" + (f"; returned to {', '.join(p.get('assignees') or [state['owner']])} for rework." if p.get("kind") == "resubmit"
                                        else f"; it is now {rec['state'] if rec else result['status']}, and {state['owner']} has been told.")
        elif result["status"] == "failed":
            ev = self._failure_of(run_id)
            msg = f"Your approval of {number} is recorded, but the next step stopped at {(ev.get('step') or '?').replace('-', ' ')}: {ev.get('reason', '')} {state['owner']} has been told."
        elif result["status"] == "waiting" and result.get("pending"):
            p = result["pending"]
            msg = f"Approved {number}; it now waits for {', '.join(p['assignees'])} at {p['title']}."
        else:
            msg = f"Approved {number}; it is now {rec['state'] if rec else result['status']}."
        still = {r["id"] for r in self._pending(user, "decision")}
        nxt = next((i for i in following if i in still), None)
        self._redirect(href("/inbox", user, open=nxt, done=msg) + (f"#r-{nxt}" if nxt else "#forme"))

    # ------------------------------------------------------- rework and stops
    def post_resubmit(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        done = self._once_done()
        if done:
            return self._redirect(done)
        state = self.hub.runner.load(run_id)
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        rt = rec["type"] if rec else state["workflow"]["record_type"]
        fields = self._collect_fields(rt, current=rec["fields"] if rec else {})
        self._check_required(state["process"], rt, fields, current=rec["fields"] if rec else {})
        attachments = list(fields.pop("attachments", None) or []) + self._attachments()
        note = self._f("note")
        result = self.hub.runner.resubmit(run_id, user, fields=fields or None, attachments=attachments or None, note=note)
        log_line(f"resubmit run={run_id} record={state.get('record')} by={user} fields={sorted(fields)} attachments={len(attachments)} status={result['status']}")
        number = rec["number"] if rec else f"run {run_id[:8]}"
        msg = self._outcome(f"Resubmitted {number}", result)
        self._redirect(self._once_finish(href("/record/" + quote(state["record"], safe=":"), user, done=msg) if state.get("record") else href("/run/" + run_id, user, done=msg)))

    def _outcome(self, lead: str, result: dict) -> str:
        if result["status"] == "waiting" and result.get("pending"):
            p = result["pending"]
            return f"{lead}; it now waits for {', '.join(p['assignees'])} at {p['title']}" + (f" (approval due {day(p['due'])})" if p.get("due") else "") + "."
        if result["status"] == "failed":
            ev = self._failure_of(result["id"])
            return f"{lead}, but it stopped again at {(ev.get('step') or '?').replace('-', ' ')}: {ev.get('reason', '')}"
        rec = self.hub.kernel.get(result["record"]) if result.get("record") else None
        return f"{lead}; it is now {rec['state'] if rec else result['status']}."

    def post_retry(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        done = self._once_done()
        if done:
            return self._redirect(done)
        state = self.hub.runner.load(run_id)
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        rt = rec["type"] if rec else self._record_type_of(state)
        fields = self._collect_fields(rt, current=rec["fields"] if rec else {}) if rt else {}
        attachments = list(fields.pop("attachments", None) or []) + self._attachments()
        result = self.hub.runner.retry(run_id, user, note=self._f("note"), fields=fields or None, attachments=attachments or None)
        log_line(f"retry run={run_id} record={result.get('record')} by={user} fields={sorted(fields)} status={result['status']}")
        number = rec["number"] if rec else f"run {run_id[:8]}"
        msg = self._outcome(f"Retried {number}" + (f" with {join_words(field_label(k).lower() for k in fields)} changed" if fields else ""), result)
        target = href("/record/" + quote(result["record"], safe=":"), user, done=msg) if result.get("record") else href("/run/" + run_id, user, done=msg)
        self._redirect(self._once_finish(target))

    def post_cancel(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        reason = self._f("reason")
        state = self.hub.runner.load(run_id)
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        word = type_name(rec["type"]).lower() if rec else "run"
        if not reason:
            raise ShellError(f"Say why you are cancelling this {word}; the reason stays in its history.", field="reason")
        if self._f("confirm") not in ("1", "on", "true", "yes"):
            raise ShellError(f"Tick “I want to cancel this {word}” to confirm; cancelling cannot be undone.", field="confirm")
        result = self.hub.runner.cancel(run_id, user, reason=reason)
        log_line(f"cancel run={run_id} record={result.get('record')} by={user} reason={reason!r}")
        msg = f"Cancelled {rec['number'] if rec else 'the run'}: {reason}"
        self._redirect(href("/record/" + quote(result["record"], safe=":"), user, done=msg) if result.get("record") else href("/run/" + run_id, user, done=msg))

    # --------------------------------------------------------------- messages
    def post_msg(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        to = self._f("to")
        if not to:
            raise ShellError("Choose who the message is for.", field="to")
        if user not in bundle["members"]:
            raise ShellError(f"{user} is not a member and cannot send messages.", 403)
        if to not in bundle["members"]:
            raise ShellError(f"{to} is not a member; messages go to members only.", 404, field="to")
        subject = self._f("subject")
        body = self._f("body")
        if not subject:
            raise ShellError("Give the message a subject.", field="subject")
        if not body:
            raise ShellError("Write the message; an empty one would tell the reader nothing.", field="body")
        channel = self._f("channel", "dm") or "dm"
        if channel not in ("dm", "email"):
            raise ShellError("Messages go by DM or by email.", field="channel")
        record = self._f("record") or None
        if record:
            rec = self.hub.kernel.get(record)
            if rec is None:
                raise ShellError(f"There is no item '{record}' to post the message on.", 404)
            self._require_read(rec["folder"])
            record = rec["locator"]
        reply = self.hub.send(user, to, subject[:300], body, channel=channel, record=record)
        is_cog = bundle["members"][to]["type"] == "cog"
        note = to == user
        replies = reply.get("replies") or ([reply] if reply.get("kind") == "reply" else [])
        answered = "; ".join(f"{r['from']} answered: {r.get('body', '')[:160]}" for r in replies[:3])
        msg = ("Note logged" if note else f"Message sent to {to}") + (f"; {answered}" if answered else "") + "."
        waiting = [t for t in (reply.get("queued") or []) if t.get("status") in ("queued", "working")]
        if waiting:
            from .queue import ordinal

            def where(t):
                pos = self.hub.queue.position(self.hub.queue.get(t["id"]) or t)
                return "working on it now" if pos == 0 else "next in its queue" if pos == 1 else f"{ordinal(pos)} in its queue"
            msg += " " + "; ".join(f"{t['cog']}: {where(t)}" for t in waiting) + ". The answer appears here when it is ready; you can carry on."
        back = self._f("return")
        if back.startswith(("/record/", "/document/", "/user/")):  # sent from a chatter or a profile: land back there
            return self._redirect(self._once_finish(back + ("&" if "?" in back else "?") + "done=" + quote(msg) + "#chatter"))
        self._redirect(self._once_finish(href("/inbox", user, done=msg) + "#messages"))

    # -------------------------------------------------------------- documents
    def post_upload(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        folder = self._f("folder")
        if folder not in bundle["folders"]:
            raise ShellError(f"There is no folder '{folder}' in the release.", 404)
        d = may_file(bundle, user, folder)
        if not d["allowed"]:
            raise ShellError(f"You cannot file documents in {folder}. " + self._why_text(d, "create", "document", folder), 403)
        filename, content = self._file()
        if not filename:
            raise ShellError("Choose a file to upload.", field="file")
        attach_to = self._f("attach_to") or None
        from .. import wopi as _wopi

        keep = self._f("keep") == "1"
        editable = _wopi.extension_of(filename) in _wopi.EDITABLE_EXTENSIONS and not keep
        doc = self.hub.dms.intake(folder=folder, by=user, content=content, filename=filename, channel="shell", sender=user,
                                  attach_to=attach_to, title=self._f("title"), doc_class="working" if editable else "external")
        note = "; it is a working document you can edit online" if editable else "; kept as received"
        self._redirect(self._once_finish(href("/files", user, folder=folder, done=f"Uploaded {filename} as {doc['number']}{note}.")))

    def _restart_form(self, rec: dict) -> str:
        """'Back to the start' for an item in flight or stopped, behind a disclosure that asks why."""
        ok, _ = self.hub.runner.may_restart(rec, self.user)
        if not ok or rec["state"] in ("open", "draft"):
            return ""
        return (f"<details class='inline restart'><summary class='button ghost'>{icon('retry')}Back to the start</summary>"
                f"<form method='post' action='/restart' class='card'>{hidden_as(self.user)}{self._once()}<input type='hidden' name='record' value='{esc(rec['locator'])}'>"
                f"<p class='why'>Stops the run it is in and returns {esc(rec['number'])} to the first step of its workflow; versions and history stay, "
                f"and its owner and whoever it waits for are told.</p>"
                f"<label class='f'><span>Why <b>*</b></span><input name='reason' required></label>"
                f"<div class='actions'><button class='reject'>{icon('retry')}Send it back to the start</button></div></form></details>")

    def _may_assign(self, rec: dict) -> bool:
        if self._viewing_cog() or not self._is_member():
            return False
        ok, _ = self.hub.runner.may_restart(rec, self.user)
        return ok or self.user in (rec.get("assignees") or []) or self.user == rec.get("created_by")

    def _assign_form(self, rec: dict) -> str:
        """'Assign' on an item: to a person (told in their inbox) or a cog (it works it from its queue)."""
        if not self._may_assign(rec):
            return ""
        members = self.bundle["members"]
        opts = "".join(f"<option value='{esc(n)}'>{esc(n)}{' (cog)' if members[n]['type'] == 'cog' else ''}</option>"
                       for n in sorted(members, key=lambda n: (members[n]["type"] != "cog", n)) if n != self.user)
        mine = self.err is not None and self.route == "/assign" and self.vals.get("record") == rec["locator"]
        chosen = self._val("to") if mine else ""
        if mine:
            opts = opts.replace(f"<option value='{esc(chosen)}'>", f"<option value='{esc(chosen)}' selected>", 1)
        err = self._form_error(["to", "instruction"]) if mine else ""
        bad = mine and self.err and self.err[1] == "instruction"
        return (f"<details class='inline restart'{' open' if mine else ''}><summary class='button ghost'>{icon('users')}Assign</summary>"
                f"<form method='post' action='/assign' class='card'>{hidden_as(self.user)}{self._once()}<input type='hidden' name='record' value='{esc(rec['locator'])}'>{err}"
                f"<label class='f'><span>To <b>*</b></span><select name='to' required><option value=''>choose…</option>{opts}</select></label>"
                f"<label class='f{' bad' if bad else ''}'><span>What to do <i>(required for a cog)</i></span><textarea name='instruction' rows='3' "
                f"placeholder='e.g. review the containment and draft the disposition'{' aria-invalid=true aria-describedby=err-instruction' if bad else ''}>{esc(self._val('instruction') if mine else '')}</textarea>"
                + (f"<span class='err' id='err-instruction'>{esc(self.err[0])}</span>" if bad else "") + "</label>"
                f"<p class='why'>A cog takes it from its queue, works it within its autonomy and reports here; a person is told in their inbox. "
                f"If the item waits at an approval, an assigned cog may also take that turn, within its own approval rules only.</p>"
                f"<div class='actions'><button>{icon('send')}Assign</button></div></form></details>")

    def post_assign(self):
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("record"))
        self.arg = rec["locator"]
        self._require_read(rec["folder"])
        if not self._may_assign(rec):
            raise ShellError(f"Only its owner, whoever it is assigned to, or someone who may approve a {rec['type']} assigns {rec['number']}.", 403)
        to, instruction = self._f("to"), (self._f("instruction") or "").strip()
        member = bundle["members"].get(to)
        if member is None:
            raise ShellError("Choose a member to assign it to.", field="to")
        page = ("/document/" if rec["type"] == "document" else "/record/") + quote(rec["locator"], safe=":")
        assignees = list(dict.fromkeys([*(rec.get("assignees") or []), to]))
        if member["type"] == "cog":
            from ..model import cog_availability
            from .queue import ordinal

            if not instruction:
                raise ShellError(f"Say what {to} should do with {rec['number']}.", field="instruction")
            ok, why = cog_availability(bundle, to)
            if not ok:
                raise ShellError(f"{to} is not available: {why}.")
            self.hub.kernel.assign(rec["locator"], assignees, by=self.user)
            self.hub.inbox.post(sender=self.user, to=to, record=rec["locator"], subject=f"Assigned {rec['number']} to {to}", body=instruction)
            task = self.hub.queue.submit(to, "assigned", by=self.user, title=instruction, record=rec["locator"],
                                         payload={"record": rec["locator"], "instruction": instruction})
            if task["status"] in ("queued", "working"):
                pos = self.hub.queue.position(task)
                where = "it is working on it" if pos == 0 else "it is next" if pos == 1 else f"it is {ordinal(pos)} in line"
                msg = f"Assigned {rec['number']} to {to}: {where}. It reports on the chatter."
            else:
                msg = f"Assigned {rec['number']} to {to}: " + ((task.get("result") or {}).get("summary") or task.get("error") or task["status"])[:200]
        else:
            self.hub.kernel.assign(rec["locator"], assignees, by=self.user)
            self.hub.inbox.post(sender=self.user, to=to, record=rec["locator"], subject=f"Assigned {rec['number']} to you",
                                body=instruction or f"{self.user} assigned {rec['number']} to you.")
            msg = f"Assigned {rec['number']} to {to}; they are told in their inbox."
        log_line(f"assign record={rec['number']} to={to} by={self.user}")
        self._redirect(self._once_finish(href(page, self.user, done=msg) + "#chatter"))

    def post_restart(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("record"))
        self.arg = rec["locator"]
        reason = self._f("reason")
        if not reason:
            raise ShellError("Say why it goes back to the start.", field="reason")
        try:
            rec = self.hub.runner.restart(rec["locator"], self.user, reason)
        except RunError as e:
            raise ShellError(str(e)) from None
        log_line(f"restart record={rec['number']} by={self.user} reason={reason[:80]}")
        page = "/document/" if rec["type"] == "document" else "/record/"
        self._redirect(self._once_finish(href(page + quote(rec["locator"], safe=":"), self.user,
                                              done=f"{rec['number']} is back at the start ({rec['state']}).")))

    def post_start_on(self):
        """Run a process on an existing item (an Ops button): the item is the submission."""
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        process = self._f("process")
        if process not in bundle["ops"]:
            raise ShellError(f"There is no process '{process}' in the release.", 404)
        rec = self.hub.kernel.require(self._f("record"))
        self._require_read(rec["folder"])
        op = bundle["ops"][process]
        wanted = op.get("record_type") or op.get("object")
        if wanted and rec["type"] != wanted:
            raise ShellError(f"{process} runs on a {type_name(wanted).lower()}, and {rec['number']} is a {type_name(rec['type']).lower()}.", 400)
        live = self.hub.runner.run_for_record(rec["locator"])
        if live and live["status"] in ("waiting", "submitted", "running"):
            raise ShellError(f"{rec['number']} is already in {live['process']} ({live['status']}); finish or cancel that run first.")
        given = {}
        if self._f("change_type") in ("minor", "major"):
            given["change_type"] = self._f("change_type")
        try:
            run_id = self.hub.runner.submit(process, {"record": given}, owner=user, record=rec["locator"])
        except RunError as e:
            raise ShellError(str(e)) from None
        state = self.hub.runner.load(run_id)
        where = (" It is waiting for " + ", ".join(state["pending"]["assignees"]) + ".") if state.get("pending") else f" It is {state['status']}."
        log_line(f"ops start process={process} record={rec['number']} by={user} -> {state['status']}")
        page = "/document/" if rec["type"] == "document" else "/record/"
        self._redirect(self._once_finish(href(page + quote(rec["locator"], safe=":"), user, done=f"Started {process.replace('-', ' ')} on {rec['number']}.{where}")))

    def post_attach(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        ref = self._f("record")
        if not ref:
            raise ShellError("Choose an item to attach to.")
        rec = self.hub.kernel.require(ref)
        d = may_file(bundle, user, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot attach documents in {rec['folder']}. " + self._why_text(d, "create", "document", rec["folder"]), 403)
        filename, content = self._file()
        if filename:
            doc = self.hub.dms.intake(folder=rec["folder"], by=user, content=content, filename=filename, channel="shell", sender=user,
                                      attach_to=rec["locator"], title=self._f("title"))
            msg = f"Attached {filename} to {rec['number']} as {doc['number']}."
        elif self._f("doc"):
            doc = self.hub.kernel.require(self._f("doc"))
            self._require_read(doc["folder"])
            self.hub.dms.attach(rec["locator"], doc["locator"], by=user)
            msg = f"Attached {doc['number']} to {rec['number']}."
        else:
            raise ShellError("Choose a file to upload or an existing document to attach.", field="file")
        self._redirect(self._once_finish(href("/record/" + quote(rec["locator"], safe=":"), user, done=msg) + "#documents"))

    def _doc_for_change(self) -> dict:
        self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            raise ShellError(f"{rec['number']} is not a document.")
        can, why_not = self._may_edit_document(rec)
        if not can:
            import re

            raise ShellError(f"You cannot change {rec['number']}: " + re.sub(r"<[^>]+>", "", why_not), 403)
        return rec

    def post_checkout(self):
        rec = self._doc_for_change()
        self.hub.dms.checkout(rec["locator"], by=self.user)
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user, done=f"{rec['number']} is checked out to you."))

    def post_checkin(self):
        rec = self._doc_for_change()
        done = self._once_done()
        if done:
            return self._redirect(done)
        locked = rec["fields"].get("checked_out_by")
        if not locked:
            raise ShellError(f"{rec['number']} is not checked out; check it out first, then check in the new version.", field="file")
        filename, content = self._file()
        if not filename:
            raise ShellError("Choose the file to check in.", field="file")
        self.hub.dms.checkin(rec["locator"], content, by=self.user, filename=filename, note=self._f("note") or "check-in")
        versions = self.hub.dms.versions(rec["locator"])
        self._redirect(self._once_finish(href("/document/" + quote(rec["locator"], safe=":"), self.user, done=f"Checked in {filename} as version {len(versions)} of {rec['number']}.")))

    def post_undo_checkout(self):
        rec = self._doc_for_change()
        try:
            self.hub.dms.undo_checkout(rec["locator"], by=self.user)
        except KernelGuardError as e:
            raise ShellError(f"{e.detail}.", 403 if e.guard == "checked-out" else 400) from None
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user, done=f"Check-out of {rec['number']} undone; nothing changed."))

    def post_link(self):
        user = self.user
        bundle = self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("record"))
        d = may_file(bundle, user, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot link items in {rec['folder']}. " + self._why_text(d, "create", "document", rec["folder"]), 403)
        target = self._f("target")
        if not target:
            raise ShellError("Name the item to link to.", field="target")
        t = self.hub.kernel.get(target)
        if t is None or not self._can_read(t["folder"])["allowed"]:
            raise ShellError(f"There is no item '{target}' you may read.", field="target")
        rel = self._f("rel")
        if rel not in self.hub.kernel.link_vocab:
            raise ShellError(f"'{rel}' is not a link relation; choose one from the list.", field="rel")
        self.hub.kernel.link(rec["locator"], rel, t["locator"], by=user)
        self._redirect(self._once_finish(href("/record/" + quote(rec["locator"], safe=":"), user, done=f"Linked {rec['number']} ({rel.replace('-', ' ')}) to {t['number']}.") + "#links"))

    def post_snapshot(self):
        user = self.user
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("record"))
        self._require_read(rec["folder"])
        doc = self.hub.dms.render_snapshot(rec["locator"], by=user)
        self._redirect(self._once_finish(href("/document/" + quote(doc["locator"], safe=":"), user, done=f"Saved a snapshot of {rec['number']} as {doc['number']}.")))

    def post_folder_new(self):
        self._need_release()
        parent, name = self._f("parent"), self._f("name")
        try:
            path = self.hub.library.add(self.hub, parent, name, by=self.user, rights_from=self._f("rights_from"))
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".", 403 if "only people" in str(e) else 400, field="name") from None
        log_line(f"library folder added path={path!r} by={self.user}")
        self._redirect(href("/files", self.user, folder=path, done=f"{path} is added, with the rights of {parent or self._f('rights_from')}."))

    def post_folder_remove(self):
        self._need_release()
        path = self._f("path")
        try:
            self.hub.library.remove(self.hub, path, by=self.user)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".", 403) from None
        log_line(f"library folder removed path={path!r} by={self.user}")
        parent = path.rsplit("/", 1)[0]
        self._redirect(href("/files", self.user, folder=parent, done=f"{path} is removed."))

    def _may_withdraw(self, rec: dict) -> str:
        """Empty when this person may withdraw the item: a draft (not in a running process) that is theirs (owner,
        author, filer, requester) or, for documents, any draft when they are a document controller."""
        from ..agent_records import START_STATES

        if rec["state"] not in START_STATES:
            return f"{rec['number']} is {rec['state']}: only a draft is withdrawn"
        run = self.hub.runner.run_for_record(rec["locator"])
        if run and run["status"] not in ("completed", "cancelled", "rejected"):
            return f"{rec['number']} is in a {run['process']} run: cancel the run instead"
        f = rec["fields"]
        if self.user in (rec.get("created_by"), f.get("owner"), f.get("author"), f.get("requested_by")):
            return ""
        if rec["type"] == "document" and "document-controller" in authz.roles_of(self.bundle, self.user):
            return ""
        return f"{rec['number']} is not yours: its owner, author or filer withdraws it"

    def post_withdraw_record(self):
        user = self.user
        self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        self._require_read(rec["folder"])
        why = self._may_withdraw(rec)
        if why:
            raise ShellError(why[:1].upper() + why[1:] + ".", 403)
        reason = self._f("reason")
        if not reason:
            raise ShellError("Say why you withdraw it; the reason stays in the history.", field="reason")
        self.hub.kernel.transition(rec["locator"], "withdrawn", by=user, reason=reason)
        log_line(f"withdraw record={rec['locator']} by={user} reason={reason!r}")
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), user, done=f"{rec['number']} is withdrawn: {reason}"))

    def post_hold(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        obj = self.hub.kernel.types.get(rec["type"], {}).get("object", rec["type"])
        d = authz.check(bundle, user, "approve", obj, rec["folder"])
        if not d["allowed"]:
            raise ShellError("Only approvers of this item may hold it. " + self._why_text(d, "approve", obj, rec["folder"]), 403)
        on = self._f("on", "1") in ("1", "true", "on")
        reason = self._f("reason")
        if not reason:
            raise ShellError(f"Say why you are {'putting' if on else 'releasing'} the hold; the reason stays in the history.", field="reason")
        self.hub.kernel.hold(rec["locator"], on, reason, by=user)
        log_line(f"hold record={rec['locator']} on={on} by={user} reason={reason!r}")
        msg = f"{rec['number']} put on hold: {reason}" if on else f"Hold on {rec['number']} released: {reason}"
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), user, done=msg))
