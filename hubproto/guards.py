"""Guards for the compile pipeline.

A Guard reports findings; it never decides. Each finding names the Guard, a
severity (``error`` blocks a release, ``warning`` is shown to the reviewer)
and the rule it concerns.

Guards here, by whitepaper category:
* Algorithmic: ``schema_guard``
* Source-Grounding: ``grounding_guard``
* Policy & Safety: ``consistency_guard`` (unknown roles, segregation of
  duties, cogs routed to without criteria, unevaluable conditions)
* Regression & Drift: ``drift_guard`` (what changed, and whether it widens
  access or automates an approval)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .frames import Frame
from .rules import rule_key, sha256_of, validate_rule


@dataclass
class Finding:
    guard: str
    severity: str
    detail: str
    rule_key: str = ""
    frame: str = ""
    line: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _where(rule: dict) -> tuple[str, int]:
    src = rule.get("source") or {}
    return src.get("frame", ""), int(src.get("line", 0) or 0)


def schema_guard(rules: list[dict]) -> list[Finding]:
    out = []
    for rule in rules:
        for err in validate_rule(rule):
            frame, line = _where(rule)
            out.append(Finding("schema", "error", err, rule_key(rule), frame, line))
    return out


def grounding_guard(rules: list[dict], frames: dict[str, Frame]) -> list[Finding]:
    out = []
    for rule in rules:
        src = rule.get("source") or {}
        frame = frames.get(src.get("frame", ""))
        key = rule_key(rule)
        if frame is None:
            out.append(Finding("source-grounding", "error", "source names an unknown Frame", key))
            continue
        if rule.get("kind") == "process" and rule.get("statuses"):
            # A process written as a graph: its Statuses line and every transition sentence cite their own line.
            subs = [rule.get("statuses_source")] + [t.get("source") for t in rule.get("transitions") or [] if t.get("source")]
            for sub in subs:
                if not sub:
                    continue
                ln = int(sub.get("line", 0) or 0)
                if not (1 <= ln <= len(frame.file_lines)) or (frame.line(ln) or "").strip() != str(sub.get("text", "")).strip():
                    out.append(Finding("source-grounding", "error", f"cited text does not match the Frame at line {ln}", key, frame.name, ln))
        line = int(src.get("line", 0) or 0)
        if not (1 <= line <= len(frame.file_lines)):
            out.append(Finding("source-grounding", "error", f"line {line} is outside the Frame", key, frame.name, line))
            continue
        if (frame.line(line) or "").strip() != str(src.get("text", "")).strip():
            out.append(Finding("source-grounding", "error",
                               "cited text does not match the Frame at that line", key, frame.name, line))
        if str(frame.version) != str(src.get("version")):
            out.append(Finding("source-grounding", "error", "cited Frame version differs", key, frame.name, line))
    return out


def consistency_guard(rules: list[dict], cog_ids: set[str], record_types: dict | None = None) -> list[Finding]:
    out: list[Finding] = []
    # A cog with a profile and no code of its own is a profile cog: the hub installs it from the release.
    cog_ids = set(cog_ids) | {r["name"] for r in rules if r.get("kind") == "cog"}
    roles = {r["name"] for r in rules if r["kind"] == "role"}
    members: dict[str, dict] = {}
    for r in rules:
        if r["kind"] == "member":
            if r["principal"] in members:
                if members[r["principal"]]["principal_type"] != r["principal_type"]:
                    out.append(Finding("consistency", "error", f"member {r['principal']} is declared as both a person and a cog", rule_key(r), *_where(r)))
                members[r["principal"]] = {**members[r["principal"]], "roles": sorted(set(members[r["principal"]]["roles"]) | set(r["roles"]))}
            else:
                members[r["principal"]] = dict(r)
    folders = {r["path"] for r in rules if r["kind"] == "folder"}
    criteria_by_cog = {}
    for r in rules:
        if r["kind"] == "approval_criteria":
            criteria_by_cog.setdefault(r["approver"], []).append(r)
    processes = {r["name"]: r for r in rules if r["kind"] == "process"}
    from .release import checkpoint_for

    def step_ok(process: str, step: str) -> bool:
        if step == "*":
            return True
        proc = processes.get(process)
        return bool(proc and checkpoint_for(proc, step))

    roles_with_members = {role for m in members.values() for role in m["roles"]}
    personas = {r["name"]: r for r in rules if r["kind"] == "persona"}
    listed = {r["who"] for r in rules if r["kind"] == "responsibility"}  # who some Roles and responsibilities table lists
    groups = {r["name"]: r for r in rules if r["kind"] == "group"}
    committees = {r["name"]: r for r in rules if r["kind"] == "committee"}
    light = _light(rules)

    def ref_ok(ref: str) -> bool:
        kind, _, name = ref.partition(":")
        if kind == "field":
            return True
        if kind == "group":
            return name in groups
        if kind == "role":
            return name in roles
        if kind == "person":
            return name in members and members[name]["principal_type"] == "person"
        if kind == "cog":
            return name in members and members[name]["principal_type"] == "cog"
        return False

    for rule in rules:
        key = rule_key(rule)
        frame, line = _where(rule)
        kind = rule["kind"]
        if kind == "group":
            out += _group_consistency(rule, key, frame, line, roles, members, light)
        elif kind == "member":
            for role in rule["roles"]:
                if role not in roles:
                    out.append(Finding("consistency", "error", f"member {rule['principal']} holds unknown role {role!r}", key, frame, line))
            if rule["principal_type"] == "cog" and rule["principal"] not in cog_ids:
                out.append(Finding("consistency", "error", f"cog {rule['principal']!r} is not in the Cog catalog", key, frame, line))
        elif kind == "cog":
            if rule["name"] not in cog_ids:
                out.append(Finding("consistency", "error", f"cog profile {rule['name']!r} names a cog that is not in the Cog catalog", key, frame, line))
            elif rule["name"] not in members or members[rule["name"]]["principal_type"] != "cog":
                out.append(Finding("consistency", "error", f"cog profile {rule['name']!r} has no matching cog member in the org model", key, frame, line))
            if not rule.get("persona"):
                out.append(Finding("consistency", "warning", f"cog {rule['name']} has no persona: say how it behaves (Persona: reviewer, "
                                   "executor, author or advisor)", key, frame, line))
            elif rule["persona"] not in personas:
                out.append(Finding("consistency", "error", f"cog {rule['name']}: there is no persona {rule['persona']!r} "
                                   f"(the personas are: {', '.join(sorted(personas)) or 'none'})", key, frame, line))
            m = members.get(rule["name"]) or {}
            from .groups import groups_of as _gof

            if m and not ({"cog:" + rule["name"]} | {"role:" + x for x in m.get("roles") or []}
                          | {"group:" + g for g in _gof(light, rule["name"])}) & listed:
                out.append(Finding("consistency", "warning", f"cog {rule['name']} is listed in no Roles and responsibilities table (nor are its "
                                   "roles): it follows only the company scope and will be routed nothing", key, frame, line))
        elif kind == "persona":
            if int(rule.get("passes") or 0) > len(rule.get("lenses") or []):
                out.append(Finding("consistency", "warning", f"persona {rule['name']} makes {rule.get('passes')} adversarial passes but names "
                                   f"{len(rule.get('lenses') or [])} lenses; the hub's default lenses fill the rest", key, frame, line))
        elif kind == "duty":
            if not ref_ok("cog:" + rule["cog"]):
                out.append(Finding("consistency", "error", f"duty for {rule['cog']!r}: there is no such cog member", key, frame, line))
        elif kind == "permission":
            if not ref_ok(rule["subject"]):
                out.append(Finding("consistency", "error", f"permission subject {rule['subject']!r} is unknown", key, frame, line))
            if rule.get("effect") == "propose":
                who = rule.get("confirmer") or ""
                if who != "person" and who not in roles:
                    out.append(Finding("consistency", "error", f"{who!r} confirms a cog's action but is not a declared role", key, frame, line))
                elif who != "person" and who not in roles_with_members:
                    out.append(Finding("consistency", "error", f"a {who.replace('-', ' ')} confirms a cog's action but nobody holds that role; fill the org model",
                                       key, frame, line))
                subj_kind, _, subj = rule["subject"].partition(":")
                if subj_kind == "role" and not any(m["principal_type"] == "cog" and subj in m["roles"] for m in members.values()):
                    out.append(Finding("consistency", "warning", f"'when a person confirms' binds cogs only, and no cog holds the {subj.replace('-', ' ')} role",
                                       key, frame, line))
            folder = rule["resource"].partition(":")[2]
            if folder not in folders:
                out.append(Finding("consistency", "error", f"folder {folder!r} is not declared", key, frame, line))
        elif kind == "routing":
            if rule["process"] not in processes:
                out.append(Finding("consistency", "error", f"routing names unknown process {rule['process']!r}", key, frame, line))
            elif not step_ok(rule["process"], rule["step"]):
                out.append(Finding("consistency", "error", f"routing names step {rule['step']!r}, which is not a checkpoint of {rule['process']}", key, frame, line))
            if not ref_ok(rule["assignee"]):
                out.append(Finding("consistency", "error", f"routing assignee {rule['assignee']!r} is unknown", key, frame, line))
            elif rule["assignee"].startswith("role:") and rule["assignee"][5:] not in roles_with_members:
                out.append(Finding("consistency", "error", f"{rule['assignee']} is routed to but no member holds that role; fill the org model", key, frame, line))
            elif rule["assignee"].startswith("cog:"):
                crit = [c for c in criteria_by_cog.get(rule["assignee"], []) if c["process"] == rule["process"]]
                if not crit:
                    out.append(Finding("consistency", "error",
                                       f"{rule['assignee']} is routed approvals in {rule['process']} but has no approval criteria for it",
                                       key, frame, line))
        elif kind == "sla":
            if rule["process"] not in processes:
                out.append(Finding("consistency", "error", f"SLA names unknown process {rule['process']!r}", key, frame, line))
            elif not step_ok(rule["process"], rule["step"]):
                out.append(Finding("consistency", "error", f"SLA names step {rule['step']!r}, which is not a checkpoint of {rule['process']}", key, frame, line))
        elif kind == "approval_criteria":
            if not ref_ok(rule["approver"]):
                out.append(Finding("consistency", "error", f"approver {rule['approver']!r} is unknown", key, frame, line))
            for c in rule.get("conditions", []):
                if c.startswith("unknown:"):
                    out.append(Finding("consistency", "error", f"condition {c[8:]!r} cannot be evaluated deterministically", key, frame, line))
            if rule.get("countersign") and not ref_ok("role:" + rule["countersign"]):
                out.append(Finding("consistency", "error", f"countersigning role {rule['countersign']!r} is not declared", key, frame, line))
        elif kind == "process" and rule.get("statuses"):
            out += _graph_consistency(rule, key, frame, line, roles, roles_with_members, members, cog_ids, criteria_by_cog, folders, record_types,
                                      committees)
        elif kind == "process":
            if rule["folder"] and rule["folder"] not in folders:
                out.append(Finding("consistency", "error", f"process folder {rule['folder']!r} is not declared", key, frame, line))
            if not rule["folder"]:
                out.append(Finding("consistency", "error", "process does not say where records are filed", key, frame, line))
            if record_types is not None:
                wanted = rule.get("record_type") or rule["object"]
                if wanted not in record_types and not any(td.get("object") == wanted for td in record_types.values()):
                    out.append(Finding("consistency", "error", f"process {rule['name']} needs record type {wanted!r}, which the record type definitions do not define", key, frame, line))
            for step in rule["steps"]:
                if step["kind"] == "cog" and step["cog"] not in cog_ids:
                    out.append(Finding("consistency", "error", f"step {step['id']!r} names unknown cog {step['cog']!r}", key, frame, step["line"]))
                for name in step.get("swarm") or []:
                    if name not in cog_ids:
                        out.append(Finding("consistency", "error", f"step {step['id']!r} swarms unknown cog {name!r}", key, frame, step["line"]))
                    elif name not in members or members[name]["principal_type"] != "cog":
                        out.append(Finding("consistency", "error", f"step {step['id']!r} swarms {name!r}, which is not a cog member of the org model", key, frame, step["line"]))
                if step["role"] not in ("system", "assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter") and step["role"] not in roles:
                    out.append(Finding("consistency", "error", f"step {step['id']!r} names unknown role {step['role']!r}", key, frame, step["line"]))
            checkpoints = [s for s in rule["steps"] if s["kind"] == "checkpoint"]
            if checkpoints and checkpoints[0] is rule["steps"][0]:
                out.append(Finding("consistency", "error", "a process cannot start with a checkpoint", key, frame, line))
            seen_ids = [s["id"] for s in rule["steps"]]
            for dup in sorted({x for x in seen_ids if seen_ids.count(x) > 1}):
                out.append(Finding("consistency", "error", f"process {rule['name']} has two steps titled alike ({dup}); step titles must differ", key, frame, line))
            if rule.get("reject_to") and not any(s["id"] == rule["reject_to"] for s in rule["steps"]):
                out.append(Finding("consistency", "error", f"rejections return to unknown step {rule['reject_to']!r}", key, frame, line))
            for cp in checkpoints:
                if not any(r["kind"] == "routing" and r["process"] == rule["name"] and r["step"] != "*"
                           and (checkpoint_for(rule, r["step"]) or {}).get("id") == cp["id"] for r in rules):
                    out.append(Finding("consistency", "warning", f"checkpoint {cp['title']!r} of {rule['name']} has no routing sentence; it will route to the role on the step", key, frame, cp["line"]))
            out += workflow_checks(rule, key, frame, line, record_types)
    out += responsibilities_guard(rules, roles, roles_with_members, members, groups)
    classed: dict[str, set[str]] = {}
    for r in rules:
        if r["kind"] == "validation_class":
            for k in r.get("kinds") or []:
                classed.setdefault(k, set()).add(r["risk"])
    for k, risks in sorted(classed.items()):
        if len(risks) > 1:
            out.append(Finding("consistency", "warning", f"changes to {k} are classed {' and '.join(sorted(risks))} risk; the higher class applies"))
    needs_stated = {r["risk"] for r in rules if r["kind"] == "validation_need"}
    if classed and not {"high", "medium"} <= needs_stated:
        out.append(Finding("consistency", "warning", "the validation section does not say what evidence "
                           + " and ".join(x for x in ("high", "medium") if x not in needs_stated) + "-risk changes need; the hub's defaults apply"))
    profiled = {r["name"] for r in rules if r["kind"] == "cog"}
    for name, m in members.items():
        if m["principal_type"] == "cog" and name not in profiled:
            src = m.get("source") or {}
            out.append(Finding("consistency", "warning", f"cog member {name} has no '## Cog: {name}' profile (what it does, its model, its persona); it is routed work without one",
                               rule_key(m), src.get("frame", ""), int(src.get("line", 0) or 0)))
    # required_by_state keys must be states some process of that record type can reach.
    if record_types:
        from .release import render_workflow

        reachable: dict[str, set[str]] = {}
        for proc in processes.values():
            wanted = proc.get("record_type") or next((t for t, td in record_types.items() if td.get("object") == proc["object"]), proc["object"])
            try:
                wf = render_workflow(proc, {}, wanted)
            except Exception:
                continue
            reachable.setdefault(wanted, set()).update(wf["states"])
        for tname, td in record_types.items():
            if tname not in reachable:
                continue
            # ``system_states``: states the hub sets directly, not through a process (a part released with its design).
            for st in td.get("required_by_state", {}):
                if st not in reachable[tname] | {"failed", "cancelled", "open"} | set(td.get("system_states") or []):
                    out.append(Finding("consistency", "warning", f"record type {tname}: required_by_state key {st!r} is not a state any process reaches"))
    # Segregation of duties across permissions: a role allowed both to create and
    # to approve the same object in the same folder is flagged, because the SoD
    # rule can then only be enforced per person at run time.
    allow = {}
    for r in rules:
        if r["kind"] == "permission" and r["effect"] == "allow":
            allow.setdefault((r["subject"], r["object"], r["resource"]), set()).add(r["action"])
    for (subject, obj, res), actions in allow.items():
        if {"create", "approve"} <= actions:
            out.append(Finding("consistency", "warning",
                               f"{subject} may both create and approve {obj} in {res}: whoever files an item never approves it "
                               f"(checked at every Gate routed to a role or a cog); a step routed to a named field "
                               f"(the requester, the assignee) is that person's unless a segregation sentence says otherwise"))
    return out


FIELD_GATE_ROLES = ("system", "assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter")


def _light(rules: list[dict]) -> dict:
    """Who is who in the rules, before rendering: members, groups, committees (for group membership)."""
    from .release import render_light

    return render_light(rules)


def _group_consistency(rule: dict, key: str, frame: str, line: int, roles: set, members: dict, light: dict) -> list[Finding]:
    """A group names members of the organization and declared roles, does not share a name with a role, and has someone
    in it."""
    from .groups import group_members

    out: list[Finding] = []
    name = rule["name"]
    if name in roles:
        out.append(Finding("consistency", "error", f"group {name}: a role has the same name; give the group another name", key, frame, line))
    for m in rule.get("members") or []:
        if m not in members:
            out.append(Finding("consistency", "error", f"group {name} lists {m}, who is not a member of the organization", key, frame, line))
    for r in rule.get("roles") or []:
        if r not in roles:
            out.append(Finding("consistency", "error", f"group {name}: everyone holding {r!r}, which is not a declared role", key, frame, line))
    if not group_members(light, name) and not [m for m in rule.get("members") or [] if m not in members]:
        out.append(Finding("consistency", "warning", f"group {name} has nobody in it", key, frame, line))
    return out


def _graph_consistency(rule: dict, key: str, frame: str, line: int, roles: set, roles_with_members: set, members: dict,
                       cog_ids: set, criteria_by_cog: dict, folders: set, record_types: dict | None,
                       committees: dict | None = None) -> list[Finding]:
    """The checks a process written as a graph gets at compile: what it names exists, a cog gate has approval criteria,
    and the graph checks of the workflow model (``hubproto.workflow_model.checks``, when that module is present)."""
    out: list[Finding] = []
    if rule["folder"] and rule["folder"] not in folders:
        out.append(Finding("consistency", "error", f"process folder {rule['folder']!r} is not declared", key, frame, line))
    if not rule["folder"]:
        out.append(Finding("consistency", "error", "process does not say where records are filed", key, frame, line))
    if not rule.get("object") and not rule.get("record_type"):
        out.append(Finding("consistency", "error", f"process {rule['name']}: say its record type ('The record type is X.') or "
                                                   "name its start ('Starts in <status> (\"Open the <item>\"): ...')", key, frame, line))
    if record_types is not None:
        wanted = rule.get("record_type") or rule.get("object")
        if wanted and wanted not in record_types and not any(td.get("object") == wanted for td in record_types.values()):
            out.append(Finding("consistency", "error", f"process {rule['name']} needs record type {wanted!r}, which the record type definitions do not define", key, frame, line))
    for t in rule.get("transitions") or []:
        tl = int(t.get("line", 0) or line)
        g = t.get("gate") or {}
        name = t.get("name") or t.get("id")
        for role in [g.get("role"), g.get("fallback")]:
            if role and role not in FIELD_GATE_ROLES and role not in roles:
                out.append(Finding("consistency", "error", f"{name!r} is decided by {role!r}, which is not a role of the org model", key, frame, tl))
            elif role and role not in FIELD_GATE_ROLES and role not in roles_with_members:
                out.append(Finding("consistency", "error", f"{name!r} is decided by the {role.replace('-', ' ')}, a role nobody holds; fill the org model", key, frame, tl))
        if g.get("kind") == "cog":
            cog = g.get("cog")
            if cog not in members or members[cog]["principal_type"] != "cog":
                out.append(Finding("consistency", "error", f"{name!r} is decided by the {cog} cog, which is not a cog member of the org model", key, frame, tl))
            elif not [c for c in criteria_by_cog.get(f"cog:{cog}", []) if c["process"] == rule["name"]]:
                out.append(Finding("consistency", "error", f"{name!r} is decided by the {cog} cog, which has no approval criteria for {rule['name']}", key, frame, tl))
        if g.get("kind") == "committee":
            c = (committees or {}).get(g.get("committee"))
            if c is None:
                out.append(Finding("consistency", "error", f"{name!r} is decided by the {g.get('committee')} committee, which is not defined "
                                   "('## Committee: <name>' with Group: and Chair:)", key, frame, tl))
            elif t.get("type") == "directed":
                # Consensus or back for revision: an objection needs a way back from the status the committee decides at.
                from .process_graph import option_verdict

                probe = {"statuses": rule.get("statuses") or [], "transitions": [x for x in rule.get("transitions") or []]}
                back = [x for x in rule.get("transitions") or [] if x.get("type") == "directed" and set(x.get("from") or []) & set(t.get("from") or [])
                        and (x.get("gate") or {}).get("kind") == "committee" and option_verdict(probe, x) == "reject"]
                if option_verdict(probe, t) == "approve" and not back:
                    out.append(Finding("consistency", "error", f"{name!r} is decided by the {g.get('committee')} committee, but an objection has no way "
                                       f"back: say where it returns the item ('From {', '.join(t.get('from') or [])} to <an earlier status> "
                                       f"(\"Return\"): the {g.get('committee')} committee decides.')", key, frame, tl))
        for a in t.get("actions") or []:
            if isinstance(a, str) and a not in cog_ids:
                near = sorted(c for c in cog_ids if c.endswith("-" + a) or c.startswith(a + "-"))
                out.append(Finding("consistency", "error", f"{name!r} runs {a!r}, which is not a cog" + (f" (did you mean {', '.join(near)}?)" if near else ""),
                                   key, frame, tl))
            elif isinstance(a, dict) and "notify" in a:
                kind_, _, who = str(a["notify"]).partition(":")
                if kind_ == "role" and who not in roles:
                    out.append(Finding("consistency", "error", f"{name!r} notifies {who!r}, which is not a role", key, frame, tl))
    out += workflow_checks(rule, key, frame, line, record_types)
    return out


def workflow_checks(rule: dict, key: str, frame: str, line: int, record_types: dict | None = None) -> list[Finding]:
    """The graph checks of ``hubproto.workflow_model`` (workstream 8), called when that module is present: its errors are
    compile errors for a process written as a graph; for numbered steps (checked by the guards above, and compiled into
    the graph with no change of meaning) they are shown as warnings."""
    try:
        from . import workflow_model  # noqa: F401
    except ImportError:
        return []
    check = getattr(workflow_model, "checks", None)
    if check is None:
        return []
    from .process_graph import build, graph_only

    rt = rule.get("record_type") or next((t for t, td in (record_types or {}).items() if td.get("object") == rule.get("object")), rule.get("object"))
    try:
        graph = graph_only(build(rule, {}, rt or ""))
        found = check(graph)
    except Exception as e:  # a check that cannot run never blocks a compile; it says so
        return [Finding("workflow", "warning", f"the workflow checks could not run on {rule['name']}: {type(e).__name__}: {e}", key, frame, line)]
    if isinstance(found, dict):
        found = [{**x, "severity": "error"} if isinstance(x, dict) else {"severity": "error", "detail": str(x)} for x in found.get("errors") or []] + \
                [{**x, "severity": "warning"} if isinstance(x, dict) else {"severity": "warning", "detail": str(x)} for x in found.get("warnings") or []]
    out = []
    graph_form = bool(rule.get("statuses"))
    lines = (graph.get("source") or {}).get("lines") or {}
    for f in found or []:
        f = f if isinstance(f, dict) else {"detail": str(f)}
        sev = str(f.get("severity") or f.get("level") or "error").lower()
        sev = "error" if sev.startswith("err") else "warning"
        if not graph_form:
            sev = "warning"
        detail = f.get("detail") or f.get("message") or f.get("text") or str(f)
        el = f.get("element") or f.get("id") or ""
        if isinstance(el, dict):
            el = el.get("id") or ""
        out.append(Finding("workflow", sev, f"{rule['name']}: {detail}", key, frame, int(lines.get(el, 0) or line)))
    return out


FIELD_STEP_ROLES = {"system", "assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter"}


def table_needs(rules: list[dict]) -> dict[str, list[tuple[str, str, int]]]:
    """{Frame: [(who, what part, line)]}: who each document gives a part (a step, a route, approval criteria or a
    countersignature, a duty, or a create, approve or update permission). Its Roles and responsibilities table must list
    each of them, or a role the cog holds."""
    out: dict[str, list[tuple[str, str, int]]] = {}
    committees = {r["name"]: r for r in rules if r.get("kind") == "committee"}
    for r in rules:
        src = r.get("source") or {}
        frame = src.get("frame") or ""
        if not frame or frame == "settings":
            continue
        k, line = r.get("kind"), int(src.get("line") or 0)
        found = []
        if k == "process":
            for st in r.get("steps") or []:
                if st.get("role") and st["role"] not in FIELD_STEP_ROLES:
                    found.append(("role:" + st["role"], "a step", int(st.get("line") or line)))
                for c in list(st.get("swarm") or []):
                    found.append(("cog:" + c, "a step", int(st.get("line") or line)))
            for t in r.get("transitions") or []:  # a process written as a graph: whoever decides a transition has a part
                g, tl = t.get("gate") or {}, int(t.get("line") or line)
                for role in (g.get("role"), g.get("fallback")):
                    if role and role not in FIELD_STEP_ROLES:
                        found.append(("role:" + role, "a decision", tl))
                if g.get("kind") == "cog" and g.get("cog"):
                    found.append(("cog:" + g["cog"], "a decision", tl))
                if g.get("kind") == "committee" and (committees.get(g.get("committee")) or {}).get("group"):
                    # A committee decides: its group's members have a part (the table lists the group, or each of them).
                    found.append(("group:" + committees[g["committee"]]["group"], "a decision (the " + g["committee"] + " committee)", tl))
        elif k == "routing" and str(r.get("assignee", "")).startswith(("role:", "cog:", "group:")):
            found.append((r["assignee"], "a route" if r.get("mode", "assign") == "assign" else "a report", line))
        elif k == "approval_criteria":
            found.append((r["approver"], "approval criteria", line))
            if r.get("countersign"):
                found.append(("role:" + r["countersign"], "a countersignature", line))
        elif k == "duty":
            found.append(("cog:" + r["cog"], "a duty", line))
        elif k == "permission" and r.get("action") in ("create", "approve", "update") and r.get("effect") in ("allow", "propose"):
            found.append((r["subject"], f"'{r['action']}' on {r['object'].replace('-', ' ')}s", line))
        if found:
            out.setdefault(frame, []).extend(found)
    return out


def table_rows_needed(rules: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """{Frame: [(who, what part)]} for each role or cog a document gives a part that its Roles and responsibilities
    table does not list (a cog counts as listed when one of its roles is): the rows a writer should add
    (``extract.add_responsibility_row``)."""
    from .groups import groups_of

    tables: dict[str, set[str]] = {}
    roles_of: dict[str, set[str]] = {}
    for r in rules:
        if r.get("kind") == "responsibility":
            tables.setdefault(r["frame"], set()).add(r["who"])
        elif r.get("kind") == "member":
            roles_of.setdefault(r["principal"], set()).update(r.get("roles") or [])
    light = _light(rules)
    out: dict[str, list[tuple[str, str]]] = {}
    for frame, needs in table_needs(rules).items():
        table = tables.get(frame, set())
        for who, what, _line in needs:
            kind, _, name = who.partition(":")
            if who in table or (kind == "cog" and (any("role:" + x in table for x in roles_of.get(name, set()))
                                                   or any("group:" + g in table for g in groups_of(light, name)))):
                continue
            if (who, what) not in out.setdefault(frame, []):
                out[frame].append((who, what))
    return {f: v for f, v in out.items() if v}


def responsibilities_guard(rules: list[dict], roles: set[str], roles_with_members: set[str], members: dict,
                          groups: dict | None = None) -> list[Finding]:
    """Each document's Roles and responsibilities table against its rules, so the table (who must read the document,
    what a cog follows) cannot drift from what the document gives people and cogs: a warning for each role or cog the
    document gives a part that its table does not list, and for each table row naming a role nobody holds, a role that
    is not declared, or a cog the organization does not have."""
    out: list[Finding] = []
    tables: dict[str, set[str]] = {}
    for r in rules:
        if r["kind"] != "responsibility":
            continue
        tables.setdefault(r["frame"], set()).add(r["who"])
        kind, _, name = r["who"].partition(":")
        frame, line = _where(r)
        if kind == "role" and name not in roles:
            out.append(Finding("consistency", "warning", f"{r['frame']}'s Roles and responsibilities table names '{name}', which is not a declared role",
                               rule_key(r), frame, line))
        elif kind == "role" and name not in roles_with_members:
            out.append(Finding("consistency", "warning", f"{r['frame']}'s Roles and responsibilities table names the {name.replace('-', ' ')} role, "
                               "which nobody holds", rule_key(r), frame, line))
        elif kind == "cog" and (members.get(name) or {}).get("principal_type") != "cog":
            out.append(Finding("consistency", "warning", f"{r['frame']}'s Roles and responsibilities table names the {name} cog, which the "
                               "organization does not have", rule_key(r), frame, line))
        elif kind == "group" and name not in (groups or {}):
            out.append(Finding("consistency", "warning", f"{r['frame']}'s Roles and responsibilities table names the {name} group, which the "
                               "organization model does not declare (## Groups), so it makes nobody read it", rule_key(r), frame, line))
    from .groups import groups_of

    light = _light(rules)
    for frame, needs in table_needs(rules).items():
        table = tables.get(frame)
        if table is None:
            out.append(Finding("consistency", "warning", f"{frame} has no Roles and responsibilities table: nobody is required to read it "
                               f"though it gives {len(needs)} part(s) to roles and cogs", "", frame, 0))
            continue
        seen = set()
        for who, what, line in needs:
            kind, _, name = who.partition(":")
            ok = who in table or (kind == "cog" and (any("role:" + x in table for x in (members.get(name) or {}).get("roles") or [])
                                                     or any("group:" + g in table for g in groups_of(light, name))))
            if ok or (who, what) in seen:
                continue
            seen.add((who, what))
            words = (f"the {name} cog" if kind == "cog" else f"the {name.replace('-', ' ')} group" if kind == "group"
                     else f"the {name.replace('-', ' ')} role")
            out.append(Finding("consistency", "warning", f"{frame} gives {words} {what} (line {line}) but its Roles and responsibilities "
                               "table does not list it", "", frame, line))
    return out


_WIDENING_KINDS = {"permission", "member", "routing", "approval_criteria", "threshold", "role"}


def rule_identity(rule: dict) -> str:
    """What a rule is about, apart from what it says: an add and a remove with the same identity are one change."""
    kind = rule.get("kind")
    fields = {
        "routing": ("process", "step", "condition"), "permission": ("subject", "action", "object", "folder"),
        "approval_criteria": ("approver", "process"), "sla": ("process", "step"), "sod": ("object",),
        "member": ("principal",), "role": ("name",), "folder": ("path",), "threshold": ("name",), "cog": ("name",),
        "persona": ("name",), "duty": ("cog", "task"), "group": ("name",), "committee": ("name",),
    }.get(kind)
    if not fields:
        return rule_key(rule)
    return sha256_of({"kind": kind, **{f: rule.get(f) for f in fields}})[:16]


def _differences(old: dict, new: dict) -> str:
    skip = {"source", "key"}
    parts = []
    for k in sorted(set(old) | set(new)):
        if k in skip or old.get(k) == new.get(k):
            continue
        parts.append(f"{k}: {old.get(k, 'unset')!r} -> {new.get(k, 'unset')!r}")
    return "; ".join(parts)


def drift_guard(old_rules: list[dict], new_rules: list[dict]) -> list[dict]:
    """Diff two rule sets. Returns changes with a ``widening`` flag. An add and a remove about the same thing are
    linked (``changes_from`` on the add, ``changed_to`` on the remove) and worded as one change."""
    old = {rule_key(r): r for r in old_rules}
    new = {rule_key(r): r for r in new_rules}
    changes: list[dict] = []
    added = {key: rule for key, rule in new.items() if key not in old}
    removed = {key: rule for key, rule in old.items() if key not in new}
    removed_by_identity: dict[str, list[str]] = {}
    for key, rule in removed.items():
        removed_by_identity.setdefault(rule_identity(rule), []).append(key)
    paired: dict[str, str] = {}  # removed key -> added key
    for key, rule in added.items():
        candidates = [k for k in removed_by_identity.get(rule_identity(rule), []) if k not in paired]
        if candidates:
            paired[candidates[0]] = key
    for key, rule in added.items():
        change = {"id": "add:" + key, "op": "add", "rule": rule, "widening": _widens(rule, old.values()),
                  "reason": _reason(rule, "add")}
        old_key = next((ok for ok, nk in paired.items() if nk == key), None)
        if old_key:
            change["changes_from"] = removed[old_key]
            change["reason"] = _reason(rule, "change") + " (" + (_differences(removed[old_key], rule) or "restated") + ")"
            change["widening"] = _widens_over(removed[old_key], rule)
        changes.append(change)
    for key, rule in removed.items():
        change = {"id": "remove:" + key, "op": "remove", "rule": rule,
                  # Removing a deny, a "when a person confirms" or a segregation sentence lets someone do what they could not
                  # (or do it unconfirmed).
                  "widening": (rule["kind"] == "permission" and rule["effect"] in ("deny", "propose")) or rule["kind"] in (
                      "sod", "validation_class", "validation_need"),
                  "reason": _reason(rule, "remove")}
        if key in paired:
            change["changed_to"] = added[paired[key]]
            change["reason"] = "superseded by the change above: " + _reason(rule, "remove")
            change["widening"] = False
        changes.append(change)
    changes.sort(key=lambda c: (c["rule"]["kind"], c["id"]))
    return changes


def _widens_over(old: dict, new: dict) -> bool:
    """A restatement widens only if it allows more than before: same effect and assignee, broader scope or limit."""
    if old.get("kind") != new.get("kind"):
        return True
    if new.get("kind") == "approval_criteria":
        return _criteria_widens(old, new)
    if new.get("kind") == "process":
        def checkpoints(r):
            return {s.get("id") for s in r.get("steps") or [] if s.get("kind") == "checkpoint"} | {
                t.get("id") for t in r.get("transitions") or [] if (t.get("gate") or {}).get("kind") in ("person", "cog", "committee")}
        return not checkpoints(old) <= checkpoints(new) or old.get("folder") != new.get("folder")
    if new.get("kind") == "routing":
        return old.get("assignee") != new.get("assignee") or (old.get("mode") or "assign") != (new.get("mode") or "assign")
    if new.get("kind") == "member":
        return not set(new.get("roles") or []) <= set(old.get("roles") or [])
    if new.get("kind") == "group":  # someone more is in it (what the group may do reaches them)
        return not (set(new.get("members") or []) <= set(old.get("members") or []) and set(new.get("roles") or []) <= set(old.get("roles") or []))
    return old != {**new, "source": old.get("source")}


def _limit_raised(o, n) -> bool:
    """A limit (None = no limit) that allows more than before."""
    return (n is None and o is not None) or (o is not None and n is not None and float(n) > float(o))


def _criteria_widens(old: dict, new: dict) -> bool:
    """A cog's approval criterion allows more when it loses a condition, a countersignature or a running limit, or a
    limit goes up (or away)."""
    if not set(old.get("conditions") or []) <= set(new.get("conditions") or []):
        return True
    if old.get("countersign") and old.get("countersign") != new.get("countersign"):
        return True
    if (old.get("currency") or None) != (new.get("currency") or None) and old.get("max_amount") is not None:
        return True
    if int(new.get("adversarial_reviews") or 0) < int(old.get("adversarial_reviews") or 0):
        return True  # fewer independent reviews before a cog signs
    return any(_limit_raised(old.get(k), new.get(k)) for k in ("max_amount", "max_per_day", "max_amount_per_month"))


def _widens(rule: dict, old_rules) -> bool:
    kind = rule["kind"]
    if kind == "permission":
        return rule["effect"] in ("allow", "propose")  # a proposing permission lets a cog do, once confirmed, what it could not
    if kind == "member":
        return True
    if kind in ("cog", "persona", "scope", "committee", "duty", "group"):
        return True  # who a cog is and how it behaves (its model, persona), the scopes, a standing duty, who is in a group: two policy owners
    if kind in ("validation_class", "validation_need"):
        return True  # what a configuration change must prove before approval: two policy owners
    if kind == "routing":
        return rule["assignee"].startswith("cog:")
    if kind == "approval_criteria":
        # A new criterion is more authority for a cog, whatever criteria it already has; a restatement of an existing
        # one (paired in drift_guard) is judged by _criteria_widens.
        return True
    if kind == "threshold":
        for o in old_rules:
            if o["kind"] == kind and o["name"] == rule["name"]:
                return rule["value"] > o["value"]
        return True
    return False


def _reason(rule: dict, op: str) -> str:
    kind = rule["kind"]
    if kind == "permission":
        return (f"{op} {rule['effect']} {rule['subject']} {rule['action']} {rule['object']} in {rule['resource']}"
                + (f" when a {rule.get('confirmer')} confirms" if rule.get("effect") == "propose" else ""))
    if kind == "routing":
        cond = rule.get("condition")
        if not cond:
            c = ""
        elif "all" in cond:
            c = " when " + " and ".join(f"{x['field']} {x['op']} {x['value']}" for x in cond["all"])
        else:
            c = f" when {cond['field']} {cond['op']} {cond['value']}"
        return f"{op} route {rule['process']}/{rule['step']}{c} to {rule['assignee']}"
    if kind == "threshold":
        return f"{op} threshold {rule['name']} = {rule['value']} {rule['unit']}"
    if kind == "approval_criteria":
        return f"{op} {rule['approver']} may approve {rule['process']} up to {rule.get('max_amount')} {rule.get('currency') or ''} when {rule.get('conditions')}"
    if kind == "member":
        return f"{op} member {rule['principal']} ({rule['principal_type']}) roles {rule['roles']}"
    if kind == "process":
        return f"{op} process {rule['name']} with {len(rule['steps'])} steps"
    if kind == "sod" and rule.get("rule") == "cog-work-signed-by-person":
        return f"{op} segregation of duties: a cog's work " + ("" if rule.get("object") in (None, "*") else f"on a {rule['object']} ") + "is signed by a person"
    if kind == "sod":
        return f"{op} segregation of duties on {rule['object']}: {rule['roles']}"
    if kind == "cog":
        return f"{op} cog profile {rule['name']}: model {rule.get('model') or 'none'}, persona {rule.get('persona') or 'none'}"
    if kind == "persona":
        return (f"{op} persona {rule['name']}: {rule.get('stance') or '?'}, {rule.get('tone') or 'no tone'}, lenses "
                f"{', '.join(rule.get('lenses') or []) or 'none'}, {int(rule.get('passes') or 0)} adversarial passes")
    if kind == "duty":
        return f"{op} duty of the {rule['cog']} cog, every {rule['every']}: {rule['task']}"
    if kind == "group":
        who = list(rule.get("members") or []) + [f"everyone holding {r}" for r in rule.get("roles") or []]
        return f"{op} group {rule['name']}: {', '.join(who) or 'nobody'}"
    if kind == "committee":
        return (f"{op} committee {rule['name']}" + (f" on the {rule['group']} group" if rule.get("group") else
                                                    f" of {', '.join(rule.get('members') or [])}")
                + (f", chaired by {rule['chair']}" if rule.get("chair") else "") + ": decides by consensus")
    if kind == "responsibility":
        return f"{op} in {rule.get('frame')}'s Roles and responsibilities: {rule.get('who')}: {rule.get('text')}"
    if kind == "validation_class":
        return f"{op} validation class: changes to {', '.join(rule.get('kinds') or [])} are {rule.get('risk')} risk"
    if kind == "validation_need":
        return f"{op} validation: {rule.get('risk')}-risk changes need {str(rule.get('evidence')).replace('-', ' ')}"
    if kind == "scope":
        return f"{op} scope {rule['name']} ({rule.get('level')}): compiles {', '.join(rule.get('compiles') or [])}" + (
            f", layers on {', '.join(rule['layers_on'])}" if rule.get("layers_on") else "")
    return f"{op} {kind} " + str({k: v for k, v in rule.items() if k not in ('kind', 'source')})


COMPILE_COMMITTEE = "compile-frame"


def committee_guard(rules: list[dict], cogs: set[str] | None = None) -> list[Finding]:
    """A committee is defined on a group that exists (or, in earlier texts, lists its members), has a person of the
    organization as its chair and somebody in it. The compile-frame committee reads the SOPs: its members are cogs."""
    from .groups import committee as _committee

    out: list[Finding] = []
    light = _light(rules)
    people = {r["principal"] for r in rules if r["kind"] == "member" and r["principal_type"] == "person"}
    everyone = {r["principal"] for r in rules if r["kind"] == "member"}
    cogs = cogs if cogs is not None else {r["principal"] for r in rules if r["kind"] == "member" and r["principal_type"] == "cog"}
    for c in (r for r in rules if r["kind"] == "committee"):
        key, (frame, line) = rule_key(c), _where(c)
        name = c["name"]
        if c.get("group") and c["group"] not in (light.get("groups") or {}):
            out.append(Finding("consistency", "error", f"committee {name} is defined on the {c['group']} group, which the organization model "
                               "does not declare (## Groups)", key, frame, line))
        elif not c.get("group") and not c.get("members"):
            out.append(Finding("consistency", "error", f"committee {name}: say the group it is defined on (Group: <group>)", key, frame, line))
        for m in c.get("members") or []:
            if m not in everyone:
                out.append(Finding("consistency", "error", f"committee {name}: {m} is not a member of the organization", key, frame, line))
        if not c.get("chair"):
            out.append(Finding("consistency", "error", f"committee {name}: name its chair, a person who intervenes when a member cannot "
                               "agree in time (Chair: <person>)", key, frame, line))
        elif c["chair"] not in people:
            out.append(Finding("consistency", "error", f"committee {name}: its chair {c['chair']} is not a person of the organization", key, frame, line))
        resolved = (_committee({**light, "committees": {name: c}}, name) or {}).get("members") or []
        if (c.get("group") in (light.get("groups") or {}) or c.get("members")) and not resolved:
            out.append(Finding("consistency", "error", f"committee {name} has nobody in it", key, frame, line))
        if name == COMPILE_COMMITTEE:
            for m in resolved:
                if m not in cogs:
                    out.append(Finding("consistency", "error", f"committee {name}: {m} is not a cog; the compile-frame committee's members read the "
                                       "SOPs with their models", key, frame, line))
    return out


def scope_guard(rules: list[dict], frame_names: set[str], frame_scopes: dict[str, str] | None = None) -> list[Finding]:
    """Scopes refer to what exists: a scope compiles Frames of this release and layers on scopes that exist, with no
    cycle and never on a scope of a narrower level. A persona no cog uses, and a Frame in no scope, are worth a
    warning."""
    from .extract import _LEVELS

    out: list[Finding] = []
    scopes = {r["name"]: r for r in rules if r["kind"] == "scope"}
    personas = [r for r in rules if r["kind"] == "persona"]
    roles = {r["name"] for r in rules if r["kind"] == "role"}
    cogs = {r["principal"] for r in rules if r["kind"] == "member" and r["principal_type"] == "cog"}
    rank = {lv: i for i, lv in enumerate(_LEVELS)}
    for s in scopes.values():
        key, (frame, line) = rule_key(s), _where(s)
        if s.get("level") not in rank:
            out.append(Finding("consistency", "error", f"scope {s['name']}: level must be one of {', '.join(_LEVELS)}", key, frame, line))
        for f in s.get("compiles") or []:
            if f not in frame_names:
                out.append(Finding("consistency", "error", f"scope {s['name']} compiles {f!r}, which is not a Frame of this release", key, frame, line))
        for parent in s.get("layers_on") or []:
            if parent not in scopes:
                out.append(Finding("consistency", "error", f"scope {s['name']} layers on {parent!r}, which is not a scope", key, frame, line))
            elif rank.get(scopes[parent].get("level"), 0) > rank.get(s.get("level"), 0):
                out.append(Finding("consistency", "error", f"scope {s['name']} ({s.get('level')}) layers on {parent} ({scopes[parent].get('level')}), a narrower scope",
                                   key, frame, line))
        seen, cur, stack = set(), s["name"], [s["name"]]
        while stack:  # a cycle through layers_on
            cur = stack.pop()
            for parent in (scopes.get(cur) or {}).get("layers_on") or []:
                if parent == s["name"]:
                    out.append(Finding("consistency", "error", f"scope {s['name']} layers on itself (through {cur})", key, frame, line))
                    stack = []
                    break
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
    used = {r.get("persona") for r in rules if r["kind"] == "cog"}
    for p in personas:
        if p["name"] not in used:
            out.append(Finding("consistency", "warning", f"persona {p['name']} is used by no cog", rule_key(p), *_where(p)))
    out += committee_guard(rules, cogs)
    if scopes:
        covered = {f for s in scopes.values() for f in s.get("compiles") or []}
        for f in sorted(frame_names - covered):
            if (frame_scopes or {}).get(f) in ("persona", "reference"):
                continue  # personas are behaviour documents; references are read, not compiled
            out.append(Finding("consistency", "warning", f"the {f} Frame is in no scope", "", f, 0))
    return out

