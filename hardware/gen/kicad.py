# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""KiCad symbol libraries and a schematic writer for generated circuits.

Sheets are drawn per chip: see the layout notes above write_sheet. Nets connect by global labels
and power symbols, so every net keeps its name across sheets and in the PCB.
"""

import os
import re
import uuid as uuidlib

import sexp
from sexp import S, Sym

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(HERE, "..", "lib")
PROJECT = "weft-cm5-carrier"
NAMESPACE = uuidlib.UUID("6b1f3d2e-8c55-4a9b-9a51-0c5a7e2d4f10")


def uid(*parts):
    """Stable UUID from a name, so regenerating does not churn the files."""
    return str(uuidlib.uuid5(NAMESPACE, "/".join(str(p) for p in parts)))


def kicad_symbol_dir():
    path = os.environ.get("KICAD10_SYMBOL_DIR") or os.environ.get("KICAD_SYMBOL_DIR")
    if not path:
        raise SystemExit("KICAD10_SYMBOL_DIR is not set (run inside `nix develop`).")
    return path


class Library:
    """Loads symbols from KiCad's libraries and the project's own libraries in lib/."""

    def __init__(self):
        self._libs = {}

    def _lib(self, name):
        if name not in self._libs:
            local = os.path.join(LIB_DIR, f"{name}.kicad_sym")
            path = local if os.path.exists(local) else os.path.join(kicad_symbol_dir(), f"{name}.kicad_sym")
            tree = sexp.parse(open(path, encoding="utf-8").read())
            self._libs[name] = {s[1]: s for s in sexp.find(tree, "symbol")}
        return self._libs[name]

    def flat(self, lib_id):
        """The symbol as a schematic embeds it: named `lib:name`, `extends` resolved."""
        lib, name = lib_id.split(":", 1)
        sym = self._lib(lib)[name]
        parent = sexp.value(sym, "extends")
        if parent is None:
            out = _rename(sym, name, name)
        else:
            base = self.flat(f"{lib}:{parent}")
            out = _rename(base, parent, name)
            out = [c for c in out if not (isinstance(c, list) and c and c[0] == "property")]
            props = [c for c in sym if isinstance(c, list) and c and c[0] == "property"]
            # Properties of the derived symbol replace the parent's; insert before sub-symbols.
            head = [c for c in out if not (isinstance(c, list) and c and c[0] == "symbol")]
            subs = [c for c in out if isinstance(c, list) and c and c[0] == "symbol"]
            out = head + props + subs
        out = list(out)
        out[1] = lib_id
        return out

    def pins(self, lib_id):
        """[(number, name, type, unit, body_style, x, y, angle, length)] of a symbol."""
        sym = self.flat(lib_id)
        result = []
        for sub in sexp.find(sym, "symbol"):
            m = re.search(r"_(\d+)_(\d+)$", sub[1])
            unit, style = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            for p in sexp.find(sub, "pin"):
                at = sexp.first(p, "at")
                length = sexp.value(p, "length")
                result.append(
                    (
                        sexp.value(p, "number"),
                        sexp.value(p, "name"),
                        str(p[1]),
                        unit,
                        style,
                        float(at[1]),
                        float(at[2]),
                        float(at[3]) if len(at) > 3 else 0.0,
                        float(length) if length is not None else 2.54,
                    )
                )
        return result

    def units(self, lib_id):
        units = {u for (_, _, _, u, _, *_) in self.pins(lib_id)}
        return sorted(u for u in units if u > 0) or [1]

    def default_footprint(self, lib_id):
        for p in sexp.find(self.flat(lib_id), "property"):
            if p[1] == "Footprint":
                return p[2]
        return ""


def _rename(sym, old, new):
    """Copy of a symbol with sub-symbol names `old_u_s` renamed to `new_u_s`."""
    out = []
    for c in sym:
        if isinstance(c, list) and c and c[0] == "symbol":
            sub = list(c)
            sub[1] = new + sub[1][len(old):] if sub[1].startswith(old) else sub[1]
            out.append(sub)
        elif isinstance(c, list) and c and c[0] == "extends":
            continue
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------- schematic writer

FONT = S("effects", S("font", S("size", 1.27, 1.27)))
GRID = 2.54


def snap(v):
    return round(v / 1.27) * 1.27


def _prop(name, val, x, y, hide=False, angle=0):
    eff = S("effects", S("font", S("size", 1.27, 1.27)))
    if hide:
        eff.append(S("hide", Sym("yes")))
    return S("property", name, val, S("at", x, y, angle), eff)


class Sheet:
    """One schematic sheet: placed symbol units with labels on every pin."""

    def __init__(self, name, filename, title):
        self.name, self.filename, self.title = name, filename, title
        self.items = []  # (part, unit)

    def add(self, part):
        part.sheet = self
        for unit in part.circuit.lib.units(part.lib_id):
            self.items.append((part, unit))


# ---------------------------------------------------------------- sheet layout
#
# A sheet is a set of clusters, one per chip or connector (a symbol unit with more than three pins):
# the unit in the middle, its pins ending in global labels or power symbols; the resistors and
# capacitors that sit on one of its signal pins hang off that pin on a short wire; its decoupling
# capacitors stand in a row below it; any other small part wired to it stacks on its right. A dashed
# frame with the reference and value surrounds each cluster. Nets keep global labels (not local ones),
# so every net keeps the name the PCB and the design rules refer to.

# Nets drawn as power symbols instead of labels.
POWER_NETS = {"GND", "SUP_3V3", "SUP_5V", "+3V3_CM5", "+5V_CM5", "+1V8_CM5", "ESP_3V3", "VBUS_IN",
              "VBUS_LAB", "+1V1_SUP", "GPIO_VREF", "VBAT", "+5V"}

CHAR = 1.0       # width of one character at 1.27 mm text, roughly
DIRS = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # label angle -> screen direction
HANG = 7.62      # length of a two-pin passive (Device:R, Device:C)
_pwr_count = [0]


def _label_len(net):
    return len(net) * CHAR + 3.0


def _rot(x, y, a):
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[int(a) % 360]


def _label_dir(angle):
    # A pin's angle points from its connection point to the body; the label goes the other way.
    return {0: 180, 90: 270, 180: 0, 270: 90}[int(angle) % 360]


def _unit_pins(lib, part, unit):
    return [p for p in lib.pins(part.lib_id) if p[3] in (0, unit) and p[4] in (0, 1)]


class Cluster:
    """Drawing items in cluster-local coordinates (mm, y down) and their bounding boxes."""

    def __init__(self, title):
        self.title = title
        self.syms, self.labels, self.powers, self.wires, self.ncs, self.boxes = [], [], [], [], [], []

    def box(self, x0, y0, x1, y1):
        self.boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def bbox(self):
        b = self.boxes
        return (min(r[0] for r in b), min(r[1] for r in b), max(r[2] for r in b), max(r[3] for r in b))

    def place(self, part, unit, pins, ox, oy, rot, fields):
        """Places a unit; returns its pins as (number, name, type, x, y, outward direction)."""
        out, xs, ys = [], [], []
        for num, name, ptype, _, _, px, py, ang, length in pins:
            rx, ry = _rot(px, py, rot)
            x, y = round(ox + rx, 4), round(oy - ry, 4)
            d = _label_dir(ang + rot)
            ux, uy = DIRS[d]
            out.append((num, name, ptype, x, y, d))
            xs += [x, x - ux * length]
            ys += [y, y - uy * length]
        if not xs:
            xs, ys = [ox - 2.54, ox + 2.54], [oy - 2.54, oy + 2.54]
        body = (min(xs), min(ys), max(xs), max(ys))
        self.box(*body)
        fl = fields(body)
        for name, fx, fy, just in fl:
            n = len(part.ref if name == "Reference" else part.value) * CHAR
            x0 = fx - n if "right" in just else fx if "left" in just else fx - n / 2
            y0 = fy - 1.27 if "bottom" in just else fy if "top" in just else fy - 0.65
            self.box(x0, y0, x0 + n, y0 + 1.3)
        self.syms.append((part, unit, ox, oy, rot, fl))
        return out, body

    def terminate(self, part, unit, num, ptype, x, y, d, upright=False, supplies=True):
        net = part.net_of(num)
        if net is None:
            if ptype != "no_connect":
                self.ncs.append((x, y, part.ref, num, unit))
            return
        if net in POWER_NETS and part.lib_id != "power:PWR_FLAG" and (supplies or net == "GND"):
            self.power(net, x, y, d, upright, outward=d if upright and d in (0, 180) else None)
        else:
            self.label(net, x, y, d, (part.ref, num, unit))

    def label(self, net, x, y, d, key):
        ux, uy = DIRS[d]
        n = _label_len(net)
        self.labels.append((net, x, y, d, key))
        self.box(x, y - 1.0, x + ux * n, y + 1.0) if uy == 0 else self.box(x - 1.0, y, x + 1.0, y + uy * n)

    def power(self, net, x, y, d, upright, outward=None):
        gnd = net == "GND"
        pd = (270 if gnd else 90) if upright else d
        rot = (pd + 90) % 360 if gnd else (pd - 90) % 360
        ux, uy = DIRS[pd]
        tip = 3.2 if gnd else 2.6
        tx, ty = x + ux * (tip + 0.6), y + uy * (tip + 0.6)
        n = 0 if gnd else len(net) * CHAR
        if uy and outward is not None:  # upright at a horizontal part's end: text away from the part
            ox = DIRS[outward][0]
            tx = x + ox * 1.0
            just = ("right" if ox < 0 else "left", "bottom" if uy < 0 else "top")
            self.box(x, y, tx + ox * n, ty + uy * 1.6)
        elif uy:  # text centred under or over the tip
            just = ("bottom",) if uy < 0 else ("top",)
            self.box(x - max(1.6, n / 2), y, x + max(1.6, n / 2), ty + uy * 1.6)
        else:
            just = ("left",) if ux > 0 else ("right",)
            self.box(x, y - 1.6, tx + ux * n, y + 1.6)
        self.powers.append((net, x, y, rot, tx, ty, just))


def _fields_ic(body):
    return []  # the frame title names the chip


def _fields_right(body):
    x0, y0, x1, y1 = body
    cy = (y0 + y1) / 2
    return [("Reference", x1 + 0.8, cy - 1.27, ("left",)), ("Value", x1 + 0.8, cy + 1.27, ("left",))]


def _fields_left(body):
    x0, y0, x1, y1 = body
    cy = (y0 + y1) / 2
    return [("Reference", x0 - 0.8, cy - 1.27, ("right",)), ("Value", x0 - 0.8, cy + 1.27, ("right",))]


def _fields_over(body):
    x0, y0, x1, y1 = body
    cx = (x0 + x1) / 2
    # Centred text: top/bottom justification would turn with the rotated symbol.
    return [("Reference", cx, y0 - 1.5, ()), ("Value", cx, y1 + 1.5, ())]


def _two_pin(lib, part, unit):
    pins = _unit_pins(lib, part, unit)
    return pins if len(pins) == 2 and part.ref[0] in "RCLDF" else None


def _anchor_cluster(lib, part, unit, members, decaps):
    pins = _unit_pins(lib, part, unit)
    cl = Cluster(f"{part.ref}  {part.value}")
    spins, body = cl.place(part, unit, pins, 0, 0, 0, _fields_ic)
    # Which members hang on a wire from a side pin: one per pin, the first that fits.
    by_net = {}
    for num, _, _, x, y, d in spins:
        net = part.net_of(num)
        if net is not None:
            by_net.setdefault(net, []).append((num, x, y, d))
    hung, spans, stacked = {}, {0: [], 180: []}, []
    for m, mu in members:
        mp = _two_pin(lib, m, mu)
        done = False
        if mp:
            for num, *_ in mp:
                net = m.net_of(num)
                if net is None or net in POWER_NETS or len(by_net.get(net, ())) != 1:
                    continue
                anum, ax, ay, ad = by_net[net][0]
                if ad not in (0, 180) or anum in hung:
                    continue
                other = m.net_of(next(p[0] for p in mp if p[0] != num))
                up = other is not None and other in POWER_NETS and other != "GND"
                span = (ay - HANG - 6, ay + 1.5) if up else (ay - 1.5, ay + HANG + 6)
                if any(not (span[1] <= s[0] or span[0] >= s[1]) for s in spans[ad]):
                    continue
                spans[ad].append(span)
                hung[anum] = (m, mu, mp, num, up)
                done = True
                break
        if not done:
            stacked.append((m, mu))
    # Terminate the pins that do not carry a hanging part; then the side extents are known.
    side_ext = {0: body[2], 180: body[0]}
    for num, _, ptype, x, y, d in spins:
        if num in hung:
            continue
        cl.terminate(part, unit, num, ptype, x, y, d, supplies=False)
    for b in cl.boxes:
        side_ext[0], side_ext[180] = max(side_ext[0], b[2]), min(side_ext[180], b[0])
    for num, _, _, x, y, d in spins:
        if num not in hung:
            continue
        m, mu, mp, mnum, up = hung[num]
        px = snap(side_ext[d] + (5.08 if d == 0 else -5.08))
        px = round(px / 2.54) * 2.54
        cl.wires.append((x, y, px, y))
        cl.box(min(x, px), y - 0.5, max(x, px), y + 0.5)
        net = part.net_of(num)
        cl.label(net, px, y, d, (part.ref, num, unit))
        # Vertical passive: the connected pin at (px, y), the body above (pull-up) or below.
        for rot in (0, 180):
            rx, ry = _rot(*next((p[5], p[6]) for p in mp if p[0] == mnum), rot)
            if (ry < 0) == up:  # connected pin at the bottom for a pull-up, at the top otherwise
                break
        oy = y + ry
        ox = px - rx
        fields = _fields_right if d == 0 else _fields_left
        mspins, _ = cl.place(m, mu, mp, ox, oy, rot, fields)
        for n2, _, pt2, x2, y2, d2 in mspins:
            if n2 == mnum:
                continue
            net2 = m.net_of(n2)
            if net2 is not None and net2 not in POWER_NETS:
                cl.terminate(m, mu, n2, pt2, x2, y2, d)  # a label pointing away from the chip
            else:
                cl.terminate(m, mu, n2, pt2, x2, y2, d2, upright=True)
    _stack(lib, cl, stacked)
    _decap_row(lib, cl, decaps)
    return cl


def _stack(lib, cl, items, x0=None):
    """Small parts in a column right of what the cluster holds so far, labels on every pin."""
    if not items:
        return
    bb = cl.bbox() if cl.boxes else (0, 0, 0, 0)
    x = bb[2] + 10.16 if x0 is None else x0
    y = bb[1]
    for m, mu in items:
        mp = _two_pin(lib, m, mu)
        pins = mp or _unit_pins(lib, m, mu)
        # Lay out once at the origin to learn the extent, then shift into place.
        probe = Cluster("")
        _small(lib, probe, m, mu, pins, bool(mp), 0, 0)
        pb = probe.bbox()
        ox, oy = snap(x - pb[0]), snap(y - pb[1])
        ox, oy = round(ox / 2.54) * 2.54, round(oy / 2.54) * 2.54
        _small(lib, cl, m, mu, pins, bool(mp), ox, oy)
        y = oy + pb[3] + 2.54


def _small(lib, cl, m, mu, pins, horizontal, ox, oy):
    if horizontal:
        rot = 90
        spins, _ = cl.place(m, mu, pins, ox, oy, rot, _fields_over)
        for num, _, ptype, x, y, d in spins:
            cl.terminate(m, mu, num, ptype, x, y, d, upright=True)
    else:
        spins, _ = cl.place(m, mu, pins, ox, oy, 0, _fields_right)
        for num, _, ptype, x, y, d in spins:
            cl.terminate(m, mu, num, ptype, x, y, d)


def _decap_row(lib, cl, decaps):
    """Decoupling capacitors standing in rows under the cluster: supply on top, ground below."""
    if not decaps:
        return
    bb = cl.bbox()
    width = max(bb[2] - bb[0], 60.0)
    x, y = round(bb[0] / 2.54) * 2.54 + 2.54, round((bb[3] + 12.7) / 2.54) * 2.54
    for m, mu in decaps:
        pins = _unit_pins(lib, m, mu)
        if x > bb[0] + width:
            x, y = round(bb[0] / 2.54) * 2.54 + 2.54, y + 25.4
        top = max(pins, key=lambda p: p[6])
        if m.net_of(top[0]) == "GND":  # supply pin to the top
            rot = 180
        else:
            rot = 0
        spins, _ = cl.place(m, mu, pins, x, y, rot, _fields_right)
        for num, _, ptype, px, py, d in spins:
            cl.terminate(m, mu, num, ptype, px, py, d, upright=True)
        x += 12.7


def _clusters(lib, items):
    """Groups a sheet's units into clusters: anchors with their passives, decoupling, the rest."""
    def signal_nets(part):
        return set(part.nets.values()) - POWER_NETS

    anchors = [(p, u) for p, u in items if len(p.nets) > 3 or p.ref[0] in "UJ"]
    order = {id(p): i for i, (p, _) in enumerate(items)}
    members = {(p.ref, u): [] for p, u in anchors}
    decaps = {(p.ref, u): [] for p, u in anchors}
    rest, rest_decaps = [], []

    def unit_nets(p, u):
        return {p.net_of(x[0]) for x in _unit_pins(lib, p, u)} - {None}

    anets = {(p.ref, u): unit_nets(p, u) for p, u in anchors}
    for p, u in items:
        if (p, u) in anchors:
            continue
        nets = set(p.nets.values())
        if p.ref.startswith("C") and len(p.nets) == 2 and nets <= POWER_NETS and nets != {"GND"}:
            supply = next(iter(nets - {"GND"}))
            cand = [a for a in anchors if supply in anets[(a[0].ref, a[1])]]
            before = [a for a in cand if order[id(a[0])] < order[id(p)]]
            pick = max(before, key=lambda a: order[id(a[0])]) if before else (cand[0] if cand else None)
            (decaps[(pick[0].ref, pick[1])] if pick else rest_decaps).append((p, u))
            continue
        sig = signal_nets(p)
        scored = [(len(sig & anets[(a[0].ref, a[1])]), -abs(order[id(a[0])] - order[id(p)]), a) for a in anchors]
        best = max(scored, key=lambda s: (s[0], s[1]), default=None)
        if best and best[0] > 0:
            members[(best[2][0].ref, best[2][1])].append((p, u))
        else:
            rest.append((p, u))
    out = [_anchor_cluster(lib, p, u, members[(p.ref, u)], decaps[(p.ref, u)]) for p, u in anchors]
    if rest or rest_decaps:
        cl = Cluster("Other parts")
        _stack(lib, cl, rest, x0=0)
        if not cl.boxes:
            cl.box(0, 0, 1, 1)
        _decap_row(lib, cl, rest_decaps)
        out.append(cl)
    return out


PAPERS = [("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]


def _pack(clusters):
    """Shelf packing in circuit order onto the smallest paper that holds the sheet."""
    sizes = []
    for cl in clusters:
        b = cl.bbox()
        sizes.append((b[2] - b[0] + 10.16, b[3] - b[1] + 15.24))  # frame margin and title
    for name, pw, ph in PAPERS + [("User", 1600, 1200), ("User", 2400, 1800)]:
        x0, y0, x1, y1 = 25.4, 25.4, pw - 20, ph - 45
        x, y, shelf, pos, ok = x0, y0, 0.0, [], True
        for w, h in sizes:
            if x + w > x1 and x > x0:
                x, y, shelf = x0, y + shelf + 7.62, 0.0
            if x + w > x1 or y + h > y1:
                ok = False
                break
            pos.append((x, y))
            x += w + 7.62
            shelf = max(shelf, h)
        if ok:
            offs = []
            for cl, (px, py) in zip(clusters, pos):
                b = cl.bbox()
                offs.append((round((px + 5.08 - b[0]) / 2.54) * 2.54, round((py + 12.7 - b[1]) / 2.54) * 2.54))
            return (name, pw, ph), offs
    raise ValueError("sheet does not fit on any paper")


def _effects(size=1.27, just=(), bold=False, hide=False):
    font = S("font", S("size", size, size))
    if bold:
        font.append(S("bold", Sym("yes")))
    eff = S("effects", font)
    if just:
        eff.append(S("justify", *[Sym(j) for j in just]))
    if hide:
        eff.append(S("hide", Sym("yes")))
    return eff


def write_sheet(sheet, lib, root_uuid, sheet_uuid, path):
    clusters = _clusters(lib, sheet.items)
    (pname, pw, ph), offs = _pack(clusters)
    lib_ids = sorted({p.lib_id for p, _ in sheet.items} | {"power:GND", "power:VCC"})
    paper = S("paper", pname) if pname != "User" else S("paper", "User", pw, ph)
    body = [
        Sym("kicad_sch"),
        S("version", Sym("20250114")),
        S("generator", "weft-cm5-carrier"),
        S("generator_version", "9.0"),
        S("uuid", sheet_uuid),
        paper,
        S("title_block", S("title", sheet.title), S("company", "The weft authors"),
          S("comment", Sym("1"), "Generated by hardware/gen; do not edit by hand.")),
        [Sym("lib_symbols")] + [lib.flat(i) for i in lib_ids],
    ]
    inst_path = f"/{root_uuid}/{sheet_uuid}" if sheet_uuid != root_uuid else f"/{root_uuid}"
    for ci, (cl, (dx, dy)) in enumerate(zip(clusters, offs)):
        r = lambda v: round(v, 4)
        b = cl.bbox()
        fx0, fy0, fx1, fy1 = b[0] + dx - 2.54, b[1] + dy - 2.54, b[2] + dx + 2.54, b[3] + dy + 2.54
        body.append(S("rectangle", S("start", r(fx0), r(fy0 - 5.08)), S("end", r(fx1), r(fy1)),
                      S("stroke", S("width", 0.254), S("type", Sym("dash")), S("color", 120, 120, 120, 1)),
                      S("fill", S("type", Sym("none"))), S("uuid", uid("frame", sheet.filename, ci))))
        body.append(S("text", cl.title, S("exclude_from_sim", Sym("no")), S("at", r(fx0 + 1.27), r(fy0 - 1.27), 0),
                      _effects(2.0, ("left", "bottom"), bold=True), S("uuid", uid("title", sheet.filename, ci))))
        for part, unit, ox, oy, rot, fields in cl.syms:
            ox, oy = r(ox + dx), r(oy + dy)
            node = S(
                "symbol",
                S("lib_id", part.lib_id),
                S("at", ox, oy, rot),
                S("unit", unit),
                S("exclude_from_sim", Sym("no")),
                S("in_bom", Sym("no" if part.dnp_bom else "yes")),
                S("on_board", Sym("yes")),
                S("dnp", Sym("yes" if part.dnp else "no")),
                S("uuid", uid("sym", part.ref, unit)),
            )
            vals = {"Reference": part.ref, "Value": part.value}
            for name in ("Reference", "Value"):
                if name not in {f[0] for f in fields}:
                    node.append(S("property", name, vals[name], S("at", ox, oy, 0), _effects(hide=True)))
            for name, fx, fy, just in fields:
                node.append(S("property", name, vals[name], S("at", r(fx + dx), r(fy + dy), 90 if rot % 180 else 0),
                              _effects(just=just)))
            node.append(S("property", "Footprint", part.footprint, S("at", ox, oy, 0), _effects(hide=True)))
            node.append(S("property", "Datasheet", "", S("at", ox, oy, 0), _effects(hide=True)))
            for k, v in sorted(part.fields.items()):
                node.append(S("property", k, v, S("at", ox, oy, 0), _effects(hide=True)))
            for num in sorted({p[0] for p in lib.pins(part.lib_id)}):
                node.append(S("pin", num, S("uuid", uid("pin", part.ref, num))))
            node.append(S("instances", S("project", PROJECT, S("path", inst_path, S("reference", part.ref), S("unit", unit)))))
            body.append(node)
        for x1, y1, x2, y2 in cl.wires:
            body.append(S("wire", S("pts", S("xy", r(x1 + dx), r(y1 + dy)), S("xy", r(x2 + dx), r(y2 + dy))),
                          S("stroke", S("width", 0), S("type", Sym("default"))),
                          S("uuid", uid("wire", sheet.filename, ci, x1, y1, x2, y2))))
        for net, x, y, d, key in cl.labels:
            x, y = r(x + dx), r(y + dy)
            just = "left" if d in (0, 90) else "right"
            body.append(S("global_label", net, S("shape", Sym("passive")), S("at", x, y, d),
                          S("fields_autoplaced", Sym("yes")), _effects(just=(just,)),
                          S("uuid", uid("gl", *key)),
                          S("property", "Intersheetrefs", "${INTERSHEET_REFS}", S("at", x, y, 0), _effects(hide=True))))
        for net, x, y, rot, tx, ty, just in cl.powers:
            _pwr_count[0] += 1
            ref = f"#PWR{_pwr_count[0]:04d}"
            lib_id = "power:GND" if net == "GND" else "power:VCC"
            x, y = r(x + dx), r(y + dy)
            body.append(S(
                "symbol", S("lib_id", lib_id), S("at", x, y, rot), S("unit", 1),
                S("exclude_from_sim", Sym("no")), S("in_bom", Sym("yes")), S("on_board", Sym("yes")),
                S("dnp", Sym("no")), S("uuid", uid("pwr", ref)),
                S("property", "Reference", ref, S("at", x, y, 0), _effects(hide=True)),
                S("property", "Value", net, S("at", r(tx + dx), r(ty + dy), 0), _effects(just=just, hide=net == "GND")),
                S("property", "Footprint", "", S("at", x, y, 0), _effects(hide=True)),
                S("property", "Datasheet", "", S("at", x, y, 0), _effects(hide=True)),
                S("pin", "1", S("uuid", uid("pwrpin", ref))),
                S("instances", S("project", PROJECT, S("path", inst_path, S("reference", ref), S("unit", 1)))),
            ))
        for x, y, ref, num, unit in cl.ncs:
            body.append(S("no_connect", S("at", r(x + dx), r(y + dy)), S("uuid", uid("nc", ref, num, unit))))
    body.append(S("sheet_instances", S("path", "/", S("page", "1"))))
    body.append(S("embedded_fonts", Sym("no")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(sexp.dump(body) + "\n")


def write_root(sheets, root_uuid, path, title):
    body = [
        Sym("kicad_sch"),
        S("version", Sym("20250114")),
        S("generator", "weft-cm5-carrier"),
        S("generator_version", "9.0"),
        S("uuid", root_uuid),
        S("paper", "A4"),
        S("title_block", S("title", title), S("company", "The weft authors"),
          S("comment", Sym("1"), "Generated by hardware/gen; do not edit by hand.")),
        [Sym("lib_symbols")],
    ]
    for i, sh in enumerate(sheets):
        x, y = 20 + (i % 4) * 65, 30 + (i // 4) * 45
        su = uid("sheet", sh.filename)
        body.append(
            S(
                "sheet",
                S("at", x, y),
                S("size", 55, 30),
                S("exclude_from_sim", Sym("no")),
                S("in_bom", Sym("yes")),
                S("on_board", Sym("yes")),
                S("dnp", Sym("no")),
                S("stroke", S("width", 0.1524), S("type", Sym("solid"))),
                S("fill", S("color", 0, 0, 0, 0.0)),
                S("uuid", su),
                S("property", "Sheetname", sh.name, S("at", x, y - 1, 0),
                  S("effects", S("font", S("size", 1.27, 1.27)), S("justify", Sym("left"), Sym("bottom")))),
                S("property", "Sheetfile", sh.filename, S("at", x, y + 31, 0),
                  S("effects", S("font", S("size", 1.27, 1.27)), S("justify", Sym("left"), Sym("top")))),
                S("instances", S("project", PROJECT, S("path", f"/{root_uuid}", S("page", str(i + 2))))),
            )
        )
    body.append(S("sheet_instances", S("path", "/", S("page", "1"))))
    body.append(S("embedded_fonts", Sym("no")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(sexp.dump(body) + "\n")
