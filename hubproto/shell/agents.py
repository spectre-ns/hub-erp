"""The agent harness in the shell: give a cog a task from its profile page, follow the run, confirm proposed actions."""

from __future__ import annotations

import json

from ..agent import Agent, AgentStore, agents_enabled
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, when

MODE_WORDS = {"act": "acts on its own through the system's governed paths", "propose": "proposes actions; a person confirms them",
              "read": "only looks and reports"}


class AgentsMixin:
    def _agent_section(self, cog: str, prof: dict) -> str:
        user = self.user
        mode = prof.get("autonomy") or "propose"
        store = AgentStore(self.hub.root / "state" / "agents")
        runs = store.runs(cog, limit=12)
        from ..model import cog_availability

        ok, why = cog_availability(self.bundle, cog)
        body = f"<h2>{icon('zap')} Working on its own</h2>"
        if not ok:
            return body + f"<div class='card crit' role='status'><b>Not available.</b> {esc(why)}. A cog comes with its model; until the model can be reached it takes no work, no chat and no tasks, and Gates route past it.</div>"
        body += (f"<p>Autonomy <span class='chip {'ok' if mode == 'act' else 'warn'}'>{esc(mode)}</span>: {esc(cog)} {esc(MODE_WORDS.get(mode, ''))}. "
                 f"Set in its profile; changing it is a policy change two policy owners approve.</p>")
        if prof.get("duties"):
            body += "<p><b>Duties</b> (taken up once a day):</p><ul>" + "".join(f"<li>{esc(d)}</li>" for d in prof["duties"]) + "</ul>"
        if not agents_enabled():
            body += "<div class='card warn'>Agent runs are switched off in this hub (HUBPROTO_AGENTS=off).</div>"
        elif self._is_member() and not self._viewing_cog() and (self.bundle["members"].get(user) or {}).get("type") == "person":
            err = self._form_error(["goal"]) if self.route == "/agent-task" else ""
            body += (f"<form method='post' action='/agent-task' class='card'>{hidden_as(user)}{self._once()}<input type='hidden' name='cog' value='{esc(cog)}'>{err}"
                     f"<label class='f'><span>Give {esc(cog)} a task <b>*</b></span><textarea name='goal' rows='2' required "
                     f"placeholder='e.g. find nonconformances older than a week and remind their owners'>{esc(self._val('goal') if self.route == '/agent-task' else '')}</textarea></label>"
                     f"<div class='actions'><button>{icon('play')}Start</button><span class='why'>It looks things up, then acts within its autonomy and a budget of 5 actions; "
                     f"you are told what it did.</span></div></form>")
        if runs:
            body += "<h3>Recent runs</h3>" + table(["When", "Goal", "By", "Status", "Actions"], [
                [esc(when(r["at"])), f"<a href='{href('/agent-run/' + r['run'], user)}'>{esc(r['goal'][:90])}</a>", esc(r["by"]),
                 f"<span class='chip {'ok' if r['status'] in ('done', 'confirmed') else 'warn'}'>{esc(r['status'])}</span>", esc(r["actions"])] for r in runs])
        return body

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
        end = next((e for e in reversed(evs) if e["type"] == "finished"), None)
        body = (f"<h1>{self._avatar(cog, size='sm')} {esc(cog)}: {esc(start.get('goal'))}</h1>"
                f"<p class='why'>Started {esc(when(start['at']))} by {esc(start.get('by'))} ({esc(start.get('trigger'))}); autonomy {esc(start.get('mode'))}; "
                f"budget {esc((start.get('budget') or {}).get('actions'))} actions.</p>")
        rows = []
        for e in evs:
            if e["type"] != "tool":
                continue
            args = json.dumps(e.get("arguments") or {}, default=str)
            rows.append([f"{icon('edit' if e.get('write') else 'search')}" + esc(e["tool"]), f"<code>{esc(args[:200])}</code>",
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
        agent = Agent(self.hub, cog)
        try:
            prep = agent.prepare(goal, by=self.user, trigger="task")
        except ValueError as e:
            raise ShellError(str(e)) from None
        tools = prep["tools"]

        def call_tool(name, args):
            with self.lock:
                return tools.execute(name, args)

        with self._unlocked():
            out = agent.think_safely(prep, call_tool=call_tool)
        res = agent.conclude(prep, out)
        log_line(f"agent run={res['run']} cog={cog} by={self.user} status={res['status']} actions={res['actions']}")
        self._redirect(self._once_finish(href("/agent-run/" + res["run"], self.user, done=f"{cog}: {res['status']}, {res['actions']} action(s).")))

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
        frames = "".join(f"<label class='check'><input type='checkbox' name='follows' value='{esc(n)}'> {esc(n)}</label>"
                         for n in sorted(self.hub.frames.frames) if self.hub.frames.get(n).scope != "reference")
        err = self._form_error(["name", "roles"]) if self.route in ("/new-person", "/new-cog") else ""
        return (f"<h2>{icon('plus')} Add someone</h2><p class='why'>A new member is a change to the organization model: it goes to the policy owners "
                f"as a proposal and takes effect when they release it. Roles decide what a member may do.</p>{err}"
                f"<details class='card'><summary><b>A new person</b></summary><form method='post' action='/new-person'>{hidden_as(user)}{self._once()}"
                f"<label class='f'><span>Name (as they sign in) <b>*</b></span><input name='name' required pattern='[a-z][a-z0-9-]+' value='{esc(self._val('name') if self.route == '/new-person' else '')}'></label>"
                f"<fieldset><legend>Roles <b>*</b></legend><div class='checks'>{roles}</div></fieldset>"
                f"<div class='actions'><button>{icon('send')}Propose</button></div></form></details>"
                f"<details class='card'><summary><b>A new cog</b></summary><form method='post' action='/new-cog'>{hidden_as(user)}{self._once()}"
                f"<label class='f'><span>Name <b>*</b></span><input name='name' required pattern='[a-z][a-z0-9-]+' placeholder='calibration-assistant' value='{esc(self._val('name') if self.route == '/new-cog' else '')}'></label>"
                f"<label class='f'><span>Model</span><input name='model' list='models' value='{esc(self._val('model') if self.route == '/new-cog' else self._default_model())}'></label>"
                f"<datalist id='models'>{''.join(f'<option value=\'{esc(c[chr(118)+chr(97)+chr(108)+chr(117)+chr(101)])}\'>{esc(c[chr(108)+chr(97)+chr(98)+chr(101)+chr(108)])}</option>' for c in self._model_choices())}</datalist>"
                f"<label class='f'><span>Prompt: its standing instructions <b>*</b></span><textarea name='prompt' rows='3' required>{esc(self._val('prompt') if self.route == '/new-cog' else '')}</textarea></label>"
                f"<fieldset><legend>Roles <b>*</b></legend><div class='checks'>{roles}</div></fieldset>"
                f"<fieldset><legend>Procedures it follows (it is routed work only from these)</legend><div class='checks'>{frames}</div></fieldset>"
                f"<label class='f'><span>Autonomy</span><select name='autonomy'><option value='propose'>propose: its actions wait for a person</option>"
                f"<option value='act'>act: it acts on its own through governed paths</option><option value='read'>read: it only looks and reports</option></select></label>"
                f"<label class='f'><span>Duties (one per line; taken up daily)</span><textarea name='duties' rows='2'></textarea></label>"
                f"<div class='actions'><button>{icon('send')}Propose</button></div></form></details>")

    def _model_choices(self) -> list[dict]:
        from ..model import available_models

        return available_models()["choices"]

    def _default_model(self) -> str:
        ch = self._model_choices()
        return ch[0]["value"] if ch else "claude-sonnet-5-5"

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
        log_line(f"cog model proposed cog={cog} model={model} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"Proposed {model} for {cog}; the policy owners review it here.")))

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
        log_line(f"new person proposed name={self._f('name')} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"Proposed {self._f('name')} as a new person; the policy owners review it here.")))

    def post_new_cog(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            p = self.hub.propose_new_cog(name=self._f("name"), roles=self._multi("roles"), model=self._f("model"), prompt=self._f("prompt"),
                                         follows=self._multi("follows"), autonomy=self._f("autonomy") or "propose",
                                         duties=[d for d in (self.form.get("duties") or "").splitlines() if d.strip()], by=self.user)
        except ValueError as e:
            raise ShellError(str(e), field="name") from None
        log_line(f"new cog proposed name={self._f('name')} by={self.user} proposal={p['id']}")
        self._redirect(self._once_finish(href("/proposal/" + p["id"], self.user, done=f"Proposed {self._f('name')} as a new cog; the policy owners review it here.")))
