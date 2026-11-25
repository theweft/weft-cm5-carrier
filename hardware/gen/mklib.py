# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Writes the project's own symbols (lib/weft.kicad_sym) and footprints (lib/weft.pretty).

Parts here are missing from KiCad's libraries. Pinouts and land patterns are copied from the
makers' datasheets, named in each part's Datasheet property; check them when the part changes.
"""

import os

import sexp
from sexp import S, Sym

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")


def _pin(kind, x, y, angle, name, number):
    return S("pin", Sym(kind), Sym("line"), S("at", x, y, angle), S("length", 2.54),
             S("name", name, S("effects", S("font", S("size", 1.27, 1.27)))),
             S("number", number, S("effects", S("font", S("size", 1.27, 1.27)))))


def box_symbol(name, ref, value, footprint, datasheet, description, left, right, bottom=(), top=()):
    """A rectangle with pins: each side is a list of (number, name, type)."""
    rows = max(len(left), len(right), 1)
    cols = max(len(bottom), len(top), 1)
    w = max(15.24, cols * 2.54 + 5.08)
    h = rows * 2.54 + 2.54
    w = round(w / 2.54) * 2.54
    x0, y0 = -w / 2, h / 2
    pins = []
    for i, (num, nm, kind) in enumerate(left):
        pins.append(_pin(kind, x0 - 2.54, y0 - 2.54 * (i + 1), 0, nm, num))
    for i, (num, nm, kind) in enumerate(right):
        pins.append(_pin(kind, -x0 + 2.54, y0 - 2.54 * (i + 1), 180, nm, num))
    for i, (num, nm, kind) in enumerate(bottom):
        pins.append(_pin(kind, x0 + 2.54 * (i + 1), -y0 - 2.54, 90, nm, num))
    for i, (num, nm, kind) in enumerate(top):
        pins.append(_pin(kind, x0 + 2.54 * (i + 1), y0 + 2.54, 270, nm, num))

    def prop(k, v, y, hide=False):
        eff = S("effects", S("font", S("size", 1.27, 1.27)))
        if hide:
            eff.append(S("hide", Sym("yes")))
        return S("property", k, v, S("at", 0, y, 0), eff)

    return S(
        "symbol", name,
        S("exclude_from_sim", Sym("no")), S("in_bom", Sym("yes")), S("on_board", Sym("yes")),
        prop("Reference", ref, y0 + 3.81), prop("Value", value, -y0 - 6.35),
        prop("Footprint", footprint, 0, True), prop("Datasheet", datasheet, 0, True),
        prop("Description", description, 0, True),
        S("symbol", f"{name}_0_1",
          S("rectangle", S("start", x0, y0), S("end", -x0, -y0),
            S("stroke", S("width", 0.254), S("type", Sym("default"))), S("fill", S("type", Sym("background"))))),
        S("symbol", f"{name}_1_1", *pins),
    )


SYMBOLS = [
    box_symbol(
        "TPS25947", "U", "TPS259470ARPWR", "weft:Texas_RPW0010A_VQFN-HR-10_2x2mm_P0.45mm",
        "https://www.ti.com/lit/ds/symlink/tps25947.pdf",
        "2.7-23 V, 5.5 A eFuse with adjustable current limit, OVLO, fault output",
        left=[("5", "IN", "power_in"), ("1", "EN/UVLO", "input"), ("2", "OVLO", "input")],
        right=[("6", "OUT", "power_out"), ("3", "AUXOFF", "open_collector"), ("4", "~{FLT}", "open_collector")],
        bottom=[("7", "DVDT", "passive"), ("9", "ILM", "passive"), ("10", "ITIMER", "passive"), ("8", "GND", "power_in")],
    ),
]


def write_symbols():
    body = [Sym("kicad_symbol_lib"), S("version", Sym("20241209")), S("generator", "weft-cm5-carrier"),
            S("generator_version", "9.0"), *SYMBOLS]
    with open(os.path.join(LIB, "weft.kicad_sym"), "w", encoding="utf-8") as f:
        f.write(sexp.dump(body) + "\n")


def _pad(num, x, y, w, h):
    return S("pad", num, Sym("smd"), Sym("roundrect"), S("at", x, y), S("size", w, h),
             S("layers", "F.Cu", "F.Mask", "F.Paste"), S("roundrect_rratio", 0.15))


def rpw0010a():
    """TI RPW0010A (VQFN-HR, 2 x 2 mm), from the datasheet's example board layout (4225183/A).

    Pads 1, 4, 7 and 10 are L-shaped: a 0.6 x 0.3 mm side pad joined to a 0.25 mm wide finger
    that runs to the package edge. Pads 5 (IN) and 6 (OUT) are 0.3 x 2.4 mm bars.
    """
    pads = []
    for num, sx, sy in (("1", -1, -1), ("4", -1, 1), ("10", 1, -1), ("7", 1, 1)):
        pads.append(_pad(num, sx * 0.9, sy * 0.7, 0.6, 0.3))  # side pad
        pads.append(_pad(num, sx * 0.725, sy * 0.875, 0.25, 0.65))  # finger, y 0.55..1.2
    for num, sx, sy in (("2", -1, -1), ("3", -1, 1), ("9", 1, -1), ("8", 1, 1)):
        pads.append(_pad(num, sx * 0.9, sy * 0.225, 0.6, 0.25))
    pads.append(_pad("5", -0.25, 0, 0.3, 2.4))
    pads.append(_pad("6", 0.25, 0, 0.3, 2.4))

    def line(layer, x1, y1, x2, y2, w=0.12):
        return S("fp_line", S("start", x1, y1), S("end", x2, y2),
                 S("stroke", S("width", w), S("type", Sym("solid"))), S("layer", layer))

    graphics = [
        line("F.CrtYd", -1.5, -1.5, 1.5, -1.5, 0.05), line("F.CrtYd", 1.5, -1.5, 1.5, 1.5, 0.05),
        line("F.CrtYd", 1.5, 1.5, -1.5, 1.5, 0.05), line("F.CrtYd", -1.5, 1.5, -1.5, -1.5, 0.05),
        line("F.Fab", -1, -1, 1, -1, 0.1), line("F.Fab", 1, -1, 1, 1, 0.1),
        line("F.Fab", 1, 1, -1, 1, 0.1), line("F.Fab", -1, 1, -1, -1, 0.1),
        S("fp_circle", S("center", -1.45, -1.35), S("end", -1.35, -1.35),
          S("stroke", S("width", 0.12), S("type", Sym("solid"))), S("fill", Sym("yes")), S("layer", "F.SilkS")),
    ]
    name = "Texas_RPW0010A_VQFN-HR-10_2x2mm_P0.45mm"
    body = S(
        "footprint", name, S("version", Sym("20241229")), S("generator", "weft-cm5-carrier"),
        S("layer", "F.Cu"),
        S("descr", "TI RPW0010A VQFN-HR 10 pin 2x2 mm (TPS25947), datasheet land pattern 4225183/A"),
        S("attr", Sym("smd")),
        S("property", "Reference", "REF**", S("at", 0, -2.3, 0), S("layer", "F.SilkS"),
          S("effects", S("font", S("size", 1, 1), S("thickness", 0.15)))),
        S("property", "Value", name, S("at", 0, 2.3, 0), S("layer", "F.Fab"),
          S("effects", S("font", S("size", 1, 1), S("thickness", 0.15)))),
        *graphics, *pads,
    )
    os.makedirs(os.path.join(LIB, "weft.pretty"), exist_ok=True)
    with open(os.path.join(LIB, "weft.pretty", f"{name}.kicad_mod"), "w", encoding="utf-8") as f:
        f.write(sexp.dump(body) + "\n")


if __name__ == "__main__":
    write_symbols()
    rpw0010a()
