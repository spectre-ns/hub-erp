import uuid, sys
out = sys.argv[1]
NS = uuid.UUID("12345678-1234-5678-1234-567812345678")
def u(s): return str(uuid.uuid5(NS, s))
ROOT = u("root")
F = '(effects (font (size 1.27 1.27)))'
FH = '(effects (font (size 1.27 1.27)) hide)'
def pin(kind, x, y, rot, name, num, length=2.54):
    return f'(pin {kind} line (at {x} {y} {rot}) (length {length}) (name "{name}" {F}) (number "{num}" {F}))'
lib = f'''  (lib_symbols
    (symbol "Device:R" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.032 0 90) {F})
      (property "Value" "R" (at 0 0 90) {F})
      (property "Footprint" "" (at -1.778 0 90) {FH})
      (property "Datasheet" "~" (at 0 0 0) {FH})
      (symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54) (stroke (width 0.254) (type default)) (fill (type none))))
      (symbol "R_1_1" {pin("passive", 0, 3.81, 270, "~", "1", 1.27)} {pin("passive", 0, -3.81, 90, "~", "2", 1.27)}))
    (symbol "Device:C" (pin_numbers hide) (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 0.635 2.54 0) {F})
      (property "Value" "C" (at 0.635 -2.54 0) {F})
      (property "Footprint" "" (at 0.9652 -3.81 0) {FH})
      (property "Datasheet" "~" (at 0 0 0) {FH})
      (symbol "C_0_1" (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762)) (stroke (width 0.508) (type default)) (fill (type none)))
        (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762)) (stroke (width 0.508) (type default)) (fill (type none))))
      (symbol "C_1_1" {pin("passive", 0, 3.81, 270, "~", "1", 2.794)} {pin("passive", 0, -3.81, 90, "~", "2", 2.794)}))
    (symbol "power:+3V3" (power) (pin_names (offset 0)) (exclude_from_sim yes) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) {FH})
      (property "Value" "+3V3" (at 0 3.556 0) {F})
      (property "Footprint" "" (at 0 0 0) {FH})
      (property "Datasheet" "" (at 0 0 0) {FH})
      (symbol "+3V3_0_1" (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none))))
      (symbol "+3V3_1_1" {pin("power_in", 0, 0, 90, "+3V3", "1", 0)}))
    (symbol "power:GND" (power) (pin_names (offset 0)) (exclude_from_sim yes) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -6.35 0) {FH})
      (property "Value" "GND" (at 0 -3.81 0) {F})
      (property "Footprint" "" (at 0 0 0) {FH})
      (property "Datasheet" "" (at 0 0 0) {FH})
      (symbol "GND_0_1" (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27)) (stroke (width 0) (type default)) (fill (type none))))
      (symbol "GND_1_1" {pin("power_in", 0, 0, 270, "GND", "1", 0)}))
    (symbol "EE:DUALOPAMP" (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 5.08 0) {F})
      (property "Value" "DUALOPAMP" (at 0 -5.08 0) {F})
      (property "Footprint" "" (at 0 0 0) {FH})
      (property "Datasheet" "~" (at 0 0 0) {FH})
      (symbol "DUALOPAMP_1_1" (polyline (pts (xy -5.08 5.08) (xy 5.08 0) (xy -5.08 -5.08) (xy -5.08 5.08)) (stroke (width 0.254) (type default)) (fill (type background)))
        {pin("input", -7.62, 2.54, 0, "+", "3")} {pin("input", -7.62, -2.54, 0, "-", "2")} {pin("output", 7.62, 0, 180, "", "1")})
      (symbol "DUALOPAMP_2_1" (polyline (pts (xy -5.08 5.08) (xy 5.08 0) (xy -5.08 -5.08) (xy -5.08 5.08)) (stroke (width 0.254) (type default)) (fill (type background)))
        {pin("input", -7.62, 2.54, 0, "+", "5")} {pin("input", -7.62, -2.54, 0, "-", "6")} {pin("output", 7.62, 0, 180, "", "7")})
      (symbol "DUALOPAMP_3_1" (polyline (pts (xy -5.08 5.08) (xy 5.08 0) (xy -5.08 -5.08) (xy -5.08 5.08)) (stroke (width 0.254) (type default)) (fill (type background)))
        {pin("power_in", 0, 7.62, 270, "V+", "8")} {pin("power_in", 0, -7.62, 90, "V-", "4")})))
'''
def wire(a, b):
    return f'  (wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) (uuid "{u("w"+str(a)+str(b))}"))'
def junction(p):
    return f'  (junction (at {p[0]} {p[1]}) (diameter 0) (color 0 0 0 0) (uuid "{u("j"+str(p))}"))'
def label(name, p):
    return f'  (label "{name}" (at {p[0]} {p[1]} 0) (fields_autoplaced yes) {F} (uuid "{u("l"+name+str(p))}"))'
def sym(lib_id, ref, value, at, pins, props, unit=1, in_bom="yes", dnp="no"):
    k = f"s{ref}u{unit}"
    s = f'  (symbol (lib_id "{lib_id}") (at {at[0]} {at[1]} 0) (unit {unit}) (exclude_from_sim no) (in_bom {in_bom}) (on_board yes) (dnp {dnp}) (fields_autoplaced yes)\n    (uuid "{u(k)}")\n'
    s += f'    (property "Reference" "{ref}" (at {at[0]+2.54} {at[1]-1.27} 0) {F})\n'
    s += f'    (property "Value" "{value}" (at {at[0]+2.54} {at[1]+1.27} 0) {F})\n'
    for pk, pv in props.items():
        s += f'    (property "{pk}" "{pv}" (at {at[0]} {at[1]} 0) {FH})\n'
    for p in pins:
        s += f'    (pin "{p}" (uuid "{u(k+"p"+p)}"))\n'
    s += f'    (instances (project "ee_attack" (path "/{ROOT}" (reference "{ref}") (unit {unit}))))\n  )'
    return s
R_FP = "Resistor_SMD:R_0603_1608Metric"; C_FP = "Capacitor_SMD:C_0603_1608Metric"
body = []
# +3V3 -> R1 -> VDIV -> R2 -> GND ; C1, C2(DNP), R3(no MPN), R4(mfr mismatch), R5(in_bom no), R6(alternate) all across VDIV/GND
body.append(sym("power:+3V3", "#PWR01", "+3V3", (50.8, 50.8), ["1"], {"Footprint": "", "Datasheet": ""}))
body.append(sym("Device:R", "R1", "10k", (50.8, 60.96), ["1", "2"], {"Footprint": R_FP, "Datasheet": "~", "MPN": "RC0603FR-0710KL", "Manufacturer": "Yageo"}))
body.append(sym("Device:R", "R2", "10k", (50.8, 72.39), ["1", "2"], {"Footprint": R_FP, "Datasheet": "~", "MPN": "rc0603fr-0710kl", "Manufacturer": "Yageo"}))
body.append(sym("power:GND", "#PWR02", "GND", (50.8, 76.2), ["1"], {"Footprint": "", "Datasheet": ""}))
body.append(wire((50.8, 50.8), (50.8, 57.15))); body.append(wire((50.8, 64.77), (50.8, 68.58))); body.append(label("VDIV", (50.8, 66.04)))
xs = [63.5, 76.2, 88.9, 101.6, 114.3, 127.0]
parts = [("Device:C", "C1", "100n", {"Footprint": C_FP, "Datasheet": "~", "MPN": "CL10B104KB8NNNC", "Manufacturer": "Samsung"}, "yes", "no"),
         ("Device:C", "C2", "100n", {"Footprint": C_FP, "Datasheet": "~", "MPN": "CL10B104KB8NNNC", "Manufacturer": "Samsung"}, "yes", "yes"),
         ("Device:R", "R3", "4k7", {"Footprint": R_FP, "Datasheet": "~"}, "yes", "no"),
         ("Device:R", "R4", "10k", {"Footprint": R_FP, "Datasheet": "~", "MPN": "RC0603FR-0710KL", "Manufacturer": "Panasonic"}, "yes", "no"),
         ("Device:R", "R5", "10k", {"Footprint": R_FP, "Datasheet": "~", "MPN": "RC0603FR-0710KL", "Manufacturer": "Yageo"}, "no", "no"),
         ("Device:R", "R6", "10k", {"Footprint": R_FP, "Datasheet": "~", "MPN": "RC0603FR-0710KL", "Manufacturer": "Yageo", "MPN2": "ERJ-3EKF1002V", "Manufacturer2": "Panasonic"}, "yes", "no")]
prev_top, prev_bot = (50.8, 64.77), (50.8, 76.2)
for x, (lid, ref, val, props, inb, dnp) in zip(xs, parts):
    body.append(sym(lid, ref, val, (x, 72.39), ["1", "2"], props, in_bom=inb, dnp=dnp))
    body.append(wire(prev_top, (x, 64.77))); body.append(wire((x, 64.77), (x, 68.58)))
    body.append(wire(prev_bot, (x, 76.2))); body.append(junction(prev_top)); body.append(junction(prev_bot))
    prev_top, prev_bot = (x, 64.77), (x, 76.2)
# U1: three units. Unit 1 follower on VDIV; unit 2 fully unconnected (ERC errors); unit 3 power
OP = {"Footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Datasheet": "~", "MPN": "LM358DR", "Manufacturer": "Texas Instruments"}
body.append(sym("EE:DUALOPAMP", "U1", "LM358", (152.4, 66.04), ["3", "2", "1"], OP, unit=1))
body.append(wire((144.78, 63.5), (140.97, 63.5))); body.append(label("VDIV", (140.97, 63.5)))
body.append(wire((144.78, 68.58), (140.97, 68.58))); body.append(wire((140.97, 68.58), (140.97, 73.66))); body.append(wire((140.97, 73.66), (165.1, 73.66)))
body.append(wire((165.1, 73.66), (165.1, 66.04))); body.append(wire((165.1, 66.04), (160.02, 66.04))); body.append(label("VOUT", (165.1, 66.04)))
body.append(sym("EE:DUALOPAMP", "U1", "LM358", (152.4, 91.44), ["5", "6", "7"], OP, unit=2))
body.append(sym("EE:DUALOPAMP", "U1", "LM358", (182.88, 66.04), ["8", "4"], OP, unit=3))
body.append(sym("power:+3V3", "#PWR03", "+3V3", (182.88, 58.42), ["1"], {"Footprint": "", "Datasheet": ""}))
body.append(sym("power:GND", "#PWR04", "GND", (182.88, 73.66), ["1"], {"Footprint": "", "Datasheet": ""}))
text = f'(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")\n  (uuid "{ROOT}")\n  (paper "A4")\n  (title_block (title "[EE] attack schematic") (rev "A"))\n' + lib + "\n".join(body) + f'\n  (sheet_instances (path "/" (page "1")))\n)\n'
open(out, "w").write(text)
print(out, len(text))
