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
    pro = os.path.join(OUT, f"{NAME}.kicad_pro")
    if not os.path.exists(pro):
        with open(pro, "w", encoding="utf-8") as f:
            json.dump({"meta": {"filename": f"{NAME}.kicad_pro", "version": 3},
                       "sheets": [], "text_variables": {}}, f, indent=2)


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
