"""Human-readable records for inspection (QMS review B3, B4): how a signature is printed, and the change order as a
complete record (the decided delta, the panel's results, the state map, divergences, validation evidence, signatures).

* ``signature_lines(decisions, members)``: each person's signature with the signer, the date and time with its zone,
  the verdict (approved or rejected), the meaning category and the meaning in the signer's words; a cog's decision under
  its own heading, as an automated decision under the rule that allows it, **not** an electronic signature.
* ``co_record_markdown(hub, co)``: the change order record as Markdown. ``hubproto.dms.render_snapshot`` files it as
  evidence on the change order (the shell's Render snapshot does; the release automation may at approval).

Pure reading: nothing here changes a record.
"""

from __future__ import annotations

VERDICTS = {"approve": "Approved", "reject": "Rejected", "agree": "Agreed", "object": "Objected"}
CATEGORIES = {"approve": "approval", "reject": "rejection", "agree": "review (agreed)", "object": "review (objected)"}


def when_utc(ts) -> str:
    """'2026-11-09T11:24:05+00:00' as '2026-11-09 11:24:05 UTC'."""
    s = str(ts or "")
    if not s:
        return "-"
    s = s.replace("T", " ")
    for tail in ("+00:00", "Z"):
        if s.endswith(tail):
            return s[: -len(tail)].split(".")[0] + " UTC"
    return s


def who_line(who: str, members: dict | None) -> str:
    m = (members or {}).get(who) or {}
    name = m.get("name") or m.get("full_name")
    rs = [r.replace("-", " ") for r in m.get("roles") or [] if r != "staff"]
    roles = ", ".join(rs[:3]) + (" and others" if len(rs) > 3 else "")
    return (f"{name} ({who})" if name else who) + (f", {roles}" if roles else "")


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()


def signature_lines(decisions: list[dict], members: dict | None = None) -> list[str]:
    """Signatures by people, then automated decisions by cogs (each under its own heading)."""
    people = [d for d in decisions if (d.get("by_type") or "person") != "cog"]
    cogs = [d for d in decisions if (d.get("by_type") or "person") == "cog"]
    out: list[str] = []
    if people:
        out += ["## Signatures", "", "| Signed by | Date and time | Verdict | Meaning category | Meaning (in the signer's words) | At | Signature |",
                "|---|---|---|---|---|---|---|"]
        for d in people:
            verdict = d.get("verdict") or ""
            out.append(f"| {_cell(who_line(d.get('by') or '', members))} | {when_utc(d.get('at'))} | {_cell(VERDICTS.get(verdict, verdict or '-'))} | "
                       f"{_cell(d.get('category') or CATEGORIES.get(verdict, verdict or '-'))} | {_cell(d.get('meaning'))} | "
                       f"{_cell((d.get('step') or '').replace('-', ' '))} | {_cell(str(d.get('signature') or '')[:24])} |")
        out.append("")
    if cogs:
        out += ["## Automated decisions (not electronic signatures)", "",
                "A cog decides within approval criteria that people approved on a change order; the people who approved those criteria "
                "are accountable for them.", "", "| Cog | Date and time | Outcome | Under the rule | At |", "|---|---|---|---|---|"]
        for d in cogs:
            rule = d.get("delegating_rule") or {}
            src = rule.get("source") if isinstance(rule, dict) else None
            under = (f"{src.get('frame')} line {src.get('line')}: {src.get('text')}" if isinstance(src, dict) and src.get("frame") else
                     (rule.get("text") if isinstance(rule, dict) else str(rule or "")) or "-")
            out.append(f"| {_cell(d.get('by'))} | {when_utc(d.get('at'))} | {_cell(VERDICTS.get(d.get('verdict') or '', d.get('verdict') or '-'))} | {_cell(under)} | "
                       f"{_cell((d.get('step') or '').replace('-', ' '))} |")
        out.append("")
    return out


def _say(rule: dict | None, processes: dict) -> str:
    if not rule:
        return ""
    from .settings import sentence

    try:
        return sentence({k: v for k, v in rule.items() if k != "source"}, processes).replace("\n", " / ")
    except Exception:
        return (rule.get("source") or {}).get("text") or ""


def co_record_markdown(hub, co_ref) -> str:
    """The change order record: what it carries, the decided delta item by item (origin, warnings with who accepted them,
    decisions, overrides, risk class), the panel's results by round, questions and answers, the state map, the
    divergences it leaves, the validation evidence and the approval."""
    from . import changecontrol as cc
    from . import eco

    co = cc._co(hub, co_ref)
    f = co["fields"]
    b = cc._bundle(hub)
    procs = b.get("processes") or {}
    members = b.get("members") or {}
    out = [f"# Change order record {co['number']}: {f.get('title') or ''}", "",
           f"Stage: **{cc.stage_of(co).replace('-', ' ')}**. Requested by {who_line(cc.requester(co), members)}. {f.get('description') or ''}", ""]
    carried = eco.carried(hub, co)
    out += ["## What it carries", ""]
    out += [f"- {c['name']}: {c['draft']['number']} rev {c['draft']['fields'].get('revision') or ''} ({c['draft']['state']})" for c in carried] or ["- no revision"]
    if f.get("implements"):
        out.append(f"- implements {', '.join(f['implements'])}")
    out.append("")
    out += ["## The delta, item by item", ""]
    items = [c for c in cc.delta(hub, co) if c.get("op") != "text"]
    adds = {c["id"] for c in items if c.get("op") == "add"}
    from .rules import rule_key

    items = [c for c in items if not (c.get("op") == "remove" and c.get("changed_to") and "add:" + rule_key(c["changed_to"]) in adds)]  # with its change
    try:
        from . import validation

        risk = {i["id"]: i["risk"] for i in validation.annotate(items, validation.policy(hub))}
        items = [{**c, "risk": risk.get(c["id"])} for c in items]
    except Exception:
        pass
    if not items:
        out += ["No configuration item.", ""]
    for n, c in enumerate(items, 1):
        new = cc._effective(c) if c.get("op") != "remove" else None
        old = c.get("changes_from") or c.get("was") or (c["rule"] if c.get("op") == "remove" else None)
        head = (f"{_say(old, procs)} -> {_say(new, procs)}" if old is not None and new is not None else
                (f"remove: {_say(old, procs)}" if new is None else _say(new, procs)))
        o = c.get("origin") or {}
        out += [f"### Item {n}: {head}", "",
                f"- State: **{c.get('state')}**" + ("; widening (both policy owners)" if cc._widening(c) else "")
                + (f"; risk class {c.get('risk') or c.get('validation_class')}" if (c.get("risk") or c.get("validation_class")) else ""),
                f"- Origin: {o.get('kind') or 'prose'}" + (f" by {o.get('by')}" if o.get("by") else "")
                + (f", accepted by {o.get('accepted_by')}" if o.get("accepted_by") else "")
                + (f"; {o.get('frame')} line {o.get('line')}: \"{o.get('quote')}\"" if o.get("line") else "")]
        if c.get("override"):
            ov = c["override"]
            out.append(f"- Override by {ov.get('by')} at {when_utc(ov.get('at'))}: in force \"{_say(ov.get('rule'), procs)}\" ({ov.get('meaning') or ''})")
        for w in c.get("warnings") or []:
            acc = w.get("accepted") or {}
            out.append(f"- Warning [{w.get('code')}]: {w.get('text')}" + (f"; accepted by {acc.get('by')} at {when_utc(acc.get('at'))}: \"{acc.get('meaning')}\"" if acc else "; not accepted"))
        for d in c.get("decisions") or []:
            out.append(f"- Decision: {who_line(d.get('by') or '', members)} {d.get('verdict')}d at {when_utc(d.get('at'))}: \"{d.get('meaning')}\""
                       + (" (stale: before the baseline changed)" if d.get("stale") else ""))
        out.append("")
    text_items = [c for c in cc.delta(hub, co) if c.get("op") == "text"]
    if text_items:
        out += ["Wording changes approved with the change order: " + ", ".join(sorted({c.get("frame") or "" for c in text_items})), ""]
    ws = cc.co_warnings(hub, co)
    if ws:
        out += ["## Warnings on the change order as a whole", ""]
        for w in ws:
            acc = w.get("accepted") or {}
            out.append(f"- [{w.get('code')}] {w.get('text')}" + (f"; accepted by {acc.get('by')} at {when_utc(acc.get('at'))}: \"{acc.get('meaning')}\"" if acc else "; not accepted"))
        out.append("")
    out += ["## Review", ""]
    for r in cc.rounds(co):
        out.append(f"### Round {r['n']} ({r.get('status')}), requested by {r.get('requested_by')} at {when_utc(r.get('at'))}")
        out.append("")
        for e in r.get("panel") or []:
            res = (r.get("results") or {}).get(e["who"]) or {}
            out.append(f"- {e['who']} ({e.get('kind')}" + (f", {e['committee']} committee" if e.get("committee") else "") + f"): {res.get('status') or 'not asked'}"
                       + (" (stand-in, no model)" if res.get("stand_in") else "") + (f"; {res.get('summary')}" if res.get("summary") else ""))
            for x in res.get("findings") or []:
                out.append(f"  - [{x.get('severity')}] {x.get('text')}")
        out.append("")
    qs = cc.questions(hub, co)
    if qs:
        out += ["## Questions", ""] + [f"- {q.get('text')} ({q.get('status', 'open')}" + (f"; answered by {q.get('by')}: \"{q.get('answer')}\"" if q.get("answer") else "") + ")"
                                       for q in qs] + [""]
    if f.get("state_map"):
        out += ["## State map", ""] + [f"- {p}: {a} -> {b_}" for p, m in sorted(f["state_map"].items()) for a, b_ in sorted((m or {}).items())] + [""]
    divs = [d for d in cc.open_divergences(hub) if d.get("co") == co["number"]]
    if divs:
        out += ["## Divergences it leaves (configuration in force differs from the prose)", ""]
        out += [f"- {d['id']}: {d.get('frame')} line {d.get('line')} says \"{d.get('prose')}\"; in force: {_say(d.get('in_force'), procs) or 'nothing'}; "
                f"accepted by {d.get('accepted_by')}" + (f"; change request {d['change_request']}" if d.get("change_request") else "") for d in divs] + [""]
    ev = f.get("validation_evidence")
    if ev:
        out += ["## Validation evidence", ""]
        for x in (ev if isinstance(ev, list) else [ev]):
            if not isinstance(x, dict):
                out.append(f"- {x}")
                continue
            out.append(f"- {x.get('kind')} by {x.get('by')} at {when_utc(x.get('at'))}: {x.get('text') or x.get('document') or ''}"
                       + (f"; expected: {x['expected']}; actual: {x['actual']}" if x.get("expected") else "") + f"; result: **{x.get('result')}**"
                       + (f"; covers {', '.join(x['items'])}" if x.get("items") else "; covers every item"))
        out.append("")
    if f.get("contributor"):
        out += [f"Contributors (may not sign it): {', '.join(f['contributor'])}", ""]
    out += signature_lines(list(co.get("decisions") or []), members)
    return "\n".join(out)
