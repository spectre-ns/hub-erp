"""Configuration scripts on a change order's page (issue #34): who may change what it carries submits a script; every
reader sees each run: the script, where it ran (the script worker, or a local subprocess labelled weaker isolation),
its exit, its output and what it carried (``hubproto.scripts``)."""

from __future__ import annotations

import json

from .. import configjson, scripts
from .forms import ShellError
from .handler import details, hidden_as, log_line
from .icons import icon
from .text import chip, esc, when

OUTPUT_SHOWN = 40000


class ScriptsMixin:
    def _co_scripts_html(self, ctx: dict) -> str:
        co = ctx["co"]
        runs = scripts.runs_of(co)
        out = ""
        if runs:
            out += f"<h3 id='co-scripts'>{icon('file-text')}Configuration scripts</h3>"
            out += ("<p class='why'>Each script ran once, when it was submitted; what its output changes is carried above with the "
                    "change order's other changes and decided item by item. The release applies what is signed: it never runs a "
                    "script again.</p>")
            out += "".join(self._co_script_run(r) for r in reversed(runs))
        if ctx["mine"] and ctx["editable"]:
            out += self._co_script_form(co)
        return out

    def _co_script_run(self, r: dict) -> str:
        status = str(r.get("status") or "")
        local = r.get("isolation") != "container"
        cls = "crit" if status == "failed" and r.get("exit") == "tripwire" else ("warn" if local or status in ("failed", "refused") else "info")
        chips = chip(scripts.STATUS.get(status, status) or "-", "ok" if status == "carried" else ("crit" if status == "failed" else None))
        chips += " " + chip("script worker" if not local else "weaker isolation: local subprocess", "info" if not local else "warn")
        head = (f"<h4 id='{esc(r.get('id'))}'>{esc(r.get('id'))}: {esc(r.get('name'))} {chips}</h4>"
                f"<p>Submitted by {self._co_who(r.get('by'))} <span class='why'>{esc(when(r.get('at')))}</span>; "
                f"ran {esc(str(r.get('seconds')))} s: {esc(r.get('detail'))}.</p>"
                f"<p class='why'>{esc(r.get('isolation_note'))} {esc(scripts.protection_words(r.get('protection') or {}))}</p>")
        facts = [("Script sha256", r.get("script_sha256")), ("Snapshot it read (sha256)", r.get("snapshot_sha256")),
                 ("Taken from release", r.get("snapshot_release")), ("Output sha256", r.get("output_sha256") or "no output")]
        body = "<dl class='facts'>" + "".join(f"<dt>{esc(k)}</dt><dd><code>{esc(str(v or '-'))}</code></dd>" for k, v in facts) + "</dl>"
        if r.get("reason"):
            body += f"<p><b>Why:</b> {esc(r['reason'])}</p>"
        if r.get("summary"):
            body += "<p><b>What its output changes:</b></p><ul>" + "".join(f"<li>{esc(s)}</li>" for s in r["summary"]) + "</ul>"
        if r.get("refusals"):
            body += "<p><b>Why nothing was carried:</b></p><ul>" + "".join(f"<li>{esc(s)}</li>" for s in r["refusals"]) + "</ul>"
        if r.get("tripped"):
            t = r["tripped"]
            body += (f"<p class='err' role='note'><b>The tripwire stopped it:</b> {esc(t.get('event'))} {esc(t.get('args'))}: "
                     f"{esc(t.get('why'))}.</p>")
        lines = str(r.get("script") or "").split("\n")
        rows = "".join(f"<tr><th scope='row'>{i}</th><td><pre class='src'>{esc(line) or ' '}</pre></td></tr>" for i, line in enumerate(lines, start=1))
        body += details(f"{icon('file-text')}The script ({len(lines)} lines)",
                        f"<div class='tbl'><table aria-label='The script'><thead><tr><th scope='col'>Line</th><th scope='col'>Text</th></tr></thead>"
                        f"<tbody>{rows}</tbody></table></div>", open_=True)
        if r.get("output") is not None:
            try:
                shown = json.dumps(json.loads(r["output"]), indent=2, sort_keys=True, ensure_ascii=False)
            except ValueError:
                shown = str(r["output"])
            cut = len(shown) > OUTPUT_SHOWN
            body += details(f"{icon('file-text')}Its output (the configuration it proposes)",
                            f"<pre class='cell' aria-label='The output'>{esc(shown[:OUTPUT_SHOWN])}</pre>"
                            + ("<p class='why'>Shown in part; the whole output is on the change order (its sha256 above).</p>" if cut else ""))
        for k, label in (("stdout", "What it printed (the script's own output, not checked)"),
                         ("stderr", "Its error output (the script's own output, not checked)"),
                         ("raw_output", "What else it wrote (outside its answer: not checked)")):
            if r.get(k):
                dropped = int(r.get(f"{k}_dropped") or 0)
                body += details(label, f"<pre class='cell'>{esc(r[k])}</pre>"
                                + (f"<p class='why'>Cut off: {dropped:,} more characters were not kept.</p>" if dropped else ""))
        return f"<div class='card {cls}'>{head}{body}</div>"

    def _co_script_form(self, co: dict) -> str:
        act, fid = "/co-script-run", "script-run"
        err = ""
        if self.err and self.route == act:
            self._err_shown = True
            err = f"<p class='err' role='alert'>{esc(self.err[0])}</p>"
        where, why = scripts.where_it_runs()
        inner = ("<p class='why'>A short Python script that reads the configuration in force as <code>config</code> (the document "
                 "<code>hubproto config export</code> writes) and leaves what it proposes in <code>result</code>: the whole document "
                 "or a part, with its <code>schema</code>, <code>hub</code>, <code>release</code> and <code>versions</code>. It may import "
                 f"only {esc(', '.join(scripts.scriptcheck.ALLOWED_IMPORTS))}. It runs once, now; its output is carried as a configuration "
                 "file is, and the release applies that output, never the script.</p>"
                 f"<p class='{'err' if where == 'refused' else 'why'}'>{esc(why)}</p>"
                 "<label class='f'><span>Script</span><textarea name='script' rows='12' spellcheck='false' "
                 "style='font-family:var(--mono,monospace)'></textarea></label>"
                 "<label class='f'><span>Or a file <i>(.py)</i></span><input type='file' name='file' accept='.py,text/x-python,text/plain'></label>"
                 "<label class='f'><span>Why <i>(optional)</i></span><input name='reason'></label>"
                 f"<div class='actions'><button class='secondary'>{icon('upload')}Run it and carry its output</button></div>")
        form = (f"<form method='post' action='{act}' enctype='multipart/form-data' class='sform' id='f-{fid}' aria-label='Run a configuration script'>"
                f"{hidden_as(self.user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                f"<input type='hidden' name='form' value='{fid}'>{err}{inner}</form>")
        return details(f"{icon('file-text')}Run a configuration script (Python)", form, open_=bool(self.err and self.route == act))

    def post_co_script_run(self):
        co = self._co_post()
        if co is None:
            return
        filename, content = self._file()
        if content:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                raise ShellError(f"{filename} is not UTF-8 text: a configuration script is Python source.", field="file") from None
        else:
            text = self.form.get("script") or ""
            filename = "script.py"
        if not text.strip():
            raise ShellError("Write the script, or choose a .py file.", field="script")
        try:
            out = scripts.propose(self.hub, text, by=self.user, co_ref=co["locator"], reason=self._f("reason"), name=filename)
        except configjson.Refused as e:
            raise ShellError(f"{filename} was not carried; nothing was: " + "; ".join(e.reasons) + ".", field="script") from None
        run = out["script"]
        log_line(f"configuration script {run['id']} on {co['number']} by={self.user} isolation={run['isolation']} exit={run['exit']} status={run['status']}")
        if not out["changed"]:
            return self._co_done(co, f"{filename} ran; its output says what is in force: nothing to carry.", "co-scripts")
        n = len(out["summary"])
        self._co_done(co, f"{filename} ran ({'script worker' if run['isolation'] == 'container' else 'weaker isolation: a local subprocess'}); "
                          f"carried on {co['number']}: {n} change{'s' if n != 1 else ''}. Request review next.", "co-scripts")
