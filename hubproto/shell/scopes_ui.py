"""Scopes and personas on screen: which Frames depend on which SOPs, which scopes compile them, which cogs follow them
and which roles must read them (derived from the rules that name them); the personas cogs behave by; a person's
required reading; what a change order affects."""

from __future__ import annotations

from .. import scopes as sc
from ..release import scope_frames
from .handler import href
from .icons import icon
from .text import esc


class ScopesMixin:
    def _scope_bundle(self) -> dict:
        return self.bundle or {}

    def _frame_link(self, name: str) -> str:
        return f"<a href='{href('/frame/' + name, self.user)}'>{esc(name)}</a>"

    def _persona_link(self, p: dict) -> str:
        if p["of"] == "cog":
            return f"<a href='{href('/user/' + p['name'], self.user)}'>{esc(p['name'])}</a> <span class='chip'>cog</span>"
        return f"<a href='{href('/scopes', self.user)}#role-{esc(p['name'])}'>{esc(p['name'].replace('-', ' '))}</a> <span class='chip'>role</span>"

    def _source_html(self, src: dict | None) -> str:
        if not src:
            return "<span class='muted'>not recorded</span>"
        if src.get("document"):
            return (f"<a href='{href('/record/' + 'rec:document:' + src['document'], self.user)}'>{esc(src['document'])}</a> rev "
                    f"{esc(src.get('revision') or '')} <span class='why'>{esc(src.get('title') or '')}</span>")
        return f"<code>{esc(src.get('file') or '')}</code> <span class='why'>the Frame file (the SOPs are not documents yet)</span>"

    def _dependency_html(self, frame: str) -> str:
        """On a Frame or SOP page: what it is compiled from, the scopes it is in, the cogs that follow it and the roles
        that must read it."""
        b = self._scope_bundle()
        if not b.get("scopes") and not b.get("cogs"):
            return ""
        d = sc.dependents(b, frame)
        src = sc.frame_sources(self.hub).get(frame)
        scopes = b.get("scopes") or {}
        scope_bits = [f"<a href='{href('/scopes', self.user)}#scope-{esc(n)}'>{esc(n)}</a> <span class='why'>({esc(scopes[n].get('level') or '')}"
                      + ("" if n in d["direct_scopes"] else ", through a scope it layers on") + ")</span>" for n in d["scopes"]]
        cogs = [self._persona_link(p) for p in d["readers"] if p["of"] == "cog"]
        roles = [self._persona_link(p) for p in d["readers"] if p["of"] == "role"]
        return (f"<div class='card' id='depends'><h3 style='margin-top:0'>{icon('layers')}Depends and depended on</h3><dl class='kv'>"
                f"<dt>Compiled from</dt><dd>{self._source_html(src)}</dd>"
                f"<dt>In scopes</dt><dd>{', '.join(scope_bits) or '<span class=muted>none</span>'}</dd>"
                + (f"<dt>Behaved by</dt><dd>{', '.join(self._persona_link({'name': n, 'of': 'cog'}) for n in d['behave']) or '<span class=muted>no cog</span>'}</dd>"
                   if d.get("persona") else
                   f"<dt>Followed by</dt><dd>{', '.join(cogs) or '<span class=muted>no cog</span>'}</dd>"
                   f"<dt>Required reading for</dt><dd>{', '.join(roles) or '<span class=muted>no role</span>'}</dd>")
                + "</dl><p class='why'>Derived from the rules that name them: a change to it reaches every cog listed (what it works from and "
                "is bound by) and every role listed (what its people must read).</p></div>")

    def _impact_html(self, frames: list[str]) -> str:
        """On a change order: the Frames, scopes and personas its revisions affect."""
        b = self._scope_bundle()
        if not frames or not (b.get("scopes") or b.get("cogs")):
            return ""
        im = sc.impact(b, frames)
        cogs = [self._persona_link(p) for p in im["readers"] if p["of"] == "cog"]
        roles = [self._persona_link(p) for p in im["readers"] if p["of"] == "role"]
        return (f"<div class='card' id='impact'><h3 style='margin-top:0'>{icon('activity')}What it affects</h3><dl class='kv'>"
                f"<dt>Frames</dt><dd>{', '.join(self._frame_link(f) for f in im['frames'])}</dd>"
                f"<dt>Scopes</dt><dd>{', '.join(esc(s) for s in im['scopes']) or '<span class=muted>none</span>'}</dd>"
                f"<dt>Cogs</dt><dd>{', '.join(cogs) or '<span class=muted>none</span>'}</dd>"
                f"<dt>Roles</dt><dd>{', '.join(roles) or '<span class=muted>none</span>'}</dd></dl>"
                "<p class='why'>The cogs listed read these SOPs and are bound by them; the people in the roles listed must read the new revisions.</p></div>")

    def _required_reading_html(self, person: str) -> str:
        b = self._scope_bundle()
        rows = sc.required_reading(b, person)
        if not rows:
            return ""
        srcs = sc.frame_sources(self.hub)
        items = "".join(f"<tr><td>{self._frame_link(r['frame'])}</td><td>{self._source_html(srcs.get(r['frame']))}</td>"
                        f"<td>{esc(', '.join(x.replace('-', ' ') for x in r['roles']))}</td></tr>" for r in rows)
        return (f"<h2 id='reading'>{icon('book')}Required reading</h2><p class='why'>Derived from the roles {esc(person)} holds: the company "
                "scope, every SOP whose rules name one of those roles, and the work instructions written for them.</p><div class='tbl'><table>"
                "<thead><tr><th scope=\"col\">SOP</th><th scope=\"col\">Document</th>"
                f"<th scope=\"col\">Because of the role</th></tr></thead><tbody>{items}</tbody></table></div>")

    def page_scopes(self):
        self._need_release()
        b = self._scope_bundle()
        scopes, personas = b.get("scopes") or {}, b.get("personas") or {}
        srcs = sc.frame_sources(self.hub)
        out = (f"<h1>{icon('layers')}Scopes and personas</h1><p class='why'>Each scope compiles SOP and policy Frames and may layer on a wider "
               "scope; later layers add and may only narrow. Who works from which SOP is derived, not listed: every cog follows the company "
               "scope and every SOP whose rules name it or one of its roles, and a person must read the same. A persona says how a cog "
               "behaves, never what it may do. Every Frame names the document it was compiled from.</p>")
        if not scopes:
            out += "<p class='muted'>The release in force defines no scopes yet.</p>"
        for n, s in scopes.items():
            own = s.get("compiles") or []
            rows = "".join(f"<tr><td>{self._frame_link(f)}</td><td>{self._source_html(srcs.get(f))}</td>"
                           f"<td>{'own' if f in own else 'from ' + esc(', '.join(s.get('layers_on') or []))}</td></tr>" for f in scope_frames(scopes, n))
            out += (f"<section class='card' id='scope-{esc(n)}'><h2 style='margin-top:0'>{esc(n)} <span class='chip'>{esc(s.get('level') or '')}</span></h2>"
                    + (f"<p class='why'>Layers on {esc(', '.join(s['layers_on']))}.</p>" if s.get("layers_on") else "")
                    + f"<div class='tbl'><table><thead><tr><th scope=\"col\">Frame</th><th scope=\"col\">Compiled from</th><th scope=\"col\">From</th></tr></thead><tbody>{rows}</tbody></table></div></section>")
        if personas:
            rows = "".join(f"<tr id='persona-{esc(n)}'><td>{self._frame_link((p.get('source') or {}).get('frame') or n)}</td>"
                           f"<td>{esc(p.get('stance') or '')}<span class='why'> {esc(p.get('tone') or '')}</span></td>"
                           f"<td>{esc(', '.join(p.get('lenses') or []) or '-')}<span class='why'> {int(p.get('passes') or 0)} pass(es)</span></td>"
                           f"<td>{', '.join(self._persona_link({'name': c, 'of': 'cog'}) for c in p.get('cogs') or []) or '<span class=muted>no cog</span>'}</td></tr>"
                           for n, p in personas.items())
            out += (f"<h2>Personas</h2><p class='why'>How cogs behave: tone, stance, how hard they review, when they escalate. Shared by any number of cogs.</p>"
                    f"<div class='tbl'><table><thead><tr><th scope=\"col\">Persona</th><th scope=\"col\">Stance</th><th scope=\"col\">Lenses</th>"
                    f"<th scope=\"col\">Cogs</th></tr></thead><tbody>{rows}</tbody></table></div>")
        cogs = b.get("cogs") or {}
        if cogs:
            rows = "".join(f"<tr id='cog-{esc(n)}'><td>{self._persona_link({'name': n, 'of': 'cog'})}</td><td>{esc(c.get('persona') or '-')}</td>"
                           f"<td>{', '.join(self._frame_link(f['frame']) + (' <span class=why>(' + esc(', '.join(x.partition(':')[2] or x for x in f['because'])) + ')</span>' if f['because'] and f['because'] != ['company'] else '') for f in sc.following(b, n))}</td></tr>"
                           for n, c in cogs.items())
            out += (f"<h2>Cogs</h2><p class='why'>What each cog follows: the company scope and every SOP whose rules name it or one of its roles "
                    "(in brackets).</p><div class='tbl'><table><thead><tr><th scope=\"col\">Cog</th><th scope=\"col\">Persona</th>"
                    f"<th scope=\"col\">So it works from</th></tr></thead><tbody>{rows}</tbody></table></div>")
        roles = sorted(b.get("roles") or [])
        if roles:
            from ..release import company_frames, frames_naming

            named = frames_naming(b, {"role:" + r for r in roles}, audience=True)
            rows = "".join(f"<tr id='role-{esc(r)}'><td>{esc(r.replace('-', ' '))}</td><td>{', '.join(self._frame_link(f) for f in company_frames(b) + sorted(f for f, w in named.items() if 'role:' + r in w and f not in company_frames(b)))}</td></tr>"
                           for r in roles)
            out += (f"<h2>Roles</h2><p class='why'>What each role must read: the company scope, every SOP whose rules name the role, and the work "
                    "instructions written for it.</p><div class='tbl'><table><thead><tr><th scope=\"col\">Role</th><th scope=\"col\">Required reading</th>"
                    f"</tr></thead><tbody>{rows}</tbody></table></div>")
        self._page("Scopes and personas", out, crumbs=[("Policies", href("/frames", self.user)), ("Scopes and personas", None)])
