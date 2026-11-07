"""The stylesheet: a light, quiet visual system (white surfaces, hairline borders, medium-weight type, one accent). Colour tokens for both themes (AA-checked by tests/test_shell_ux.py
and tests/test_shell_views.py), one type scale, one spacing scale, and the layouts of the app shell, the three views
(list, kanban, form), the dashboard, the inbox, the document library and the chatter."""

LIGHT = {"bg": "#f6f7f8", "panel": "#ffffff", "fg": "#1e1f21", "muted": "#626569", "line": "#e4e6e9", "line-strong": "#d2d5da", "ctl": "#8c9097",
         "accent": "#4255c9", "accent2": "#0e6f6a", "btn-fg": "#ffffff", "accent-fg": "#ffffff", "accent-soft": "#eef0fc", "hover": "#f3f4f6",
         "ok": "#1b6e43", "ok-bg": "#e3f3ea", "warn": "#7f5300", "warn-bg": "#fcf0d7", "crit": "#b42a22", "crit-bg": "#fce9e7",
         "info": "#235a9e", "info-bg": "#e6effa", "secondary": "#5c6066", "top": "#ffffff", "top-fg": "#1e1f21", "focus": "#4255c9",
         "chip": "#eff1f3", "th": "#fafbfc", "shadow": "rgba(30,31,33,.06)", "shadow-lg": "rgba(30,31,33,.14)", "avatar-fg": "#ffffff",
         "c1": "#4255c9", "c2": "#0e8a83", "c3": "#2f74c0", "c4": "#b8661a", "c5": "#737a84", "c6": "#c2416b"}
DARK = {"bg": "#1e1f21", "panel": "#26272a", "fg": "#f2f1f0", "muted": "#a9abaf", "line": "#3a3b3e", "line-strong": "#4a4b4f", "ctl": "#7d8087",
        "accent": "#9aa6ff", "accent2": "#5fcfc8", "btn-fg": "#16171a", "accent-fg": "#16171a", "accent-soft": "#2c2f48", "hover": "#2e2f33",
        "ok": "#86d8a9", "ok-bg": "#1b3a2a", "warn": "#f0c674", "warn-bg": "#463711", "crit": "#f4a39b", "crit-bg": "#4a1f1b",
        "info": "#9cc4ec", "info-bg": "#1c3550", "secondary": "#a9adb3", "top": "#1e1f21", "top-fg": "#f2f1f0", "focus": "#9aa6ff",
        "chip": "#303135", "th": "#2a2b2e", "shadow": "rgba(0,0,0,.3)", "shadow-lg": "rgba(0,0,0,.5)", "avatar-fg": "#ffffff",
        "c1": "#9aa6ff", "c2": "#5fcfc8", "c3": "#8fbbe8", "c4": "#e8b26a", "c5": "#aab0b8", "c6": "#ef93b0"}
# Text/background pairs that must meet WCAG AA (4.5:1) or the non-text floor (3:1) in both themes.
CONTRAST_PAIRS = [("fg", "panel", 4.5), ("fg", "bg", 4.5), ("muted", "panel", 4.5), ("muted", "bg", 4.5), ("accent", "panel", 4.5),
                  ("accent", "bg", 4.5), ("btn-fg", "accent", 4.5), ("btn-fg", "secondary", 4.5), ("btn-fg", "crit", 4.5),
                  ("ok", "ok-bg", 4.5), ("warn", "warn-bg", 4.5), ("crit", "crit-bg", 4.5), ("top-fg", "top", 4.5),
                  ("ctl", "panel", 3), ("fg", "chip", 4.5), ("focus", "panel", 3), ("crit", "panel", 4.5), ("ok", "panel", 4.5),
                  ("warn", "panel", 4.5), ("fg", "th", 4.5),
                  ("accent-fg", "accent", 4.5), ("accent", "accent-soft", 4.5), ("fg", "hover", 4.5), ("muted", "chip", 4.5), ("accent", "chip", 4.5),
                  # the second accent (teal), the info chip and the six chart colours (non-text: 3:1 against both surfaces)
                  ("accent2", "panel", 4.5), ("accent2", "bg", 4.5), ("btn-fg", "accent2", 4.5), ("info", "info-bg", 4.5), ("info", "panel", 4.5),
                  ("c1", "panel", 3), ("c2", "panel", 3), ("c3", "panel", 3), ("c4", "panel", 3), ("c5", "panel", 3), ("c6", "panel", 3),
                  ("c1", "bg", 3), ("c2", "bg", 3), ("c3", "bg", 3), ("c4", "bg", 3), ("c5", "bg", 3), ("c6", "bg", 3)]
# Avatar backgrounds (white initials on each): every one meets 4.5:1 with white, in both themes.
AVATAR_COLOURS = ["#4255c9", "#0e6f6a", "#2f6aa8", "#9a4a1a", "#6b4fbb", "#b2384a", "#2e7d32", "#4a5568"]
AVATAR_FG = "#ffffff"


def _tokens(t: dict) -> str:
    return ";".join(f"--{k}:{v}" for k, v in t.items())


CSS = f"""
:root{{{_tokens(LIGHT)};--fs-s:12px;--fs-m:14px;--fs-l:16px;--fs-xl:22px;--fs-xxl:28px;--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px;--r:6px;--r-lg:10px;--ease:cubic-bezier(.2,0,0,1)}}
:root{{color-scheme:light}}:root[data-theme=dark]{{{_tokens(DARK)};color-scheme:dark}}
@media (prefers-color-scheme: dark){{:root[data-theme=auto]{{{_tokens(DARK)};color-scheme:dark}}}}
button.theme{{background:transparent;color:var(--muted);border:1px solid transparent;min-height:32px;padding:4px 8px}}button.theme:hover{{background:var(--hover);color:var(--fg)}}button.theme .i-dark,:root[data-theme=dark] button.theme .i-light{{display:none}}:root[data-theme=dark] button.theme .i-dark{{display:inline}}
*{{box-sizing:border-box}}body{{margin:0;font:400 var(--fs-m)/1.5 "Inter","SF Pro Text",system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}}
a{{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:2px}}b,strong{{font-weight:600}}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible,[tabindex]:focus-visible{{outline:3px solid var(--focus);outline-offset:2px}}
.skip{{position:absolute;left:-999px;top:var(--sp-2);background:var(--panel);color:var(--fg);padding:var(--sp-2) var(--sp-3);border:2px solid var(--fg);z-index:10}}.skip:focus{{left:var(--sp-2)}}
svg.i{{width:1.25em;height:1.25em;vertical-align:-0.3em;flex:none}}svg.i.big{{width:2em;height:2em}}svg.i.lg{{width:1.6em;height:1.6em}}
/* --- top bar, app launcher, app bar --- */
.top{{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-3);padding:var(--sp-1) var(--sp-4);background:var(--top);color:var(--top-fg);min-height:48px;border-bottom:1px solid var(--line)}}
.top a{{color:var(--top-fg);font-weight:500;text-decoration:none}}.top a:hover{{color:var(--accent)}}.top .brand{{font-size:var(--fs-l);font-weight:600;display:flex;align-items:center;gap:var(--sp-2);letter-spacing:-.01em}}.top .brand svg.i{{color:var(--accent)}}
.top .crumb{{display:flex;flex-wrap:wrap;gap:var(--sp-1);list-style:none;margin:0;padding:0;align-items:center}}
.top .crumb li+li::before{{content:"/";margin:0 var(--sp-2);color:var(--line-strong)}}.top .crumb li{{white-space:nowrap;color:var(--muted)}}.top .crumb li a{{font-weight:400;color:var(--muted)}}.top .crumb li:last-child a{{color:var(--fg)}}.top .crumb li:first-child{{display:none}}.top .crumb li:nth-child(2)::before{{content:"/";margin:0 var(--sp-2) 0 0}}
.top form{{margin-left:auto;display:flex;gap:var(--sp-2);align-items:center}}.top form label{{display:flex;gap:var(--sp-2);align-items:center;color:var(--muted)}}.top .who{{font-weight:500;color:var(--fg)}}
.top form select{{max-width:14em;min-height:32px;padding:3px var(--sp-2)}}.top form button{{min-height:32px;padding:3px var(--sp-3)}}
details.apps{{position:relative}}details.apps>summary{{list-style:none;cursor:pointer;display:flex;align-items:center;padding:var(--sp-1);border-radius:var(--r);color:var(--top-fg)}}
details.apps>summary::-webkit-details-marker{{display:none}}details.apps>summary:hover,details.apps[open]>summary{{background:var(--hover)}}
.launcher{{position:absolute;top:calc(100% + 4px);left:0;z-index:20;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:0 12px 32px var(--shadow-lg),0 1px 3px var(--shadow);padding:var(--sp-3);width:min(92vw,720px);display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:var(--sp-2)}}
.launcher .app{{display:block;padding:var(--sp-2);border-radius:var(--r)}}.launcher .app:hover,.launcher .app:has([aria-current]){{background:var(--hover)}}.launcher .apptitle{{display:block;color:var(--fg);font-weight:500;text-decoration:none}}
.launcher .app .ic{{display:flex;align-items:center;justify-content:center;width:40px;height:40px;border-radius:10px;margin-bottom:var(--sp-1);background:var(--accent-soft);color:var(--accent)}}
.launcher .app ul{{list-style:none;margin:var(--sp-1) 0 0;padding:0;font-weight:400;font-size:var(--fs-s)}}.launcher .app ul a{{color:var(--muted);font-weight:400}}
.appbar{{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-1) var(--sp-2);padding:0 var(--sp-4);background:var(--panel);border-bottom:1px solid var(--line);min-height:42px;font-size:var(--fs-m)}}
.appbar .appname{{font-weight:600;display:flex;align-items:center;gap:var(--sp-1);color:var(--fg);margin-right:var(--sp-3)}}.appbar .appname svg.i{{color:var(--accent)}}
.appbar a{{color:var(--muted);text-decoration:none;padding:10px var(--sp-2) 8px;border-bottom:2px solid transparent;display:inline-flex;align-items:center;gap:6px;transition:color .15s var(--ease)}}
.appbar a:hover{{color:var(--fg)}}.appbar a[aria-current=page]{{border-bottom-color:var(--fg);color:var(--fg);font-weight:500}}
.avatar{{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;font-size:var(--fs-s);font-weight:600;color:var(--avatar-fg);vertical-align:middle;flex:none;letter-spacing:.02em}}
.avatar.sm{{width:22px;height:22px;font-size:10px}}.avatar.lg{{width:56px;height:56px;font-size:var(--fs-xl)}}.avatar.cog{{border-radius:8px}}.avatar svg.i{{width:70%;height:70%}}
.avatars{{display:inline-flex;align-items:center}}.avatars .avatar+.avatar{{margin-left:-8px;box-shadow:0 0 0 2px var(--panel)}}
.named{{display:inline-flex;align-items:center;gap:6px;vertical-align:middle}}.named a{{text-decoration:none;font-weight:500;color:var(--fg)}}
/* --- layout, rail --- */
.layout{{display:grid;grid-template-columns:232px 1fr;min-height:calc(100vh - 48px)}}
nav.rail{{background:var(--panel);border-right:1px solid var(--line);padding:var(--sp-4) var(--sp-3)}}
nav.rail ul{{list-style:none;margin:0;padding:0}}nav.rail li a{{display:flex;align-items:center;gap:10px;padding:6px 10px;margin:1px 0;border-radius:var(--r);color:var(--fg);text-decoration:none;transition:background .12s var(--ease)}}nav.rail li a svg.i{{color:var(--muted)}}
nav.rail li a:hover{{background:var(--hover)}}nav.rail li a[aria-current=page]{{background:var(--accent-soft);font-weight:500;color:var(--accent)}}nav.rail li a[aria-current=page] svg.i{{color:var(--accent)}}
nav.rail h2{{font-size:var(--fs-s);font-weight:500;letter-spacing:.01em;color:var(--muted);margin:var(--sp-6) 0 var(--sp-1) 10px;border:0;padding:0}}
nav.rail .tree ul{{padding-left:14px}}nav.rail .tree>ul{{padding-left:0}}nav.rail .tree a{{padding:2px 8px;font-size:var(--fs-s);border-left:0}}
main{{padding:var(--sp-6) 32px;max-width:1400px;min-width:0}}
h1{{font-size:var(--fs-xl);font-weight:500;letter-spacing:-.015em;line-height:1.25;margin:0 0 var(--sp-4);display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}}h2{{font-size:var(--fs-l);font-weight:500;letter-spacing:-.005em;margin:28px 0 var(--sp-3);border:0;padding:0}}
h3{{font-size:var(--fs-m);font-weight:600;margin:var(--sp-3) 0 var(--sp-1)}}h4{{font-size:var(--fs-m);font-weight:500;margin:var(--sp-2) 0 2px}}
/* --- tables, chips, cards, controls --- */
.tbl{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:var(--sp-2) 0 var(--sp-4);background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg)}}
table{{border-collapse:collapse;width:100%;background:var(--panel);font-size:var(--fs-m)}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}}th{{background:var(--th);font-size:var(--fs-s);font-weight:500;color:var(--muted);white-space:nowrap}}
th a{{color:var(--muted);text-decoration:none}}th a[aria-sort]{{color:var(--fg)}}tr:hover td{{background:var(--hover)}}tbody tr:last-child td{{border-bottom:0}}
th[scope=row]{{font-weight:500;background:transparent;text-transform:none;letter-spacing:0;color:var(--fg);white-space:nowrap}}td.num{{white-space:nowrap}}td.sel,th:first-child:has(input[data-select-all]){{width:2em}}
.chip{{display:inline-block;padding:1px 9px;border-radius:999px;font-size:var(--fs-s);font-weight:500;background:var(--chip);color:var(--fg);max-width:100%;overflow-wrap:anywhere;line-height:1.55}}
.chip.ok{{background:var(--ok-bg);color:var(--ok)}}.chip.warn{{background:var(--warn-bg);color:var(--warn)}}.chip.crit{{background:var(--crit-bg);color:var(--crit)}}.chip.info{{background:var(--info-bg);color:var(--info)}}
.tag{{display:inline-block;padding:0 7px;border:1px solid var(--line-strong);border-radius:var(--r);font-size:var(--fs-s);color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-4) 20px;margin:var(--sp-3) 0}}
.card.crit{{border-color:color-mix(in srgb,var(--crit) 45%,var(--line));background:color-mix(in srgb,var(--crit-bg) 35%,var(--panel))}}.card.warn{{border-color:color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn-bg) 35%,var(--panel))}}.card.decide{{border-color:color-mix(in srgb,var(--accent) 35%,var(--line));box-shadow:inset 3px 0 0 var(--accent)}}.card.quiet{{background:transparent}}.card.info{{border-color:color-mix(in srgb,var(--info) 35%,var(--line));background:color-mix(in srgb,var(--info-bg) 40%,var(--panel))}}ul.staged li,ul.blockers li{{margin:4px 0}}
.why{{font-size:var(--fs-s);color:var(--muted)}}code{{font-size:var(--fs-s);background:var(--chip);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}}
pre{{background:var(--chip);padding:12px;border-radius:var(--r);font-size:var(--fs-s);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}}pre.cell{{margin:0}}pre.src{{margin:0;background:none;padding:0}}
button,.button{{background:var(--accent);color:var(--btn-fg);border:1px solid transparent;border-radius:var(--r);padding:6px 14px;cursor:pointer;font:inherit;font-weight:500;min-height:34px;display:inline-flex;align-items:center;gap:6px;text-decoration:none;line-height:1.2;transition:background .15s var(--ease),border-color .15s var(--ease),box-shadow .15s var(--ease)}}button:hover,.button:hover{{background:color-mix(in srgb,var(--accent) 88%,var(--fg))}}
button.secondary,.button.secondary{{background:var(--panel);color:var(--fg);border-color:var(--line-strong)}}button.secondary:hover,.button.secondary:hover{{background:var(--hover);border-color:var(--ctl)}}button.danger{{background:var(--crit);color:var(--btn-fg)}}button.danger:hover{{background:color-mix(in srgb,var(--crit) 88%,var(--fg))}}
button.reject{{background:var(--panel);color:var(--crit);border:1px solid color-mix(in srgb,var(--crit) 55%,var(--line))}}button.reject:hover{{background:var(--crit-bg)}}button[disabled]{{opacity:.5;cursor:not-allowed}}
button.ghost,.button.ghost{{background:transparent;color:var(--fg);border:1px solid var(--line-strong)}}button.ghost:hover,.button.ghost:hover{{background:var(--hover);border-color:var(--ctl)}}
input,select,textarea{{padding:6px 10px;border:1px solid var(--ctl);border-radius:var(--r);background:var(--panel);color:var(--fg);font:inherit;width:100%;max-width:30em;min-height:34px;transition:border-color .15s var(--ease),box-shadow .15s var(--ease)}}input:hover,select:hover,textarea:hover{{border-color:var(--fg)}}input:focus,select:focus,textarea:focus{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}}
textarea{{min-height:4.5em}}input[type=checkbox]{{width:auto;min-height:0;vertical-align:middle;margin-right:var(--sp-1)}}input[type=file]{{padding:4px 0;border:0;background:none}}
form.inline{{display:inline-block;margin-right:var(--sp-2)}}
.fields{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--sp-2) var(--sp-4);align-items:start}}
label.f{{display:block;font-size:var(--fs-m)}}label.f>span{{display:block;color:var(--muted);font-size:var(--fs-s);font-weight:500;margin-bottom:4px}}label.f>span b{{color:var(--crit)}}
label.f.bad>span{{color:var(--crit);font-weight:700}}label.f.bad input,label.f.bad select,label.f.bad textarea{{border:2px solid var(--crit)}}
.err{{color:var(--crit);font-size:var(--fs-s);font-weight:600;margin:2px 0}}.status{{background:var(--ok-bg);color:var(--ok);border-radius:var(--r-lg);padding:10px 14px;margin:0 0 var(--sp-4);font-weight:500}}
.status.crit{{background:var(--crit-bg);color:var(--crit)}}.wide{{max-width:100%}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:var(--fs-m)}}.kv dt{{color:var(--muted)}}.kv dd{{margin:0;word-break:break-word}}
.facts{{display:flex;gap:var(--sp-6);flex-wrap:wrap;align-items:baseline;margin:var(--sp-2) 0}}.facts b{{font-size:var(--fs-l);font-weight:500}}
.actions{{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:var(--sp-3);margin-top:var(--sp-3)}}
.muted{{color:var(--muted)}}.vh{{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.brief table{{margin:6px 0}}details summary{{cursor:pointer;color:var(--fg)}}details.card>summary{{font-weight:600}}details .why{{margin-top:var(--sp-1)}}
.msg{{white-space:pre-line}}.diff ins{{background:var(--ok-bg);color:var(--ok);text-decoration:none}}.diff del{{background:var(--crit-bg);color:var(--crit)}}
.who{{display:flex;gap:var(--sp-3);flex-wrap:wrap}}.who .card{{min-width:12em;display:flex;gap:var(--sp-3);align-items:center}}
/* --- control panel: views, search, filters, group-by, paging --- */
.cp{{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-3);align-items:center;margin:0 0 var(--sp-3)}}.cp form{{display:contents}}
.cp .search{{display:flex;align-items:center;gap:var(--sp-1);flex:1 1 16em;max-width:36em}}.cp .search label{{flex:1}}.cp .search input{{max-width:none;min-height:34px}}
.cp .views{{display:inline-flex;gap:2px;padding:2px;background:var(--chip);border-radius:var(--r);margin-left:auto}}
.cp .views a,.cp .views span{{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:4px;color:var(--muted);text-decoration:none;font-size:var(--fs-s);font-weight:500}}.cp .views a:hover{{color:var(--fg)}}
.cp .views [aria-current]{{background:var(--panel);color:var(--fg);box-shadow:0 1px 2px var(--shadow-lg)}}
.chips{{display:flex;flex-wrap:wrap;gap:var(--sp-1);align-items:center}}.chips a{{display:inline-flex;align-items:center;gap:4px;padding:3px 11px;border-radius:999px;border:1px solid var(--line-strong);color:var(--fg);text-decoration:none;font-size:var(--fs-s);background:var(--panel)}}.chips a:hover{{background:var(--hover)}}
.chips a[aria-current]{{background:var(--accent-soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,transparent);font-weight:500}}.chips .lbl{{font-size:var(--fs-s);color:var(--muted);margin-right:2px}}
.cp select.small{{width:auto;min-height:34px;padding:3px var(--sp-2);max-width:12em}}
.pager{{display:flex;gap:var(--sp-2);align-items:center;font-size:var(--fs-s);color:var(--muted);margin:var(--sp-2) 0}}.pager a,.pager b{{padding:2px 8px;border-radius:4px}}.pager b{{background:var(--chip);color:var(--fg)}}
.groupname td,.groupname th{{background:var(--th);font-weight:600}}
.bulk{{display:flex;gap:var(--sp-3);align-items:end;flex-wrap:wrap;background:var(--accent-soft);border:1px solid color-mix(in srgb,var(--accent) 25%,transparent);border-radius:var(--r-lg);padding:var(--sp-2) var(--sp-3);margin:var(--sp-2) 0}}.bulk label.f{{margin:0}}
/* --- kanban --- */
.board{{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:var(--sp-3);overflow-x:auto;padding-bottom:var(--sp-2);align-items:start}}
.col{{background:var(--th);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-2);min-height:120px}}.col h3{{margin:2px 0 var(--sp-2);position:sticky;top:0;background:var(--th);font-weight:500;display:flex;align-items:center;gap:var(--sp-2);padding:2px 4px}}
.col h3 .n{{margin-left:auto;background:var(--panel);border-radius:999px;padding:0 8px;font-size:var(--fs-s);color:var(--muted)}}.col.empty{{opacity:.75}}
.kcard{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:10px 12px;margin:6px 0;font-size:var(--fs-m);transition:box-shadow .15s var(--ease),border-color .15s var(--ease)}}.kcard:hover{{box-shadow:0 4px 12px var(--shadow-lg);border-color:var(--line-strong)}}
.kcard.risk-high{{border-left-color:var(--crit)}}.kcard.risk-medium{{border-left-color:var(--warn)}}.kcard.risk-low{{border-left-color:var(--ok)}}
.kcard .kc-head{{display:flex;gap:var(--sp-2);align-items:flex-start}}.kcard .kc-head a{{font-weight:500;text-decoration:none}}.kcard .kc-head .ty{{color:var(--muted);flex:none}}
.kcard .kc-title{{display:block;color:var(--fg);overflow-wrap:anywhere}}.kcard .kc-meta{{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;font-size:var(--fs-s);color:var(--muted);margin:4px 0}}
.kcard .kc-foot{{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-s);color:var(--muted);margin-top:4px}}.kcard .kc-foot .cnt{{display:inline-flex;align-items:center;gap:2px}}.kcard .kc-foot .avatars{{margin-left:auto}}
/* --- form view: sheet, status bar, smart buttons, tabs --- */
.formhead{{display:flex;flex-wrap:wrap;gap:var(--sp-2) var(--sp-3);align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg) var(--r-lg) 0 0;padding:10px 20px}}
.formhead .btns{{display:flex;flex-wrap:wrap;gap:var(--sp-1)}}.ops{{display:flex;flex-wrap:wrap;gap:var(--sp-1);align-items:center;margin:var(--sp-2) 0 0}}.ops .lbl{{font-size:var(--fs-s);color:var(--muted);margin-right:var(--sp-1)}}body.leader kbd.hk{{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}}kbd.hk::before{{content:'o';opacity:.55;margin-right:1px}}kbd.hk{{display:inline-block;min-width:1.2em;padding:0 .3em;margin-left:.4em;border:1px solid var(--ctl);border-bottom-width:2px;border-radius:4px;font:600 11px/1.5 ui-monospace,monospace;color:var(--muted);background:var(--chip)}}.ops button,.ops a.button{{min-height:32px;padding:4px 10px;font-size:var(--fs-s)}}.formhead .btns a,.formhead .btns button{{min-height:32px;padding:4px 10px;font-size:var(--fs-s)}}
.statusbar{{display:flex;align-items:center;gap:2px;list-style:none;margin:0;padding:0;margin-left:auto;overflow-x:auto;max-width:100%}}
.statusbar li{{position:relative;padding:4px 12px;font-size:var(--fs-s);color:var(--muted);white-space:nowrap;border-radius:999px}}
.statusbar li+li::before{{content:"";position:absolute;left:-3px;top:50%;width:4px;height:1px;background:var(--line-strong)}}
.statusbar li.done{{color:var(--fg)}}.statusbar li.current{{background:var(--accent-soft);color:var(--accent);font-weight:500;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 30%,transparent)}}.statusbar li.exit{{opacity:.8;font-style:italic}}
.statusbar li .chip{{background:transparent;color:inherit;padding:0;font-weight:inherit}}
.smart{{display:flex;flex-wrap:wrap;background:var(--th);border:1px solid var(--line);border-top:0}}
.smart a{{display:flex;align-items:center;gap:var(--sp-2);padding:10px 20px;border-right:1px solid var(--line);color:var(--fg);text-decoration:none;min-width:9em}}.smart a:hover{{background:var(--hover)}}
.smart a b{{display:block;font-size:var(--fs-l);font-weight:500;line-height:1.1}}.smart a span{{font-size:var(--fs-s);color:var(--muted)}}.smart a svg.i{{color:var(--accent);width:1.6em;height:1.6em}}
.sheet{{background:var(--panel);border:1px solid var(--line);border-top:0;border-radius:0 0 var(--r-lg) var(--r-lg);padding:var(--sp-6) 32px}}
.sheet .title{{font-size:var(--fs-xxl);font-weight:500;letter-spacing:-.02em;margin:0 0 var(--sp-2);line-height:1.2;overflow-wrap:anywhere}}.sheet .subtitle{{color:var(--muted);margin-bottom:var(--sp-3)}}
.sheet .sheetfields{{display:grid;grid-template-columns:1fr 1fr;gap:var(--sp-2) var(--sp-6)}}
.sheet .sheetfields .fld{{display:grid;grid-template-columns:minmax(8em,40%) 1fr;gap:var(--sp-3);padding:7px 0;border-bottom:1px solid var(--line);align-items:baseline}}
.sheet .sheetfields .fld dt{{color:var(--muted);font-size:var(--fs-s);font-weight:500}}.sheet .sheetfields .fld dd{{margin:0;overflow-wrap:anywhere}}
.tabs{{display:flex;gap:var(--sp-2);flex-wrap:wrap;margin:var(--sp-6) 0 0;border-bottom:1px solid var(--line)}}.tabs a{{padding:8px 4px;margin-bottom:-1px;border:0;border-bottom:2px solid transparent;text-decoration:none;color:var(--muted);font-size:var(--fs-m);display:inline-flex;align-items:center;gap:6px}}
.tabs a:hover{{color:var(--fg)}}.tabs a[aria-selected=true]{{border-bottom-color:var(--fg);font-weight:500;color:var(--fg)}}
.tab-panel{{padding-top:var(--sp-2)}}.tab-panel>h2{{border:0;margin-top:var(--sp-3)}}.js .tab-panel:not(.active){{display:none}}
/* --- chatter --- */
.chatter{{margin-top:var(--sp-6);background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-4) 20px}}
.chatter h2{{border:0;margin:0 0 var(--sp-2);display:flex;align-items:center;gap:var(--sp-2)}}
.composer{{border:1px solid var(--line-strong);border-radius:var(--r-lg);padding:10px 14px;margin-bottom:var(--sp-4);background:var(--panel)}}.composer:focus-within{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}}.composer .fields{{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}}
.timeline{{list-style:none;margin:0;padding:0}}.timeline>li{{display:grid;grid-template-columns:36px 1fr;gap:var(--sp-2) var(--sp-3);padding:var(--sp-3) 0;border-top:1px solid var(--line)}}
.timeline .tl-head{{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:baseline}}.timeline .tl-head b{{font-weight:500}}.timeline .tl-head time,.timeline .tl-head .muted{{font-size:var(--fs-s);color:var(--muted)}}
.timeline .tl-body{{margin-top:2px;overflow-wrap:anywhere}}.timeline li.kind-decision .tl-body{{border-left:3px solid var(--accent2);padding-left:var(--sp-2)}}.timeline li.kind-message .tl-body{{background:var(--th);border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-2) var(--sp-3)}}
.timeline li.kind-note .tl-body{{background:var(--warn-bg);color:var(--warn);border-radius:var(--r);padding:var(--sp-2) var(--sp-3)}}
.timeline .kind-chip{{font-size:var(--fs-s);color:var(--muted);display:inline-flex;align-items:center;gap:3px}}
/* --- dashboard --- */
.kpis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:var(--sp-3);margin:var(--sp-3) 0}}
.kpi{{display:block;background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-4);text-decoration:none;color:var(--fg);position:relative;overflow:hidden;transition:box-shadow .15s var(--ease),border-color .15s var(--ease)}}
.kpi:hover{{border-color:var(--line-strong);box-shadow:0 4px 14px var(--shadow-lg)}}.kpi .kpi-h{{display:flex;align-items:center;gap:var(--sp-2);color:var(--muted);font-size:var(--fs-s)}}.kpi .kpi-h svg.i{{color:var(--accent)}}
.kpi b{{display:block;font-size:var(--fs-xxl);font-weight:500;letter-spacing:-.02em;line-height:1.1;margin:6px 0 4px}}.kpi.crit b{{color:var(--crit)}}.kpi .spark{{display:block;width:100%;height:28px;margin-top:4px}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:var(--sp-3)}}.chart{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-4);min-width:0}}
.chart h2{{border:0;margin:0 0 var(--sp-3);font-size:var(--fs-m);font-weight:500;display:flex;align-items:center;gap:var(--sp-2)}}.chart>svg{{width:100%;height:auto;display:block}}.chart .legend{{display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-3);font-size:var(--fs-s);color:var(--muted);margin-top:var(--sp-1)}}
.chart .legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}}
.sw{{fill:var(--c1)}}.sw2{{fill:var(--c2)}}.sw3{{fill:var(--c3)}}.sw4{{fill:var(--c4)}}.sw5{{fill:var(--c5)}}.sw6{{fill:var(--c6)}}.sw-ok{{fill:var(--ok)}}.sw-warn{{fill:var(--warn)}}.sw-crit{{fill:var(--crit)}}.sw-line{{fill:var(--line)}}
.legend .sw{{background:var(--c1)}}.legend .sw2{{background:var(--c2)}}.legend .sw3{{background:var(--c3)}}.legend .sw4{{background:var(--c4)}}.legend .sw5{{background:var(--c5)}}.legend .sw6{{background:var(--c6)}}.legend .sw-ok{{background:var(--ok)}}.legend .sw-warn{{background:var(--warn)}}.legend .sw-crit{{background:var(--crit)}}
svg .axis{{stroke:var(--line);stroke-width:1}}svg .lbl{{fill:var(--muted);font:11px system-ui,sans-serif}}svg .val{{fill:var(--fg);font:600 11px system-ui,sans-serif}}svg .spark-line{{fill:none;stroke:var(--accent);stroke-width:1.5}}svg .spark-area{{fill:var(--accent);opacity:.12}}
svg a:hover rect,svg a:focus rect{{opacity:.75}}
.quick{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:var(--sp-2);margin:var(--sp-2) 0 var(--sp-3)}}
.quick a{{display:flex;align-items:center;gap:var(--sp-2);background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:10px 14px;color:var(--fg);text-decoration:none;font-weight:500;font-size:var(--fs-s);transition:border-color .15s var(--ease),box-shadow .15s var(--ease)}}
.quick a:hover{{border-color:var(--line-strong);box-shadow:0 2px 8px var(--shadow-lg)}}.quick a svg.i{{color:var(--accent);width:1.6em;height:1.6em}}
.apphome{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:var(--sp-3)}}.apphome .card{{margin:0}}.apphome .card h3{{margin:0 0 4px;display:flex;align-items:center;gap:var(--sp-2)}}
.apphome .card b.n{{font-size:var(--fs-xl);font-weight:500}}
/* --- inbox --- */
.filterbar{{display:flex;gap:var(--sp-2);align-items:end;flex-wrap:wrap;margin:var(--sp-2) 0}}.filterbar .f{{margin:0}}.queue{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(18em,2fr) 3fr;gap:var(--sp-2) var(--sp-3);align-items:start}}
.queue>li{{grid-column:1;background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:10px 14px;margin:0;display:grid;grid-template-columns:auto 1fr;gap:var(--sp-1) var(--sp-2);align-items:start;transition:border-color .15s var(--ease)}}.queue>li:hover{{border-color:var(--line-strong)}}
.queue>li .ty{{color:var(--accent);padding-top:2px}}.queue h3{{margin:0;font-weight:500}}.queue h3 a{{text-decoration:none;color:var(--fg)}}.queue h3 a:hover{{color:var(--accent)}}
.queue .meta{{grid-column:2;display:flex;gap:var(--sp-1) var(--sp-2);flex-wrap:wrap;align-items:center;font-size:var(--fs-s);color:var(--muted)}}
.queue>li.open{{grid-column:2;grid-row:1/span 60;display:block;border-color:color-mix(in srgb,var(--accent) 35%,var(--line));box-shadow:inset 3px 0 0 var(--accent),0 4px 16px var(--shadow);position:sticky;top:var(--sp-2);padding:var(--sp-4) 20px}}.queue:not(:has(li.open)){{grid-template-columns:1fr}}
.queue .card.decide{{border:0;box-shadow:none;padding:0;margin:0}}
.notes{{list-style:none;padding:0;margin:0}}.notes li{{display:flex;gap:var(--sp-2);align-items:flex-start;padding:4px 0;border-bottom:1px solid var(--line)}}.notes li svg.i{{color:var(--accent2);flex:none;margin-top:2px}}
/* --- document library --- */
.library{{display:grid;grid-template-columns:220px 1fr;gap:var(--sp-4);align-items:start}}.library .ftree{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-2)}}
.library .ftree ul{{list-style:none;margin:0;padding:0}}.library .ftree ul ul{{padding-left:16px}}.library .ftree a{{display:flex;align-items:center;gap:6px;padding:3px 6px;border-radius:4px;color:var(--fg);text-decoration:none;font-size:var(--fs-m)}}
.library .ftree a:hover{{background:var(--hover)}}.library .ftree a[aria-current=page]{{background:var(--accent-soft);font-weight:500;color:var(--accent)}}.library .ftree a svg.i{{color:var(--accent2)}}.library .ftree a.denied{{color:var(--muted)}}
.toolbar{{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:center;margin:0 0 var(--sp-3)}}.toolbar .button,.toolbar button{{min-height:34px;padding:4px 12px}}.toolbar .sort{{margin-left:auto;display:flex;gap:var(--sp-1);align-items:center;font-size:var(--fs-s);color:var(--muted)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:var(--sp-3);margin:var(--sp-2) 0}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:10px;display:flex;flex-direction:column;gap:6px;min-width:0;transition:box-shadow .15s var(--ease)}}.tile:hover{{box-shadow:0 4px 14px var(--shadow-lg)}}
.tile .thumb{{height:92px;border-radius:6px;background:var(--th);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;color:var(--accent);overflow:hidden;position:relative}}.tile .thumb svg.i{{width:48px;height:48px}}
.tile .thumb pre{{margin:0;padding:6px;font-size:9px;line-height:1.25;background:transparent;color:var(--muted);width:100%;height:100%;overflow:hidden;white-space:pre-wrap}}
.tile .name{{font-weight:500;overflow-wrap:anywhere}}.tile .name a{{text-decoration:none;color:var(--fg)}}.tile .name a:hover{{color:var(--accent)}}.tile .tmeta{{font-size:var(--fs-s);color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}}
.tile .tlinks{{display:flex;gap:var(--sp-2);font-size:var(--fs-s);margin-top:auto}}.tile .tlinks a{{display:inline-flex;align-items:center;gap:3px}}
/* --- profile --- */
.profile-head{{display:flex;gap:var(--sp-4);align-items:center;flex-wrap:wrap}}.profile-head h1{{margin:0}}.profile-head .roles{{display:flex;gap:4px;flex-wrap:wrap}}
.people{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:var(--sp-3)}}.people .card{{margin:0;display:grid;grid-template-columns:auto 1fr;gap:var(--sp-3);align-items:start}}.people .card h3{{margin:0}}
/* --- responsive --- */
@media(max-width:1100px){{.queue{{grid-template-columns:1fr}}.queue>li.open{{grid-column:1;grid-row:auto;position:static}}.library{{grid-template-columns:1fr}}.sheet .sheetfields{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr}}nav.rail{{border-right:0;border-bottom:1px solid var(--line);padding:var(--sp-2) var(--sp-3)}}
nav.rail ul.main{{display:flex;flex-wrap:wrap;gap:var(--sp-1)}}nav.rail .tree,nav.rail h2{{display:none}}
main{{padding:var(--sp-4)}}.grid2{{grid-template-columns:1fr}}.sheet{{padding:var(--sp-4)}}.top .crumb{{display:none}}}}
@media(max-width:600px){{main{{padding:var(--sp-3) var(--sp-4)}}.top{{gap:var(--sp-2)}}.top form label span.who{{display:none}}.statusbar{{margin-left:0;width:100%}}.formhead{{padding:var(--sp-2) var(--sp-3)}}
.sheet .sheetfields .fld{{grid-template-columns:1fr}}.sheet .title{{font-size:var(--fs-xl)}}.smart a{{min-width:0;flex:1}}.kpis{{grid-template-columns:1fr 1fr}}.board{{grid-auto-columns:minmax(85vw,1fr)}}.cp .views{{margin-left:0}}}}
@media print{{.top,.appbar,nav.rail,.skip,.formhead .btns,.composer,.cp,.tabs,.actions,form,details.cancel,.chatter form{{display:none!important}}.layout{{display:block}}main{{max-width:none;padding:0}}.sheet,.card,.formhead,.smart{{box-shadow:none;border-color:#999}}.tab-panel{{display:block!important}}body{{background:#fff;color:#000}}}}

.vh{{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}}
.mx{{position:fixed;right:16px;bottom:16px;z-index:50;display:flex;flex-direction:column;align-items:flex-end;gap:8px;font-size:var(--fs-m)}}
.mx-toggle{{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;border-radius:999px;background:var(--fg);color:var(--panel);border:0;box-shadow:0 6px 20px var(--shadow-lg);cursor:pointer;font-weight:500}}.mx-toggle:hover{{background:var(--fg)}}
.mx-toggle svg.i{{width:18px;height:18px}}.mx.open .mx-toggle span{{display:none}}.mx.open .mx-toggle{{padding:10px}}
.mx-panel{{order:-1;width:min(380px,calc(100vw - 32px));height:min(540px,calc(100vh - 120px));display:flex;flex-direction:column;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:14px;box-shadow:0 16px 40px var(--shadow-lg);overflow:hidden}}
.mx-panel[hidden]{{display:none}}
.mx-head{{display:flex;align-items:center;gap:6px;padding:10px 12px;background:var(--panel);color:var(--fg);border-bottom:1px solid var(--line)}}
.mx-head select{{flex:1;min-width:0;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 6px}}.mx-pick{{flex:1;display:flex}}
.mx-head button{{background:transparent;color:var(--muted);border:0;padding:4px;min-height:auto;cursor:pointer}}.mx-head button svg.i{{width:18px;height:18px}}
.mx-context{{margin:0;padding:6px 12px;font-size:var(--fs-s);color:var(--muted);border-bottom:1px solid var(--line);background:var(--chip)}}
.mx-list{{list-style:none;margin:0;padding:10px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px}}
.mx-msg{{display:flex;gap:6px;align-items:flex-end;max-width:100%}}.mx-msg.mine{{justify-content:flex-end}}
.mx-bubble{{max-width:78%;padding:8px 10px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.4}}
.mx-msg.theirs .mx-bubble{{background:var(--chip);border-bottom-left-radius:4px}}.mx-msg.mine .mx-bubble{{background:var(--accent);color:var(--accent-fg);border-bottom-right-radius:4px}}
.mx-when{{display:block;margin-top:3px;font-size:10px;opacity:.7}}.mx-looked{{display:block;margin-top:4px;font-size:11px;opacity:.75;font-style:italic}}.mx-looked::before{{content:'🔎 '}}.mx-av{{flex:none}}
.mx-empty,.mx-error{{list-style:none;color:var(--muted);font-size:var(--fs-s);text-align:center;padding:12px}}.mx-error{{color:var(--crit)}}
.mx-form{{display:flex;gap:6px;padding:8px;border-top:1px solid var(--line)}}.mx-form textarea{{flex:1;resize:none;min-height:40px;max-height:120px;border:1px solid var(--ctl);border-radius:8px;padding:6px 8px;font:inherit;background:var(--panel);color:var(--fg)}}
.mx-form button{{align-self:flex-end;border-radius:8px;padding:8px 10px}}.mx-form button[disabled]{{opacity:.5;cursor:progress}}
.tpl-link{{display:inline-flex;align-items:center;gap:4px;margin-left:6px}}.tpl-ph{{display:inline-block;min-width:1.8em;text-align:center;padding:0 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:var(--fs-s);font-weight:500}}.mx-wait{{font-size:var(--fs-s);color:var(--muted)}}.mx-msg.mine .mx-bubble a{{color:inherit}}.mx-dots{{display:inline-flex;gap:3px}}.mx-dots i{{width:6px;height:6px;border-radius:50%;background:var(--muted);animation:mxb 1.2s infinite ease-in-out}}.mx-dots i:nth-child(2){{animation-delay:.15s}}.mx-dots i:nth-child(3){{animation-delay:.3s}}
@keyframes mxb{{0%,80%,100%{{opacity:.25;transform:translateY(0)}}40%{{opacity:1;transform:translateY(-3px)}}}}
@media (prefers-reduced-motion:reduce){{.mx-dots i{{animation:none;opacity:.6}}}}
@media (max-width:600px){{.mx{{right:8px;bottom:8px;left:8px;align-items:stretch}}.mx-panel{{width:auto;height:min(70vh,520px)}}.mx-toggle{{align-self:flex-end}}}}
@media print{{.mx{{display:none!important}}}}

pre.diff{{font:12px/1.45 ui-monospace,monospace;background:var(--chip);border:1px solid var(--line);border-radius:6px;padding:8px;overflow-x:auto;white-space:pre}}
pre.diff .add{{color:var(--ok);background:color-mix(in srgb,var(--ok) 12%,transparent);display:inline-block;width:100%}}pre.diff .del{{color:var(--crit);background:color-mix(in srgb,var(--crit) 10%,transparent);display:inline-block;width:100%}}pre.diff .hunk{{color:var(--muted)}}

.checks{{display:flex;flex-wrap:wrap;gap:4px 14px}}.checks label.check{{display:inline-flex;align-items:center;gap:4px;font-size:var(--fs-s)}}fieldset{{border:1px solid var(--line);border-radius:6px;margin:8px 0;padding:6px 10px}}legend{{font-size:var(--fs-s);color:var(--muted)}}

details.modelpick{{margin-top:6px}}details.modelpick summary{{cursor:pointer;color:var(--accent);font-size:var(--fs-s);width:max-content}}
details.modelpick fieldset{{border:0;padding:0;margin:8px 0;display:grid;gap:6px;max-width:34em}}
label.mopt{{display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-lg);cursor:pointer;background:var(--panel)}}
label.mopt input{{grid-row:1 / span 2}}label.mopt .mname{{font-weight:500}}label.mopt .mdetail{{font-size:var(--fs-s);color:var(--muted)}}
label.mopt:hover{{border-color:var(--accent)}}label.mopt.cur{{border-color:var(--accent);background:var(--accent-soft)}}label.mopt.off{{opacity:.55;cursor:not-allowed}}
label.mopt:has(input:checked){{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}}

details.restart{{display:inline-block;position:relative}}details.restart>summary{{list-style:none;cursor:pointer}}details.restart>summary::-webkit-details-marker{{display:none}}
details.restart[open]>form{{position:absolute;z-index:20;top:110%;left:0;width:min(420px,80vw);box-shadow:0 12px 32px var(--shadow-lg)}}
.chip.work{{background:color-mix(in srgb,var(--accent) 18%,var(--chip));color:var(--fg)}}.chip.work svg.i{{animation:pulse 1.4s ease-in-out infinite}}@keyframes pulse{{50%{{opacity:.35}}}}
.timeline>li.kind-working{{background:color-mix(in srgb,var(--accent) 7%,transparent);border-radius:6px}}.timeline>li.kind-working .tl-body form.inline{{margin:0 0 0 var(--sp-1)}}
ol.cogq{{list-style:none;padding:0;margin:var(--sp-2) 0;display:grid;gap:var(--sp-1)}}ol.cogq>li{{display:grid;grid-template-columns:minmax(10em,auto) 1fr;gap:var(--sp-2);align-items:baseline;padding:var(--sp-2);border:1px solid var(--line);border-radius:6px;background:var(--panel)}}
ol.cogq>li.working{{border-left:4px solid var(--accent)}}ol.cogq form.inline{{margin:0}}@media (max-width:640px){{ol.cogq>li{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{.chip.work svg.i{{animation:none}}}}
.settings-layout{{display:grid;grid-template-columns:minmax(11rem,14rem) 1fr;gap:var(--sp-4);align-items:start}}.settings-main{{min-width:0}}
.settings-nav ul{{list-style:none;margin:0;padding:0;display:grid;gap:2px;position:sticky;top:var(--sp-2)}}.settings-nav a{{display:flex;gap:var(--sp-2);align-items:center;padding:6px 10px;border-radius:6px;color:var(--fg);text-decoration:none}}
.settings-nav a:hover{{background:var(--hover)}}.settings-nav a[aria-current]{{background:var(--accent-soft);color:var(--accent);font-weight:500}}.settings-nav .count{{margin-left:auto;background:var(--accent);color:var(--accent-fg);border-radius:999px;padding:0 7px;font-size:var(--fs-s)}}
form.sform{{margin:var(--sp-2) 0}}form.sform.row{{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:end}}form.sform.inline{{display:inline-block;margin:0 0 0 var(--sp-1)}}form.sform.inline button{{min-height:28px;padding:2px 8px;font-size:var(--fs-s)}}
fieldset.checks{{border:1px solid var(--line);border-radius:6px;padding:var(--sp-2);margin:var(--sp-2) 0;display:flex;flex-wrap:wrap;gap:4px var(--sp-3)}}fieldset.checks legend{{font-size:var(--fs-s);color:var(--muted);padding:0 4px}}
ul.chips-list{{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-2)}}ul.chips-list li{{display:flex;align-items:center}}ol.edits li{{margin:2px 0}}
section.cog-rails{{border-top:1px solid var(--line);padding-top:var(--sp-2);margin-top:var(--sp-3)}}.card.rail p{{margin:4px 0}}.pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
@media (max-width:760px){{.settings-layout{{grid-template-columns:1fr}}.settings-nav ul{{position:static;display:flex;flex-wrap:wrap}}}}
.hk-armed{{outline:3px solid var(--accent);outline-offset:2px}}
ol.job-steps{{list-style:none;padding:0;margin:var(--sp-3) 0;display:grid;gap:2px}}ol.job-steps>li{{display:grid;grid-template-columns:28px 1fr;gap:var(--sp-2);align-items:start;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r);background:var(--panel)}}ol.job-steps>li.running{{border-color:color-mix(in srgb,var(--accent) 40%,var(--line));box-shadow:inset 3px 0 0 var(--accent)}}ol.job-steps>li.failed{{border-color:color-mix(in srgb,var(--crit) 45%,var(--line))}}.mark{{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;font-weight:600;background:var(--chip)}}.mark.ok{{background:var(--ok-bg);color:var(--ok)}}.mark.crit{{background:var(--crit-bg);color:var(--crit)}}.mark.work{{background:var(--accent-soft);color:var(--accent)}}.mark.muted{{color:var(--muted)}}p.automation-line{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
@media (hover:none),(pointer:coarse){{.hk-hint,kbd.hk{{display:none!important}}}}
@media(max-width:600px){{.appbar,nav.rail ul.main,.formhead .btns,.ops,.smart{{flex-wrap:nowrap!important;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:thin}}
.appbar>*,nav.rail ul.main>li,.formhead .btns>*,.ops>*,.smart>a{{flex:0 0 auto}}nav.rail ul.main a span{{white-space:nowrap}}
.formhead .btns details[open]{{flex:1 0 100%}}.statusbar{{scroll-snap-type:x proximity}}.statusbar li{{scroll-snap-align:center;flex:0 0 auto}}}}
"""
