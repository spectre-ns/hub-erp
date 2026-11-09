"""Workflow graphs drawn on the server as inline SVG (change control v2, section 10.5): no script and no CDN, colours from
the theme tokens (CSS classes), every status and transition named for assistive technology.

* ``render(graph, layout, ...)``: the drawing. ``mode`` "view" (record pages and Settings: the current status and the
  path taken; a picture), "read" (the editor for someone who may not edit: focusable, named elements), "edit" (the
  editor's canvas: also connect handles and wide hit lines), or "diff" (marked elements; the rest muted).
* ``diff_drawing(old, new, ...)``: one drawing of old and new together: added "+", removed dashed and struck through
  "−", changed "~", the rest muted; never colour alone (badges, dash patterns, ``<title>`` text).
* ``diff_html(...)``: the visual diff with its linked Changes list and Changes / Before / After views, for the editor
  and for a change order's page.

The geometry is ``hubproto.workflow_model``'s; the editor's script recomputes the same curves while something is dragged
(each transition's label carries what it needs: kind, sources, target, bend, index, label offset).
"""

from __future__ import annotations

from .. import workflow_model as wm
from .text import esc

WF_CSS = """
.wf{width:100%;height:auto;display:block;font-family:inherit;-webkit-user-select:none;user-select:none;overflow:visible}
.wf .wf-box{fill:var(--panel);stroke:var(--line-strong);stroke-width:1.5}.wf .wf-cat{stroke:none}
.wf .cat-todo .wf-cat{fill:var(--ctl)}.wf .cat-in_progress .wf-cat{fill:var(--info)}.wf .cat-done .wf-cat{fill:var(--ok)}
.wf .wf-name{font-size:13px;font-weight:600;fill:var(--fg)}.wf .wf-sub{font-size:11.5px;fill:var(--muted)}
.wf .wf-line{fill:none;stroke:var(--ctl);stroke-width:1.6}.wf .wf-hit{fill:none;stroke:transparent;stroke-width:14;pointer-events:stroke}
.wf .wf-lbox{fill:var(--panel);stroke:var(--line-strong);stroke-width:1}.wf .wf-lname{font-size:12px;font-weight:600;fill:var(--fg)}
.wf .wf-lbadge{font-size:11px;fill:var(--muted)}.wf .wf-ic{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.wf .wf-start{fill:var(--fg)}.wf .wf-any .wf-lbox{stroke-dasharray:4 3}
.wf marker path{fill:var(--ctl)}.wf marker.on path{fill:var(--accent)}.wf marker.add path{fill:var(--ok)}.wf marker.del path{fill:var(--crit)}.wf marker.chg path{fill:var(--warn)}
.wf .wf-cur .wf-box{stroke:var(--accent);stroke-width:3}.wf .wf-seen .wf-box{fill:var(--accent-soft)}
.wf .wf-taken .wf-line{stroke:var(--accent);stroke-width:2.4}.wf .wf-l.wf-taken .wf-lbox{stroke:var(--accent)}
.wf-edit .wf-s,.wf-edit .wf-l{cursor:grab}.wf-edit.dragging,.wf-edit.dragging .wf-s,.wf-edit.dragging .wf-l{cursor:grabbing}
.wf-edit .wf-e{cursor:pointer}.wf .wf-handle{fill:var(--panel);stroke:var(--accent);stroke-width:2;opacity:0;cursor:crosshair;transition:opacity .12s}
.wf .wf-handle.on,.wf .wf-handle:hover,.wf.connecting .wf-handle{opacity:1}
.wf .wf-s.wf-sel .wf-box,.wf .wf-l.wf-sel .wf-lbox,.wf .wf-hl .wf-box,.wf .wf-l.wf-hl .wf-lbox{stroke:var(--accent);stroke-width:3}
.wf .wf-e.wf-sel .wf-line,.wf .wf-e.wf-hl .wf-line{stroke:var(--accent);stroke-width:2.8}
.wf g:focus{outline:none}.wf g:focus-visible .wf-box,.wf g:focus-visible .wf-lbox{stroke:var(--focus);stroke-width:3.5}
.wf .wf-target .wf-box{stroke:var(--accent);stroke-width:3;stroke-dasharray:5 3}.wf .wf-ghost{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:6 4;pointer-events:none}
.wf .wf-flag circle{fill:var(--crit-bg);stroke:var(--crit)}.wf .wf-flag text{fill:var(--crit);font-size:12px;font-weight:700}.wf .wf-flag.warn circle{fill:var(--warn-bg);stroke:var(--warn)}.wf .wf-flag.warn text{fill:var(--warn)}
.wf-diff .m-same{opacity:.42}.wf .wf-mk circle{stroke-width:1.5}.wf .wf-mk text{font-size:14px;font-weight:700;text-anchor:middle}
.wf .m-add .wf-box,.wf .m-add .wf-lbox{stroke:var(--ok);stroke-width:2.5}.wf .m-add .wf-line{stroke:var(--ok);stroke-width:2.2}.wf .m-add .wf-mk circle{fill:var(--ok-bg);stroke:var(--ok)}.wf .m-add .wf-mk text{fill:var(--ok)}
.wf .m-del .wf-box,.wf .m-del .wf-lbox{stroke:var(--crit);stroke-width:2;stroke-dasharray:6 4}.wf .m-del .wf-line{stroke:var(--crit);stroke-dasharray:6 4}.wf .m-del .wf-mk circle{fill:var(--crit-bg);stroke:var(--crit)}.wf .m-del .wf-mk text{fill:var(--crit)}
.wf .m-del .wf-name,.wf .m-del .wf-lname{text-decoration:line-through}
.wf .m-chg .wf-box,.wf .m-chg .wf-lbox{stroke:var(--warn);stroke-width:2.5}.wf .m-chg .wf-line{stroke:var(--warn);stroke-width:2.2}.wf .m-chg .wf-mk circle{fill:var(--warn-bg);stroke:var(--warn)}.wf .m-chg .wf-mk text{fill:var(--warn)}
.wf-views{display:flex;gap:var(--sp-1);flex-wrap:wrap;margin:0 0 var(--sp-2)}.wf-legend{display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-3);font-size:var(--fs-s);color:var(--muted);margin:var(--sp-1) 0}
.wf-diffbox{display:grid;grid-template-columns:minmax(0,2fr) minmax(15rem,1fr);gap:var(--sp-3);align-items:start}.wf-diffbox .wf-pane{min-width:0;overflow:auto;border:1px solid var(--line);border-radius:var(--r-lg);background:var(--bg)}
ol.wf-changes{margin:0;padding-left:1.2em}ol.wf-changes li{margin:2px 0}ol.wf-changes button.linkish,ul.wf-checks button.linkish{background:none;border:0;padding:0;color:var(--accent);text-align:left;font:inherit;min-height:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
ol.wf-changes li.wf-hl{background:var(--accent-soft);border-radius:4px}ol.wf-changes ul{margin:2px 0;padding-left:1.1em;color:var(--muted)}
.wf-sign{display:inline-block;min-width:1.4em;text-align:center;font-weight:700;border-radius:4px;margin-right:4px}.wf-sign.add{color:var(--ok);background:var(--ok-bg)}.wf-sign.del{color:var(--crit);background:var(--crit-bg)}.wf-sign.chg{color:var(--warn);background:var(--warn-bg)}
.wf-card .wf{max-height:560px}@media(max-width:900px){.wf-diffbox{grid-template-columns:1fr}}
"""

ICON_PATHS = {
    "person": '<circle cx="8" cy="5" r="3"/><path d="M2.5 15c0-3.2 2.5-5 5.5-5s5.5 1.8 5.5 5"/>',
    "cog": '<rect x="2.5" y="4.5" width="11" height="9" rx="2"/><path d="M8 4.5V2"/><circle cx="6" cy="9" r=".8"/><circle cx="10" cy="9" r=".8"/>',
    "auto": '<circle cx="8" cy="8" r="2.5"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>',
}
MARK_SIGN = {"add": "+", "del": "−", "chg": "~"}
MARK_WORD = {"add": "Added", "del": "Removed", "chg": "Changed"}


def _n(v) -> str:
    return wm._n(v)


def _lines(name: str, width: int = 22) -> list[str]:
    """A status name in at most two lines of about ``width`` characters (the second cut with an ellipsis)."""
    words, lines, cur = str(name).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    if len(lines) == 2 and len(lines[1]) > width:
        lines[1] = lines[1][: width - 1].rstrip() + "…"
    if lines and len(lines[0]) > width + 4:
        lines[0] = lines[0][: width + 3] + "…"
    return lines or [""]


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def badge_text(t: dict) -> tuple[str, str]:
    icon_kind, who = wm.gate_badge(t.get("gate"))
    bits = [who]
    nv, na = len(t.get("validators") or []), len(t.get("actions") or [])
    if nv:
        bits.append(wm.plural(nv, "check"))
    if na:
        bits.append(wm.plural(na, "action"))
    return icon_kind, " · ".join(bits)


def label_size(name: str, badge: str) -> tuple[int, int]:
    w = max(len(name) * 7.0, len(badge) * 6.1 + 16) + 22
    return max(72, min(176, int((w + 7) // 8 * 8))), 38


def ways_out(g: dict, sid: str) -> int:
    return sum(1 for a, t, b in wm.edges(g) if a == sid)


def status_label(g: dict, s: dict, items: dict | None = None, current: str | None = None) -> str:
    bits = [s["name"], wm.status_words(s), wm.plural(ways_out(g, s["id"]), "way") + " out"]
    if items is not None:
        bits.append(wm.plural(int(items.get(s["id"]) or 0), "item"))
    if current == s["id"]:
        bits.append("the current status")
    return ", ".join(bits)


def transition_label(g: dict, t: dict) -> str:
    bits = [f"Transition {t.get('name') or t['id']}", wm.route_words(g, t), wm.gate_words(g, t.get("gate"), initial=t.get("type") == "initial")]
    nv, na = len(t.get("validators") or []), len(t.get("actions") or [])
    if nv or na:
        bits.append(", ".join(x for x in (wm.plural(nv, "check") if nv else "", wm.plural(na, "action") if na else "") if x))
    return "; ".join(bits)


def geometry(g: dict, layout: dict) -> dict[str, dict]:
    """Every transition's drawing (workflow_model.transition_geometry) with its bend and index."""
    bends = wm.bends(g, layout)
    globals_to: dict[str, list[str]] = {}
    loops_at: dict[str, list[str]] = {}
    for t in g.get("transitions") or []:
        if t.get("type") == "global":
            globals_to.setdefault(t.get("to"), []).append(t["id"])
        elif t.get("to") in (t.get("from") or []):
            loops_at.setdefault(t.get("to"), []).append(t["id"])
    out = {}
    sids = set(wm.status_map(g))
    for t in g.get("transitions") or []:
        if t.get("to") not in sids:
            continue
        k, n = 0, 1
        if t.get("type") == "global":
            ids = globals_to.get(t["to"], [])
            k, n = ids.index(t["id"]), len(ids)
        elif t["id"] in loops_at.get(t.get("to"), []):
            k = loops_at[t["to"]].index(t["id"])
        geo = wm.transition_geometry(g, layout, t, bend=bends.get(t["id"], 0.0), k=k, n=n)
        geo.update({"bend": bends.get(t["id"], 0.0), "k": k, "n": n})
        out[t["id"]] = geo
    return out


def bounds(g: dict, layout: dict, geos: dict, pad: int = 28) -> list[int]:
    xs, ys = [], []
    for s in g.get("statuses") or []:
        x, y, w, h = wm.box(layout, s["id"])
        xs += [x, x + w]
        ys += [y, y + h]
    tm = wm.transition_map(g)
    for tid, geo in geos.items():
        t = tm[tid]
        _, badge = badge_text(t)
        lw, lh = label_size(t.get("name") or tid, badge)
        lx, ly = geo["label"]
        xs += [lx - lw / 2, lx + lw / 2]
        ys += [ly - lh / 2, ly + lh / 2]
        if geo["kind"] == "loop":
            ys.append(ly + lh / 2)
        if geo.get("start"):
            xs.append(geo["start"][0] - 10)
    if not xs:
        return [0, 0, 400, 200]
    x0, y0, x1, y1 = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    return [int(x0 // 8 * 8), int(y0 // 8 * 8), int((x1 - x0 + 15) // 8 * 8), int((y1 - y0 + 15) // 8 * 8)]


def _mark_badge(mark: str | None, x: float, y: float) -> str:
    if mark not in MARK_SIGN:
        return ""
    return (f'<g class="wf-mk" aria-hidden="true"><circle cx="{_n(x)}" cy="{_n(y)}" r="10"/><text x="{_n(x)}" y="{_n(y + 5)}">{MARK_SIGN[mark]}</text></g>')


def _flag(level: str | None, x: float, y: float) -> str:
    if not level:
        return ""
    return (f'<g class="wf-flag{" warn" if level == "warning" else ""}" aria-hidden="true"><circle cx="{_n(x)}" cy="{_n(y)}" r="9"/>'
            f'<text x="{_n(x)}" y="{_n(y + 4.5)}" text-anchor="middle">!</text></g>')


def render(g: dict, layout: dict, *, idp: str = "wf", mode: str = "view", current: str | None = None, seen: set | None = None,
           taken: set | None = None, marks: dict | None = None, items: dict | None = None, flags: dict | None = None,
           selected: str | None = None, label: str = "", inner_only: bool = False, view_box: list | None = None) -> str | tuple[str, list]:
    """The SVG drawing of a workflow. ``marks``: {"status:<id>" | "transition:<id>": "add" | "del" | "chg"} (diff mode);
    ``flags``: the same keys -> "error" | "warning" (the editor's checks); ``selected``: "s:<id>" or "t:<id>";
    ``inner_only`` returns (the inside of the <svg>, its view box) for the editor to swap in."""
    layout = wm.complete_layout(g, layout)
    sm = wm.status_map(g)
    tm = wm.transition_map(g)
    geos = geometry(g, layout)
    vb = view_box or bounds(g, layout, geos)
    marks, flags, seen, taken = marks or {}, flags or {}, seen or set(), taken or set()
    edit = mode == "edit"
    focusable = mode in ("edit", "diff", "read")
    arrow = lambda cls: f"url(#{idp}-ar{('-' + cls) if cls else ''})"
    parts = [f'<defs>' + "".join(
        f'<marker id="{idp}-ar{("-" + c) if c else ""}" class="{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>' for c in ("", "on", "add", "del", "chg"))
        + "".join(f'<symbol id="{idp}-i-{k}" viewBox="0 0 16 16">{v}</symbol>' for k, v in ICON_PATHS.items()) + "</defs>"]

    def mark_of(key: str) -> str:
        m = marks.get(key)
        if m:
            return m
        return "same" if mode == "diff" else ""

    # Lines first, then statuses, then labels (so a label is never hidden by a line or a box), then the connect
    # handles on top of everything (a label never covers one). Tab order: the statuses, then the transitions.
    edges_out, labels_out = [], []
    for t in g.get("transitions") or []:
        geo = geos.get(t["id"])
        if not geo:
            continue
        key = f"transition:{t['id']}"
        m = mark_of(key)
        cls = " ".join(x for x in ("wf-e", f"m-{m}" if m else "", "wf-taken" if t["id"] in taken else "",
                                   "wf-sel" if selected == f"t:{t['id']}" else "") if x)
        mk = m if m in ("add", "del", "chg") else ("on" if t["id"] in taken else "")
        paths = ""
        for part, d, has_arrow in geo["parts"]:
            hit = f'<path class="wf-hit" data-part="{esc(part)}" d="{d}"/>' if edit else ""
            paths += hit + f'<path class="wf-line" data-part="{esc(part)}" d="{d}"' + (f' marker-end="{arrow(mk)}"' if has_arrow else "") + "/>"
        if geo.get("start"):
            sx, sy = geo["start"]
            paths += f'<circle class="wf-start" data-part="start" cx="{_n(sx)}" cy="{_n(sy)}" r="6"/>'
        edges_out.append(f'<g class="{cls}" data-t="{esc(t["id"])}" aria-hidden="true">{paths}</g>')
        icon_kind, badge = badge_text(t)
        name = t.get("name") or t["id"]
        if t.get("type") == "global":
            name = "Any status → " + name
        name = _short(name, 22)
        badge = _short(badge, 26)
        lw, lh = label_size(name, badge)
        lx, ly = geo["label"]
        off = layout.get(wm.label_key(t["id"])) or [0, 0]
        lcls = " ".join(x for x in ("wf-l", "wf-any" if t.get("type") == "global" else "", f"m-{m}" if m else "",
                                    "wf-taken" if t["id"] in taken else "", "wf-sel" if selected == f"t:{t['id']}" else "") if x)
        aria = transition_label(g, t) + (f". {MARK_WORD[m]}" if m in MARK_WORD else "")
        attrs = (f' tabindex="0" role="button" aria-roledescription="transition" aria-label="{esc(aria)}"' if focusable else "")
        data = (f' data-id="{esc(t["id"])}" data-kind="{geo["kind"]}" data-src="{esc(",".join(x for x in (t.get("from") or []) if x in sm))}" '
                f'data-dst="{esc(t.get("to"))}" data-bend="{_n(geo["bend"])}" data-k="{geo["k"]}" data-n="{geo["n"]}" data-dx="{off[0]}" data-dy="{off[1]}"')
        fl = flags.get(key)
        labels_out.append(
            f'<g class="{lcls}" id="{idp}-t-{esc(t["id"])}"{data}{attrs} transform="translate({_n(lx)},{_n(ly)})">'
            f'<title>{esc(wm.sentence(g, t))}{(" " + MARK_WORD[m] + ".") if m in MARK_WORD else ""}</title>'
            f'<rect class="wf-lbox" x="{-lw / 2:g}" y="{-lh / 2:g}" width="{lw}" height="{lh}" rx="7"/>'
            f'<text class="wf-lname" x="{-lw / 2 + 10:g}" y="-3">{esc(name)}</text>'
            f'<use class="wf-ic" href="#{idp}-i-{icon_kind}" x="{-lw / 2 + 9:g}" y="3.5" width="12" height="12"/>'
            f'<text class="wf-lbadge" x="{-lw / 2 + 25:g}" y="13">{esc(badge)}</text>'
            + _mark_badge(m, lw / 2, -lh / 2) + _flag(fl, -lw / 2, -lh / 2) + "</g>")
    nodes, handles = [], []
    for s in g.get("statuses") or []:
        x, y, w, h = wm.box(layout, s["id"])
        key = f"status:{s['id']}"
        m = mark_of(key)
        cls = " ".join(x_ for x_ in ("wf-s", f"cat-{s.get('category')}", f"m-{m}" if m else "", "wf-cur" if s["id"] == current else "",
                                     "wf-seen" if s["id"] in seen and s["id"] != current else "",
                                     "wf-sel" if selected == f"s:{s['id']}" else "") if x_)
        lines = _lines(s["name"])
        sub = [wm.CATEGORY_WORDS.get(s.get("category"), "").capitalize()]
        if s.get("category") == "done" and (s.get("outcome") or "completed") != "completed":
            sub[0] += f", {s.get('outcome')}"
        if s.get("sla_days"):
            sub.append(f"{s['sla_days']} days")
        if items is not None and items.get(s["id"]):
            sub.append(wm.plural(int(items[s["id"]]), "item"))
        if s["id"] == current:
            sub.insert(0, "Current")
        if s["id"] in wm.SYSTEM_STATUSES:
            sub.append("system")
        texts = "".join(f'<text class="wf-name" x="16" y="{(22 if len(lines) == 2 else 27) + 16 * i}">{esc(ln)}</text>' for i, ln in enumerate(lines))
        texts += f'<text class="wf-sub" x="16" y="{55 if len(lines) == 2 else 47}">{esc(" · ".join(sub))}</text>'
        aria = status_label(g, s, items, current) + (f". {MARK_WORD[m]}" if m in MARK_WORD else "")
        attrs = f' tabindex="0" role="button" aria-roledescription="status" aria-label="{esc(aria)}"' if focusable else ""
        if edit:
            handles.append(f'<circle class="wf-handle" data-handle="{esc(s["id"])}" cx="{_n(x + w)}" cy="{_n(y + h / 2)}" r="7"><title>Drag to connect '
                           f'{esc(s["name"])} to another status</title></circle>')
        nodes.append(
            f'<g class="{cls}" id="{idp}-s-{esc(s["id"])}" data-id="{esc(s["id"])}"{attrs} transform="translate({_n(x)},{_n(y)})">'
            f'<title>{esc(status_label(g, s, items, current))}{(". " + MARK_WORD[m]) if m in MARK_WORD else ""}</title>'
            f'<rect class="wf-box" width="{w:g}" height="{h:g}" rx="8"/><path class="wf-cat" d="M8,0 H11 V{h:g} H8 A8,8 0 0 1 0,{h - 8:g} V8 A8,8 0 0 1 8,0 Z"/>'
            f'{texts}{_mark_badge(m, w, 0)}{_flag(flags.get(key), 0, 0)}</g>')
    inner = ("".join(parts) + f'<g class="wf-edges">{"".join(edges_out)}</g><g class="wf-nodes">{"".join(nodes)}</g>'
             f'<g class="wf-labels">{"".join(labels_out)}</g>' + (f'<g class="wf-handles">{"".join(handles)}</g>' if edit else ""))
    if inner_only:
        return inner, vb
    cls = f"wf wf-{mode}"
    if focusable:
        a11y = f'role="group" aria-roledescription="workflow diagram" aria-label="{esc(label or "Workflow")}"'
    else:
        a11y = f'role="img" aria-label="{esc(label or summary(g, current))}"'
    return (f'<svg class="{cls}" id="{idp}" xmlns="http://www.w3.org/2000/svg" viewBox="{" ".join(str(v) for v in vb)}" '
            f'preserveAspectRatio="xMidYMid meet" {a11y}>{inner}</svg>')


def summary(g: dict, current: str | None = None) -> str:
    """The drawing in words, for a screen reader: the statuses in order and where the item is."""
    names = ", ".join(s["name"] for s in g.get("statuses") or [])
    cur = f" The item is in {wm.name_of(g, current)}." if current else ""
    return f"Workflow with {wm.plural(len(g.get('statuses') or []), 'status')} ({names}) and {wm.plural(len(g.get('transitions') or []), 'transition')}.{cur}"


def path_taken(g: dict, moves: list[tuple[str, str]]) -> tuple[set, set]:
    """(statuses visited, transitions taken) from the item's moves [(from, to)], matched to the graph's transitions."""
    seen, taken = set(), set()
    sm = wm.status_map(g)
    edges = wm.edges(g)
    for a, b in moves:
        if a in sm:
            seen.add(a)
        if b in sm:
            seen.add(b)
        t = next((t for x, t, y in edges if x == a and y == b and t.get("type") != "global"), None) or \
            next((t for x, t, y in edges if x == a and y == b), None)
        if t:
            taken.add(t["id"])
    if moves:
        first = moves[0][0]
        start = next((t for t in g.get("transitions") or [] if t.get("type") == "initial" and t.get("to") == first), None)
        if start:
            taken.add(start["id"])
    return seen, taken


# ---------------------------------------------------------------- the visual diff
def diff_drawing(old: dict, new: dict, old_layout: dict, new_layout: dict, *, items: list[dict] | None = None) -> tuple[dict, dict, dict]:
    """One graph and layout holding old and new together, and the marks: what the new graph has (added and changed
    elements marked), plus what was removed at its old place."""
    old_layout = wm.complete_layout(old, old_layout)
    new_layout = wm.complete_layout(new, new_layout)
    merged = {**new, "statuses": [dict(s) for s in new.get("statuses") or []], "transitions": [dict(t) for t in new.get("transitions") or []]}
    lay = dict(new_layout)
    marks: dict[str, str] = {}
    nsm, ntm = wm.status_map(new), wm.transition_map(new)
    for s in old.get("statuses") or []:
        if s["id"] not in nsm:
            merged["statuses"].append(dict(s))
            lay[s["id"]] = old_layout.get(s["id"]) or wm.free_spot(lay)
            marks[f"status:{s['id']}"] = "del"
    msm = wm.status_map(merged)
    for t in old.get("transitions") or []:
        if t["id"] not in ntm:
            t2 = dict(t)
            if t2.get("from"):
                t2["from"] = [x for x in t2["from"] if x in msm]
            if t2.get("to") in msm and (t2.get("type") != "directed" or t2.get("from")):
                merged["transitions"].append(t2)
                if wm.label_key(t["id"]) in old_layout:
                    lay[wm.label_key(t["id"])] = old_layout[wm.label_key(t["id"])]
            marks[f"transition:{t['id']}"] = "del"
    for item in items if items is not None else wm.diff(old, new):
        key = item.get("key")
        if item.get("element") == "layout" or not key:
            continue
        if item.get("op") == "add":
            marks[key] = "add"
        elif item.get("op") == "remove":
            marks[key] = "del"
        elif marks.get(key) not in ("add", "del"):
            marks[key] = "chg"
        for child in item.get("children") or []:
            if child.get("change") == "rerouted" and marks.get(child["key"]) not in ("add", "del"):
                marks[child["key"]] = "chg"
    return merged, lay, marks


def changes_list(items: list[dict], idp: str) -> str:
    """The Changes list: each delta item linked to its element in the drawing (selecting either highlights the other)."""
    if not items:
        return "<p class='muted'>No change from the workflow in force.</p>"
    out = []
    for i in items:
        sign = {"add": "add", "remove": "del"}.get(i.get("op"), "chg")
        el = i.get("element")
        target = f"{idp}-{'s' if el == 'status' else 't'}-{i.get('element_id')}" if el in ("status", "transition") else ""
        words = esc(i.get("text") or "")
        link = (f"<button type='button' class='linkish' data-wf-key='{esc(i.get('key'))}' data-wf-target='{esc(target)}'>{words}</button>"
                if target else words)
        kids = "".join(f"<li>{esc(c.get('text'))}</li>" for c in i.get("children") or [])
        flag = " <span class='chip warn'>widening</span>" if i.get("widening") else (" <span class='chip'>no effect</span>" if i.get("no_effect") else "")
        out.append(f"<li data-wf-key='{esc(i.get('key'))}'><span class='wf-sign {sign}' aria-hidden='true'>{MARK_SIGN[sign]}</span>"
                   f"<span class='vh'>{MARK_WORD[sign]}: </span>{link}{flag}" + (f"<ul>{kids}</ul>" if kids else "") + "</li>")
    return f"<ol class='wf-changes' data-wf-for='{esc(idp)}'>" + "".join(out) + "</ol>"


DIFF_JS = r"""(function(){document.querySelectorAll('[data-wf-diff]').forEach(function(box){if(box.__wf)return;box.__wf=1;
function hl(key,on){box.querySelectorAll('.wf-hl').forEach(function(e){e.classList.remove('wf-hl')});if(!on)return;
 var p=key.split(':'),el=p[0],id=p.slice(1).join(':');box.querySelectorAll('svg.wf').forEach(function(svg){
  var sel=el==='status'?'.wf-s[data-id="'+CSS.escape(id)+'"]':'.wf-l[data-id="'+CSS.escape(id)+'"],.wf-e[data-t="'+CSS.escape(id)+'"]';
  svg.querySelectorAll(sel).forEach(function(n){n.classList.add('wf-hl')})});
 box.querySelectorAll('li[data-wf-key="'+CSS.escape(key)+'"]').forEach(function(li){li.classList.add('wf-hl')})}
box.addEventListener('click',function(e){var b=e.target.closest('button[data-wf-key]');if(b){hl(b.getAttribute('data-wf-key'),true);
 var t=box.querySelector('.wf-pane:not([hidden]) #'+CSS.escape(b.getAttribute('data-wf-target')));if(t&&t.focus)t.focus({preventScroll:false});return}
 var g=e.target.closest('.wf-s,.wf-l');if(g){var key=(g.classList.contains('wf-s')?'status:':'transition:')+g.getAttribute('data-id');hl(key,true);
 var li=box.querySelector('li[data-wf-key="'+CSS.escape(key)+'"]');if(li)li.scrollIntoView({block:'nearest'})}
 var v=e.target.closest('[data-wf-view]');if(v){var want=v.getAttribute('data-wf-view');box.querySelectorAll('[data-wf-view]').forEach(function(x){x.setAttribute('aria-pressed',x===v?'true':'false')});
 box.querySelectorAll('.wf-pane').forEach(function(p){p.hidden=p.getAttribute('data-wf-pane')!==want})}});
box.addEventListener('keydown',function(e){if(e.key!=='Enter'&&e.key!==' ')return;var g=e.target.closest&&e.target.closest('.wf-s,.wf-l');if(!g)return;e.preventDefault();g.dispatchEvent(new MouseEvent('click',{bubbles:true}))});
box.querySelectorAll('.wf-pane').forEach(function(p,i){if(i>0)p.hidden=true});box.querySelectorAll('[data-wf-view]').forEach(function(x,i){x.setAttribute('aria-pressed',i===0?'true':'false')});
})})();"""


def diff_html(old: dict, new: dict, old_layout: dict, new_layout: dict, *, idp: str, items: list[dict] | None = None,
              title: str = "", script: bool = True) -> str:
    """The visual diff (section 10.4): the Changes drawing (old and new together), Before and After, and the linked
    Changes list. Without JavaScript all three drawings show, one after the other. Reusable: the editor and a change
    order's page embed it."""
    items = items if items is not None else wm.diff(old, new, old_layout=old_layout, new_layout=new_layout)
    merged, lay, marks = diff_drawing(old, new, old_layout, new_layout, items=items)
    name = title or (new.get("process") or "workflow")
    changes = render(merged, lay, idp=f"{idp}-c", mode="diff", marks=marks, label=f"Changes to {name}: old and new together")
    before = render(old, old_layout, idp=f"{idp}-b", mode="read", label=f"{name} before the change")
    after = render(new, new_layout, idp=f"{idp}-a", mode="read", label=f"{name} after the change")
    legend = ("<div class='wf-legend'><span><span class='wf-sign add'>+</span>added</span><span><span class='wf-sign del'>−</span>removed "
              "(dashed, struck through)</span><span><span class='wf-sign chg'>~</span>changed</span><span>the rest unchanged (faded)</span></div>")
    views = ("<div class='wf-views' role='group' aria-label='View'>"
             + "".join(f"<button type='button' class='ghost' data-wf-view='{k}'>{v}</button>" for k, v in (("changes", "Changes"), ("before", "Before"), ("after", "After")))
             + "</div>")
    panes = (f"<div class='wf-pane' data-wf-pane='changes'><h4 class='vh'>Changes</h4>{changes}</div>"
             f"<div class='wf-pane' data-wf-pane='before'><h4 class='vh'>Before</h4>{before}</div>"
             f"<div class='wf-pane' data-wf-pane='after'><h4 class='vh'>After</h4>{after}</div>")
    return (f"<style>{WF_CSS}</style><div class='wf-diffwrap' data-wf-diff id='{esc(idp)}'>{views}<div class='wf-diffbox'><div>{panes}{legend}</div>"
            f"<div><h3>Changes</h3>{changes_list(items, idp + '-c')}</div></div></div>" + (f"<script>{DIFF_JS}</script>" if script else ""))
