"""CAD and ECAD: stdlib extraction, the worker seam, and the design-release process."""

import io
import zipfile

import pytest

from conftest import release_all, seen
from hubproto import worker
from hubproto.cad import bom_csv, call_worker, fcstd_parts, kicad_sch_bom
from hubproto.runner import RunError

SCHEMATIC = """(kicad_sch (version 20231120) (generator "eeschema")
  (symbol (lib_id "Device:R") (at 10 10 0)
    (property "Reference" "R1" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0)))
  (symbol (lib_id "Device:R") (at 20 10 0)
    (property "Reference" "R2" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0))
    (property "MPN" "RC0603FR-0710KL" (at 0 0 0))
    (property "Manufacturer" "Yageo" (at 0 0 0)))
  (symbol (lib_id "Device:C") (at 30 10 0)
    (property "Reference" "C1" (at 0 0 0))
    (property "Value" "100n" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0))
    (property "MPN" "CL10B104KB8NNNC" (at 0 0 0))
    (property "Manufacturer" "Samsung" (at 0 0 0)))
  (symbol (lib_id "power:GND") (at 40 10 0)
    (property "Reference" "#PWR01" (at 0 0 0))
    (property "Value" "GND" (at 0 0 0)))
)
"""

DOCUMENT_XML = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4" ProgramVersion="1.0" FileVersion="1">
 <Properties Count="2">
  <Property name="Label" type="App::PropertyString"><String value="Bracket assembly"/></Property>
  <Property name="Company" type="App::PropertyString"><String value="Example Instruments"/></Property>
 </Properties>
 <Objects Count="2">
  <Object type="PartDesign::Body" name="Body"/>
  <Object type="Part::Feature" name="Plate"/>
 </Objects>
 <ObjectData Count="2">
  <Object name="Body">
   <Properties Count="3">
    <Property name="Label" type="App::PropertyString"><String value="Bracket"/></Property>
    <Property name="PartNumber" type="App::PropertyString"><String value="PN-BRK-01"/></Property>
    <Property name="Revision" type="App::PropertyString"><String value="B"/></Property>
   </Properties>
  </Object>
  <Object name="Plate">
   <Properties Count="2">
    <Property name="Label" type="App::PropertyString"><String value="Plate"/></Property>
    <Property name="Material" type="App::PropertyString"><String value="AL6061"/></Property>
   </Properties>
  </Object>
 </ObjectData>
</Document>
"""


def fcstd_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Document.xml", DOCUMENT_XML)
    return buf.getvalue()


def test_kicad_schematic_bom_groups_by_value_footprint_and_mpn():
    lines = kicad_sch_bom(SCHEMATIC.encode())
    assert [(l["value"], l["quantity"], l["references"]) for l in lines] == [("100n", 1, ["C1"]), ("10k", 2, ["R1", "R2"])]
    assert lines[1]["mpn"] == "RC0603FR-0710KL" and lines[1]["manufacturer"] == "Yageo"
    csv_text = bom_csv(lines).decode()
    assert "R1 R2,2,10k" in csv_text and "#PWR01" not in csv_text


def test_fcstd_parts_and_properties():
    info = fcstd_parts(fcstd_bytes())
    assert info["properties"]["Label"] == "Bracket assembly" and info["properties"]["Company"] == "Example Instruments"
    parts = {p["label"]: p for p in info["parts"]}
    assert parts["Bracket"]["part_number"] == "PN-BRK-01" and parts["Bracket"]["revision"] == "B"
    assert parts["Plate"]["material"] == "AL6061"
    with pytest.raises(ValueError):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.txt", "x")
        fcstd_parts(buf.getvalue())


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setenv("HUBPROTO_CAD_FAKE", "1")
    server = worker.serve_in_thread("127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("HUBPROTO_CAD_WORKER_URL", url)
    yield url
    server.shutdown()


def test_worker_roundtrip_in_fake_mode(fake_worker):
    env = call_worker("kicad-erc", "a.kicad_sch", SCHEMATIC.encode())
    assert env["ok"] and env["payload"]["errors"] == 0 and env["binding"]["fake"] is True
    env = call_worker("kicad-erc", "b.kicad_sch", (SCHEMATIC + " FAKE_ERROR ").encode())
    assert env["payload"]["errors"] == 1
    env = call_worker("freecad-export", "m.FCStd", fcstd_bytes(), {"formats": ["step"]})
    assert env["ok"] and {f["doc_type"] for f in env["payload"]["files"]} == {"model-step", "model-properties"}
    env = call_worker("nope", "x", b"")
    assert env["error"]["code"] == "invalid-input"


def test_worker_unconfigured_is_a_soft_failure(monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    env = call_worker("kicad-drc", "b.kicad_pcb", b"(kicad_pcb)")
    assert env["ok"] is False and env["error"]["code"] == "worker-unconfigured"


def _design_input(schematic: str = SCHEMATIC) -> dict:
    return {"record": {"title": "Sensor board", "kind": "electrical",
                       "user_needs": ["The board must read temperature to within 0.5 C."],
                       "attachments": [{"name": "sensor.kicad_sch", "content": schematic, "doc_class": "controlled", "doc_type": "schematic"},
                                       {"name": "bracket.FCStd", "content_b64": __import__("base64").b64encode(fcstd_bytes()).decode(),
                                        "doc_class": "controlled", "doc_type": "model"}]}}


def test_design_release_end_to_end(hub, fake_worker):
    release_all(hub)
    k = hub.kernel
    from hubproto import inventory

    resistor = inventory.create_part(k, title="10k 0603", mpn="RC0603FR-0710KL", manufacturer="Yageo", by="alice")
    inventory.receive(k, part=resistor, quantity=1, location=inventory.location_of_kind(k, "MAIN", "store")["locator"], by="alice")
    run_id = hub.runner.submit("design-release", _design_input(), owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting", [e for e in hub.runner.track(run_id) if e["type"] in ("run.failed", "step.result")]
    design = k.get(state["record"])
    assert design["type"] == "design" and design["state"] == "awaiting-review-the-design"
    assert state["pending"]["assignees"] == ["bob"] and state["pending"]["due"]
    docs = {d["fields"]["doc_type"]: d for d in hub.dms.documents_of(design["locator"])}
    assert {"schematic", "model", "bom", "schematic-pdf", "erc-report", "model-step", "model-properties"} <= set(docs)
    assert docs["bom"]["fields"]["doc_class"] == "evidence" and docs["bom"]["fields"]["provenance"]["source"] == docs["schematic"]["locator"]
    f = design["fields"]
    assert [l["value"] for l in f["bom_resolved"]] == ["10k"] and f["bom_resolved"][0]["short"] == 1
    unresolved = {l["value"]: l for l in f["bom_unresolved"]}
    assert set(unresolved) == {"100n", "Bracket"}, "the capacitor and the FreeCAD bracket are unknown parts"
    assert unresolved["100n"]["proposal"]["fields"]["mpn"] == "CL10B104KB8NNNC" and unresolved["Bracket"]["mpn"] == "PN-BRK-01"
    assert f["checks"]["erc"] == {"ran": True, "errors": 0, "warnings": 0}
    assert f["parts"][0]["part_number"] == "PN-BRK-01"
    with pytest.raises(RunError, match="not an assignee"):
        hub.runner.decide(run_id, "alice", "approve", meaning="ok", payload_digest=seen(hub.runner, run_id))
    with pytest.raises(RunError, match="requirements not accepted"):
        hub.runner.decide(run_id, "bob", "approve", meaning="too early", payload_digest=seen(hub.runner, run_id))
    for req in k.query(type_="requirement"):
        rr = hub.runner.run_for_record(req["locator"])
        hub.runner.decide(rr["id"], "bob", "approve", meaning="accepted", fields={"acceptance_criteria": "ATP-1", "verification_method": "test"}, payload_digest=seen(hub.runner, rr["id"]))
    state = hub.runner.decide(run_id, "bob", "approve", meaning="design package reviewed", payload_digest=seen(hub.runner, run_id))
    assert state["status"] == "completed"
    design = k.get(design["locator"])
    assert design["state"] == "released" and design["fields"]["part"]
    part = k.get(design["fields"]["part"])
    assert part["type"] == "part" and part["state"] == "released" and part["fields"]["revision"] == "A"
    labels = {(l["rel"], l["target"]) for l in part["links"]}
    assert ("defined-by", design["locator"]) in labels
    assert ("realized-by", resistor["locator"]) in labels, "the assembly is made of its BOM's parts"
    snapshot = [d for d in hub.dms.documents_of(design["locator"]) if d["fields"]["doc_type"] == "snapshot"]
    assert snapshot and b"design package reviewed" in hub.dms.content(snapshot[-1]["locator"])[0]


def test_design_rule_errors_stop_the_run_before_review(hub, fake_worker):
    release_all(hub)
    run_id = hub.runner.submit("design-release", _design_input(SCHEMATIC + "\nFAKE_ERROR\n"), owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "failed"
    result = next(e for e in hub.runner.track(run_id) if e["type"] == "step.result" and e["step"] == "export-the-design")
    assert any(p["check"] == "erc" and p["severity"] == "error" for p in result["problems"])
    assert hub.kernel.get(state["record"])["state"] == "failed"


def test_design_release_without_a_worker_still_reaches_review(hub, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    release_all(hub)
    run_id = hub.runner.submit("design-release", _design_input(), owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting"
    result = next(e for e in hub.runner.track(run_id) if e["type"] == "step.result" and e["step"] == "export-the-design")
    assert all(p["severity"] == "warning" for p in result["problems"]) and any("not set" in p["detail"] for p in result["problems"])
    design = hub.kernel.get(state["record"])
    assert design["fields"]["checks"]["erc"]["ran"] is False and design["fields"]["bom_lines"]
