"""Document review by cogs: one cog reviews an item against the SOPs it follows, and a swarm fans the same
review out to several cogs and merges their findings. Used by a process step that names several cogs, by an
@mention in an item's chatter, and by the Swarm cog.

A review is evidence: it is filed as an evidence document attached to the item, and it never signs anything by
itself. Progression happens only where policy already lets the cog decide (``runner.cog_decide``)."""

from __future__ import annotations

import json
import os
from datetime import date

from .envelope import problem

ERROR_FIELDS = ("errors",)


def gather(ctx, record: dict) -> dict:
    """What a reviewer looks at: the record's fields, its documents (with any extracted text), CAD checks and
    exports, requirement trace and conditions."""
    docs = []
    if ctx.dms is not None:
        try:
            for d in ctx.dms.documents_of(record["locator"]):
                f = d["fields"]
                docs.append({"number": d["number"], "title": f.get("title"), "doc_class": f.get("doc_class"), "doc_type": f.get("doc_type"),
                             "state": d["state"], "filename": f.get("filename"), "revision": f"{f.get('revision', '')}.{f.get('minor', 0)}",
                             "text": (f.get("text") or "")[:4000]})
        except Exception:
            pass
    f = record["fields"]
    return {"number": record["number"], "type": record["type"], "state": record["state"], "folder": record["folder"],
            "fields": {k: v for k, v in f.items() if k not in ("text",) and not isinstance(v, (bytes,))},
            "documents": docs, "checks": f.get("checks") or {}, "conditions": f.get("conditions") or {},
            "requirement_trace": f.get("requirement_trace") or [], "decisions": record.get("decisions") or []}


def checklist(evidence: dict, sop_texts: dict[str, str]) -> list[dict]:
    """Deterministic findings any reviewer would raise: failed checks, unaccepted requirements, missing fields,
    documents not in a reviewable state. The model, when configured, adds judgement on top of these."""
    out: list[dict] = []
    checks = evidence.get("checks") or {}
    for name, result in checks.items():
        if isinstance(result, dict):
            if result.get("ran") is False:
                out.append(problem(f"{name}", f"{name.upper()} did not run", "warning"))
            elif result.get("errors"):
                out.append(problem(f"{name}", f"{name.upper()} reports {result['errors']} error(s)", "error"))
            elif result.get("warnings"):
                out.append(problem(f"{name}", f"{name.upper()} reports {result['warnings']} warning(s)", "warning"))
    for t in evidence.get("requirement_trace") or []:
        if t.get("state") != "accepted":
            out.append(problem("requirements", f"requirement {t.get('requirement')} is {t.get('state')}, not accepted", "error"))
    cond = evidence.get("conditions") or {}
    for k, v in cond.items():
        if v is False:
            out.append(problem("conditions", f"condition {k} does not hold", "warning"))
    if not evidence.get("documents"):
        out.append(problem("documents", "no document is attached; the review covers the item's fields only", "warning"))
    for d in evidence.get("documents") or []:
        if d.get("doc_class") == "controlled" and d.get("state") in ("obsolete",):
            out.append(problem("documents", f"{d['number']} is obsolete", "error"))
    fields = evidence.get("fields") or {}
    for k in ("title", "description"):
        if k in fields and not fields.get(k):
            out.append(problem("fields", f"{k} is empty", "warning"))
    if not sop_texts:
        out.append(problem("sops", "the reviewer follows no SOP that covers this item; the review is unscoped", "warning"))
    return out


def review(cog_id: str, profile: dict, ctx, record: dict, question: str = "") -> dict:
    """One cog's review of one item: deterministic checklist plus the model's judgement in the cog's own words.
    Returns {"cog", "verdict", "findings", "summary", "model", "sops", "stand_in"}."""
    sops = ctx.sop_texts(profile.get("follows") or []) if profile else {}
    evidence = gather(ctx, record)
    findings = checklist(evidence, sops)
    verdict = "fail" if any(p["severity"] == "error" for p in findings) else "pass"
    budget = int(os.environ.get("HUBPROTO_SOP_CHARS") or 14000)  # a small local model has a small window; the SOPs share it
    per = max(1500, budget // max(1, len(sops)))
    system = (profile.get("prompt") or f"You are {cog_id}, a reviewer on the team.") + "\n\n" + \
        "Review the item strictly against these procedures, and only these:\n\n" + \
        "\n\n".join(f"# {name}\n{text[:per]}" for name, text in sops.items())
    user = (f"Question: {question or 'Review this item.'}\n\nITEM:\n{json.dumps(evidence, indent=1, default=str)[:6000]}\n\n"
            f"FINDINGS:\n" + "\n".join(f"- [{p['severity']}] {p['check']}: {p['detail']}" for p in findings) + "\n\n"
            "Answer with: a short assessment, any further findings as '- [error|warning] check: detail' lines, and 'Verdict: pass' or 'Verdict: fail'.")
    text, model, stand_in = "", profile.get("model") or "stand-in", True
    if ctx.model is not None:
        out = ctx.model.complete(model=model, system=system, user=user, by=cog_id)
        text, stand_in = out["text"], out["stand_in"]
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- [error]") or line.startswith("- [warning]"):
                sev = "error" if line.startswith("- [error]") else "warning"
                rest = line.split("]", 1)[1].strip()
                check, _, detail = rest.partition(":")
                if not any(p["detail"] == detail.strip() for p in findings):
                    findings.append(problem(check.strip() or "model", detail.strip() or rest, sev))
        if "verdict: fail" in text.lower():
            verdict = "fail"
    verdict = "fail" if any(p["severity"] == "error" for p in findings) else verdict
    summary = text.strip() or ("No findings." if not findings else "; ".join(p["detail"] for p in findings))
    return {"cog": cog_id, "verdict": verdict, "findings": findings, "summary": summary, "model": model,
            "sops": sorted(sops), "stand_in": stand_in, "date": date.today().isoformat()}


def render(reviews: list[dict], record: dict, question: str = "") -> str:
    lines = [f"# Review of {record['number']}", f"Question: {question or 'review'}", ""]
    for r in reviews:
        lines += [f"## {r['cog']}: {r['verdict']}", f"Model: {r['model']}{' (stand-in)' if r.get('stand_in') else ''}; procedures: {', '.join(r['sops']) or 'none'}", ""]
        for p in r["findings"]:
            lines.append(f"- [{p['severity']}] {p['check']}: {p['detail']}")
        lines += ["", r["summary"], ""]
    verdict = "pass" if reviews and all(r["verdict"] == "pass" for r in reviews) else "fail"
    lines.append(f"Overall: {verdict}")
    return "\n".join(lines)


def file_review(ctx, record: dict, reviews: list[dict], question: str = "", by: str = "") -> dict | None:
    """File the review as evidence attached to the item (needs the DMS and a records reach)."""
    if ctx.dms is None:
        return None
    text = render(reviews, record, question)
    who = ", ".join(r["cog"] for r in reviews)
    try:
        return ctx.dms.create(title=f"Review of {record['number']} by {who}", doc_class="evidence", folder=record["folder"], by=by or who,
                              content=text.encode("utf-8"), filename=f"review-{record['number']}-{date.today().isoformat()}.md",
                              mime="text/markdown", doc_type="review", attach_to=record["locator"], run_id=ctx.run_id, release=ctx.release,
                              fields={"text": text, "verdict": "pass" if all(r["verdict"] == "pass" for r in reviews) else "fail",
                                      "reviewers": [r["cog"] for r in reviews]})
    except Exception:
        return None


def swarm(cog_ids: list[str], ctx, record: dict, question: str = "", process: str = "") -> list[dict]:
    """Fan the review out to each cog with its own profile and SOPs; a cog that does not follow the item's SOP
    declines with a finding instead of reviewing outside its scope."""
    from .runner import Runner

    out = []
    for cog_id in cog_ids:
        profile = (ctx.bundle.get("cogs") or {}).get(cog_id) or {}
        why = Runner.cog_follows(ctx.bundle, cog_id, process) if process else ""
        if why:
            out.append({"cog": cog_id, "verdict": "declined", "findings": [problem("scope", why, "warning")], "summary": why,
                        "model": profile.get("model") or "stand-in", "sops": profile.get("follows") or [], "stand_in": True,
                        "date": date.today().isoformat()})
            continue
        sub = type(ctx)(**{**ctx.__dict__, "extras": {**ctx.extras, "cog_profile": profile}})
        out.append(review(cog_id, profile, sub, record, question))
    return out
