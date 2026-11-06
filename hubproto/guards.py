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
from .rules import rule_key, validate_rule


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
        line = int(src.get("line", 0) or 0)
        if not (1 <= line <= len(frame.lines)):
            out.append(Finding("source-grounding", "error", f"line {line} is outside the Frame", key, frame.name, line))
            continue
        if frame.lines[line - 1].strip() != str(src.get("text", "")).strip():
            out.append(Finding("source-grounding", "error",
                               "cited text does not match the Frame at that line", key, frame.name, line))
        if str(frame.version) != str(src.get("version")):
            out.append(Finding("source-grounding", "error", "cited Frame version differs", key, frame.name, line))
    return out


def consistency_guard(rules: list[dict], cog_ids: set[str], record_types: dict | None = None) -> list[Finding]:
    out: list[Finding] = []
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
                if step["role"] != "system" and step["role"] not in roles:
                    out.append(Finding("consistency", "error", f"step {step['id']!r} names unknown role {step['role']!r}", key, frame, step["line"]))
            checkpoints = [s for s in rule["steps"] if s["kind"] == "checkpoint"]
            if checkpoints and checkpoints[0] is rule["steps"][0]:
                out.append(Finding("consistency", "error", "a process cannot start with a checkpoint", key, frame, line))
            if rule.get("reject_to") and not any(s["id"] == rule["reject_to"] for s in rule["steps"]):
                out.append(Finding("consistency", "error", f"rejections return to unknown step {rule['reject_to']!r}", key, frame, line))
            for cp in checkpoints:
                if not any(r["kind"] == "routing" and r["process"] == rule["name"] and r["step"] != "*"
                           and (checkpoint_for(rule, r["step"]) or {}).get("id") == cp["id"] for r in rules):
                    out.append(Finding("consistency", "warning", f"checkpoint {cp['title']!r} of {rule['name']} has no routing sentence; it will route to the role on the step", key, frame, cp["line"]))
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
                               f"{subject} may both create and approve {obj} in {res}; segregation of duties is enforced per person at run time"))
    return out


_WIDENING_KINDS = {"permission", "member", "routing", "approval_criteria", "threshold", "role"}


def drift_guard(old_rules: list[dict], new_rules: list[dict]) -> list[dict]:
    """Diff two rule sets. Returns changes with a ``widening`` flag."""
    old = {rule_key(r): r for r in old_rules}
    new = {rule_key(r): r for r in new_rules}
    changes: list[dict] = []
    for key, rule in new.items():
        if key not in old:
            changes.append({"id": "add:" + key, "op": "add", "rule": rule, "widening": _widens(rule, old.values()),
                            "reason": _reason(rule, "add")})
    for key, rule in old.items():
        if key not in new:
            changes.append({"id": "remove:" + key, "op": "remove", "rule": rule,
                            "widening": rule["kind"] == "permission" and rule["effect"] == "deny",
                            "reason": _reason(rule, "remove")})
    changes.sort(key=lambda c: (c["rule"]["kind"], c["id"]))
    return changes


def _widens(rule: dict, old_rules) -> bool:
    kind = rule["kind"]
    if kind == "permission":
        return rule["effect"] == "allow"
    if kind == "member":
        return True
    if kind == "routing":
        return rule["assignee"].startswith("cog:")
    if kind == "approval_criteria":
        for o in old_rules:
            if o["kind"] == kind and o["approver"] == rule["approver"] and o["process"] == rule["process"]:
                if rule.get("max_amount") is None and o.get("max_amount") is not None:
                    return True
                if (rule.get("max_amount") or 0) > (o.get("max_amount") or 0):
                    return True
                if set(rule.get("conditions", [])) < set(o.get("conditions", [])):
                    return True
                return False
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
    return f"{op} {kind} " + str({k: v for k, v in rule.items() if k not in ('kind', 'source')})
