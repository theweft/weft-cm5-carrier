# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""KiCad symbol libraries and a schematic writer for generated circuits.

Every symbol pin ends at a global label with its net name (or a no-connect flag), so a sheet is a
readable, ERC-checkable view of the circuit described in code; KiCad connects sheets by label.
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


POWER_NETS = {"GND", "SUP_3V3", "SUP_5V", "+3V3_CM5", "+5V_CM5", "+1V8_CM5", "ESP_3V3", "VBUS_IN"}


def _group(boxes):
    """Orders units so each IC is followed by the small parts wired to it."""
    def nets(part):
        return set(part.nets.values()) - POWER_NETS

    big = [b for b in boxes if len(b[0].nets) > 3 or b[0].ref[0] in "UJ"]
    small = [b for b in boxes if b not in big]
    big.sort(key=lambda b: -(b[6] - b[5]))
    follow = {id(b): [] for b in big}
    rest = []
    for s in small:
        best = max(big, key=lambda b: len(nets(b[0]) & nets(s[0])), default=None)
        if best is not None and nets(best[0]) & nets(s[0]):
            follow[id(best)].append(s)
        else:
            rest.append(s)
    out = []
    for b in big:
        out.append(b)
        out.extend(follow[id(b)])
    return out + rest


def _label_dir(angle):
    # A pin's angle points from its connection point to the body; the label goes the other way.
    return {0: 180, 90: 270, 180: 0, 270: 90}[int(angle) % 360]


def write_sheet(sheet, lib, root_uuid, sheet_uuid, path):
    placed = []
    lib_ids = sorted({p.lib_id for p, _ in sheet.items})
    # Layout: shelf packing, units sorted by height.
    boxes = []
    for part, unit in sheet.items:
        pins = [p for p in lib.pins(part.lib_id) if p[3] in (0, unit) and p[4] in (0, 1)]
        if not pins:
            continue
        xs = [p[5] for p in pins] or [0]
        ys = [p[6] for p in pins] or [0]
        lab = max((len(part.net_of(p[0]) or "") for p in pins), default=4) * 1.0 + 6
        x0, x1 = min(xs) - lab, max(xs) + lab
        y0, y1 = min(ys) - lab * 0.6, max(ys) + lab * 0.6
        boxes.append((part, unit, pins, x0, x1, y0, y1))
    boxes = _group(boxes)
    width = max(420.0, min(1600.0, sum((b[4] - b[3]) for b in boxes) ** 0.5 * 18))
    margin, cx, cy, shelf = 20.0, 20.0, 30.0, 0.0
    for part, unit, pins, x0, x1, y0, y1 in boxes:
        w, h = x1 - x0, y1 - y0
        if cx + w > width - margin and cx > margin:
            cx, cy, shelf = margin, cy + shelf + 10, 0.0
        ox, oy = snap(cx - x0), snap(cy + y1)  # symbol origin; lib y is up
        placed.append((part, unit, pins, ox, oy))
        cx += w + 8
        shelf = max(shelf, h)
    height = cy + shelf + 30
    paper = S("paper", "User", round(width, 1), round(max(297.0, height), 1))

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
    for part, unit, pins, ox, oy in placed:
        sym_uuid = uid("sym", part.ref, unit)
        node = S(
            "symbol",
            S("lib_id", part.lib_id),
            S("at", ox, oy, 0),
            S("unit", unit),
            S("exclude_from_sim", Sym("no")),
            S("in_bom", Sym("no" if part.dnp_bom else "yes")),
            S("on_board", Sym("yes")),
            S("dnp", Sym("yes" if part.dnp else "no")),
            S("uuid", sym_uuid),
            _prop("Reference", part.ref, ox, oy - 3),
            _prop("Value", part.value, ox, oy + 3),
            _prop("Footprint", part.footprint, ox, oy, hide=True),
            _prop("Datasheet", "", ox, oy, hide=True),
        )
        for k, v in sorted(part.fields.items()):
            node.append(_prop(k, v, ox, oy, hide=True))
        for num in sorted({p[0] for p in lib.pins(part.lib_id)}):
            node.append(S("pin", num, S("uuid", uid("pin", part.ref, num))))
        node.append(S("instances", S("project", PROJECT, S("path", inst_path, S("reference", part.ref), S("unit", unit)))))
        body.append(node)
        for num, name, ptype, _, _, px, py, angle, _ in pins:
            x, y = round(ox + px, 4), round(oy - py, 4)
            net = part.net_of(num)
            if net is None:
                if ptype != "no_connect":
                    body.append(S("no_connect", S("at", x, y), S("uuid", uid("nc", part.ref, num, unit))))
                continue
            d = _label_dir(angle)
            just = "left" if d in (0, 90) else "right"
            body.append(
                S(
                    "global_label",
                    net,
                    S("shape", Sym("passive")),
                    S("at", x, y, d),
                    S("fields_autoplaced", Sym("yes")),
                    S("effects", S("font", S("size", 1.27, 1.27)), S("justify", Sym(just))),
                    S("uuid", uid("gl", part.ref, num, unit)),
                    _prop("Intersheetrefs", "${INTERSHEET_REFS}", x, y, hide=True),
                )
            )
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
