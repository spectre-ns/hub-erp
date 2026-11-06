"""The app model: which app each record type and process belongs to (a hand-written grouping over the release's
processes and the kernel's record types), the app launcher, the app bar, and avatars for people and cogs."""

from __future__ import annotations

from .css import AVATAR_COLOURS
from .icons import icon
from .text import esc, type_name, verb_for

# Apps in launcher order: key, name, icon, the record types it lists (first is the app's main type), extra pages.
# A record type may sit in two apps (items are engineering's catalogue and inventory's stock).
APP_TABLE: list[tuple[str, str, str, list[str]]] = [
    ("inbox", "Inbox", "inbox", ["task"]),
    ("documents", "Documents", "folder", ["document"]),
    ("quality", "Quality", "shield", ["ncr", "capa", "audit", "calibration", "complaint", "management-review", "training", "change-request",
                                       "supplier-evaluation", "asset"]),
    ("engineering", "Engineering", "tool", ["design", "requirement", "part", "item"]),
    ("purchasing", "Purchasing", "cart", ["purchase-request", "purchase-order", "receipt"]),
    ("inventory", "Inventory", "box", ["item", "receipt"]),
    ("finance", "Finance", "coins", ["bill", "journal", "journal-reversal"]),
    ("people", "People", "users", []),
    ("policy", "Policy", "book", []),
]
# A release may carry record types the table does not name: their folder's root decides the app.
FOLDER_APPS = {"Quality": "quality", "Engineering": "engineering", "Purchasing": "purchasing", "Inventory": "inventory", "Finance": "finance",
               "Tasks": "inbox"}
# Fixed pages per app (name, path, icon) that are not record-type lists.
APP_PAGES: dict[str, list[tuple[str, str, str]]] = {
    "inbox": [("Waiting for me", "/inbox", "inbox"), ("My work", "/boards", "kanban"), ("Runs", "/runs", "activity")],
    "documents": [("Files by folder", "/files", "folder"), ("Controlled documents", "/items/document?filter=controlled", "file-text")],
    "purchasing": [("Suppliers", "/suppliers", "truck")],
    "people": [("Members and cogs", "/people", "users")],
    "policy": [("Policy versions", "/releases", "layers"), ("Policies", "/frames", "book"), ("Why", "/why", "help"),
               ("Conformance", "/conformance", "check-circle"), ("People and roles", "/admin", "users")],
}
PLURALS = {"ncr": "Nonconformances", "capa": "CAPAs", "audit": "Audits", "calibration": "Calibration", "complaint": "Complaints",
           "management-review": "Management reviews", "training": "Training", "change-request": "Change requests", "supplier-evaluation": "Supplier evaluations",
           "asset": "Assets", "design": "Designs", "requirement": "Requirements", "part": "Parts", "item": "Items", "purchase-request": "Purchase requests",
           "purchase-order": "Purchase orders", "receipt": "Receipts", "bill": "Supplier bills", "journal": "Journals", "journal-reversal": "Reversals",
           "document": "Documents", "task": "Tasks"}


def plural(rt: str) -> str:
    if rt in PLURALS:
        return PLURALS[rt]
    name = type_name(rt)
    return name if name.endswith("s") else name + "s"


def app_map(bundle: dict | None, types: dict, policy_owner: bool = False) -> list[dict]:
    """The apps with their record types and processes, built from the release and the record-type config."""
    bundle = bundle or {}
    known = [t for t in types if t != "$comment"]
    processes: dict[str, str] = {}  # record type -> process that creates it
    for name, op in (bundle.get("ops") or {}).items():
        rt = op.get("record_type") or ""
        if rt and rt not in processes:
            processes[rt] = name
    folder_of: dict[str, str] = {name: op.get("folder", "") for name, op in (bundle.get("ops") or {}).items()}
    placed: set[str] = set()
    apps: list[dict] = []
    for key, name, ic, rts in APP_TABLE:
        if key == "policy" and not policy_owner:
            continue
        entries = [rt for rt in rts if rt in known]
        placed.update(entries)
        apps.append({"key": key, "name": name, "icon": ic, "types": entries, "pages": list(APP_PAGES.get(key, []))})
    by_key = {a["key"]: a for a in apps}
    for rt in known:  # anything the table did not name: by the folder of the process that creates it
        if rt in placed:
            continue
        root = folder_of.get(processes.get(rt, ""), "").split("/")[0]
        app = by_key.get(FOLDER_APPS.get(root, ""))
        if app is None:
            app = by_key.setdefault("other", {"key": "other", "name": "Other", "icon": "grid", "types": [], "pages": []})
            if app not in apps:
                apps.insert(len(apps) - (1 if policy_owner else 0), app)
        app["types"].append(rt)
        placed.add(rt)
    for app in apps:
        app["processes"] = [(processes[rt], rt) for rt in app["types"] if rt in processes]
    return apps


def app_for_type(apps: list[dict], rt: str) -> dict | None:
    for app in apps:
        if rt in app["types"]:
            return app
    return None


def app_for_route(apps: list[dict], route: str, rt: str = "") -> dict | None:
    """Which app the current page belongs to."""
    if rt:
        return app_for_type(apps, rt)
    for app in apps:
        for _, path, _ in app["pages"]:
            if route == path.split("?")[0] or (path != "/" and route.startswith(path.split("?")[0] + "/")):
                return app
        if route == f"/app/{app['key']}":
            return app
    if route.startswith(("/document/", "/doc/", "/edit/", "/files")):
        return next((a for a in apps if a["key"] == "documents"), None)
    if route.startswith(("/user/", "/people")):
        return next((a for a in apps if a["key"] == "people"), None)
    if route.startswith(("/release", "/frame", "/why", "/conformance", "/admin")):
        return next((a for a in apps if a["key"] == "policy"), None)
    if route in ("/inbox", "/boards", "/runs") or route.startswith("/run/"):
        return next((a for a in apps if a["key"] == "inbox"), None)
    return None


def launcher_html(apps: list[dict], user: str, href, current: str | None) -> str:
    """The app grid behind the top-bar grid icon: every app with its record types underneath."""
    out = ""
    for app in apps:
        cur = ' aria-current="true"' if app["key"] == current else ""
        subs = "".join(f'<li><a href="{href("/items/" + rt, user)}">{esc(plural(rt))}</a></li>' for rt in app["types"][:5])
        subs += "".join(f'<li><a href="{href(path.split("?")[0], user, **dict(p.split("=") for p in path.split("?")[1].split("&")) if "?" in path else {})}">{esc(label)}</a></li>'
                        for label, path, _ in app["pages"][:3])
        out += (f'<a class="app"{cur} href="{href("/app/" + app["key"], user)}"><span class="ic">{icon(app["icon"])}</span>{esc(app["name"])}'
                f'{"<ul>" + subs + "</ul>" if subs else ""}</a>')
    return f'<details class="apps"><summary aria-label="Apps" title="Apps">{icon("apps", cls="lg")}</summary><div class="launcher">{out}</div></details>'


def appbar_html(app: dict | None, user: str, href, route: str, rt: str = "", start: str = "") -> str:
    """The second bar: the current app's name and its record types and processes, the current one marked."""
    if app is None:
        return ""
    items = ""
    for t in app["types"]:
        cur = ' aria-current="page"' if (route.startswith("/items/") and rt == t) else ""
        items += f'<a{cur} href="{href("/items/" + t, user)}">{esc(plural(t))}</a>'
    for label, path, ic in app["pages"]:
        base = path.split("?")[0]
        params = dict(p.split("=") for p in path.split("?")[1].split("&")) if "?" in path else {}
        cur = ' aria-current="page"' if route == base and not params else ""
        items += f'<a{cur} href="{href(base, user, **params)}">{icon(ic)}{esc(label)}</a>'
    starts = "".join(f'<a href="{href("/", user, start=p)}#start-{esc(p)}">{icon("plus")}{esc(verb_for(t, p))}</a>' for p, t in app["processes"][:4])
    return (f'<nav class="appbar" aria-label="{esc(app["name"])} app"><a class="appname" href="{href("/app/" + app["key"], user)}">{icon(app["icon"])}{esc(app["name"])}</a>'
            f'{items}{starts}</nav>')


# ---------------------------------------------------------------- avatars
def initials(name: str) -> str:
    parts = [p for p in name.replace("_", "-").replace(".", "-").split("-") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name[:2] or "?").upper()


def avatar_colour(name: str) -> str:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) % 1000003
    return AVATAR_COLOURS[h % len(AVATAR_COLOURS)]


def avatar(name: str, bundle: dict | None = None, size: str = "", href=None, named: bool = False, title: str | None = None) -> str:
    """Initials in a coloured circle for a person; a robot glyph in a rounded square for a cog. The name is in the
    title and, with ``named``, beside it as text (so the avatar never carries information alone)."""
    name = name or "?"
    member = ((bundle or {}).get("members") or {}).get(name) or {}
    is_cog = member.get("type") == "cog"
    cls = "avatar" + (" cog" if is_cog else "") + (f" {size}" if size else "")
    t = title or (f"{name} (cog)" if is_cog else name)
    inner = icon("robot", label=f"{name}, a cog") if is_cog else esc(initials(name))
    a11y = "" if is_cog else f' role="img" aria-label="{esc(name)}"'
    circle = f'<span class="{cls}" style="background:{avatar_colour(name)}" title="{esc(t)}"{a11y}>{inner}</span>'
    if not named:
        return circle
    link = f'<a href="{href("/user/" + name)}">{esc(name)}</a>' if href else f"<span>{esc(name)}</span>"
    return f'<span class="named">{circle}{link}</span>'


def avatars(names: list[str], bundle: dict | None = None, limit: int = 3) -> str:
    """A stack of small avatars with the full list in the title."""
    names = [n for n in names if n]
    if not names:
        return ""
    shown = names[:limit]
    more = len(names) - len(shown)
    out = "".join(avatar(n, bundle, size="sm") for n in shown)
    if more:
        out += f'<span class="avatar sm" style="background:var(--muted)" title="{esc(", ".join(names[limit:]))}">+{more}</span>'
    return f'<span class="avatars" title="{esc(", ".join(names))}">{out}</span>'
