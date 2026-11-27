# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Builds the carrier PCB from the IO Board layout and the generated schematic's netlist.

    python3 pcb.py <netlist> <out.kicad_pcb>

1. Loads the IO Board PCB (hardware/base), keeps every footprint, track and zone whose net is
   unchanged, renames nets that were renamed, and deletes routing of nets the carrier splits.
2. Extends the outline to the right and places the new parts in block regions there; a few parts
   that belong next to IO Board circuits are placed into free space beside them.
3. Adds ground zones in the extension and the 5 V power path (zones and vias).
Routing is a separate step (route.py, Freerouting).
"""

import collections
import math
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sexp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "..", "base", "CM5IO.kicad_pcb")
LIB = os.path.join(HERE, "..", "lib")
MM = pcbnew.FromMM
KEEP = []

# Old outline x 78.975..239.025, y 69.975..160.025; the extension adds 110 mm to the right.
X0, Y0, X1, Y1 = 78.975, 69.975, 349.025, 160.025
EXT = (240.5, 71.0, 348.0, 159.0)

REGIONS = {  # block -> (x0, y0, x1, y1) in the extension
    "Power": (241.0, 112.0, 262.0, 150.0),
    "GPIO path": (241.0, 82.0, 284.0, 112.0),
    "Supervisor": (284.0, 82.0, 318.0, 118.0),
    "USB": (262.0, 118.0, 322.0, 150.0),
    "Control": (318.0, 82.0, 347.0, 132.0),
    "ESP32": (318.0, 132.0, 347.0, 158.0),
    "Headers": (241.0, 71.0, 347.0, 82.0),
}
# Areas the 5 V power path needs (power_path): connector to shunt, shunt and eFuse, and the
# output pour with its via cluster.
POWER_PATH = [(243.5, 146.0, 258.5, 156.0), (252.5, 135.5, 258.5, 149.5), (255.5, 129.5, 264.0, 138.5)]

FIXED = {  # ref -> (x, y, rotation, layer-flip)
    "J11": (247.5, 157.5, 0),  # power USB-C, moved from the IO Board position
    "J301": (262.0, 157.5, 0),  # lab USB-C
    "J302": (281.0, 153.0, 0),  # USB-A (capture)
    "J303": (300.0, 153.0, 0),  # USB-A (spare)
    "U601": (335.5, 146.0, -90),  # ESP32-S3, antenna to the right edge
    "J701": (268.0, 74.5, -90),  # 2x20 CM5 control header along the front edge
    "J702": (309.0, 74.5, -90),  # 2x10 SD and power header
    "J202": (331.0, 74.5, -90),  # supervisor aux 2x4
    "J201": (342.0, 73.5, -90),  # SWD
}


def load_netlist(path):
    tree = sexp.parse(open(path, encoding="utf-8").read())
    comps = {}
    for c in sexp.find(sexp.first(tree, "components"), "comp"):
        ref = sexp.value(c, "ref")
        sp = sexp.first(c, "sheetpath")
        comps[ref] = {
            "footprint": sexp.value(c, "footprint") or "",
            "value": sexp.value(c, "value"),
            "path": sexp.value(sp, "tstamps") + (sexp.value(c, "tstamps") or ""),
            "sheet": sexp.value(sp, "names"),
            "pins": {},
            "near": next((f[2] for f in sexp.find(sexp.first(c, "fields") or [], "field")
                          if len(f) > 2 and f[1][1] == "Near"), None),
        }
    for n in sexp.find(sexp.first(tree, "nets"), "net"):
        name = sexp.value(n, "name")
        if name.startswith("unconnected-"):
            continue
        for node in sexp.find(n, "node"):
            comps[sexp.value(node, "ref")]["pins"][sexp.value(node, "pin")] = name.lstrip("/")
    return comps


def footprint_lib_path(nick):
    if nick in ("weft", "CM5IO"):
        return os.path.join(LIB, f"{nick}.pretty")
    return os.path.join(os.environ["KICAD10_FOOTPRINT_DIR"], f"{nick}.pretty")


def net(board, name, cache={}):
    if name not in cache:
        ni = board.FindNet(name)
        if ni is None:
            ni = pcbnew.NETINFO_ITEM(board, name)
            board.Add(ni)
        cache[name] = ni
    return cache[name]


class Space:
    """Occupied rectangles; finds free spots by spiral search."""

    def __init__(self):
        self.rects = []

    def add(self, r):
        self.rects.append(r)

    def free(self, r):
        x0, y0, x1, y1 = r
        if x0 < X0 + 0.8 or y0 < Y0 + 0.8 or x1 > X1 - 0.8 or y1 > Y1 - 0.8:
            return False
        return all(x1 <= a0 or x0 >= a1 or y1 <= b0 or y0 >= b1 for a0, b0, a1, b1 in self.rects)

    def find(self, w, h, tx, ty, region=None, step=0.5, rmax=60.0):
        r = 0.0
        while r <= rmax:
            n = max(1, int(2 * math.pi * r / step))
            for k in range(n):
                a = 2 * math.pi * k / n
                cx, cy = round((tx + r * math.cos(a)) * 4) / 4, round((ty + r * math.sin(a)) * 4) / 4
                rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if region and not (rect[0] >= region[0] and rect[1] >= region[1]
                                   and rect[2] <= region[2] and rect[3] <= region[3]):
                    continue
                if self.free(rect):
                    return cx, cy
            r += step
        return None


def bbox_mm(fp):
    """Courtyard box (or pads and graphics when a footprint has no courtyard)."""
    cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
    return (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
            pcbnew.ToMM(bb.GetBottom()))


def zone(board, netname, layer, rect, priority=10, solid=True):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net(board, netname))
    ol = z.Outline()
    ol.NewOutline()
    x0, y0, x1, y1 = rect
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ol.Append(MM(x), MM(y))
    z.SetAssignedPriority(priority)
    z.SetMinThickness(MM(0.2))
    z.SetLocalClearance(MM(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if solid else pcbnew.ZONE_CONNECTION_THERMAL)
    board.Add(z)
    return z


def via(board, netname, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    v.SetWidth(MM(0.6))
    v.SetDrill(MM(0.3))
    v.SetNet(net(board, netname))
    board.Add(v)
    return v


def copper_rects(board, exclude_net):
    """Bounding boxes of all copper (pads, tracks, vias) of other nets, on any layer."""
    out = []
    for t in board.GetTracks():
        if t.GetNetname() != exclude_net:
            bb = t.GetBoundingBox()
            out.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                        pcbnew.ToMM(bb.GetBottom())))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != exclude_net:
                bb = p.GetBoundingBox()
                out.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                            pcbnew.ToMM(bb.GetBottom())))
    return out


def power_path(board, fps):
    """USB-C VBUS -> 5 mΩ shunt -> eFuse -> CM5 5 V, in copper zones; 5 A needs no thin tracks.

    The eFuse's IN and OUT are 0.3 mm bars 0.2 mm apart, fed from their ends: VBUS_SHUNT from
    below, +5V_CM5 from above. The CM5's 5 V then runs on inner layer 2 to the IO Board's own
    5 V copper (F.Cu strip and B.Cu zone around x 198..208, y 128..136), joined by via clusters.
    """
    efuse = next(fp for fp in fps.values() if "RPW0010A" in fp.GetFPIDAsString())
    shunt = next(fp for fp in fps.values() if fp.GetValue() == "5m")

    def pad_box(fp, netname):
        bbs = [p.GetBoundingBox() for p in fp.Pads() if p.GetNetname() == netname]
        return (min(pcbnew.ToMM(b.GetLeft()) for b in bbs), min(pcbnew.ToMM(b.GetTop()) for b in bbs),
                max(pcbnew.ToMM(b.GetRight()) for b in bbs), max(pcbnew.ToMM(b.GetBottom()) for b in bbs))

    pin5 = pad_box(efuse, "VBUS_SHUNT")
    pin6 = pad_box(efuse, "+5V_CM5")
    s_out = pad_box(shunt, "VBUS_SHUNT")
    s_in = pad_box(shunt, "VBUS_IN")
    j_in = pad_box(fps["J11"], "VBUS_IN")
    zone(board, "VBUS_SHUNT", pcbnew.F_Cu, (s_out[0] - 0.5, pin5[3] - 0.2, pin5[2] + 0.05, s_out[3] + 0.3))
    zone(board, "VBUS_IN", pcbnew.F_Cu, (min(j_in[0], s_in[0]) - 0.5, s_in[1] - 0.3,
                                         max(j_in[2], s_in[2]) + 0.5, j_in[3] + 0.3))
    out = (pin6[0] - 0.05, pin6[1] - 6.0, pin6[0] + 7.0, pin6[1] + 0.2)
    zone(board, "+5V_CM5", pcbnew.F_Cu, out)
    zone(board, "+5V_CM5", pcbnew.B_Cu, (out[0] + 2.0, out[1], out[2], out[3] - 1.5))
    # Inner layer 2: one zone from the via cluster to the IO Board's 5 V copper.
    for i in range(4):
        for j in range(3):
            via(board, "+5V_CM5", out[0] + 2.8 + i * 1.1, out[1] + 0.8 + j * 1.1)
    # Inner layer 2 from the eFuse to the IO Board's 5 V copper.
    corridor = (197.0, 128.5, out[2], max(out[1] + 4.0, out[3] - 1.5))
    zone(board, "+5V_CM5", pcbnew.In2_Cu, corridor)
    # ... and under the IO Board's B.Cu 5 V zone (x 184..205, y 130..153), where the vias go.
    zone(board, "+5V_CM5", pcbnew.In2_Cu, (184.5, 128.5, 205.0, 152.5), priority=11)
    blocked = copper_rects(board, "+5V_CM5")
    # Existing vias of any net, for drill-to-drill spacing.
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = pcbnew.ToMM(t.GetPosition().x), pcbnew.ToMM(t.GetPosition().y)
            blocked.append((x - 0.4, y - 0.4, x + 0.4, y + 0.4))
    count = 0
    for i in range(26):
        for j in range(29):
            x, y = 185.0 + i * 0.8, 130.5 + j * 0.8
            if all(x + 0.55 <= a or x - 0.55 >= c or y + 0.55 <= b or y - 0.55 >= d for a, b, c, d in blocked):
                via(board, "+5V_CM5", x, y)
                blocked.append((x - 0.3, y - 0.3, x + 0.3, y + 0.3))
                count += 1
    print(f"power path: {count} vias into the IO Board's 5 V copper")
    if count < 10:
        raise SystemExit("power path: fewer than 10 vias into the IO Board's 5 V copper; check the layout")


def improve(board, fps, auto, moves=int(os.environ.get("ANNEAL_MOVES", "600000"))):
    """Simulated annealing over the automatically placed parts (anneal.py)."""
    import anneal

    auto_set = set(auto)
    region = (EXT[0], 78.5, EXT[2], EXT[3])  # the extension below the front headers
    parts = []
    for ref in auto:
        fp = fps[ref]
        x0, y0 = pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)
        rot0 = fp.GetOrientationDegrees()
        shapes = {}
        for rot in (0, 90, 180, 270):
            fp.SetOrientationDegrees(rot)
            fp.SetPosition(pcbnew.VECTOR2I(0, 0))
            pads = [(p.GetNetname(), pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y))
                    for p in fp.Pads() if p.GetNetname()]
            shapes[rot] = (bbox_mm(fp), pads)
        fp.SetOrientationDegrees(rot0)
        fp.SetPosition(pcbnew.VECTOR2I(MM(x0), MM(y0)))
        many = fp.GetPadCount() > 4
        parts.append(anneal.Part(ref, shapes, region, x0, y0, int(round(rot0)) % 360, fp.GetFPIDAsString(),
                                 margin=1.5 if many else 0.5))
    fixed_pads, obstacles = [], []
    for ref, fp in fps.items():
        if ref in auto_set:
            continue
        for p in fp.Pads():
            if p.GetNetname():
                fixed_pads.append((p.GetNetname(), pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)))
        x0, y0, x1, y1 = bbox_mm(fp)
        if x1 > region[0]:
            obstacles.append((x0 - 0.25, y0 - 0.25, x1 + 0.25, y1 + 0.25))
    obstacles.extend(POWER_PATH)
    # Decoupling: a capacitor between a supply and ground is pulled to the closest chip pin of
    # that supply, as placed by the greedy pass.
    by_ref = {p.ref: p for p in parts}
    chips = [p for p in parts if len(p.shapes[0][1]) > 4]
    decaps = {}
    for p in parts:
        nets = {n for n, _, _ in p.shapes[0][1]}
        if len(p.shapes[0][1]) == 2 and "GND" in nets and len(nets) == 2:
            (supply,) = nets - {"GND"}
            best = None
            for c in chips:
                for n, x, y in c.pads():
                    if n == supply:
                        d = abs(x - p.x) + abs(y - p.y)
                        if best is None or d < best[0]:
                            best = (d, c.ref)
            for n, x, y in fixed_pads:
                if n == supply and x > region[0]:
                    d = abs(x - p.x) + abs(y - p.y)
                    if best is None or d < best[0]:
                        best = (d, (x, y))
            if best:
                t = best[1]
                decaps[p.ref] = [(t, 0.0, 0.0)] if isinstance(t, str) else [(None, t[0], t[1])]
    anneal.anneal(parts, fixed_pads, obstacles, decaps, moves=moves)
    for p in parts:
        fp = fps[p.ref]
        fp.SetOrientationDegrees(p.rot)
        fp.SetPosition(pcbnew.VECTOR2I(MM(p.x), MM(p.y)))
    del by_ref


def main(netlist, out):
    comps = load_netlist(netlist)
    board = pcbnew.LoadBoard(BASE)
    old = {}  # (ref, pad) -> old net
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    for ref, fp in fps.items():
        for pad in fp.Pads():
            old[(ref, pad.GetNumber())] = pad.GetNetname()

    # 1. Footprints of the IO Board that the carrier removed (R4, R5).
    removed = [r for r in fps if r not in comps and fps[r].GetPadCount() > 0
               and not r.startswith(("H", "MH")) and fps[r].Pads()[0].GetNetname()]
    print("removed:", removed)
    moved = {"J11"}
    gone = {}  # pad areas of removed and moved footprints: tracks touching them are cut
    for r in removed + sorted(moved):
        for pad in fps[r].Pads():
            bb = pad.GetBoundingBox()
            gone.setdefault(pad.GetNetname(), []).append(
                (pcbnew.ToMM(bb.GetLeft()) - 0.3, pcbnew.ToMM(bb.GetTop()) - 0.3,
                 pcbnew.ToMM(bb.GetRight()) + 0.3, pcbnew.ToMM(bb.GetBottom()) + 0.3))

    # 2. Net mapping: an old net keeps its routing when all its pads land on one new net.
    new_of = collections.defaultdict(list)
    for (ref, padnum), oldnet in old.items():
        if not oldnet or oldnet.startswith("unconnected-") or ref in removed or ref in moved:
            continue
        if ref in comps:
            new_of[oldnet].append(comps[ref]["pins"].get(padnum))
    rename, drop = {}, set()
    for oldnet, news in new_of.items():
        news = [n for n in news if n is not None]  # mechanical pads without a schematic pin
        if not news:
            continue
        if len(set(news)) == 1 and (len(news) > 1 or oldnet not in gone):
            rename[oldnet] = news[0]
        else:
            drop.add(oldnet)  # split, or left with a single pad: reroute
    print("dropped routing of:", sorted(drop))

    j11 = fps["J11"]

    def touches(t, boxes):
        pts = [t.GetPosition()] if t.Type() == pcbnew.PCB_VIA_T else [t.GetStart(), t.GetEnd()]
        return any(b[0] <= pcbnew.ToMM(v.x) <= b[2] and b[1] <= pcbnew.ToMM(v.y) <= b[3]
                   for v in pts for b in boxes)

    for t in list(board.GetTracks()):
        n = t.GetNetname()
        if n in drop or (n in gone and touches(t, gone[n])):
            board.Remove(t)
            KEEP.append(t)  # a removed item's wrapper must outlive the board (SWIG)
            continue
        if n in rename:
            t.SetNet(net(board, rename[n]))
    for r in removed:
        KEEP.append(fps.pop(r))
        board.Remove(KEEP[-1])
    for z in [board.GetArea(i) for i in range(board.GetAreaCount())]:
        n = z.GetNetname()
        if n in rename:
            z.SetNet(net(board, rename[n]))

    # 3. Pad nets and schematic links for the IO Board footprints.
    for ref, fp in fps.items():
        c = comps.get(ref)
        if c is None:
            continue
        fp.SetPath(pcbnew.KIID_PATH(c["path"]))
        symbol_pins = {p for p in c["pins"]}
        for pad in fp.Pads():
            name = c["pins"].get(pad.GetNumber())
            if name:
                pad.SetNet(net(board, name))
            elif pad.GetNumber() in symbol_pins or not old.get((ref, pad.GetNumber())):
                pad.SetNetCode(0)
            # else: a mechanical pad the symbol has no pin for (shield, mounting): keeps its net

    # 4. Outline.
    for d in list(board.GetDrawings()):
        if d.GetLayer() == pcbnew.Edge_Cuts:
            board.Remove(d)
            KEEP.append(d)
    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECT)
    rect.SetStart(pcbnew.VECTOR2I(MM(X0), MM(Y0)))
    rect.SetEnd(pcbnew.VECTOR2I(MM(X1), MM(Y1)))
    rect.SetLayer(pcbnew.Edge_Cuts)
    rect.SetWidth(MM(0.1))
    board.Add(rect)

    # 5. Obstacles: IO Board footprints and copper on the top layer.
    space = Space()
    for ref, fp in fps.items():
        if ref in moved:
            continue
        x0, y0, x1, y1 = bbox_mm(fp)
        space.add((x0 - 0.3, y0 - 0.3, x1 + 0.3, y1 + 0.3))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T or t.GetLayer() == pcbnew.F_Cu:
            bb = t.GetBoundingBox()
            space.add((pcbnew.ToMM(bb.GetLeft()) - 0.2, pcbnew.ToMM(bb.GetTop()) - 0.2,
                       pcbnew.ToMM(bb.GetRight()) + 0.2, pcbnew.ToMM(bb.GetBottom()) + 0.2))

    # 6. New footprints.
    new = {}
    for ref, c in sorted(comps.items()):
        if ref in fps or not c["footprint"]:
            continue
        nick, name = c["footprint"].split(":", 1)
        fp = pcbnew.FootprintLoad(footprint_lib_path(nick), name)
        if fp is None:
            raise SystemExit(f"{ref}: footprint {c['footprint']} not found")
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        fp.SetPath(pcbnew.KIID_PATH(c["path"]))
        board.Add(fp)
        for pad in fp.Pads():
            name = c["pins"].get(pad.GetNumber())
            if name:
                pad.SetNet(net(board, name))
        new[ref] = fp
    j11.SetPosition(pcbnew.VECTOR2I(MM(FIXED["J11"][0]), MM(FIXED["J11"][1])))
    fps.update(new)

    def put(fp, x, y, rot=None):
        if rot is not None:
            fp.SetOrientationDegrees(rot)
        # Centre the footprint's bounding box on (x, y).
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        bx0, by0, bx1, by1 = bbox_mm(fp)
        fp.SetPosition(pcbnew.VECTOR2I(MM(x - (bx0 + bx1) / 2), MM(y - (by0 + by1) / 2)))
        x0, y0, x1, y1 = bbox_mm(fp)
        space.add((x0 - 0.25, y0 - 0.25, x1 + 0.25, y1 + 0.25))

    for ref, (x, y, rot) in FIXED.items():
        if ref in new or ref == "J11":
            put(fps[ref], x, y, rot)

    block_of = {ref: c["sheet"].strip("/") for ref, c in comps.items()}
    pins_of = {ref: c["pins"] for ref, c in comps.items()}
    power_nets = {"GND", "SUP_3V3", "SUP_5V", "+3V3_CM5", "+5V_CM5", "ESP_3V3", "+1V8_CM5", "VBUS_IN"}

    def size(fp):
        x0, y0, x1, y1 = bbox_mm(fp)
        return x1 - x0, y1 - y0

    def anchor_for(ref, placed_only=True):
        mine = set(pins_of[ref].values()) - power_nets
        best, score = None, 0
        for other, pins in pins_of.items():
            if other == ref or other not in fps or other.startswith("#"):
                continue
            if placed_only and other not in placed and other not in FIXED and other not in fps_old:
                continue
            s = len(mine & set(pins.values()))
            big = other[0] in "UJY" or other.startswith("Module")
            if s and (s > score or (s == score and big)):
                best, score = other, s
        return best

    fps_old = {r for r in fps if r not in new}
    placed = set(FIXED)
    # The M.2 card covers this area: keep it free.
    space.add((209.0, Y0, 239.5, Y1))
    for r in POWER_PATH:  # the 5 V power path (power_path below)
        space.add(r)
    # Placement hints from the circuit: "x,y[,rotation]" is fixed, a reference means "next to".
    local = {}
    for ref in new:
        near = comps[ref]["near"]
        if not near:
            continue
        parts = near.split(",")
        if len(parts) >= 3:
            put(new[ref], float(parts[0]), float(parts[1]), float(parts[2]))
            placed.add(ref)
        else:
            local[ref] = near
    order = sorted(new, key=lambda r: (r[0] not in "UJYQ" or r.startswith("JP"), -size(new[r])[0] * size(new[r])[1]))
    for ref in order:
        if ref in placed:
            continue
        fp = new[ref]
        w, h = size(fp)
        big = ref[0] in "UJY" and w * h > 6
        pad = 2.0 if big else 0.4
        if ref in local:
            if "," in local[ref]:
                tx, ty = (float(v) for v in local[ref].split(","))
            else:
                ax0, ay0, ax1, ay1 = bbox_mm(fps[local[ref]])
                tx, ty = (ax0 + ax1) / 2, (ay0 + ay1) / 2
            spot = space.find(w + pad, h + pad, tx, ty, region=(X0, Y0, 239.0, Y1), rmax=40)
        else:
            region = REGIONS[block_of[ref]]
            a = anchor_for(ref)
            if a is not None and not big:
                ax0, ay0, ax1, ay1 = bbox_mm(fps[a])
                tx, ty = (ax0 + ax1) / 2, (ay0 + ay1) / 2
                tx = min(max(tx, region[0]), region[2])
                ty = min(max(ty, region[1]), region[3])
            else:
                tx, ty = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
            spot = space.find(w + pad, h + pad, tx, ty, region=region)
            if spot is None:
                spot = space.find(w + pad, h + pad, tx, ty, region=EXT, rmax=120)
        if spot is None:
            reg = REGIONS.get(block_of[ref], EXT)
            big_rects = [r for r in space.rects if not (r[2] <= reg[0] or r[0] >= reg[2] or r[3] <= reg[1] or r[1] >= reg[3])]
            raise SystemExit(f"no room for {ref} ({w:.1f} x {h:.1f} mm); large obstacles: {big_rects[:20]}")
        put(fp, *spot)
        if big:  # keep a ring free for its passives
            space.rects[-1] = tuple(v + d for v, d in zip(space.rects[-1], (-0.5, -0.5, 0.5, 0.5)))
        placed.add(ref)

    # 6b. Improve the automatic part of the placement by simulated annealing.
    auto = [r for r in order if r not in local and not comps[r]["near"] and r not in FIXED]
    improve(board, fps, auto)

    power_path(board, fps)

    # 7. Ground zones over the extension on all four layers.
    for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(net(board, "GND"))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((238.5, Y0 + 0.5), (X1 - 0.5, Y0 + 0.5), (X1 - 0.5, Y1 - 0.5), (238.5, Y1 - 0.5)):
            ol.Append(MM(x), MM(y))
        z.SetAssignedPriority(1)  # overlaps the IO Board's GND zones (priority 0) at the seam
        z.SetMinThickness(MM(0.2))
        z.SetLocalClearance(MM(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        board.Add(z)
    pcbnew.SaveBoard(out, board)
    print(f"{len(fps)} footprints, {len(new)} new; saved {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
