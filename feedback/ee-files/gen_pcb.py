import uuid, sys
out = sys.argv[1]
NS = uuid.UUID("22345678-1234-5678-1234-567812345678")
def u(s): return str(uuid.uuid5(NS, s))
LAYERS = '''  (layers
    (0 "F.Cu" signal) (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive") (33 "F.Adhes" user "F.Adhesive") (34 "B.Paste" user) (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen") (38 "B.Mask" user) (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings") (41 "Cmts.User" user "User.Comments") (42 "Eco1.User" user "User.Eco1") (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user) (45 "Margin" user) (46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard") (48 "B.Fab" user) (49 "F.Fab" user))
'''
NETS = {"GND": 1, "VDIV": 2, "+3V3": 3}
def fp(ref, val, x, y, n1, n2, mpn, mfr):
    k = "fp" + ref
    def pad(num, px, net):
        return f'    (pad "{num}" smd roundrect (at {px} 0) (size 0.875 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (net {NETS[net]} "{net}") (uuid "{u(k+num)}"))'
    return f'''  (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu") (uuid "{u(k)}") (at {x} {y})
    (property "Reference" "{ref}" (at 0 -1.43 0) (layer "F.SilkS") (uuid "{u(k+"r")}") (effects (font (size 1 1) (thickness 0.15))))
    (property "Value" "{val}" (at 0 1.43 0) (layer "F.Fab") (uuid "{u(k+"v")}") (effects (font (size 1 1) (thickness 0.15))))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(k+"f")}") (effects (font (size 1.27 1.27))))
    (property "Datasheet" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(k+"d")}") (effects (font (size 1.27 1.27))))
    (property "Description" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(k+"e")}") (effects (font (size 1.27 1.27))))
    (property "MPN" "{mpn}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(k+"m")}") (effects (font (size 1.27 1.27))))
    (property "Manufacturer" "{mfr}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(k+"n")}") (effects (font (size 1.27 1.27))))
    (attr smd)
    (fp_line (start -0.237258 -0.5225) (end 0.237258 -0.5225) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "{u(k+"l1")}"))
    (fp_line (start -0.237258 0.5225) (end 0.237258 0.5225) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "{u(k+"l2")}"))
    (fp_rect (start -1.48 -0.73) (end 1.48 0.73) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd") (uuid "{u(k+"c")}"))
{pad("1", -0.8375, n1)}
{pad("2", 0.8375, n2)}
  )'''
def seg(a, b, net, w=0.25):
    return f'  (segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width {w}) (layer "F.Cu") (net {NETS[net]}) (uuid "{u("seg"+str(a)+str(b))}"))'
body = []
body.append(fp("R1", "10k", 100, 100, "+3V3", "VDIV", "RC0603FR-0710KL", "Yageo"))
body.append(fp("R2", "10k", 100, 105, "VDIV", "GND", "RC0603FR-0710KL", "Yageo"))
body.append(fp("C1", "100n", 106, 100, "VDIV", "GND", "CL10B104KB8NNNC", "Samsung"))
# VDIV: R1.2 -> C1.1 (routed), R1.2 -> R2.1 (routed)
body.append(seg((100.8375, 100), (105.1625, 100), "VDIV"))
body.append(seg((100.8375, 100), (100.8375, 102.0), "VDIV")); body.append(seg((100.8375, 102.0), (99.1625, 102.0), "VDIV")); body.append(seg((99.1625, 102.0), (99.1625, 105), "VDIV"))
# Deliberate clearance violation: a +3V3 trace 0.1 mm from the VDIV trace (default clearance 0.2 mm)
body.append(seg((99.1625, 100), (99.1625, 101.65), "+3V3")); body.append(seg((99.1625, 101.65), (97.0, 101.65), "+3V3"))
# GND between R2.2 and C1.2 deliberately left unrouted (unconnected item)
text = f'''(kicad_pcb (version 20240108) (generator "pcbnew") (generator_version "8.0")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (title_block (title "[EE] attack board") (rev "A"))
{LAYERS}  (setup (pad_to_mask_clearance 0) (allow_soldermask_bridges_in_footprints no)
    (pcbplotparams (layerselection 0x00010fc_ffffffff) (plot_on_all_layers_selection 0x0000000_00000000) (disableapertmacros no) (usegerberextensions no) (usegerberattributes yes) (usegerberadvancedattributes yes) (creategerberjobfile yes) (dashed_line_dash_ratio 12.000000) (dashed_line_gap_ratio 3.000000) (svgprecision 4) (plotframeref no) (mode 1) (useauxorigin no) (hpglpennumber 1) (hpglpenspeed 20) (hpglpendiameter 15.000000) (pdf_front_fp_property_popups yes) (pdf_back_fp_property_popups yes) (dxfpolygonmode yes) (dxfimperialunits yes) (dxfusepcbnewfont yes) (psnegative no) (psa4output no) (plotreference yes) (plotvalue yes) (plotfptext yes) (plotinvisibletext no) (sketchpadsonfab no) (subtractmaskfromsilk no) (outputformat 1) (mirror no) (drillshape 1) (scaleselection 1) (outputdirectory "")))
  (net 0 "")
  (net 1 "GND")
  (net 2 "VDIV")
  (net 3 "+3V3")
''' + "\n".join(body) + f'''
  (gr_rect (start 95 96) (end 112 109) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u("edge")}"))
)
'''
open(out, "w").write(text)
print(out, len(text))
