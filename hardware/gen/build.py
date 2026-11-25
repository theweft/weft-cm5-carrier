# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Generates the KiCad project in hardware/kicad from carrier.py."""

import json
import os

import carrier
import kicad
import mklib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "kicad")
NAME = kicad.PROJECT


def write_tables(libs):
    """Project library tables: the project's own libraries plus every KiCad library used, so the
    project needs no global KiCad configuration (CI, fresh machines)."""
    std_sym = sorted(n for n in libs["sym"] if n not in ("weft", "CM5IO"))
    std_fp = sorted(n for n in libs["fp"] if n not in ("weft", "CM5IO"))
    sym_rows = "".join(f'\t(lib (name "{n}")(type "KiCad")(uri "${{KICAD10_SYMBOL_DIR}}/{n}.kicad_sym")(options "")(descr ""))\n' for n in std_sym)
    fp_rows = "".join(f'\t(lib (name "{n}")(type "KiCad")(uri "${{KICAD10_FOOTPRINT_DIR}}/{n}.pretty")(options "")(descr ""))\n' for n in std_fp)
    return _write_tables(sym_rows, fp_rows)


def _write_tables(sym_rows, fp_rows):
    with open(os.path.join(OUT, "sym-lib-table"), "w", encoding="utf-8") as f:
        f.write('(sym_lib_table\n\t(version 7)\n'
                '\t(lib (name "weft")(type "KiCad")(uri "${KIPRJMOD}/../lib/weft.kicad_sym")(options "")(descr "weft parts"))\n'
                '\t(lib (name "CM5IO")(type "KiCad")(uri "${KIPRJMOD}/../lib/CM5IO.kicad_sym")(options "")(descr "Raspberry Pi CM5 IO Board"))\n'
                + sym_rows + ')\n')
    with open(os.path.join(OUT, "fp-lib-table"), "w", encoding="utf-8") as f:
        f.write('(fp_lib_table\n\t(version 7)\n'
                '\t(lib (name "weft")(type "KiCad")(uri "${KIPRJMOD}/../lib/weft.pretty")(options "")(descr "weft parts"))\n'
                '\t(lib (name "CM5IO")(type "KiCad")(uri "${KIPRJMOD}/../lib/CM5IO.pretty")(options "")(descr "Raspberry Pi CM5 IO Board"))\n'
                + fp_rows + ')\n')
    write_project()


# Supply nets of the carrier: wider tracks (the router reads these from the net classes).
SUPPLY_CLASSES = {
    "Supply_0.6": (0.6, ["VBUS_IN", "VBUS_SHUNT", "+5V_CM5", "USBA3_VBUS", "USBA4_VBUS"]),
    "Supply_0.4": (0.4, ["SUP_5V", "SUP_3V3", "ESP_3V3", "VBUS_LAB", "+3V3_CM5", "+1V1_SUP", "GND"]),
    "Supply_0.3": (0.3, ["+1V8_CM5", "GPIO_VREF", "VBAT", "VBAT_CELL"]),
}


def write_project():
    """The project file carries the design rules: the IO Board's (0.125 mm clearance, 0.45 mm vias,
    its impedance classes for HDMI, USB 3, MIPI and Ethernet), with patterns for renamed nets."""
    pro = json.load(open(os.path.join(HERE, "..", "base", "CM5IO.kicad_pro"), encoding="utf-8"))
    pro["meta"]["filename"] = f"{NAME}.kicad_pro"
    pro["sheets"] = []
    pro.pop("boards", None)
    ns = pro["net_settings"]
    for pat in ns.get("netclass_patterns", []):
        pat["pattern"] = pat["pattern"].rsplit("/", 1)[-1]
    default = next(c for c in ns["classes"] if c["name"] == "Default")
    for name, (width, nets) in SUPPLY_CLASSES.items():
        cls = dict(default, name=name, track_width=width)
        ns["classes"] = [c for c in ns["classes"] if c["name"] != name] + [cls]
        ns["netclass_patterns"] += [{"netclass": name, "pattern": n} for n in nets]
    ns.pop("netclass_assignments", None)
    with open(os.path.join(OUT, f"{NAME}.kicad_pro"), "w", encoding="utf-8") as f:
        json.dump(pro, f, indent=2)
        f.write("\n")


def main():
    os.makedirs(OUT, exist_ok=True)
    mklib.write_symbols()
    mklib.rpw0010a()
    c = carrier.build()
    sheets = {}
    for name, filename, title in carrier.SHEETS:
        sheets[name] = kicad.Sheet(name, filename, title)
    for part in c.parts.values():
        sheets[part.block].add(part)
    root_uuid = kicad.uid("root")
    for sh in sheets.values():
        kicad.write_sheet(sh, c.lib, root_uuid, kicad.uid("sheet", sh.filename), os.path.join(OUT, sh.filename))
    kicad.write_root(list(sheets.values()), root_uuid, os.path.join(OUT, f"{NAME}.kicad_sch"),
                     "weft CM5 lab carrier")
    write_tables({"sym": {p.lib_id.split(":")[0] for p in c.parts.values()},
                  "fp": {p.footprint.split(":")[0] for p in c.parts.values() if p.footprint}})
    nets = c.nets()
    print(f"{len(c.parts)} parts, {len(nets)} nets, {len(sheets)} sheets")


if __name__ == "__main__":
    main()
