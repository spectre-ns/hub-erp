"""The stylesheet: tokens for colour (both themes, AA-checked by tests/test_shell_ux.py), type and spacing."""

LIGHT = {"bg": "#f3f5f7", "panel": "#ffffff", "fg": "#1c2730", "muted": "#55636f", "line": "#c9d1d8", "ctl": "#6b7a88",
         "accent": "#0b5f8a", "btn-fg": "#ffffff", "ok": "#186640", "ok-bg": "#dcefe4", "warn": "#7a5000", "warn-bg": "#f7ebcf",
         "crit": "#a3302a", "crit-bg": "#f6dcda", "secondary": "#5c6a76", "top": "#0b5f8a", "top-fg": "#ffffff", "focus": "#1c2730",
         "chip": "#e4e9ee", "th": "#e9eef2"}
DARK = {"bg": "#141a20", "panel": "#1d252d", "fg": "#e6ebef", "muted": "#aab8c4", "line": "#34404a", "ctl": "#7d8b98",
        "accent": "#7cc4ee", "btn-fg": "#0d1a24", "ok": "#8fdcb0", "ok-bg": "#1d3b2c", "warn": "#f0c674", "warn-bg": "#4a3a12",
        "crit": "#f5a8a1", "crit-bg": "#4d1f1b", "secondary": "#8a98a4", "top": "#0b3a55", "top-fg": "#ffffff", "focus": "#ffffff",
        "chip": "#2b353f", "th": "#242e37"}
# Text/background pairs that must meet WCAG AA (4.5:1) or the non-text floor (3:1) in both themes.
CONTRAST_PAIRS = [("fg", "panel", 4.5), ("fg", "bg", 4.5), ("muted", "panel", 4.5), ("muted", "bg", 4.5), ("accent", "panel", 4.5),
                  ("accent", "bg", 4.5), ("btn-fg", "accent", 4.5), ("btn-fg", "secondary", 4.5), ("btn-fg", "crit", 4.5),
                  ("ok", "ok-bg", 4.5), ("warn", "warn-bg", 4.5), ("crit", "crit-bg", 4.5), ("top-fg", "top", 4.5),
                  ("ctl", "panel", 3), ("fg", "chip", 4.5), ("focus", "panel", 3), ("crit", "panel", 4.5), ("ok", "panel", 4.5),
                  ("warn", "panel", 4.5), ("fg", "th", 4.5)]


def _tokens(t: dict) -> str:
    return ";".join(f"--{k}:{v}" for k, v in t.items())


CSS = f"""
:root{{{_tokens(LIGHT)};--fs-s:12px;--fs-m:14px;--fs-l:16px;--fs-xl:22px;--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px}}
@media (prefers-color-scheme: dark){{:root{{{_tokens(DARK)}}}}}
*{{box-sizing:border-box}}body{{margin:0;font:var(--fs-m)/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
a{{color:var(--accent)}}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible,[tabindex]:focus-visible{{outline:3px solid var(--focus);outline-offset:2px}}
.skip{{position:absolute;left:-999px;top:var(--sp-2);background:var(--panel);color:var(--fg);padding:var(--sp-2) var(--sp-3);border:2px solid var(--fg);z-index:10}}.skip:focus{{left:var(--sp-2)}}
.top{{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-3);padding:var(--sp-2) var(--sp-4);background:var(--top);color:var(--top-fg)}}
.top a{{color:var(--top-fg);font-weight:600}}.top .crumb{{display:flex;flex-wrap:wrap;gap:var(--sp-1);list-style:none;margin:0;padding:0}}
.top .crumb li+li::before{{content:"\\203A";margin:0 var(--sp-1)}}.top .crumb li{{white-space:nowrap}}.top form{{margin-left:auto;display:flex;gap:var(--sp-2);align-items:center}}
.top form label{{display:flex;gap:var(--sp-2);align-items:center}}.top .who{{font-weight:600}}
.layout{{display:grid;grid-template-columns:230px 1fr;min-height:calc(100vh - 44px)}}
nav.rail{{background:var(--panel);border-right:1px solid var(--line);padding:var(--sp-3)}}
nav.rail ul{{list-style:none;margin:0;padding:0}}nav.rail li a{{display:block;padding:5px 10px;border-radius:6px;color:var(--fg);text-decoration:none;border-left:4px solid transparent}}
nav.rail li a:hover{{background:var(--bg)}}nav.rail li a[aria-current=page]{{background:var(--chip);font-weight:700;border-left-color:var(--accent)}}
nav.rail h2{{font-size:var(--fs-s);text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:var(--sp-4) 0 var(--sp-1) 10px;border:0;padding:0}}
nav.rail .tree ul{{padding-left:14px}}nav.rail .tree>ul{{padding-left:0}}nav.rail .tree a{{padding:2px 10px;font-size:var(--fs-s)}}
main{{padding:var(--sp-4) var(--sp-6);max-width:1240px;min-width:0}}
h1{{font-size:var(--fs-xl);margin:0 0 var(--sp-3)}}h2{{font-size:var(--fs-l);margin:var(--sp-6) 0 var(--sp-2);border-bottom:1px solid var(--line);padding-bottom:3px}}
h3{{font-size:var(--fs-m);margin:var(--sp-3) 0 var(--sp-1)}}h4{{font-size:var(--fs-m);margin:var(--sp-2) 0 2px}}
.tbl{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:var(--sp-2) 0}}
table{{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);font-size:var(--fs-m)}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}}th{{background:var(--th);font-size:var(--fs-s)}}
th[scope=row]{{font-weight:600;background:transparent;white-space:nowrap}}td.num{{white-space:nowrap}}
.chip{{display:inline-block;padding:0 var(--sp-2);border-radius:999px;font-size:var(--fs-s);font-weight:600;background:var(--chip);color:var(--fg);max-width:100%;overflow-wrap:anywhere}}
.chip.ok{{background:var(--ok-bg);color:var(--ok)}}.chip.warn{{background:var(--warn-bg);color:var(--warn)}}.chip.crit{{background:var(--crit-bg);color:var(--crit)}}
.tag{{display:inline-block;padding:0 6px;border:1px solid var(--ctl);border-radius:4px;font-size:var(--fs-s);color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-3) var(--sp-4);margin:var(--sp-3) 0}}
.card.crit{{border-color:var(--crit)}}.card.warn{{border-color:var(--warn)}}.card.decide{{border-left:4px solid var(--accent)}}.card.quiet{{border-left:3px solid var(--line)}}
.why{{font-size:var(--fs-s);color:var(--muted)}}code{{font-size:var(--fs-s);background:var(--th);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}}
pre{{background:var(--th);padding:10px;border-radius:6px;font-size:var(--fs-s);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}}pre.cell{{margin:0}}pre.src{{margin:0;background:none;padding:0}}
button{{background:var(--accent);color:var(--btn-fg);border:0;border-radius:6px;padding:6px var(--sp-3);cursor:pointer;font:inherit;font-weight:600;min-height:36px}}
button.secondary{{background:var(--secondary);color:#fff}}button.danger{{background:var(--crit);color:var(--btn-fg)}}
button.reject{{background:var(--panel);color:var(--crit);border:2px solid var(--crit)}}button[disabled]{{opacity:.55;cursor:not-allowed}}
input,select,textarea{{padding:6px var(--sp-2);border:1px solid var(--ctl);border-radius:6px;background:var(--panel);color:var(--fg);font:inherit;width:100%;max-width:30em;min-height:36px}}
textarea{{min-height:4.5em}}input[type=checkbox]{{width:auto;min-height:0;vertical-align:middle;margin-right:var(--sp-1)}}input[type=file]{{padding:4px 0;border:0;background:none}}
form.inline{{display:inline-block;margin-right:var(--sp-2)}}
.fields{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--sp-2) var(--sp-4);align-items:start}}
label.f{{display:block;font-size:var(--fs-m)}}label.f>span{{display:block;color:var(--muted);font-size:var(--fs-s);margin-bottom:2px}}label.f>span b{{color:var(--crit)}}
label.f.bad>span{{color:var(--crit);font-weight:700}}label.f.bad input,label.f.bad select,label.f.bad textarea{{border:2px solid var(--crit)}}
.err{{color:var(--crit);font-size:var(--fs-s);font-weight:600;margin:2px 0}}.status{{background:var(--ok-bg);color:var(--ok);border-radius:6px;padding:var(--sp-2) var(--sp-3);margin:0 0 var(--sp-3);font-weight:600}}
.status.crit{{background:var(--crit-bg);color:var(--crit)}}.wide{{max-width:100%}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:var(--fs-m)}}.kv dt{{color:var(--muted)}}.kv dd{{margin:0;word-break:break-word}}
.facts{{display:flex;gap:var(--sp-4);flex-wrap:wrap;align-items:baseline;margin:var(--sp-2) 0}}.facts b{{font-size:var(--fs-l)}}
.actions{{display:flex;gap:var(--sp-3);align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:var(--sp-2);margin-top:var(--sp-2)}}
.queue{{list-style:none;margin:0;padding:0}}.queue>li{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2) var(--sp-4);margin:var(--sp-2) 0;display:flex;gap:var(--sp-3);flex-wrap:wrap;align-items:center}}
.queue>li.open{{display:block;border-left:4px solid var(--accent)}}.queue h3{{margin:0;flex:1 1 16em}}.queue .meta{{display:flex;gap:var(--sp-2);flex-wrap:wrap;align-items:center;font-size:var(--fs-s);color:var(--muted)}}
.board{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(200px,1fr);gap:var(--sp-3);overflow-x:auto;padding-bottom:var(--sp-2)}}
.col{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2);min-height:120px}}.col h3{{margin:2px 0 var(--sp-2);position:sticky;top:0;background:var(--panel)}}.col.empty{{opacity:.7}}
.kcard{{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px var(--sp-2);margin:6px 0;font-size:var(--fs-m)}}
.tabs{{display:flex;gap:var(--sp-1);flex-wrap:wrap;margin:var(--sp-2) 0 var(--sp-4)}}.tabs a{{padding:3px 10px;border:1px solid var(--ctl);border-radius:6px 6px 0 0;border-bottom:0;text-decoration:none;color:var(--fg);font-size:var(--fs-m)}}
.counts{{display:flex;gap:var(--sp-3);flex-wrap:wrap}}.counts .card{{min-width:160px;margin:0}}.counts b{{font-size:var(--fs-xl);display:block}}
.muted{{color:var(--muted)}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.brief table{{margin:6px 0}}details summary{{cursor:pointer;color:var(--fg)}}details.card>summary{{font-weight:600}}details .why{{margin-top:var(--sp-1)}}
.msg{{white-space:pre-line}}.diff ins{{background:var(--ok-bg);color:var(--ok);text-decoration:none}}.diff del{{background:var(--crit-bg);color:var(--crit)}}
.who{{display:flex;gap:var(--sp-3);flex-wrap:wrap}}.who .card{{min-width:12em}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr}}nav.rail{{border-right:0;border-bottom:1px solid var(--line);padding:var(--sp-2) var(--sp-3)}}
nav.rail ul.main{{display:flex;flex-wrap:wrap;gap:var(--sp-1)}}nav.rail .tree,nav.rail h2{{display:none}}
main{{padding:var(--sp-3) var(--sp-4)}}.grid2{{grid-template-columns:1fr}}.counts .card{{min-width:120px}}}}
"""
