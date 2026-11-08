"""The Odoo-style shell: app launcher and app bar per role, the three views of a record type (list with sort, filter
chips and group-by; kanban by workflow state; form with status bar, smart buttons, tabs and chatter), the dashboard's
server-rendered SVG charts, the document library, people and cog profiles, cogs watched read-only, and the visual
system's contrast and accessibility invariants on the new pages."""

import re
import shutil
from datetime import date
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest

from conftest import BIG_BILL, SMALL_BILL, ROOT, dated, release_all
from hubproto.app import Hub
from hubproto.shell import AVATAR_COLOURS, AVATAR_FG, CONTRAST_PAIRS, DARK, LIGHT, free_port, serve_in_thread
from hubproto.shell.apps import app_map, avatar, initials
from hubproto.shell.charts import day_bars, hbars, sparkline, stacked_bars
from hubproto.shell.icons import ICONS, file_icon, icon


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


def get(shell, path: str, **params):
    url = shell["base"] + path + (("&" if "?" in path else "?") + urlencode(params) if params else "")
    try:
        with urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(shell, path: str, fields: dict):
    req = Request(shell["base"] + path, data=urlencode(fields, doseq=True).encode("utf-8"),
                  headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, r.url, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.url, e.read().decode("utf-8")


def main_of(html: str) -> str:
    return html.split('<main id="main">')[1].split("</main>")[0]


def plain(html: str) -> str:
    html = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def start(shell, process: str, who: str = "alice", **fields) -> dict:
    status, url, text = post(shell, "/start", {"as": who, "process": process, **fields})
    assert status == 200 and "/run/" in url, text[:400]
    return shell["hub"].runner.load(url.split("/run/")[1].split("?")[0])


_po = [2100]


def _bill(hub, base: dict, amount: float) -> dict:
    """A bill against a fresh purchase order (a PO is fully billed after its first bill)."""
    _po[0] += 1
    hub.kernel.append("po.issued", f"po:PO-{_po[0]}", {"supplier": "acme-metals", "amount": amount, "currency": "USD", "description": "[views] fixtures", "received": True}, by="seed")
    hub.kernel._cache = None
    return {**dated(base), "po_ref": f"PO-{_po[0]}"}


def big_bill(hub) -> dict:
    return _bill(hub, BIG_BILL, 12000.0)


def small_bill(hub) -> dict:
    return _bill(hub, SMALL_BILL, 1500.0)


# ------------------------------------------------------------- 1. app launcher and app bar
def test_app_launcher_lists_apps_by_role_and_the_app_bar_follows_the_page(shell):
    status, text = get(shell, "/inbox", **{"as": "alice"})
    launcher = text.split('<details class="apps">')[1].split("</details>")[0]
    for app in ("Inbox", "Documents", "Quality", "Engineering", "Purchasing", "Inventory", "Finance", "People"):
        assert f"</span>{app}" in launcher, app
    assert "Policy" not in launcher, "the Policy app is for policy owners"
    assert "Nonconformances" in launcher and "Supplier bills" in launcher and "Purchase requests" in launcher and 'href="/app/quality?as=alice"' in launcher
    status, text = get(shell, "/items/bill", **{"as": "bob"})
    launcher = text.split('<details class="apps">')[1].split("</details>")[0]
    assert "</span>Policy" in launcher and 'aria-current="true" href="/app/finance?as=bob"' in launcher
    bar = text.split('<nav class="appbar"')[1].split("</nav>")[0]
    assert "Finance</a>" in bar and 'aria-current="page" href="/items/bill?as=bob">Supplier bills</a>' in bar and "Journals" in bar and "File a supplier bill" in bar
    assert '<span class="avatar' in text.split('<div class="top">')[1].split("</form>")[0], "the acting person has an avatar in the top bar"
    # The map is data-driven: every record type of the release lands in exactly one app or more, none is lost.
    hub = shell["hub"]
    apps = app_map(hub.releases.bundle(hub.releases.current()["digest"]), hub.kernel.types, policy_owner=True)
    placed = {t for a in apps for t in a["types"]}
    assert placed == {t for t in hub.kernel.types if t != "$comment"}
    assert [a["key"] for a in apps][-1] == "policy" and ("quality", "Quality") == (apps[2]["key"], apps[2]["name"])
    assert any(p == "nonconformance" for p, _ in next(a for a in apps if a["key"] == "quality")["processes"])


def test_app_page_counts_and_links_each_record_type(shell):
    start(shell, "nonconformance", title="[views] app page NCR", description="scratch", source="inspection", severity="low")
    status, text = get(shell, "/app/quality", **{"as": "carol"})
    assert status == 200 and "Nonconformances</a>" in text and 'href="/items/ncr?as=carol&view=kanban"' in text
    assert "Report a nonconformance" in text and "<svg" in text.split("Items by stage")[1]
    status, text = get(shell, "/app/nope", **{"as": "carol"})
    assert status == 404 and "There is no app" in text


# ------------------------------------------------------------- 2. the three views
def test_list_view_sorts_filters_groups_pages_and_selects(shell):
    hub = shell["hub"]
    big = start(shell, "purchase-to-pay", **{k: str(v) for k, v in big_bill(hub).items()})
    small = start(shell, "purchase-to-pay", **{k: str(v) for k, v in small_bill(hub).items()})
    status, text = get(shell, "/items/bill", **{"as": "bob"})
    assert status == 200 and "Supplier bills" in text and '<th scope="col"><a href="/items/bill?as=bob&sort=number&dir=asc">Number' in text
    assert 'name="sel"' in text and 'action="/bulk-hold"' in text and 'type="search" name="text"' in text
    chips = text.split('<span class="chips">')[1].split("</span></span>")[0]
    for label in ("My items", "Open", "Waiting for me", "Overdue", "This week"):
        assert label in chips, label
    assert '<select name="group"' in text and ">Assignee<" in text and ">Supplier<" in text
    rows_asc = re.findall(r"<tr>.*?</tr>", get(shell, "/items/bill", **{"as": "bob", "sort": "amount", "dir": "asc"})[1], re.S)
    rows_desc = re.findall(r"<tr>.*?</tr>", get(shell, "/items/bill", **{"as": "bob", "sort": "amount", "dir": "desc"})[1], re.S)
    amounts = lambda rows: [re.search(r"([\d,]+\.\d\d) USD", r).group(1) for r in rows if " USD" in r]
    assert amounts(rows_asc) == sorted(amounts(rows_asc), key=lambda a: float(a.replace(",", ""))) and amounts(rows_desc) == list(reversed(amounts(rows_asc)))
    assert 'aria-sort="descending"' in get(shell, "/items/bill", **{"as": "bob", "sort": "amount", "dir": "desc"})[1]
    status, text = get(shell, "/items/bill", **{"as": "bob", "filter": "waiting"})
    numbers = {hub.kernel.get(big["record"])["number"], hub.kernel.get(small["record"])["number"]}
    chips = text.split('<span class="chips">')[1].split("</span></span>")[0]
    assert hub.kernel.get(big["record"])["number"] in text and 'aria-current="true"' in chips.split("Waiting for me")[0].rsplit("<a", 1)[1]
    status, text = get(shell, "/items/bill", **{"as": "bob", "group": "state"})
    assert '<tr class="groupname"><th scope="row"' in text and "Awaiting: Approve the bill" in text.split('<tr class="groupname">')[1]
    status, text = get(shell, "/items/bill", **{"as": "bob", "group": "supplier"})
    assert "Acme Metals" in text.split('<tr class="groupname">')[1]
    status, text = get(shell, "/items/bill", **{"as": "bob", "text": "nothing-matches-this"})
    assert "No supplier bills match" in text
    status, text = get(shell, "/items/nonsense", **{"as": "bob"})
    assert status == 404 and "no item type" in text
    # bulk hold: only what already exists (hold with a reason), refused without a reason, applied to the selection
    status, url, text = post(shell, "/bulk-hold", {"as": "bob", "type": "bill", "sel": [big["record"]], "reason": ""})
    assert status == 400 and "Say why" in text and not hub.kernel.get(big["record"])["hold"]
    status, url, text = post(shell, "/bulk-hold", {"as": "bob", "type": "bill", "sel": [big["record"], small["record"]], "reason": "[views] audit"})
    assert status == 200 and "Put on hold:" in text and hub.kernel.get(big["record"])["hold"] and hub.kernel.get(small["record"])["hold"]
    status, url, text = post(shell, "/bulk-hold", {"as": "alice", "type": "bill", "sel": [big["record"]], "reason": "mine"})
    assert status == 200 and "not its approver" in text
    for loc in (big["record"], small["record"]):
        hub.kernel.hold(loc, False, "done", by="bob")


def test_list_pages_at_25(shell):
    for i in range(27):
        start(shell, "task", title=f"[views] page task {i}", assignee="carol")
    status, text = get(shell, "/items/task", **{"as": "carol"})
    assert text.count('name="sel"') == 25 and "Page 1 of" in text
    status, text = get(shell, "/items/task", **{"as": "carol", "page": 2})
    assert 0 < text.count('name="sel"') <= 25


def test_kanban_groups_by_workflow_state_in_order_with_counts_and_card_facts(shell):
    hub = shell["hub"]
    state = start(shell, "purchase-to-pay", **{k: str(v) for k, v in big_bill(hub).items()})
    number = hub.kernel.get(state["record"])["number"]
    status, text = get(shell, "/items/bill", **{"as": "bob", "view": "kanban"})
    assert status == 200 and '<span aria-current="page">' in text.split('class="views"')[1].split("</span>")[0] + "</span>" or "Kanban" in text
    cols = re.findall(r"<div class='col[^']*'><h3>(.*?)<span class='n'>(\d+)</span>", text)
    assert [c[0] for c in cols][:3] == ["Open", "Awaiting: Approve the bill", "Posted"], cols
    assert int(cols[1][1]) >= 1
    card = text.split(f">{number}</a>")[1].split("</div></div>")[0]
    assert "12,000.00 USD" in card and "Acme Metals" in card and "chip crit" in card and "alice" in card
    full = text.split(f">{number}</a>")[0].rsplit('<div class="kcard', 1)[1] + card
    assert "risk-high" in full, "the left edge is coloured by risk"
    assert 'title="documents"' in text and 'title="links"' in text and '<span class="avatars"' in text
    # the Boards page uses the same cards and offers the kanban
    status, text = get(shell, "/boards", **{"as": "bob", "type": "bill"})
    assert "kcard" in text and "Open as the supplier bill kanban" in text


def test_form_view_has_a_status_bar_action_buttons_smart_buttons_tabs_and_a_chatter(shell):
    hub = shell["hub"]
    state = start(shell, "purchase-to-pay", **{k: str(v) for k, v in big_bill(hub).items()})
    loc = state["record"]
    status, text = get(shell, "/record/" + quote(loc, safe=":"), **{"as": "bob"})
    assert status == 200
    bar = text.split('<ol class="statusbar"')[1].split("</ol>")[0]
    states = re.findall(r'<li class="([^"]+)" data-state="([^"]+)"', bar)
    assert [s for _, s in states] == ["open", "awaiting-approve-the-bill", "posted", "rejected"]
    assert dict((s, c) for c, s in states)["awaiting-approve-the-bill"] == "current" and 'aria-current="step"' in bar
    btns = text.split('<div class="btns">')[1].split('<ol class="statusbar"')[0].split('<div class="statusbar"')[0]
    assert "Approve<kbd" in btns and "Reject<kbd" in btns and "Put on hold" in btns and "Render snapshot" in btns and "Start a process" in btns and "Print" in btns
    assert "Attach" not in btns, "bob may not file documents in Finance/Payables"
    smart = text.split('<div class="smart"')[1].split("</div>")[0]
    assert re.search(r'href="#documents">.*?<b>\d+</b>Documents', smart) and "Links" in smart and "Runs" in smart and "Decisions" in smart
    tabs = text.split('<nav class="tabs"')[1].split("</nav>")[0]
    assert [t for t in re.findall(r'data-tab="tab-([a-z]+)"', tabs)] == ["details", "documents", "links", "history", "workflow"]
    assert 'class="sheetfields"' in text and "<dt>Amount</dt><dd>12,000.00 USD</dd>" in text
    chatter = text.split('<section class="chatter"')[1]
    assert "created the supplier bill" in chatter and 'class="timeline"' in chatter and 'name="record" value="' + loc + '"' in chatter
    assert 'list="mentions"' in chatter and '<datalist id="mentions">' in chatter and 'value="@quality-assistant"' in chatter and 'value="@design-review-committee"' in chatter
    assert "Log note" in chatter and "Send message" in chatter
    assert 'action="/decide"' in text and "Approve BILL-" in text, "the decision card is on the form for the assignee"
    status, text = get(shell, "/record/" + quote(loc, safe=":"), **{"as": "alice"})
    btns = text.split('<div class="btns">')[1].split('<ol class="statusbar"')[0].split('<div class="statusbar"')[0]
    assert "Approve<kbd" not in btns and "Attach" in btns and "New document" in btns


def test_chatter_posts_on_the_item_and_a_mentioned_cog_answers_there(shell):
    hub = shell["hub"]
    state = start(shell, "nonconformance", title="[views] chatter NCR", description="deep scratch on the housing", source="inspection", severity="high")
    loc = state["record"]
    number = hub.kernel.get(loc)["number"]
    back = f"/record/{loc}?as=alice"
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "quality-assistant", "subject": "Review", "record": loc, "return": back,
                                             "body": "@quality-assistant please provide a quality review on this nonconformance"})
    assert status == 200 and "/record/" in url and "quality-assistant answered" in text, text[:300]
    chatter = text.split('<section class="chatter"')[1]
    assert "please provide a quality review" in chatter and "Review of" in chatter and 'href="/user/quality-assistant?as=alice">@quality-assistant</a>' in chatter
    assert [m["kind"] for m in hub.inbox.on_record(loc) if m["kind"] in ("message", "reply")] == ["message", "reply"]
    assert "evidence" in chatter.lower() or any(d["fields"].get("doc_type") == "review" for d in hub.dms.documents_of(loc))
    # the approver's inbox links the review notification to the item
    status, text = get(shell, "/inbox", **{"as": "carol"})
    notes = text.split("id='notifications'")[1].split("<h2")[0]
    assert "Review posted" in notes and f"Open {number}</a>" in notes
    # a note is kept with the item and sent to nobody else
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "alice", "subject": f"Note on {number}", "record": loc, "return": back, "body": "checked the lot on the floor"})
    assert status == 200 and "Note logged" in text and "checked the lot on the floor" in text.split('<section class="chatter"')[1]
    assert not [m for m in hub.inbox.for_principal("carol") if "checked the lot" in m["body"]]
    # a refused message re-renders the composer on the item
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "carol", "subject": "x", "record": loc, "return": back, "body": ""})
    assert status == 400 and "Write the message" in text and 'class="statusbar"' in text


def test_reviews_by_cogs_show_on_the_form_and_the_kanban_card(shell):
    hub = shell["hub"]
    state = start(shell, "nonconformance", title="[views] reviewed NCR", description="bent pins", source="inspection", severity="high")
    loc = state["record"]
    doc = hub.dms.create(title="Review of NCR by quality-assistant", doc_class="evidence", folder="Quality/Nonconformances", by="quality-assistant",
                         content=b"# Review\nVerdict: pass", filename="review.md", mime="text/markdown", doc_type="review", attach_to=loc)
    hub.kernel.set_fields(loc, {"reviews": [{"cog": "quality-assistant", "verdict": "pass", "summary": "containment is adequate", "model": "claude-sonnet-5-5",
                                             "sops": ["ncr-sop"], "stand_in": True, "findings": 2}], "review_verdict": "pass"}, by="quality-assistant")
    status, text = get(shell, "/record/" + quote(loc, safe=":"), **{"as": "carol"})
    assert 'data-tab="tab-reviews"' in text and "Reviews" in text.split('<div class="smart"')[1].split("</div>")[0]
    panel = text.split('id="tab-reviews"')[1].split("</section>")[0]
    assert "containment is adequate" in panel and '<span class="chip ok">pass</span>' in panel and "claude-sonnet-5-5 (stand-in)" in panel and doc["number"] in panel and "ncr-sop" in panel
    status, text = get(shell, "/items/ncr", **{"as": "carol", "view": "kanban"})
    assert "review pass" in text.split(">" + hub.kernel.get(loc)["number"] + "</a>")[1].split("kc-foot")[0]


# ------------------------------------------------------------- 3. dashboard
def test_dashboard_has_kpi_tiles_with_sparklines_and_linked_svg_charts(shell):
    hub = shell["hub"]
    state = start(shell, "purchase-to-pay", **{k: str(v) for k, v in big_bill(hub).items()})
    status, text = get(shell, "/", **{"as": "bob"})
    assert status == 200 and "<h1>Welcome, bob</h1>" in text
    kpis = text.split('<div class="kpis">')[1].split('<div class="charts">')[0]
    for label in ("Waiting for me", "Overdue", "Returned or stopped", "My runs", "Open NCRs", "POs awaiting receipt", "Bills to approve", "Documents checked out"):
        assert label in kpis, label
    assert kpis.count('<svg class="spark"') == 8 and 'href="/inbox?as=bob"' in kpis and 'href="/items/bill?as=bob&bucket=waiting"' in kpis
    waiting = int(re.search(r"Waiting for me</span><b>(\d+)</b>", kpis).group(1))
    assert waiting == len(hub.runner.pending_for("bob"))
    charts = text.split('<div class="charts">')[1].split("Start something")[0]
    assert charts.count("<svg") >= 3 and "cdn" not in charts.lower() and "Items by stage, per app" in charts and "Decisions per day" in charts
    assert "Cycle time by process" in charts and "Overdue by assignee" in charts
    assert re.search(r'<a href="/items/all\?as=bob&amp;bucket=waiting&amp;folder=Finance"><title>Finance: \d+ Waiting</title><rect', charts), "a bar segment links to the filtered list"
    assert 'href="/items/all?as=bob&amp;decided=' in charts or "Decisions per day" in charts
    assert 'size="' not in text and 'cols="' not in text, "no font-size attributes (the accessibility suite forbids size=)"
    quick = text.split('<div class="quick"')[1].split("</div>")[0]
    assert "Create a task" in quick and "<svg" in quick and 'href="/?as=bob&start=task#start-task"' in quick
    assert '<details class="card start" id="start-task"' in text, "the Start entries stay"
    status, text = get(shell, "/items/all", **{"as": "bob", "bucket": "waiting", "folder": "Finance"})
    assert status == 200 and hub.kernel.get(state["record"])["number"] in text


def test_charts_are_plain_svg_with_titles_and_links():
    svg = stacked_bars([("Quality", [("Open", 3, "/items/ncr?bucket=open"), ("Done", 1, None)])], legend=[("Open", "open"), ("Done", "done")], label="x")
    assert svg.startswith("<svg") and 'role="img"' in svg and '<a href="/items/ncr?bucket=open"><title>Quality: 3 Open</title><rect class="sw"' in svg and "font-size=" not in svg
    assert '<text class="val"' in svg and 'class="legend"' in svg
    bars = day_bars([("2026-11-01", 2), ("2026-11-02", 0)], href_for=lambda d: f"/items/all?decided={d}")
    assert '<a href="/items/all?decided=2026-11-01">' in bars and bars.count("<a ") == 1, "empty days are not links"
    assert "peak 2" in bars
    h = hbars([("alice", 2.0, "/x", "2")], swatch="sw-crit")
    assert 'class="sw-crit"' in h and "<title>alice: 2</title>" in h
    assert sparkline([0, 1, 3]).count("<polyline") == 1 and sparkline([]).startswith("<svg")


# ------------------------------------------------------------- 4. inbox layout
def test_inbox_rows_carry_type_icons_avatars_and_chips_and_the_open_card_sits_in_the_queue(shell):
    hub = shell["hub"]
    state = start(shell, "purchase-to-pay", **{k: str(v) for k, v in big_bill(hub).items()})
    status, text = get(shell, "/inbox", **{"as": "bob", "open": state["id"]})
    queue = text.split("<ol class='queue'>")[1].split("</ol>")[0]
    assert '<span class="ty"><svg' in queue and '<span class="avatars"' in queue and '<li class="open"' in queue
    row = re.search(r'<li id="r-[0-9a-f]+"><span class="ty">.*?</li>', queue, re.S)
    assert row and "<h3><a href=" in row.group(0) and "chip" in row.group(0)
    css = text.split("<style>")[1].split("</style>")[0]
    assert ".queue{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(18em,2fr) 3fr" in css and ".queue>li.open{grid-column:2" in css
    assert "@media(max-width:1100px){.queue{grid-template-columns:1fr}" in css, "stacked on narrow screens"


# ------------------------------------------------------------- 5. documents app
def test_document_library_has_a_folder_tree_tiles_type_icons_previews_sorting_and_toolbar(shell):
    hub = shell["hub"]
    md = hub.dms.create(title="[views] Cleaning WI", doc_class="controlled", folder="Quality/Controlled Documents", by="carol", content=b"# Cleaning\nWipe the jig.", filename="cleaning.md", mime="text/markdown")
    xlsx = hub.dms.create(title="[views] Inspection plan", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=b"PK", filename="plan.xlsx",
                          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    status, text = get(shell, "/files", **{"as": "carol", "folder": "Quality/Controlled Documents"})
    assert status == 200
    tree = text.split('<nav class="ftree"')[1].split("</nav>")[0]
    assert 'aria-current="page"' in tree and "Controlled Documents" in tree and "<svg" in tree and 'class="denied"' in tree, "folders carol may not read are greyed"
    toolbar = text.split('<div class="toolbar">')[1].split("</div>")[0]
    assert 'href="#new-document">' in toolbar and "New document" in toolbar and 'href="#upload">' in toolbar and "Upload" in toolbar and '<select name="sort"' in toolbar
    tiles = text.split('<div class="tiles">')[1].split("</div></div><h2>")[0]
    assert "ft-md" in tiles and "ft-xlsx" in tiles and ">MD</text>" in tiles and ">X</text>" in tiles
    assert "<pre aria-hidden='true'>Cleaning" in tiles, "a text preview for markdown, as words not source (review 2026-11-07, ux-20)"
    assert "Edit online" in tiles and "Download" in tiles and md["number"] in tiles and xlsx["number"] in tiles and "chip" in tiles
    status, text = get(shell, "/files", **{"as": "carol", "folder": "Quality/Controlled Documents", "sort": "name"})
    assert text.index("[views] Cleaning WI") < text.index("[views] Inspection plan")
    status, text = get(shell, "/files", **{"as": "carol", "folder": "Quality/Controlled Documents", "sort": "date"})
    assert text.index("[views] Inspection plan") < text.index("[views] Cleaning WI")
    status, text = get(shell, "/documents", **{"as": "carol"})
    assert status == 200 and "Recent documents" in text and 'class="tiles"' in text
    assert "<h1" in text and "Documents" in text.split("<h1")[1].split("</h1>")[0]


# ------------------------------------------------------------- 6. cogs as users
def test_people_page_and_cog_profile(shell):
    hub = shell["hub"]
    small = start(shell, "purchase-to-pay", **{k: str(v) for k, v in small_bill(hub).items()})
    status, text = get(shell, "/people", **{"as": "alice"})
    assert status == 200 and "<h2>Cogs" in text and 'href="/user/ap-reviewer?as=alice"' in text and 'href="/user/bob?as=alice"' in text
    cog_card = text.split('href="/user/ap-reviewer?as=alice"')[0].rsplit('<div class="card">', 1)[1] + text.split('href="/user/ap-reviewer?as=alice"')[1].split("</div></div>")[0]
    assert "ollama/qwen3:14b" in cog_card and 'href="/frame/finance-policy?as=alice">finance-policy</a>' in cog_card and "avatar cog" in cog_card
    status, text = get(shell, "/user/ap-reviewer", **{"as": "alice"})
    assert status == 200 and "<h1>ap-reviewer" in text and "payables reviewer" in text and "finance clerk" in text
    about = text.split("About this cog")[1].split("May approve")[0]
    assert "ollama/qwen3:14b" in about and "You are the payables reviewer" in about and 'href="/frame/finance-policy?as=alice">finance-policy</a>' in about
    assert re.search(r"\d+ model calls? since the hub started", about) and "Version" in about and "Reaches" in about
    assert "up to 2,000.00 USD" in text.split("May approve")[1].split("</ul>")[0]
    decs = text.split("Recent decisions")[1].split("</h2>")[1].split("<h2")[0]
    assert hub.kernel.get(small["record"])["number"] in decs and "within fixed criteria" in decs, "the cog's own decision with its reasons"
    status, text = get(shell, "/user/bob", **{"as": "alice"})
    may = text.split("May approve")[1].split("</ul>")[0]
    assert status == 200 and "Approve the bill (Supplier bill) for amounts over 10,000.00 USD" in may and "Review the design (Design)" in may and 'action="/msg"' in text
    status, text = get(shell, "/user/nobody", **{"as": "alice"})
    assert status == 404
    assert initials("ap-reviewer") == "AR" and initials("bob") == "BO"
    assert 'class="avatar cog' in avatar("ap-reviewer", hub.releases.bundle(hub.releases.current()["digest"])) and "robot" not in avatar("bob")


def test_a_cog_can_be_watched_but_never_acted_as(shell):
    hub = shell["hub"]
    hub.runner.cog_users_inline = False  # the item waits for the cog instead of being taken at once
    try:
        small = start(shell, "purchase-to-pay", **{k: str(v) for k, v in small_bill(hub).items()})
        assert small["pending"]["assignees"] == ["ap-reviewer"]
        status, text = get(shell, "/inbox")
        assert "See what ap-reviewer is doing" in text.split("<h2>Cogs</h2>")[1]
        status, text = get(shell, "/inbox", **{"as": "ap-reviewer", "open": small["id"]})
        assert status == 200 and "You are watching <b>ap-reviewer</b>" in text and 'action="/decide"' not in text
        card = text.split("<article class='card decide'")[1].split("</article>")[0]
        assert "Read-only" in card and "decides this on its own" in card and "finance-policy line" in card and "Supplier bill" in card
        assert '<optgroup label="See what a cog is doing (read-only)">' in text and 'value="ap-reviewer" selected' in text
        status, url, text = post(shell, "/decide", {"as": "ap-reviewer", "run": small["id"], "verdict": "approve", "meaning": "x", "confirm": "1", "digest": small["seen_digest"]})
        assert status == 403 and "watching ap-reviewer" in text and hub.runner.load(small["id"])["status"] == "waiting"
        status, url, text = post(shell, "/msg", {"as": "ap-reviewer", "to": "alice", "subject": "x", "body": "y"})
        assert status == 403
        status, text = get(shell, "/user/ap-reviewer", **{"as": "ap-reviewer"})
        assert status == 200 and hub.kernel.get(small["record"])["number"] in text.split("Waiting for ap-reviewer")[1]
        # the cog takes its turn through the runner; the chatter and the kanban show it as the decider with its avatar
        hub.runner.cog_decide(small["id"], "ap-reviewer")
        status, text = get(shell, "/record/" + quote(small["record"], safe=":"), **{"as": "alice"})
        chatter = text.split('<section class="chatter"')[1]
        assert 'class="avatar cog' in chatter and "ap-reviewer" in chatter and ("approved it" in chatter or "approved" in chatter)
    finally:
        hub.runner.cog_users_inline = True


# ------------------------------------------------------------- 7. visual system
def test_palette_and_avatars_meet_contrast_in_both_themes():
    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    assert LIGHT["top"] == LIGHT["panel"] and LIGHT["accent"] != LIGHT["top"], "a light top bar, one accent"
    assert {k for k, _, _ in CONTRAST_PAIRS} >= {"accent2", "info", "c1", "c6"}, "the teal, the info chip and the chart colours are checked"
    for theme in (LIGHT, DARK):
        for fg, bg, need in CONTRAST_PAIRS:
            assert ratio(theme[fg], theme[bg]) >= need, (fg, bg)
    for c in AVATAR_COLOURS:
        assert ratio(c, AVATAR_FG) >= 4.5, c
    assert len(set(AVATAR_COLOURS)) == len(AVATAR_COLOURS) >= 6


def test_icons_are_inline_currentcolor_svgs():
    for name in ("apps", "inbox", "robot", "kanban", "list", "form", "paperclip", "printer"):
        svg = icon(name)
        assert svg.startswith('<svg class="i" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"') and 'aria-hidden="true"' in svg
    assert 'role="img" aria-label="Apps"' in icon("apps", label="Apps")
    assert len(ICONS) >= 50 and all("<" in v for v in ICONS.values())
    assert ">PDF</text>" in file_icon("drawing.pdf") and "ft-pdf" in file_icon("drawing.pdf") and ">SCH</text>" in file_icon("x.kicad_sch")
    assert "font-size=" not in file_icon("a.docx") and 'aria-label="docx"' in file_icon("a.docx")


@pytest.mark.parametrize("path,who", [("/items/bill", "bob"), ("/items/bill?view=kanban", "bob"), ("/items/all", "bob"), ("/app/quality", "carol"),
                                      ("/people", "alice"), ("/user/ap-reviewer", "alice"), ("/user/bob", "alice"), ("/files?folder=Quality/Controlled%20Documents", "carol"),
                                      ("/suppliers", "dave"), ("/", "alice"), ("/inbox", "ap-reviewer")])
def test_new_pages_keep_the_accessibility_invariants(shell, path, who):
    status, text = get(shell, path, **{"as": who})
    assert status == 200, text[:300]
    assert text.index('<a class="skip" href="#main">Skip to content</a>') < text.index('<div class="top">')
    assert '<main id="main">' in text and '<nav class="rail" aria-label="Workspace"><ul' in text and ">Go</button>" in text and "onchange=" not in text
    assert 'size="' not in text and 'cols="' not in text
    for m in re.finditer(r'<(input|select|textarea)[^>]*name="([^"]+)"[^>]*>', text):
        if 'type="hidden"' in m.group(0):
            continue
        before = text[: m.start()]
        assert before.rfind("<label") > before.rfind("</label>"), (path, m.group(0)[:80])
    for t in re.finditer(r"<table>", text):
        assert text[t.start(): t.start() + 40].startswith('<table><thead><tr><th scope="col">'), path
    for m in re.finditer(r'<span class="chip [a-z]*">(.*?)</span>', text):
        assert plain(m.group(1)).strip(), "a chip always carries words, never colour alone"
    assert "@media print" in text and "@media(max-width:600px)" in text
    main = main_of(text)
    assert main.count("<svg") == main.count("</svg>") and main.count("<table>") == main.count("</table>")


def test_record_form_prints_and_links_back_to_its_list(shell):
    hub = shell["hub"]
    rec = hub.kernel.query(type_="bill")[0]
    status, text = get(shell, "/record/" + quote(rec["locator"], safe=":"), **{"as": "bob"})
    assert 'onclick="print()"' in text and ".formhead .btns,.composer" in text.split("@media print")[1]
    assert re.search(r'<ol class="crumb"[^>]*>.*Supplier bill</a>', text, re.S) and 'href="/items/bill?as=bob">Supplier bill</a>' in text


def test_a_document_waiting_for_your_approval_shows_the_decision_on_its_own_page(shell):
    """A document in document control: whoever it waits for sees Approve and Reject on the document page too, not only
    on the record page; anyone else sees who it waits for."""
    hub = shell["hub"]
    status, url, text = post(shell, "/start", {"as": "alice", "process": "document-control", "title": "[views] Decide on the document page",
                                               "change_type": "major", "doc_type": "sop", "body": "steps"})
    run_id = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(run_id)
    who = state["pending"]["assignees"][0]
    loc = state["record"]
    status, text = get(shell, "/document/" + loc, **{"as": who})
    body = main_of(text)
    assert status == 200 and "id='decide'" in body and 'value="approve"' in body.replace("'", '"'), "the decision card is on the document page"
    other = next(p for p in ("dave", "bob", "carol") if p not in state["pending"]["assignees"] and p != "alice")
    status, text = get(shell, "/document/" + loc, **{"as": other})
    assert "Waiting for" in main_of(text)


def test_people_who_file_in_a_folder_may_add_subfolders_with_the_same_rights(shell):
    hub = shell["hub"]
    status, url, text = post(shell, "/folder-new", {"as": "carol", "parent": "Quality/Controlled Documents", "name": "Work instructions"})
    assert status == 200 and "Quality/Controlled Documents/Work instructions" in hub.releases.bundle(hub.releases.current()["digest"])["folders"]
    from hubproto import authz

    b = hub.releases.bundle(hub.releases.current()["digest"])
    path = "Quality/Controlled Documents/Work instructions"
    assert authz.check(b, "carol", "create", "document", path)["allowed"] == authz.check(b, "carol", "create", "document", "Quality/Controlled Documents")["allowed"]
    status, text = get(shell, "/files", **{"as": "carol", "folder": "Quality/Controlled Documents"})
    assert "Work instructions" in main_of(text) and "added by carol" in main_of(text)
    assert 'id="new-folder"' in main_of(text) and "Add to Quality/Controlled Documents" in main_of(text), "the New folder button is in the toolbar"
    status, text = get(shell, "/files", **{"as": "carol"})
    assert 'id="new-folder"' in main_of(text) and 'name="parent"' in main_of(text), "at the top of the library: choose where"
    status, text = get(shell, "/files", **{"as": "dave", "folder": "Quality/Controlled Documents"})
    assert 'id="new-folder"' in main_of(text) and "you cannot add folders to it" in main_of(text), "where you may not, it says why"
    status, url, text = post(shell, "/folder-new", {"as": "dave", "parent": "Quality/Controlled Documents", "name": "Mine"})
    assert status == 403, "only people who may file there"
    status, url, text = post(shell, "/folder-new", {"as": "carol", "parent": "Quality/Controlled Documents", "name": "../escape"})
    assert status == 400
    status, url, text = post(shell, "/folder-remove", {"as": "carol", "path": path})
    assert status == 200 and path not in hub.releases.bundle(hub.releases.current()["digest"])["folders"]


def test_a_policy_owner_adds_top_level_folders_and_subfolders_anywhere(shell):
    from hubproto import authz

    hub = shell["hub"]
    status, url, text = post(shell, "/folder-new", {"as": "bob", "parent": "", "name": "Projects", "rights_from": "Policy"})
    assert status == 200, text[:300]
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert "Projects" in b["folders"] and b["library_rights"]["Projects"] == "Policy"
    for who in ("alice", "dave"):
        assert authz.check(b, who, "create", "document", "Projects")["allowed"] == authz.check(b, who, "create", "document", "Policy")["allowed"]
    assert any(m["to"] == "carol" and "New top-level folder Projects" in m["subject"] for m in hub.inbox.all()), "the other policy owner is told"
    status, url, text = post(shell, "/folder-new", {"as": "alice", "parent": "", "name": "Mine", "rights_from": "Policy"})
    assert status in (400, 403), "only a policy owner adds at the top"
    status, url, text = post(shell, "/folder-new", {"as": "bob", "parent": "Quality/Controlled Documents", "name": "Bob notes"})
    assert status == 200, "a policy owner adds subfolders where he does not file"
    status, text = get(shell, "/files", **{"as": "bob"})
    assert "the top of the library" in main_of(text) and 'name="rights_from"' in main_of(text)
