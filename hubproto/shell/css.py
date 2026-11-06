"""The stylesheet: an Odoo-like visual system. Colour tokens for both themes (AA-checked by tests/test_shell_ux.py
and tests/test_shell_views.py), one type scale, one spacing scale, and the layouts of the app shell, the three views
(list, kanban, form), the dashboard, the inbox, the document library and the chatter."""

LIGHT = {"bg": "#f1f2f4", "panel": "#ffffff", "fg": "#1f2429", "muted": "#5c6670", "line": "#d4d8dd", "ctl": "#6f7a85",
         "accent": "#714b67", "accent2": "#01666b", "btn-fg": "#ffffff", "ok": "#1e6b3a", "ok-bg": "#dcf0e3", "warn": "#7a5000", "warn-bg": "#f8ebcf",
         "crit": "#a8302a", "crit-bg": "#f8dddb", "info": "#2a5f8f", "info-bg": "#dde9f5", "secondary": "#5c6a76", "top": "#714b67",
         "top-fg": "#ffffff", "focus": "#1f2429", "chip": "#e6e9ed", "th": "#eceef1", "shadow": "rgba(31,36,41,.08)", "avatar-fg": "#ffffff",
         "c1": "#714b67", "c2": "#017e84", "c3": "#2a5f8f", "c4": "#b2681a", "c5": "#6b7a88", "c6": "#8a3f6f"}
DARK = {"bg": "#15181c", "panel": "#1f2429", "fg": "#e7eaee", "muted": "#aeb6bf", "line": "#394149", "ctl": "#8691a0",
        "accent": "#d8a7cb", "accent2": "#5fd0d6", "btn-fg": "#15181c", "ok": "#8fdcb0", "ok-bg": "#1d3b2c", "warn": "#f0c674", "warn-bg": "#4a3a12",
        "crit": "#f5a8a1", "crit-bg": "#4d1f1b", "info": "#9cc4ec", "info-bg": "#1c3550", "secondary": "#8a98a4", "top": "#3a2534",
        "top-fg": "#ffffff", "focus": "#ffffff", "chip": "#2c343c", "th": "#262d34", "shadow": "rgba(0,0,0,.4)", "avatar-fg": "#ffffff",
        "c1": "#d8a7cb", "c2": "#5fd0d6", "c3": "#8fbbe8", "c4": "#e8b26a", "c5": "#aab6c2", "c6": "#e39bc7"}
# Text/background pairs that must meet WCAG AA (4.5:1) or the non-text floor (3:1) in both themes.
CONTRAST_PAIRS = [("fg", "panel", 4.5), ("fg", "bg", 4.5), ("muted", "panel", 4.5), ("muted", "bg", 4.5), ("accent", "panel", 4.5),
                  ("accent", "bg", 4.5), ("btn-fg", "accent", 4.5), ("btn-fg", "secondary", 4.5), ("btn-fg", "crit", 4.5),
                  ("ok", "ok-bg", 4.5), ("warn", "warn-bg", 4.5), ("crit", "crit-bg", 4.5), ("top-fg", "top", 4.5),
                  ("ctl", "panel", 3), ("fg", "chip", 4.5), ("focus", "panel", 3), ("crit", "panel", 4.5), ("ok", "panel", 4.5),
                  ("warn", "panel", 4.5), ("fg", "th", 4.5),
                  # the second accent (teal), the info chip and the six chart colours (non-text: 3:1 against both surfaces)
                  ("accent2", "panel", 4.5), ("accent2", "bg", 4.5), ("btn-fg", "accent2", 4.5), ("info", "info-bg", 4.5), ("info", "panel", 4.5),
                  ("c1", "panel", 3), ("c2", "panel", 3), ("c3", "panel", 3), ("c4", "panel", 3), ("c5", "panel", 3), ("c6", "panel", 3),
                  ("c1", "bg", 3), ("c2", "bg", 3), ("c3", "bg", 3), ("c4", "bg", 3), ("c5", "bg", 3), ("c6", "bg", 3)]
# Avatar backgrounds (white initials on each): every one meets 4.5:1 with white, in both themes.
AVATAR_COLOURS = ["#714b67", "#017e84", "#2a5f8f", "#9a4a1a", "#5a3d8f", "#b2384a", "#2e7d32", "#4a5568"]
AVATAR_FG = "#ffffff"


def _tokens(t: dict) -> str:
    return ";".join(f"--{k}:{v}" for k, v in t.items())


CSS = f"""
:root{{{_tokens(LIGHT)};--fs-s:12px;--fs-m:14px;--fs-l:16px;--fs-xl:22px;--fs-xxl:28px;--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px;--r:6px}}
@media (prefers-color-scheme: dark){{:root{{{_tokens(DARK)}}}}}
*{{box-sizing:border-box}}body{{margin:0;font:var(--fs-m)/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}}
a{{color:var(--accent)}}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible,[tabindex]:focus-visible{{outline:3px solid var(--focus);outline-offset:2px}}
.skip{{position:absolute;left:-999px;top:var(--sp-2);background:var(--panel);color:var(--fg);padding:var(--sp-2) var(--sp-3);border:2px solid var(--fg);z-index:10}}.skip:focus{{left:var(--sp-2)}}
svg.i{{width:1.25em;height:1.25em;vertical-align:-0.3em;flex:none}}svg.i.big{{width:2em;height:2em}}svg.i.lg{{width:1.6em;height:1.6em}}
/* --- top bar, app launcher, app bar --- */
.top{{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-3);padding:var(--sp-1) var(--sp-4);background:var(--top);color:var(--top-fg);min-height:46px}}
.top a{{color:var(--top-fg);font-weight:600;text-decoration:none}}.top a:hover{{text-decoration:underline}}.top .brand{{font-size:var(--fs-l);display:flex;align-items:center;gap:var(--sp-2)}}
.top .crumb{{display:flex;flex-wrap:wrap;gap:var(--sp-1);list-style:none;margin:0;padding:0;align-items:center}}
.top .crumb li+li::before{{content:"\\203A";margin:0 var(--sp-1);opacity:.7}}.top .crumb li{{white-space:nowrap}}.top .crumb li a{{font-weight:500}}
.top form{{margin-left:auto;display:flex;gap:var(--sp-2);align-items:center}}.top form label{{display:flex;gap:var(--sp-2);align-items:center}}.top .who{{font-weight:600}}
.top form select{{max-width:14em;min-height:32px;padding:3px var(--sp-2)}}.top form button{{min-height:32px;padding:3px var(--sp-3)}}
details.apps{{position:relative}}details.apps>summary{{list-style:none;cursor:pointer;display:flex;align-items:center;padding:var(--sp-1);border-radius:var(--r);color:var(--top-fg)}}
details.apps>summary::-webkit-details-marker{{display:none}}details.apps>summary:hover,details.apps[open]>summary{{background:rgba(255,255,255,.18)}}
.launcher{{position:absolute;top:100%;left:0;z-index:20;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px var(--shadow);padding:var(--sp-3);width:min(92vw,720px);display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:var(--sp-2)}}
.launcher .app{{display:block;padding:var(--sp-2);border-radius:var(--r);color:var(--fg);font-weight:600;text-decoration:none}}.launcher .app:hover,.launcher .app[aria-current]{{background:var(--chip)}}
.launcher .app .ic{{display:flex;align-items:center;justify-content:center;width:40px;height:40px;border-radius:10px;margin-bottom:var(--sp-1);background:var(--chip);color:var(--accent)}}
.launcher .app ul{{list-style:none;margin:var(--sp-1) 0 0;padding:0;font-weight:400;font-size:var(--fs-s)}}.launcher .app ul a{{color:var(--muted);font-weight:400}}
.appbar{{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-1) var(--sp-3);padding:0 var(--sp-4);background:var(--panel);border-bottom:1px solid var(--line);min-height:38px;font-size:var(--fs-m)}}
.appbar .appname{{font-weight:700;display:flex;align-items:center;gap:var(--sp-1);color:var(--accent);margin-right:var(--sp-2)}}
.appbar a{{color:var(--fg);text-decoration:none;padding:7px var(--sp-2);border-bottom:3px solid transparent;display:inline-flex;align-items:center;gap:4px}}
.appbar a:hover{{background:var(--chip)}}.appbar a[aria-current=page]{{border-bottom-color:var(--accent);font-weight:600}}
.avatar{{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;font-size:var(--fs-s);font-weight:700;color:var(--avatar-fg);vertical-align:middle;flex:none;letter-spacing:.02em}}
.avatar.sm{{width:22px;height:22px;font-size:10px}}.avatar.lg{{width:56px;height:56px;font-size:var(--fs-xl)}}.avatar.cog{{border-radius:8px}}.avatar svg.i{{width:70%;height:70%}}
.avatars{{display:inline-flex;align-items:center}}.avatars .avatar+.avatar{{margin-left:-8px;box-shadow:0 0 0 2px var(--panel)}}
.named{{display:inline-flex;align-items:center;gap:6px;vertical-align:middle}}.named a{{text-decoration:none;font-weight:600;color:var(--fg)}}
/* --- layout, rail --- */
.layout{{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 46px)}}
nav.rail{{background:var(--panel);border-right:1px solid var(--line);padding:var(--sp-3)}}
nav.rail ul{{list-style:none;margin:0;padding:0}}nav.rail li a{{display:flex;align-items:center;gap:var(--sp-2);padding:6px 10px;border-radius:var(--r);color:var(--fg);text-decoration:none;border-left:3px solid transparent}}
nav.rail li a:hover{{background:var(--bg)}}nav.rail li a[aria-current=page]{{background:var(--chip);font-weight:700;border-left-color:var(--accent);color:var(--accent)}}
nav.rail h2{{font-size:var(--fs-s);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:var(--sp-4) 0 var(--sp-1) 10px;border:0;padding:0}}
nav.rail .tree ul{{padding-left:14px}}nav.rail .tree>ul{{padding-left:0}}nav.rail .tree a{{padding:2px 8px;font-size:var(--fs-s);border-left:0}}
main{{padding:var(--sp-4) var(--sp-6);max-width:1400px;min-width:0}}
h1{{font-size:var(--fs-xl);margin:0 0 var(--sp-3);display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}}h2{{font-size:var(--fs-l);margin:var(--sp-6) 0 var(--sp-2);border-bottom:1px solid var(--line);padding-bottom:3px}}
h3{{font-size:var(--fs-m);margin:var(--sp-3) 0 var(--sp-1)}}h4{{font-size:var(--fs-m);margin:var(--sp-2) 0 2px}}
/* --- tables, chips, cards, controls --- */
.tbl{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:var(--sp-2) 0;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);box-shadow:0 1px 3px var(--shadow)}}
table{{border-collapse:collapse;width:100%;background:var(--panel);font-size:var(--fs-m)}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}}th{{background:var(--th);font-size:var(--fs-s);text-transform:uppercase;letter-spacing:.03em;color:var(--muted);white-space:nowrap}}
th a{{color:var(--muted);text-decoration:none}}th a[aria-sort]{{color:var(--accent)}}tr:hover td{{background:var(--bg)}}tbody tr:last-child td{{border-bottom:0}}
th[scope=row]{{font-weight:600;background:transparent;text-transform:none;letter-spacing:0;color:var(--fg);white-space:nowrap}}td.num{{white-space:nowrap}}td.sel,th.sel{{width:2em}}
.chip{{display:inline-block;padding:0 var(--sp-2);border-radius:999px;font-size:var(--fs-s);font-weight:600;background:var(--chip);color:var(--fg);max-width:100%;overflow-wrap:anywhere;line-height:1.6}}
.chip.ok{{background:var(--ok-bg);color:var(--ok)}}.chip.warn{{background:var(--warn-bg);color:var(--warn)}}.chip.crit{{background:var(--crit-bg);color:var(--crit)}}.chip.info{{background:var(--info-bg);color:var(--info)}}
.tag{{display:inline-block;padding:0 6px;border:1px solid var(--ctl);border-radius:4px;font-size:var(--fs-s);color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-3) var(--sp-4);margin:var(--sp-3) 0;box-shadow:0 1px 3px var(--shadow)}}
.card.crit{{border-color:var(--crit)}}.card.warn{{border-color:var(--warn)}}.card.decide{{border-left:4px solid var(--accent)}}.card.quiet{{border-left:3px solid var(--line);box-shadow:none}}
.why{{font-size:var(--fs-s);color:var(--muted)}}code{{font-size:var(--fs-s);background:var(--th);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}}
pre{{background:var(--th);padding:10px;border-radius:var(--r);font-size:var(--fs-s);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}}pre.cell{{margin:0}}pre.src{{margin:0;background:none;padding:0}}
button,.button{{background:var(--accent);color:var(--btn-fg);border:0;border-radius:var(--r);padding:6px var(--sp-3);cursor:pointer;font:inherit;font-weight:600;min-height:36px;display:inline-flex;align-items:center;gap:6px;text-decoration:none;line-height:1.2}}
button.secondary,.button.secondary{{background:var(--secondary);color:var(--btn-fg)}}button.danger{{background:var(--crit);color:var(--btn-fg)}}
button.reject{{background:var(--panel);color:var(--crit);border:2px solid var(--crit)}}button[disabled]{{opacity:.55;cursor:not-allowed}}
button.ghost,.button.ghost{{background:var(--panel);color:var(--fg);border:1px solid var(--ctl)}}button.ghost:hover,.button.ghost:hover{{background:var(--chip)}}
input,select,textarea{{padding:6px var(--sp-2);border:1px solid var(--ctl);border-radius:var(--r);background:var(--panel);color:var(--fg);font:inherit;width:100%;max-width:30em;min-height:36px}}
textarea{{min-height:4.5em}}input[type=checkbox]{{width:auto;min-height:0;vertical-align:middle;margin-right:var(--sp-1)}}input[type=file]{{padding:4px 0;border:0;background:none}}
form.inline{{display:inline-block;margin-right:var(--sp-2)}}
.fields{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--sp-2) var(--sp-4);align-items:start}}
label.f{{display:block;font-size:var(--fs-m)}}label.f>span{{display:block;color:var(--muted);font-size:var(--fs-s);margin-bottom:2px}}label.f>span b{{color:var(--crit)}}
label.f.bad>span{{color:var(--crit);font-weight:700}}label.f.bad input,label.f.bad select,label.f.bad textarea{{border:2px solid var(--crit)}}
.err{{color:var(--crit);font-size:var(--fs-s);font-weight:600;margin:2px 0}}.status{{background:var(--ok-bg);color:var(--ok);border-radius:var(--r);padding:var(--sp-2) var(--sp-3);margin:0 0 var(--sp-3);font-weight:600}}
.status.crit{{background:var(--crit-bg);color:var(--crit)}}.wide{{max-width:100%}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:var(--fs-m)}}.kv dt{{color:var(--muted)}}.kv dd{{margin:0;word-break:break-word}}
.facts{{display:flex;gap:var(--sp-4);flex-wrap:wrap;align-items:baseline;margin:var(--sp-2) 0}}.facts b{{font-size:var(--fs-l)}}
.actions{{display:flex;gap:var(--sp-3);align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:var(--sp-2);margin-top:var(--sp-2)}}
.muted{{color:var(--muted)}}.vh{{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.brief table{{margin:6px 0}}details summary{{cursor:pointer;color:var(--fg)}}details.card>summary{{font-weight:600}}details .why{{margin-top:var(--sp-1)}}
.msg{{white-space:pre-line}}.diff ins{{background:var(--ok-bg);color:var(--ok);text-decoration:none}}.diff del{{background:var(--crit-bg);color:var(--crit)}}
.who{{display:flex;gap:var(--sp-3);flex-wrap:wrap}}.who .card{{min-width:12em;display:flex;gap:var(--sp-3);align-items:center}}
/* --- control panel: views, search, filters, group-by, paging --- */
.cp{{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-3);align-items:center;margin:0 0 var(--sp-3)}}.cp form{{display:contents}}
.cp .search{{display:flex;align-items:center;gap:var(--sp-1);flex:1 1 16em;max-width:36em}}.cp .search label{{flex:1}}.cp .search input{{max-width:none;min-height:34px}}
.cp .views{{display:inline-flex;border:1px solid var(--ctl);border-radius:var(--r);overflow:hidden;margin-left:auto}}
.cp .views a,.cp .views span{{display:inline-flex;align-items:center;gap:4px;padding:5px 9px;color:var(--fg);text-decoration:none;font-size:var(--fs-s)}}.cp .views a+a,.cp .views a+span,.cp .views span+a{{border-left:1px solid var(--ctl)}}
.cp .views [aria-current]{{background:var(--accent);color:var(--btn-fg)}}
.chips{{display:flex;flex-wrap:wrap;gap:var(--sp-1);align-items:center}}.chips a{{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:999px;border:1px solid var(--ctl);color:var(--fg);text-decoration:none;font-size:var(--fs-s);background:var(--panel)}}
.chips a[aria-current]{{background:var(--accent);color:var(--btn-fg);border-color:var(--accent)}}.chips .lbl{{font-size:var(--fs-s);color:var(--muted);margin-right:2px}}
.cp select.small{{width:auto;min-height:34px;padding:3px var(--sp-2);max-width:12em}}
.pager{{display:flex;gap:var(--sp-2);align-items:center;font-size:var(--fs-s);color:var(--muted);margin:var(--sp-2) 0}}.pager a,.pager b{{padding:2px 8px;border-radius:4px}}.pager b{{background:var(--chip);color:var(--fg)}}
.groupname td,.groupname th{{background:var(--chip);font-weight:700}}
.bulk{{display:flex;gap:var(--sp-3);align-items:end;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-2) var(--sp-3);margin:var(--sp-2) 0}}.bulk label.f{{margin:0}}
/* --- kanban --- */
.board{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:var(--sp-3);overflow-x:auto;padding-bottom:var(--sp-2);align-items:start}}
.col{{background:var(--chip);border-radius:8px;padding:var(--sp-2);min-height:120px}}.col h3{{margin:2px 0 var(--sp-2);position:sticky;top:0;background:var(--chip);display:flex;align-items:center;gap:var(--sp-2);padding:2px 4px}}
.col h3 .n{{margin-left:auto;background:var(--panel);border-radius:999px;padding:0 8px;font-size:var(--fs-s);color:var(--muted)}}.col.empty{{opacity:.75}}
.kcard{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--r);padding:var(--sp-2) 10px;margin:6px 0;font-size:var(--fs-m);box-shadow:0 1px 2px var(--shadow)}}
.kcard.risk-high{{border-left-color:var(--crit)}}.kcard.risk-medium{{border-left-color:var(--warn)}}.kcard.risk-low{{border-left-color:var(--ok)}}
.kcard .kc-head{{display:flex;gap:var(--sp-2);align-items:flex-start}}.kcard .kc-head a{{font-weight:600;text-decoration:none}}.kcard .kc-head .ty{{color:var(--muted);flex:none}}
.kcard .kc-title{{display:block;color:var(--fg);overflow-wrap:anywhere}}.kcard .kc-meta{{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;font-size:var(--fs-s);color:var(--muted);margin:4px 0}}
.kcard .kc-foot{{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-s);color:var(--muted);margin-top:4px}}.kcard .kc-foot .cnt{{display:inline-flex;align-items:center;gap:2px}}.kcard .kc-foot .avatars{{margin-left:auto}}
/* --- form view: sheet, status bar, smart buttons, tabs --- */
.formhead{{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-3);align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:8px 8px 0 0;padding:var(--sp-2) var(--sp-4);box-shadow:0 1px 3px var(--shadow)}}
.formhead .btns{{display:flex;flex-wrap:wrap;gap:var(--sp-1)}}.formhead .btns a,.formhead .btns button{{min-height:32px;padding:4px 10px;font-size:var(--fs-s)}}
.statusbar{{display:flex;list-style:none;margin:0;padding:0;margin-left:auto;overflow-x:auto;max-width:100%}}
.statusbar li{{position:relative;padding:5px 14px 5px 22px;font-size:var(--fs-s);color:var(--muted);background:var(--chip);white-space:nowrap;border:1px solid var(--line);border-left:0;clip-path:polygon(0 0,calc(100% - 10px) 0,100% 50%,calc(100% - 10px) 100%,0 100%,10px 50%)}}
.statusbar li:first-child{{padding-left:14px;clip-path:polygon(0 0,calc(100% - 10px) 0,100% 50%,calc(100% - 10px) 100%,0 100%);border-left:1px solid var(--line);border-radius:4px 0 0 4px}}
.statusbar li:last-child{{clip-path:polygon(0 0,100% 0,100% 100%,0 100%,10px 50%);border-radius:0 4px 4px 0}}
.statusbar li.done{{color:var(--fg)}}.statusbar li.current{{background:var(--accent);color:var(--btn-fg);font-weight:700}}.statusbar li.exit{{opacity:.8;font-style:italic}}
.statusbar li .chip{{background:transparent;color:inherit;padding:0;font-weight:inherit}}
.smart{{display:flex;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-top:0}}
.smart a{{display:flex;align-items:center;gap:var(--sp-2);padding:var(--sp-2) var(--sp-4);border-right:1px solid var(--line);color:var(--fg);text-decoration:none;min-width:9em}}.smart a:hover{{background:var(--chip)}}
.smart a b{{display:block;font-size:var(--fs-l);line-height:1.1}}.smart a span{{font-size:var(--fs-s);color:var(--muted)}}.smart a svg.i{{color:var(--accent);width:1.6em;height:1.6em}}
.sheet{{background:var(--panel);border:1px solid var(--line);border-top:0;border-radius:0 0 8px 8px;padding:var(--sp-4) var(--sp-6);box-shadow:0 1px 3px var(--shadow)}}
.sheet .title{{font-size:var(--fs-xxl);font-weight:600;margin:0 0 var(--sp-2);line-height:1.2;overflow-wrap:anywhere}}.sheet .subtitle{{color:var(--muted);margin-bottom:var(--sp-3)}}
.sheet .sheetfields{{display:grid;grid-template-columns:1fr 1fr;gap:var(--sp-2) var(--sp-6)}}
.sheet .sheetfields .fld{{display:grid;grid-template-columns:minmax(8em,40%) 1fr;gap:var(--sp-3);padding:4px 0;border-bottom:1px dotted var(--line);align-items:baseline}}
.sheet .sheetfields .fld dt{{color:var(--muted);font-size:var(--fs-s);font-weight:600}}.sheet .sheetfields .fld dd{{margin:0;overflow-wrap:anywhere}}
.tabs{{display:flex;gap:0;flex-wrap:wrap;margin:var(--sp-4) 0 0;border-bottom:1px solid var(--line)}}.tabs a{{padding:7px 14px;border:0;border-bottom:3px solid transparent;text-decoration:none;color:var(--fg);font-size:var(--fs-m);display:inline-flex;align-items:center;gap:6px}}
.tabs a:hover{{background:var(--chip)}}.tabs a[aria-selected=true]{{border-bottom-color:var(--accent);font-weight:700;color:var(--accent)}}
.tab-panel{{padding-top:var(--sp-2)}}.tab-panel>h2{{border:0;margin-top:var(--sp-3)}}.js .tab-panel:not(.active){{display:none}}
/* --- chatter --- */
.chatter{{margin-top:var(--sp-6);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-3) var(--sp-4)}}
.chatter h2{{border:0;margin:0 0 var(--sp-2);display:flex;align-items:center;gap:var(--sp-2)}}
.composer{{border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-2) var(--sp-3);margin-bottom:var(--sp-3);background:var(--bg)}}.composer .fields{{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}}
.timeline{{list-style:none;margin:0;padding:0}}.timeline>li{{display:grid;grid-template-columns:36px 1fr;gap:var(--sp-2) var(--sp-3);padding:var(--sp-2) 0;border-top:1px solid var(--line)}}
.timeline .tl-head{{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:baseline}}.timeline .tl-head b{{font-weight:600}}.timeline .tl-head time,.timeline .tl-head .muted{{font-size:var(--fs-s);color:var(--muted)}}
.timeline .tl-body{{margin-top:2px;overflow-wrap:anywhere}}.timeline li.kind-decision .tl-body{{border-left:3px solid var(--accent2);padding-left:var(--sp-2)}}.timeline li.kind-message .tl-body{{background:var(--bg);border-radius:var(--r);padding:var(--sp-2) var(--sp-3)}}
.timeline li.kind-note .tl-body{{background:var(--warn-bg);color:var(--warn);border-radius:var(--r);padding:var(--sp-2) var(--sp-3)}}
.timeline .kind-chip{{font-size:var(--fs-s);color:var(--muted);display:inline-flex;align-items:center;gap:3px}}
/* --- dashboard --- */
.kpis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:var(--sp-3);margin:var(--sp-3) 0}}
.kpi{{display:block;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-3);text-decoration:none;color:var(--fg);box-shadow:0 1px 3px var(--shadow);position:relative;overflow:hidden}}
.kpi:hover{{border-color:var(--accent)}}.kpi .kpi-h{{display:flex;align-items:center;gap:var(--sp-2);color:var(--muted);font-size:var(--fs-s)}}.kpi .kpi-h svg.i{{color:var(--accent)}}
.kpi b{{display:block;font-size:var(--fs-xxl);line-height:1.1;margin:4px 0}}.kpi.crit b{{color:var(--crit)}}.kpi .spark{{display:block;width:100%;height:28px;margin-top:4px}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:var(--sp-3)}}.chart{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-3);box-shadow:0 1px 3px var(--shadow);min-width:0}}
.chart h2{{border:0;margin:0 0 var(--sp-2);font-size:var(--fs-m);display:flex;align-items:center;gap:var(--sp-2)}}.chart svg{{width:100%;height:auto;display:block}}.chart .legend{{display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-3);font-size:var(--fs-s);color:var(--muted);margin-top:var(--sp-1)}}
.chart .legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}}
.sw{{fill:var(--c1)}}.sw2{{fill:var(--c2)}}.sw3{{fill:var(--c3)}}.sw4{{fill:var(--c4)}}.sw5{{fill:var(--c5)}}.sw6{{fill:var(--c6)}}.sw-ok{{fill:var(--ok)}}.sw-warn{{fill:var(--warn)}}.sw-crit{{fill:var(--crit)}}.sw-line{{fill:var(--line)}}
.legend .sw{{background:var(--c1)}}.legend .sw2{{background:var(--c2)}}.legend .sw3{{background:var(--c3)}}.legend .sw4{{background:var(--c4)}}.legend .sw5{{background:var(--c5)}}.legend .sw6{{background:var(--c6)}}.legend .sw-ok{{background:var(--ok)}}.legend .sw-warn{{background:var(--warn)}}.legend .sw-crit{{background:var(--crit)}}
svg .axis{{stroke:var(--line);stroke-width:1}}svg .lbl{{fill:var(--muted);font:11px system-ui,sans-serif}}svg .val{{fill:var(--fg);font:600 11px system-ui,sans-serif}}svg .spark-line{{fill:none;stroke:var(--accent);stroke-width:1.5}}svg .spark-area{{fill:var(--accent);opacity:.12}}
svg a:hover rect,svg a:focus rect{{opacity:.75}}
.quick{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:var(--sp-2);margin:var(--sp-2) 0 var(--sp-3)}}
.quick a{{display:flex;align-items:center;gap:var(--sp-2);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2) var(--sp-3);color:var(--fg);text-decoration:none;font-weight:600;font-size:var(--fs-s)}}
.quick a:hover{{border-color:var(--accent);color:var(--accent)}}.quick a svg.i{{color:var(--accent);width:1.6em;height:1.6em}}
.apphome{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--sp-3)}}.apphome .card{{margin:0}}.apphome .card h3{{margin:0 0 4px;display:flex;align-items:center;gap:var(--sp-2)}}
.apphome .card b.n{{font-size:var(--fs-xl)}}
/* --- inbox --- */
.queue{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(18em,2fr) 3fr;gap:var(--sp-2) var(--sp-3);align-items:start}}
.queue>li{{grid-column:1;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2) var(--sp-3);margin:0;display:grid;grid-template-columns:auto 1fr;gap:var(--sp-1) var(--sp-2);align-items:start;box-shadow:0 1px 2px var(--shadow)}}
.queue>li .ty{{color:var(--accent);padding-top:2px}}.queue h3{{margin:0;font-weight:600}}.queue h3 a{{text-decoration:none;color:var(--fg)}}.queue h3 a:hover{{color:var(--accent)}}
.queue .meta{{grid-column:2;display:flex;gap:var(--sp-1) var(--sp-2);flex-wrap:wrap;align-items:center;font-size:var(--fs-s);color:var(--muted)}}
.queue>li.open{{grid-column:2;grid-row:1/span 60;display:block;border-left:4px solid var(--accent);position:sticky;top:var(--sp-2);padding:var(--sp-3) var(--sp-4)}}.queue:not(:has(li.open)){{grid-template-columns:1fr}}
.queue .card.decide{{border:0;box-shadow:none;padding:0;margin:0}}
.notes{{list-style:none;padding:0;margin:0}}.notes li{{display:flex;gap:var(--sp-2);align-items:flex-start;padding:4px 0;border-bottom:1px solid var(--line)}}.notes li svg.i{{color:var(--accent2);flex:none;margin-top:2px}}
/* --- document library --- */
.library{{display:grid;grid-template-columns:220px 1fr;gap:var(--sp-4);align-items:start}}.library .ftree{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2)}}
.library .ftree ul{{list-style:none;margin:0;padding:0}}.library .ftree ul ul{{padding-left:16px}}.library .ftree a{{display:flex;align-items:center;gap:6px;padding:3px 6px;border-radius:4px;color:var(--fg);text-decoration:none;font-size:var(--fs-m)}}
.library .ftree a:hover{{background:var(--chip)}}.library .ftree a[aria-current=page]{{background:var(--chip);font-weight:700;color:var(--accent)}}.library .ftree a svg.i{{color:var(--accent2)}}.library .ftree a.denied{{color:var(--muted)}}
.toolbar{{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:center;margin:0 0 var(--sp-3)}}.toolbar .button,.toolbar button{{min-height:34px;padding:4px 12px}}.toolbar .sort{{margin-left:auto;display:flex;gap:var(--sp-1);align-items:center;font-size:var(--fs-s);color:var(--muted)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:var(--sp-3);margin:var(--sp-2) 0}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:var(--sp-2);display:flex;flex-direction:column;gap:4px;box-shadow:0 1px 3px var(--shadow);min-width:0}}
.tile .thumb{{height:92px;border-radius:4px;background:var(--chip);display:flex;align-items:center;justify-content:center;color:var(--accent);overflow:hidden;position:relative}}.tile .thumb svg.i{{width:48px;height:48px}}
.tile .thumb pre{{margin:0;padding:6px;font-size:9px;line-height:1.25;background:transparent;color:var(--muted);width:100%;height:100%;overflow:hidden;white-space:pre-wrap}}
.tile .name{{font-weight:600;overflow-wrap:anywhere}}.tile .name a{{text-decoration:none;color:var(--fg)}}.tile .name a:hover{{color:var(--accent)}}.tile .tmeta{{font-size:var(--fs-s);color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}}
.tile .tlinks{{display:flex;gap:var(--sp-2);font-size:var(--fs-s);margin-top:auto}}.tile .tlinks a{{display:inline-flex;align-items:center;gap:3px}}
/* --- profile --- */
.profile-head{{display:flex;gap:var(--sp-4);align-items:center;flex-wrap:wrap}}.profile-head h1{{margin:0}}.profile-head .roles{{display:flex;gap:4px;flex-wrap:wrap}}
.people{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:var(--sp-3)}}.people .card{{margin:0;display:grid;grid-template-columns:auto 1fr;gap:var(--sp-3);align-items:start}}.people .card h3{{margin:0}}
/* --- responsive --- */
@media(max-width:1100px){{.queue{{grid-template-columns:1fr}}.queue>li.open{{grid-column:1;grid-row:auto;position:static}}.library{{grid-template-columns:1fr}}.sheet .sheetfields{{grid-template-columns:1fr}}}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr}}nav.rail{{border-right:0;border-bottom:1px solid var(--line);padding:var(--sp-2) var(--sp-3)}}
nav.rail ul.main{{display:flex;flex-wrap:wrap;gap:var(--sp-1)}}nav.rail .tree,nav.rail h2{{display:none}}
main{{padding:var(--sp-3) var(--sp-4)}}.grid2{{grid-template-columns:1fr}}.sheet{{padding:var(--sp-3) var(--sp-4)}}.top .crumb{{display:none}}}}
@media(max-width:600px){{main{{padding:var(--sp-3) var(--sp-4)}}.top{{gap:var(--sp-2)}}.top form label span.who{{display:none}}.statusbar{{margin-left:0;width:100%}}.formhead{{padding:var(--sp-2) var(--sp-3)}}
.sheet .sheetfields .fld{{grid-template-columns:1fr}}.sheet .title{{font-size:var(--fs-xl)}}.smart a{{min-width:0;flex:1}}.kpis{{grid-template-columns:1fr 1fr}}.board{{grid-auto-columns:minmax(85vw,1fr)}}.cp .views{{margin-left:0}}}}
@media print{{.top,.appbar,nav.rail,.skip,.formhead .btns,.composer,.cp,.tabs,.actions,form,details.cancel,.chatter form{{display:none!important}}.layout{{display:block}}main{{max-width:none;padding:0}}.sheet,.card,.formhead,.smart{{box-shadow:none;border-color:#999}}.tab-panel{{display:block!important}}body{{background:#fff;color:#000}}}}
"""
