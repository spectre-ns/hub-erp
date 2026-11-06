"""CAD and ECAD integration: FreeCAD and KiCad.

Two layers, on purpose:

* Metadata extraction runs in the hub with the standard library. An FCStd
  file is a zip holding ``Document.xml``; a KiCad schematic is an
  S-expression text file. Parts lists, properties and bills of material come
  out of those without the tools installed.
* Geometry export and rule checks (STEP, drawing PDF, Gerbers, ERC, DRC)
  need FreeCAD and KiCad. They run in a separate worker image behind the
  ``/invoke`` envelope seam (``hubproto.worker``); the hub never imports the
  tools. When no worker is configured, the exporter records what it could
  not do as warnings and the Guards treat the checks as not run.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from .cogs import Cog, RunContext
from .envelope import make_envelope, problem
from .routing import threshold

CAD_EXTENSIONS = {".fcstd": "freecad", ".kicad_sch": "kicad-schematic", ".kicad_pcb": "kicad-board",
                  ".kicad_pro": "kicad-project", ".step": "step", ".stp": "step"}


# ---------------------------------------------------------------- stdlib extraction

def fcstd_parts(content: bytes) -> dict:
    """Parts list and document properties from a FreeCAD .FCStd (zip with Document.xml)."""
    out = {"objects": [], "properties": {}, "parts": []}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        if "Document.xml" not in z.namelist():
            raise ValueError("not a FreeCAD document: Document.xml missing")
        root = ET.fromstring(z.read("Document.xml"))
    top = root.find("Properties")
    for prop in (top.iter("Property") if top is not None else []):
        name = prop.get("name")
        child = next(iter(prop), None)
        if name in ("Label", "Comment", "Company", "CreatedBy", "LastModifiedBy", "Id", "License") and child is not None:
            out["properties"][name] = child.get("value", "")
    for obj in root.iter("Object"):
        if obj.get("type") is None:
            continue
        entry = {"name": obj.get("name"), "type": obj.get("type")}
        out["objects"].append(entry)
    # Object data carries labels and the custom Part Number / Revision / Material properties.
    data = root.find("ObjectData")
    for od in (list(data) if data is not None else []):
        name = od.get("name")
        props = {}
        for prop in od.iter("Property"):
            child = next(iter(prop), None)
            if child is not None and child.get("value") is not None:
                props[prop.get("name")] = child.get("value")
        label = props.get("Label", name)
        otype = next((o["type"] for o in out["objects"] if o["name"] == name), "")
        if otype.startswith(("Part::", "PartDesign::Body", "App::Part", "App::Link")):
            out["parts"].append({"name": name, "label": label, "type": otype,
                                 "part_number": props.get("PartNumber") or props.get("Part_Number") or "",
                                 "revision": props.get("Revision", ""), "material": props.get("Material", ""),
                                 "quantity": int(props.get("Quantity", 1) or 1)})
    return out


_SYMBOL_RE = re.compile(r'\(symbol\s+\(lib_id\s+"(?P<lib>[^"]+)"')


def _sexpr_blocks(text: str, head: str) -> list[str]:
    """Return the text of every top-level-ish S-expression block starting with (head ."""
    out = []
    i = 0
    needle = f"({head}"
    while True:
        i = text.find(needle, i)
        if i < 0:
            break
        if text[i + len(needle)] not in " \n\t(":
            i += len(needle)
            continue
        depth = 0
        j = i
        while j < len(text):
            c = text[j]
            if c == '"':
                j = text.find('"', j + 1)
                while j > 0 and text[j - 1] == "\\":
                    j = text.find('"', j + 1)
                if j < 0:
                    j = len(text)
                    break
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[i:j + 1])
        i = j + 1
    return out


_PROP_RE = re.compile(r'\(property\s+"(?P<k>[^"]+)"\s+"(?P<v>[^"]*)"')


def kicad_sch_bom(content: bytes) -> list[dict]:
    """Bill of materials from a KiCad 6+ schematic: one line per reference, grouped by value, footprint and MPN."""
    text = content.decode("utf-8", errors="replace")
    lines: dict[tuple, dict] = {}
    for block in _sexpr_blocks(text, "symbol"):
        m = _SYMBOL_RE.match(block)
        if not m:
            continue
        props = {k: v for k, v in _PROP_RE.findall(block)}
        ref = props.get("Reference", "")
        if not ref or ref.startswith("#") or "(in_bom no)" in block.replace(" ", ""):
            continue
        key = (props.get("Value", ""), props.get("Footprint", ""), props.get("MPN", props.get("mpn", "")),
               props.get("Manufacturer", props.get("manufacturer", "")))
        line = lines.setdefault(key, {"value": key[0], "footprint": key[1], "mpn": key[2], "manufacturer": key[3],
                                      "lib_id": m.group("lib"), "references": [], "quantity": 0})
        line["references"].append(ref)
        line["quantity"] += 1
    return sorted(lines.values(), key=lambda l: (l["value"], l["footprint"], l["mpn"]))


def bom_csv(lines: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["references", "quantity", "value", "footprint", "mpn", "manufacturer", "item"])
    for l in lines:
        w.writerow([" ".join(l.get("references", [])), l.get("quantity", 1), l.get("value", ""), l.get("footprint", ""),
                    l.get("mpn", ""), l.get("manufacturer", ""), l.get("item", "")])
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------- worker client

def worker_url() -> str:
    return os.environ.get("HUBPROTO_CAD_WORKER_URL", "")


def call_worker(tool: str, filename: str, content: bytes, options: dict | None = None, timeout: float = 120.0) -> dict:
    """POST an envelope request to the CAD worker. Returns its envelope (error if unreachable)."""
    url = worker_url()
    if not url:
        return make_envelope("cad-worker", "0", tool, error={"code": "worker-unconfigured",
                                                             "detail": "HUBPROTO_CAD_WORKER_URL is not set"})
    body = json.dumps({"tool": tool, "filename": filename, "content_b64": base64.b64encode(content).decode("ascii"),
                       "options": options or {}}).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/invoke", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return make_envelope("cad-worker", "0", tool, error={"code": "worker-unreachable", "detail": str(e)})


# ---------------------------------------------------------------- hub-side cogs

class CADExporter(Cog):
    id = "cad-exporter"
    reaches = ["kernel:documents", "worker:cad"]
    description = ("Reads the CAD and ECAD documents attached to the design record: parts lists and BOMs in the hub, "
                   "STEP, drawings, Gerbers, ERC and DRC through the CAD worker. Files every export as evidence.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        if rec is None or ctx.dms is None:
            return make_envelope(self.id, self.version, "run", error={"code": "no-record", "detail": "run has no record or DMS"})
        problems = []
        exports, bom_lines, parts, checks = [], [], [], {}
        limit = threshold(ctx.bundle, "design-rule-error-limit", 0)
        docs = [d for d in ctx.dms.documents_of(rec["locator"]) if d["fields"].get("doc_class") in ("controlled", "external", "working")]
        sources = []
        for d in docs:
            name = (d["fields"].get("filename") or "").lower()
            ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
            kind = CAD_EXTENSIONS.get(ext)
            if not kind:
                continue
            content, version = ctx.dms.content(d["locator"])
            sources.append({"document": d["locator"], "kind": kind, "filename": d["fields"].get("filename"),
                            "sha256": version["sha256"], "version": version["version"]})
            if kind == "freecad":
                try:
                    info = fcstd_parts(content)
                    parts.extend(info["parts"])
                    ctx.kernel.set_fields(d["locator"], {"cad_properties": info["properties"], "cad_objects": len(info["objects"])},
                                          by=self.id, run_id=ctx.run_id, release=ctx.release)
                except (zipfile.BadZipFile, ValueError, ET.ParseError) as e:
                    problems.append(problem("fcstd", f"{d['number']}: {e}"))
                env = call_worker("freecad-export", d["fields"].get("filename", "model.FCStd"), content, {"formats": ["step", "pdf"]})
                exports += self._file_exports(ctx, rec, d, env, problems, "freecad")
            elif kind == "kicad-schematic":
                lines = kicad_sch_bom(content)
                bom_lines.extend(lines)
                bom_doc = ctx.dms.create(title=f"BOM from {d['fields'].get('filename')}", doc_class="evidence", folder=rec["folder"],
                                         by=self.id, content=bom_csv(lines), filename=f"{rec['number']}-bom.csv", mime="text/csv",
                                         doc_type="bom", run_id=ctx.run_id, release=ctx.release,
                                         provenance={"source": d["locator"], "sha256": version["sha256"]}, attach_to=rec["locator"])
                exports.append({"document": bom_doc["locator"], "doc_type": "bom", "source": d["locator"]})
                env = call_worker("kicad-erc", d["fields"].get("filename", "schematic.kicad_sch"), content)
                checks["erc"] = self._check_result(ctx, rec, d, env, problems, "erc", limit)
                env = call_worker("kicad-export", d["fields"].get("filename", "schematic.kicad_sch"), content, {"formats": ["pdf", "netlist"]})
                exports += self._file_exports(ctx, rec, d, env, problems, "kicad")
            elif kind == "kicad-board":
                env = call_worker("kicad-drc", d["fields"].get("filename", "board.kicad_pcb"), content)
                checks["drc"] = self._check_result(ctx, rec, d, env, problems, "drc", limit)
                env = call_worker("kicad-export", d["fields"].get("filename", "board.kicad_pcb"), content, {"formats": ["gerbers", "step"]})
                exports += self._file_exports(ctx, rec, d, env, problems, "kicad")
        if not sources:
            problems.append(problem("no-cad-files", "no FreeCAD or KiCad document is attached to the design record"))
        fields = {"exports": exports, "bom_lines": bom_lines, "parts": parts, "checks": checks, "sources": sources}
        ctx.kernel.set_fields(rec["locator"], fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields}, problems)

    def _file_exports(self, ctx, rec, source_doc, env, problems, tool) -> list[dict]:
        out = []
        if env.get("error"):
            code = env["error"].get("code", "")
            sev = "warning" if code in ("worker-unconfigured", "worker-unreachable", "tool-unavailable") else "error"
            problems.append(problem(f"{tool}-export", f"{source_doc['number']}: {env['error'].get('detail')}", sev))
            return out
        for f in (env.get("payload") or {}).get("files", []):
            content = base64.b64decode(f["content_b64"])
            doc = ctx.dms.create(title=f"{f['name']} from {source_doc['fields'].get('filename')}", doc_class="evidence",
                                 folder=rec["folder"], by=self.id, content=content, filename=f["name"], mime=f.get("mime", ""),
                                 doc_type=f.get("doc_type", "export"), run_id=ctx.run_id, release=ctx.release,
                                 provenance={"source": source_doc["locator"], "tool": tool, "binding": env.get("binding")},
                                 attach_to=rec["locator"])
            out.append({"document": doc["locator"], "doc_type": f.get("doc_type", "export"), "source": source_doc["locator"]})
        return out

    def _check_result(self, ctx, rec, source_doc, env, problems, check, limit) -> dict:
        if env.get("error"):
            problems.append(problem(check, f"{check.upper()} not run: {env['error'].get('detail')}", "warning"))
            return {"ran": False, "errors": None, "warnings": None}
        payload = env.get("payload") or {}
        errors = int(payload.get("errors", 0))
        warnings = int(payload.get("warnings", 0))
        report = payload.get("report")
        if report is not None:
            ctx.dms.create(title=f"{check.upper()} report for {source_doc['fields'].get('filename')}", doc_class="evidence",
                           folder=rec["folder"], by=self.id, content=json.dumps(report, indent=1).encode("utf-8"),
                           filename=f"{rec['number']}-{check}.json", mime="application/json", doc_type=f"{check}-report",
                           run_id=ctx.run_id, release=ctx.release, provenance={"source": source_doc["locator"]},
                           attach_to=rec["locator"])
        if errors > (limit or 0):
            problems.append(problem(check, f"{check.upper()} reports {errors} error(s); the limit is {limit}"))
        return {"ran": True, "errors": errors, "warnings": warnings}


class BOMReconciler(Cog):
    id = "bom-reconciler"
    reaches = ["kernel:read"]
    description = ("Matches every BOM line to an inventory item by MPN and manufacturer, proposes items for unknown lines, "
                   "and flags manufacturers not on the approved list. Creates nothing: proposals wait at the Gate.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        lines = list(prior.get("bom_lines") or [])
        items = {}
        for it in ctx.kernel.query(type_="item"):
            key = (str(it["fields"].get("mpn", "")).lower(), str(it["fields"].get("manufacturer", "")).lower())
            items[key] = it
            items[(key[0], "")] = items.get((key[0], ""), it)
        resolved, unresolved, unapproved = [], [], []
        for line in lines:
            key = (str(line.get("mpn", "")).lower(), str(line.get("manufacturer", "")).lower())
            it = items.get(key) or items.get((key[0], ""))
            if it is None or not key[0]:
                unresolved.append({**line, "proposal": {"type": "item", "fields": {"name": line.get("value"), "mpn": line.get("mpn"),
                                                                                     "manufacturer": line.get("manufacturer"),
                                                                                     "footprint": line.get("footprint")}}})
                continue
            entry = {**line, "item": it["number"], "stock": it["fields"].get("stock"), "unit": it["fields"].get("unit", "ea"),
                     "short": max(0, int(line.get("quantity", 1)) - int(it["fields"].get("stock") or 0))}
            resolved.append(entry)
            if not it["fields"].get("approved_manufacturer", True):
                unapproved.append(entry)
        problems = []
        if unresolved:
            problems.append(problem("bom-resolved", f"{len(unresolved)} BOM line(s) match no inventory item; proposals attached", "warning"))
        if unapproved:
            problems.append(problem("approved-manufacturer", f"{len(unapproved)} line(s) use a manufacturer not on the approved list", "warning"))
        conditions = dict(prior.get("conditions") or {})
        conditions["bom_resolved"] = not unresolved
        conditions["design_checks_clean"] = all((c.get("ran") and c.get("errors") == 0) for c in (prior.get("checks") or {}).values()) \
            and bool(prior.get("checks"))
        fields = {"bom_resolved": resolved, "bom_unresolved": unresolved, "bom_unapproved": unapproved, "conditions": conditions}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields}, problems)


class PartReleaser(Cog):
    id = "part-releaser"
    reaches = ["kernel:records"]
    description = "After design approval: creates or revises the part record and links the design, under the grant."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        part_ref = prior.get("part")
        part = ctx.kernel.get(part_ref) if part_ref else None
        if part is None:
            part = ctx.kernel.create("part", rec["folder"], {"title": prior.get("title", rec["number"]), "revision": "A",
                                                             "design": rec["locator"], "released_by": ctx.grant.get("decided_by")},
                                     by=self.id, run_id=ctx.run_id, release=ctx.release, state="released")
        else:
            from .dms import next_revision
            rev = next_revision(part["fields"].get("revision") or "A")
            ctx.kernel.set_fields(part["locator"], {"revision": rev, "design": rec["locator"], "released_by": ctx.grant.get("decided_by")},
                                  by=self.id, run_id=ctx.run_id, release=ctx.release)
            part = ctx.kernel.get(part["locator"])
        ctx.kernel.link(part["locator"], "defined-by", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        for line in prior.get("bom_resolved") or []:
            it = ctx.kernel.get(f"item:{line['item']}")
            if it:
                ctx.kernel.link(part["locator"], "realized-by", it["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        ctx.kernel.set_fields(rec["locator"], {"part": part["locator"], "revision": part["fields"].get("revision")},
                              by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "part": part["locator"], "revision": part["fields"].get("revision")})


def register(registry) -> None:
    for cls in (CADExporter, BOMReconciler, PartReleaser):
        registry.register(cls())
