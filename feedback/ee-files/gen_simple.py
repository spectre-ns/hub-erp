"""Minimal-format schematic (like examples/sensor.kicad_sch) from a parts list: (lib_id, ref, value, footprint, props, unit, in_bom, dnp)."""
import sys, json
out = sys.argv[1]; parts = json.load(open(sys.argv[2]))
lines = ['(kicad_sch (version 20231120) (generator "eeschema")']
for i, p in enumerate(parts):
    lid, ref, val, fp, props = p["lib"], p["ref"], p["value"], p.get("fp", ""), p.get("props", {})
    lines.append(f'  (symbol (lib_id "{lid}") (at {10 + 10 * (i % 20)} {10 + 10 * (i // 20)} 0) (unit {p.get("unit", 1)}) (in_bom {p.get("in_bom", "yes")}) (on_board yes) (dnp {p.get("dnp", "no")})')
    lines.append(f'    (property "Reference" "{ref}" (at 0 0 0))')
    lines.append(f'    (property "Value" "{val}" (at 0 0 0))')
    if fp: lines.append(f'    (property "Footprint" "{fp}" (at 0 0 0))')
    for k, v in props.items(): lines.append(f'    (property "{k}" "{v}" (at 0 0 0))')
    lines.append('  )')
lines.append(')')
open(out, "w").write("\n".join(lines) + "\n"); print(out, len(parts), "symbols")
