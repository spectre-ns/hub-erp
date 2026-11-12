"""The agent harness in the shell: give a cog a task from its profile page, follow the run, confirm proposed actions."""

from __future__ import annotations

import json
from urllib.parse import quote

from ..agent import Agent, AgentStore, agents_enabled
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, short, when



class AgentsMixin:
    def _agent_section(self, cog: str, prof: dict) -> str:
        user = self.user
        store = AgentStore(self.hub.root / "state" / "agents")
        runs = store.runs(cog, limit=12)
        from ..model import cog_availability

        ok, why = cog_availability(self.bundle, cog)
        body = f"<h2>{icon('zap')} Working on its own</h2>"
        if not ok:
            return body + f"<div class='card crit' role='status'><b>Not available.</b> {esc(why)}. A cog comes with its model; until the model can be reached it takes no work, no chat and no tasks, and Gates route past it.</div>"
        body += (f"<p>Autonomy <span class='chip'>derived</span>: for each action, {esc(cog)} acts where the SOPs give it or its roles the "
                 "permission or the step, proposes where an SOP says a person confirms, and otherwise only reads and says what should be done. "
                 "Changing it is a change to those SOPs, through a change order.</p>")
        duties = [d for d in (self.bundle.get("duties") or []) if d.get("cog") == cog]
        if duties:
            body += ("<p><b>Duties</b> (from the SOPs that own the work):</p><ul>"
                     + "".join(f"<li>{esc(d['source']['text'])} <span class='why'>{self._rule_link(d['source'])}</span></li>" for d in duties) + "</ul>")
        elif prof.get("duties"):
            body += "<p><b>Duties</b> (taken up once a day):</p><ul>" + "".join(f"<li>{esc(d)}</li>" for d in prof["duties"]) + "</ul>"
        if not agents_enabled():
            body += "<div class='card warn'>Agent runs are switched off in this hub (HUBPROTO_AGENTS=off).</div>"
        elif self._is_member() and not self._viewing_cog() and (self.bundle["members"].get(user) or {}).get("type") == "person":
            err = self._form_error(["goal"]) if self.route == "/agent-task" else ""
            body += (f"<form method='post' action='/agent-task' class='card'>{hidden_as(user)}{self._once()}<input type='hidden' name='cog' value='{esc(cog)}'>{err}"
                     f"<label class='f'><span>Give {esc(cog)} a task <b>*</b></span><textarea name='goal' rows='2' required "
                     f"placeholder='e.g. find nonconformances older than a week and remind their owners'>{esc(self._val('goal') if self.route == '/agent-task' else '')}</textarea></label>"
                     f"<div class='actions'><button>{icon('play')}Add to its queue</button><span class='why'>It takes it when it gets to it, looks things up, then acts where the SOPs let it, within a budget of 5 actions; "
                     f"you are told what it did.</span></div></form>")
        runs = [r for r in runs if self._run_seen_by(r.get("by"), cog)]
        if runs:
            body += "<h3>Recent runs</h3>" + table(["When", "Asked", "By", "Status", "Actions"], [
                [esc(when(r["at"])), f"<a href='{href('/agent-run/' + r['run'], user)}'>{esc(short(self._run_title(r), 90))}</a>", esc(r["by"]),
                 f"<span class='chip {'ok' if r['status'] in ('done', 'confirmed') else 'warn'}'>{esc(r['status'])}</span>", esc(r["actions"])] for r in runs])
        return body

    def _run_seen_by(self, by: str | None, cog: str | None) -> bool:
        """An agent run (its goal, tool calls and results) is shown to whoever asked for it, the cog, and policy owners."""
        return self.user in (by, cog) or self._policy_owner()

    def _run_title(self, run: dict) -> str:
        """A run is titled by what was asked: the queue task's title, not the prompt the hub wrote for the cog."""
        rid = run.get("run")
        for t in self.hub.queue.list(cog=run.get("cog"), status=("done", "failed"), limit=200, newest_first=True):
            if (t.get("result") or {}).get("run") == rid:
                return t.get("title") or run.get("goal") or ""
        return run.get("goal") or ""

    def page_agent_run(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see agent runs.", 403)
        evs = AgentStore(self.hub.root / "state" / "agents").load(self.arg)
        if not evs:
            raise ShellError(f"There is no agent run '{self.arg}'.", 404)
        start = evs[0]
        cog = start.get("cog")
        if not self._run_seen_by(start.get("by"), cog):
            raise ShellError(f"This run was asked by {start.get('by') or 'someone else'}; only they, the cog and the policy owners see what it read and did.", 403)
        end = next((e for e in reversed(evs) if e["type"] == "finished"), None)
        title = self._run_title({"run": self.arg, "cog": cog, "goal": start.get("goal")})
        body = (f"<h1>{self._avatar(cog, size='sm')} {esc(cog)}: {esc(short(title, 160))}</h1>"
                + (f"<details><summary>What the cog was told</summary><p class='why' style='white-space:pre-wrap'>{esc(start.get('goal'))}</p></details>"
                   if title != start.get("goal") else "") +
                f"<p class='why'>Started {esc(when(start['at']))} by {esc(start.get('by'))} ({esc(start.get('trigger'))}); autonomy {esc(start.get('mode'))}; "
                f"budget {esc((start.get('budget') or {}).get('actions'))} actions.</p>")
        rows = []
        for e in evs:
            if e["type"] != "tool":
                continue
            a = e.get("arguments") or {}
            words = "; ".join(f"{k.replace('_', ' ')}: {short(v if isinstance(v, str) else json.dumps(v, default=str), 120)}" for k, v in a.items()) if isinstance(a, dict) else str(a)
            rows.append([f"{icon('edit' if e.get('write') else 'search')}" + esc(e["tool"].replace("_", " ")), esc(short(words, 260)),
                         "<span class='chip ok'>ok</span>" if e.get("ok") else "<span class='chip crit'>refused</span>", f"<span class='muted'>{esc((e.get('result') or '')[:300])}</span>"])
        body += "<h2>Steps</h2>" + table(["Tool", "With", "", "Result"], rows, "No steps yet.")
        if end:
            body += f"<h2>Summary <span class='chip {'ok' if end['status'] in ('done', 'confirmed') else 'warn'}'>{esc(end['status'])}</span></h2><p style='white-space:pre-wrap'>{esc(end.get('summary'))}</p>"
            if end.get("status") == "proposed" and end.get("proposed"):
                body += "<h3>Waiting for confirmation</h3><ol>" + "".join(f"<li><b>{esc(p['tool'])}</b> <code>{esc(json.dumps(p['arguments'], default=str)[:240])}</code></li>" for p in end["proposed"]) + "</ol>"
                if user == start.get("by") or self._policy_owner():
                    body += (f"<form method='post' action='/agent-confirm'>{hidden_as(user)}{self._once()}<input type='hidden' name='run' value='{esc(start['run'])}'>"
                             f"<button>{icon('check')}Confirm and carry them out</button> <span class='why'>as {esc(cog)}, through the same governed paths</span></form>")
        else:
            body += "<p class='muted'>Still running.</p>"
        self._page(f"{cog} run", body, crumbs=[("People", href("/people", user)), (cog, href("/user/" + cog, user)), ("Run", None)])

    def post_agent_task(self):
        if self._viewing_cog():
            raise ShellError("Nothing can be started in a cog's name.", 403)
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        cog, goal = self._f("cog"), self._f("goal")
        if not goal:
            raise ShellError("Say what the cog should do.", field="goal")
        from ..model import cog_availability

        if (self.bundle["members"].get(cog) or {}).get("type") != "cog":
            raise ShellError(f"{cog} is not a cog of the organization.", 404)
        if not agents_enabled():
            raise ShellError("Agent runs are switched off in this hub (HUBPROTO_AGENTS=off).")
        ok, why = cog_availability(self.bundle, cog)
        if not ok:
            raise ShellError(f"{cog} is not available: {why}.")
        task = self.hub.queue.submit(cog, "task", by=self.user, title=goal, payload={"goal": goal})
        log_line(f"cog task {task['id']} queued for {cog} by {self.user}: {task['status']}")
        res = task.get("result") or {}
        if task["status"] == "done" and res.get("run"):
            return self._redirect(self._once_finish(href("/agent-run/" + res["run"], self.user, done=f"{cog}: {res['status']}, {res['actions']} action(s).")))
        if task["status"] == "failed":
            raise ShellError(f"{cog} could not take the task: {task.get('error')}")
        from .queue import ordinal

        pos = self.hub.queue.position(task)
        where = "it is working on it now" if pos == 0 else "it is next" if pos == 1 else f"it is {ordinal(pos)} in line"
        self._redirect(self._once_finish(href("/user/" + cog, self.user, done=f"Given to {cog}: {where}. You are told when it has finished.") + "#cog-work"))

    def post_agent_confirm(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        run = self._f("run")
        evs = AgentStore(self.hub.root / "state" / "agents").load(run)
        if not evs:
            raise ShellError(f"There is no agent run '{run}'.", 404)
        if self.user != evs[0].get("by") and not self._policy_owner():
            raise ShellError("Only the person who gave the task, or a policy owner, confirms its actions.", 403)
        try:
            out = Agent(self.hub, evs[0]["cog"]).confirm(run, self.user)
        except ValueError as e:
            raise ShellError(str(e)) from None
        ok = sum(1 for r in out["results"] if "error" not in r["result"])
        self._redirect(self._once_finish(href("/agent-run/" + run, self.user, done=f"Carried out {ok} of {len(out['results'])} action(s).")))


class NewMemberMixin:
    def _new_member_forms(self) -> str:
        """On the People page: propose a new person or a new cog (both become a policy proposal)."""
        user, bundle = self.user, self.bundle
        if (bundle["members"].get(user) or {}).get("type") != "person":
            return ""
        roles = "".join(f"<label class='check'><input type='checkbox' name='roles' value='{esc(r)}'> {esc(r.replace('-', ' '))}</label>" for r in sorted(bundle.get("roles") or []))
        chosen = self._val("model") if self.route == "/new-cog" else self._default_model()
        personas = sorted((bundle.get("personas") or {}).items())
        servable = [c for c in self._model_choices() if c["available"]]
        err = self._form_error(["name", "roles"]) if self.route in ("/new-person", "/new-cog") else ""
        return (f"<h2>{icon('plus')} Add someone</h2><p class='why'>A new member is a change to the organization model: it goes to the policy owners "
                f"as a proposal and takes effect when they release it. Roles decide what a member may do.</p>{err}"
                f"<details class='card'><summary><b>A new person</b></summary><form method='post' action='/new-person'>{hidden_as(user)}{self._once()}"
                f"<label class='f'><span>Name (as they sign in) <b>*</b></span><input name='name' required pattern='[a-z][a-z0-9-]+' value='{esc(self._val('name') if self.route == '/new-person' else '')}'></label>"
                f"<fieldset><legend>Roles <b>*</b></legend><div class='checks'>{roles}</div></fieldset>"
                f"<div class='actions'><button>{icon('send')}Propose</button></div></form></details>"
                + ("<details class='card'><summary><b>A new cog</b></summary><p class='card warn'>No model can be reached from this hub, so a new cog "
                   "could not answer: a cog comes with its model. Ask an administrator to make Ollama reachable (or set an Anthropic key), "
                   "then add the cog.</p></details>" if not servable else "")
                + (f"<details class='card'><summary><b>A new cog</b></summary><form method='post' action='/new-cog'>{hidden_as(user)}{self._once()}"
                f"<label class='f'><span>Name <b>*</b></span><input name='name' required pattern='[a-z][a-z0-9-]+' placeholder='calibration-assistant' value='{esc(self._val('name') if self.route == '/new-cog' else '')}'></label>"
                f"<label class='f'><span>Model <i>(what Ollama serves now; a cog comes with its model)</i></span><select name='model' required>"
                + "".join(f"<option value='{esc(c['value'])}'{' selected' if c['value'] == chosen else ''}{'' if c['available'] else ' disabled'}>{esc(c['label'])}</option>"
                          for c in self._model_choices()) + "</select></label>"
                f"<label class='f'><span>About: what it does, in one paragraph <b>*</b></span><textarea name='about' rows='3' required>{esc(self._val('about') if self.route == '/new-cog' else '')}</textarea></label>"
                f"<label class='f'><span>Persona: how it behaves</span><select name='persona'>"
                + "".join(f"<option value='{esc(n)}'>{esc(n)}: {esc(p.get('stance') or '')}, {esc(p.get('tone') or '')}</option>" for n, p in personas)
                + "</select></label>"
                f"<fieldset><legend>Roles <b>*</b></legend><div class='checks'>{roles}</div></fieldset>"
                f"<p class='why'>What it follows and what it may do come from the SOPs that name it or its roles; its duties are sentences in "
                f"the SOP that owns the work. Give it only the roles its work needs.</p>"
                f"<div class='actions'><button>{icon('send')}Propose</button></div></form></details>" if servable else ""))

    def _sop_names(self) -> list[str]:
        """The procedures a member can follow: SOPs and work instructions, not the organization model, cog profiles or references."""
        out = []
        for n in sorted(self.hub.frames.frames):
            f = self.hub.frames.get(n)
            if f.scope == "reference" or n in ("org-model", "cog-profiles"):
                continue
            out.append(n)
        return out

    def _model_choices(self) -> list[dict]:
        from ..model import available_models

        return available_models()["choices"]

    def _default_model(self) -> str:
        """For a new cog: the model the organization's cogs already use (when Ollama serves it), else the largest
        model Ollama serves, else nothing (no model, no cog)."""
        from collections import Counter

        # Never one of Ollama's cloud models by default: sending a cog's work off the machine is chosen, not defaulted.
        ch = [c for c in self._model_choices() if c["available"] and not c.get("cloud")]
        served = {c["value"] for c in ch}
        used = Counter(p.get("model") for p in ((self.bundle or {}).get("cogs") or {}).values() if p.get("model") in served)
        if used:
            return used.most_common(1)[0][0]
        return ch[0]["value"] if ch else ""

    def post_cog_model(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        cog, model = self._f("cog"), self._f("model")
        current = ((self.bundle.get("cogs") or {}).get(cog) or {}).get("model")
        if not model or model == current:
            raise ShellError(f"{cog} already uses {current}; choose another model to propose." if model else "Choose a model.")
        try:
            p = self.hub.propose_cog_model(cog, model, by=self.user)
        except ValueError as e:
            raise ShellError(str(e)) from None
        from ..model import OFF_MACHINE, resolve_model

        away = f" {model} {OFF_MACHINE}: {cog}'s prompts would leave this machine." if resolve_model(model)["backend"] == "ollama-cloud" else ""
        if p.get("change_order"):
            log_line(f"cog model change on {p['change_order']} cog={cog} model={model} by={self.user}")
            return self._redirect(self._once_finish(href("/record/" + quote(p["change_order"]), self.user,
                                                         done=f"{cog} → {model} is written into the cog profiles on {p['change_order']}. "
                                                              f"{cog} keeps {current} until that revision is approved and the change order is released." + away)))
        log_line(f"cog model proposed cog={cog} model={model} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user,
                                              done=f"Proposed {model} for {cog} as #{p.get('number')}. A cog's model is part of the policy: "
                                                   f"{cog} keeps {current} until both policy owners approve this change and one of them releases it." + away)))

    def _multi(self, name: str) -> list[str]:
        vals = (getattr(self, "multi", None) or {}).get(name)
        if vals is None:
            vals = [v for v in (self.form.get(name) or "").split(",") if v]
        return [v.strip() for v in vals if v.strip()]

    def post_new_person(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            p = self.hub.propose_new_person(name=self._f("name"), roles=self._multi("roles"), by=self.user)
        except ValueError as e:
            raise ShellError(str(e), field="name") from None
        if p.get("change_order"):
            return self._redirect(self._once_finish(href("/record/" + quote(p["change_order"]), self.user,
                                                         done=f"{self._f('name')} is written into the organization model on {p['change_order']}.")))
        log_line(f"new person proposed name={self._f('name')} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"Proposed {self._f('name')} as a new person; the policy owners review it here.")))

    def post_new_cog(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            p = self.hub.propose_new_cog(name=self._f("name"), roles=self._multi("roles"), model=self._f("model"),
                                         about=self._f("about") or self._f("prompt"), persona=self._f("persona"), by=self.user)
        except ValueError as e:
            raise ShellError(str(e), field="name") from None
        if p.get("change_order"):
            return self._redirect(self._once_finish(href("/record/" + quote(p["change_order"]), self.user,
                                                         done=f"{self._f('name')} is written into the organization model and the cog profiles on {p['change_order']}.")))
        log_line(f"new cog proposed name={self._f('name')} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"Proposed {self._f('name')} as a new cog; the policy owners review it here.")))
