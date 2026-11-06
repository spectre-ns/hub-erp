"""The CAD worker: FreeCAD and KiCad behind the /invoke envelope seam.

Runs in its own image (docker/cad-worker). The hub posts a tool name, a
file and options; the worker runs the tool in a subprocess and returns an
envelope whose payload carries exported files (base64) or a check report.
No kernel, no DMS, no policy here: the worker handles bytes only.

Set HUBPROTO_CAD_FAKE=1 to answer with synthetic exports when the tools are
not installed (tests and the hub-only compose profile).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .envelope import make_envelope

VERSION = "0.1.0"


def _tool(name: str) -> str | None:
    return shutil.which(name)


def binding() -> dict:
    return {"freecad": _tool("FreeCADCmd") or _tool("freecadcmd"), "kicad-cli": _tool("kicad-cli"),
            "fake": os.environ.get("HUBPROTO_CAD_FAKE") == "1"}


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _files_payload(entries: list[tuple[Path, str, str]]) -> dict:
    return {"files": [{"name": p.name, "doc_type": kind, "mime": mime, "content_b64": _b64(p)} for p, kind, mime in entries if p.exists()]}


_FREECAD_SCRIPT = r"""
import os, FreeCAD, Part
doc = FreeCAD.openDocument(os.environ["HUB_SRC"])
out = os.environ["HUB_OUT"]
shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
if shapes:
    comp = Part.makeCompound(shapes)
    comp.exportStep(out + "/model.step")
    comp.exportStl(out + "/model.stl")
    with open(out + "/props.json", "w") as fh:
        import json
        json.dump({"volume": comp.Volume, "area": comp.Area, "bbox": [comp.BoundBox.XLength, comp.BoundBox.YLength, comp.BoundBox.ZLength]}, fh)
"""


def freecad_export(src: Path, out: Path, options: dict) -> dict:
    b = binding()
    if b["freecad"]:
        script = out / "export.py"
        script.write_text(_FREECAD_SCRIPT)
        env = {**os.environ, "HUB_SRC": str(src), "HUB_OUT": str(out)}
        subprocess.run([b["freecad"], str(script)], check=False, capture_output=True, timeout=600, env=env)
        entries = [(out / "model.step", "model-step", "model/step"), (out / "model.stl", "model-stl", "model/stl"),
                   (out / "props.json", "model-properties", "application/json")]
        payload = _files_payload(entries)
        if not payload["files"]:
            return make_envelope("freecad-exporter", VERSION, "freecad-export",
                                 error={"code": "tool-failed", "detail": "FreeCAD produced no output"})
        return make_envelope("freecad-exporter", VERSION, "freecad-export", payload=payload)
    if b["fake"]:
        (out / "model.step").write_text("ISO-10303-21;\nHEADER;\n/* synthetic STEP from the fake worker */\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
        (out / "props.json").write_text(json.dumps({"volume": 1.0, "area": 6.0, "bbox": [1, 1, 1], "fake": True}))
        return make_envelope("freecad-exporter", VERSION, "freecad-export", payload=_files_payload(
            [(out / "model.step", "model-step", "model/step"), (out / "props.json", "model-properties", "application/json")]))
    return make_envelope("freecad-exporter", VERSION, "freecad-export", error={"code": "tool-unavailable", "detail": "FreeCAD is not installed in this worker"})


def kicad_run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([binding()["kicad-cli"]] + args, check=False, capture_output=True, text=True, timeout=600)


def kicad_check(kind: str, src: Path, out: Path) -> dict:
    b = binding()
    tool = f"kicad-{kind}"
    report = out / f"{kind}.json"
    if b["kicad-cli"]:
        sub = "sch" if kind == "erc" else "pcb"
        kicad_run([sub, kind, "--format", "json", "--output", str(report), str(src)])
        if not report.exists():
            return make_envelope(tool, VERSION, tool, error={"code": "tool-failed", "detail": f"kicad-cli produced no {kind} report"})
        data = json.loads(report.read_text())
        errors = sum(1 for sheet in data.get("sheets", data.get("violations", [])) if isinstance(sheet, dict)
                     for v in sheet.get("violations", [sheet]) if v.get("severity") == "error")
        warnings = sum(1 for sheet in data.get("sheets", data.get("violations", [])) if isinstance(sheet, dict)
                       for v in sheet.get("violations", [sheet]) if v.get("severity") == "warning")
        return make_envelope(tool, VERSION, tool, payload={"errors": errors, "warnings": warnings, "report": data})
    if b["fake"]:
        text = src.read_text(errors="replace")
        errors = text.count("FAKE_ERROR")
        data = {"source": src.name, "fake": True, "violations": [{"severity": "error", "description": "synthetic"}] * errors}
        return make_envelope(tool, VERSION, tool, payload={"errors": errors, "warnings": 0, "report": data})
    return make_envelope(tool, VERSION, tool, error={"code": "tool-unavailable", "detail": "KiCad is not installed in this worker"})


def kicad_export(src: Path, out: Path, options: dict) -> dict:
    b = binding()
    formats = options.get("formats") or []
    entries: list[tuple[Path, str, str]] = []
    if b["kicad-cli"]:
        if src.suffix == ".kicad_sch":
            if "pdf" in formats:
                kicad_run(["sch", "export", "pdf", "--output", str(out / "schematic.pdf"), str(src)])
                entries.append((out / "schematic.pdf", "schematic-pdf", "application/pdf"))
            if "netlist" in formats:
                kicad_run(["sch", "export", "netlist", "--output", str(out / "netlist.net"), str(src)])
                entries.append((out / "netlist.net", "netlist", "text/plain"))
        else:
            if "gerbers" in formats:
                gdir = out / "gerbers"
                gdir.mkdir()
                kicad_run(["pcb", "export", "gerbers", "--output", str(gdir), str(src)])
                kicad_run(["pcb", "export", "drill", "--output", str(gdir), str(src)])
                shutil.make_archive(str(out / "gerbers"), "zip", gdir)
                entries.append((out / "gerbers.zip", "gerber-package", "application/zip"))
            if "step" in formats:
                kicad_run(["pcb", "export", "step", "--output", str(out / "board.step"), str(src)])
                entries.append((out / "board.step", "board-step", "model/step"))
        return make_envelope("kicad-exporter", VERSION, "kicad-export", payload=_files_payload(entries))
    if b["fake"]:
        if src.suffix == ".kicad_sch":
            (out / "schematic.pdf").write_bytes(b"%PDF-1.4\n% synthetic schematic from the fake worker\n%%EOF\n")
            entries.append((out / "schematic.pdf", "schematic-pdf", "application/pdf"))
        else:
            (out / "gerbers.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
            entries.append((out / "gerbers.zip", "gerber-package", "application/zip"))
        return make_envelope("kicad-exporter", VERSION, "kicad-export", payload=_files_payload(entries))
    return make_envelope("kicad-exporter", VERSION, "kicad-export", error={"code": "tool-unavailable", "detail": "KiCad is not installed in this worker"})


TOOLS = {"freecad-export": freecad_export, "kicad-export": kicad_export,
         "kicad-erc": lambda s, o, opt: kicad_check("erc", s, o), "kicad-drc": lambda s, o, opt: kicad_check("drc", s, o)}


def invoke(request: dict) -> dict:
    tool = request.get("tool")
    if tool not in TOOLS:
        return make_envelope("cad-worker", VERSION, str(tool), error={"code": "invalid-input", "detail": f"unknown tool {tool!r}"})
    filename = Path(request.get("filename") or "input").name
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / filename
        src.write_bytes(base64.b64decode(request.get("content_b64", "")))
        out = Path(tmp) / "out"
        out.mkdir()
        try:
            env = TOOLS[tool](src, out, request.get("options") or {})
        except Exception as e:  # keep the seam: always an envelope
            env = make_envelope("cad-worker", VERSION, tool, error={"code": "invocation-failed", "detail": f"{type(e).__name__}: {e}"})
    env["timing"] = round(time.perf_counter() - started, 3)
    env["binding"] = {k: bool(v) for k, v in binding().items()}
    return env


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True, "binding": {k: bool(v) for k, v in binding().items()}})
        if self.path == "/cogs":
            return self._send(200, {"tools": sorted(TOOLS), "version": VERSION})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invoke":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return self._send(400, make_envelope("cad-worker", VERSION, "?", error={"code": "invalid-input", "detail": "bad JSON"}))
        self._send(200, invoke(request))


def serve(host: str = "0.0.0.0", port: int = 9000):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"cad worker at http://{host}:{port}/healthz binding={binding()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_in_thread(host: str = "127.0.0.1", port: int = 0):
    import threading

    server = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "9000")))
    a = ap.parse_args()
    serve(a.host, a.port)
