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
        if rule.get("kind") == "cog":
            for name in list(rule.get("follows") or []) + list(rule.get("reviews") or []):
                if name not in frames:
                    out.append(Finding("source-grounding", "error", f"cog {rule['name']} follows {name!r}, which is not a Frame", rule_key(rule),
                                       src.get("frame", ""), int(src.get("line", 0) or 0)))
        frame = frames.get(src.get("frame", ""))
        key = rule_key(rule)
        if frame is None:
            out.append(Finding("source-grounding", "error", "source names an unknown Frame", key))
            continue
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

    def ref_ok(ref: str) -> bool:
        kind, _, name = ref.partition(":")
        if kind == "field":
            return True
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
        if kind == "member":
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
            if not rule.get("follows"):
                out.append(Finding("consistency", "warning", f"cog {rule['name']} follows no SOP; it will be routed nothing", key, frame, line))
            if rule.get("autonomy") and rule["autonomy"] not in ("read", "propose", "act"):
                out.append(Finding("consistency", "error", f"cog {rule['name']}: Autonomy must be read, propose or act, not {rule['autonomy']!r}", key, frame, line))
        elif kind == "permission":
            if not ref_ok(rule["subject"]):
                out.append(Finding("consistency", "error", f"permission subject {rule['subject']!r} is unknown", key, frame, line))
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
    profiled = {r["name"] for r in rules if r["kind"] == "cog"}
    for name, m in members.items():
        if m["principal_type"] == "cog" and name not in profiled:
            src = m.get("source") or {}
            out.append(Finding("consistency", "warning", f"cog member {name} has no '## Cog: {name}' profile (model, prompt, the SOPs it follows); it is routed work without one",
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
            for st in td.get("required_by_state", {}):
                if st not in reachable[tname] | {"failed", "cancelled", "open"}:
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


_WIDENING_KINDS = {"permission", "member", "routing", "approval_criteria", "threshold", "role"}


def rule_identity(rule: dict) -> str:
    """What a rule is about, apart from what it says: an add and a remove with the same identity are one change."""
    kind = rule.get("kind")
    fields = {
        "routing": ("process", "step", "condition"), "permission": ("subject", "action", "object", "folder"),
        "approval_criteria": ("approver", "process"), "sla": ("process", "step"), "sod": ("object",),
        "member": ("principal",), "role": ("name",), "folder": ("path",), "threshold": ("name",), "cog": ("name",),
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
                  # Removing a deny or a segregation sentence lets someone do what they could not.
                  "widening": (rule["kind"] == "permission" and rule["effect"] == "deny") or rule["kind"] == "sod",
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
            return {s.get("id") for s in r.get("steps") or [] if s.get("kind") == "checkpoint"}
        return not checkpoints(old) <= checkpoints(new) or old.get("folder") != new.get("folder")
    if new.get("kind") == "routing":
        return old.get("assignee") != new.get("assignee") or (old.get("mode") or "assign") != (new.get("mode") or "assign")
    if new.get("kind") == "member":
        return not set(new.get("roles") or []) <= set(old.get("roles") or [])
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
        return rule["effect"] == "allow"
    if kind == "member":
        return True
    if kind in ("cog", "persona", "scope"):
        return True  # what a cog or role works from (its model, prompt, persona, the scopes it layers): two policy owners
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
        return f"{op} {rule['effect']} {rule['subject']} {rule['action']} {rule['object']} in {rule['resource']}"
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
    if kind == "sod":
        return f"{op} segregation of duties on {rule['object']}: {rule['roles']}"
    if kind == "cog":
        return f"{op} cog profile {rule['name']}: model {rule.get('model') or 'none'}" + (f", follows {', '.join(rule['follows'])}" if rule.get("follows") else "")
    if kind == "persona":
        return f"{op} persona {rule['name']} ({rule.get('of')}): layers {', '.join(rule.get('layers') or []) or 'nothing'}" + (
            f", also reads {', '.join(rule['reads'])}" if rule.get("reads") else "")
    if kind == "scope":
        return f"{op} scope {rule['name']} ({rule.get('level')}): compiles {', '.join(rule.get('compiles') or [])}" + (
            f", layers on {', '.join(rule['layers_on'])}" if rule.get("layers_on") else "")
    return f"{op} {kind} " + str({k: v for k, v in rule.items() if k not in ('kind', 'source')})


def scope_guard(rules: list[dict], frame_names: set[str], frame_scopes: dict[str, str] | None = None) -> list[Finding]:
    """Scopes and personas refer to what exists: a scope compiles Frames of this release and layers on scopes that
    exist, with no cycle and never on a scope of a narrower level; a persona layers scopes or Frames that exist, and
    belongs to a cog or a role the release declares. A Frame in no scope is worth a warning."""
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
    for p in personas:
        key, (frame, line) = rule_key(p), _where(p)
        if p.get("of") == "cog" and p["name"] not in cogs:
            out.append(Finding("consistency", "error", f"persona {p['name']} is for a cog the organization does not have", key, frame, line))
        if p.get("of") == "role" and p["name"] not in roles:
            out.append(Finding("consistency", "error", f"persona {p['name']} is for a role the organization does not declare", key, frame, line))
        for layer in (p.get("layers") or []) + (p.get("reads") or []):
            if layer not in scopes and layer not in frame_names:
                out.append(Finding("consistency", "error", f"persona {p['name']} layers {layer!r}, which is neither a scope nor a Frame", key, frame, line))
    cog_personas = {p["name"] for p in personas if p.get("of") == "cog"}
    for r in rules:
        if r["kind"] == "cog" and r["name"] in cog_personas and r.get("follows"):
            out.append(Finding("consistency", "warning", f"cog {r['name']}: its persona decides what it follows; the Follows line in its profile is ignored",
                               rule_key(r), *_where(r)))
    if scopes:
        covered = {f for s in scopes.values() for f in s.get("compiles") or []}
        for f in sorted(frame_names - covered):
            if (frame_scopes or {}).get(f) in ("persona", "reference"):
                continue  # personas layer scopes; references are read, not compiled
            out.append(Finding("consistency", "warning", f"the {f} Frame is in no scope", "", f, 0))
    return out

