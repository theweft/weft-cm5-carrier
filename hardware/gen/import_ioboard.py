# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""One-time import of the Raspberry Pi CM5 IO Board circuit from its KiCad netlist.

Usage: python3 import_ioboard.py <cm5io.net> > ioboard.json

The netlist comes from Raspberry Pi's CM5 IO Board KiCad files (revision 2), exported with
`kicad-cli sch export netlist --format kicadsexpr`. The result is the IO Board circuit as data
(components, fields, pin-to-net), which carrier.py modifies and extends.
"""

import json
import re
import sys

import sexp

KEEP_FIELDS = {"Field6": "MPN", "Field7": "Manufacturer"}


def clean_net(name):
    if name.startswith("Net-("):
        inner = name[5:-1]
        inner = inner.replace("{slash}", "_").replace("~{", "n").replace("}", "")
        return "IO_" + re.sub(r"[^A-Za-z0-9_+.]", "_", inner)
    if name.startswith("unconnected-"):
        return None
    base = name.rsplit("/", 1)[-1]
    return {"+5v": "+5V_CM5", "+3.3v": "+3V3_CM5", "+1.8v": "+1V8_CM5"}.get(base, base)


def main(path):
    tree = sexp.parse(open(path, encoding="utf-8").read())
    comps = {}
    for c in sexp.find(sexp.first(tree, "components"), "comp"):
        ref = sexp.value(c, "ref")
        lib = sexp.first(c, "libsource")
        fields = {}
        fnode = sexp.first(c, "fields")
        for f in sexp.find(fnode, "field") if fnode else []:
            name = f[1][1]
            if name in KEEP_FIELDS and len(f) > 2 and f[2].strip() not in ("", "nf"):
                fields[KEEP_FIELDS[name]] = f[2].strip()
        comps[ref] = {
            "lib_id": f"{sexp.value(lib, 'lib')}:{sexp.value(lib, 'part')}",
            "value": sexp.value(c, "value"),
            "footprint": sexp.value(c, "footprint"),
            "fields": fields,
            "sheet": sexp.value(sexp.first(c, "sheetpath"), "names"),
            "pins": {},
        }
    for n in sexp.find(sexp.first(tree, "nets"), "net"):
        net = clean_net(sexp.value(n, "name"))
        if net is None:
            continue
        for node in sexp.find(n, "node"):
            comps[sexp.value(node, "ref")]["pins"][sexp.value(node, "pin")] = net
    json.dump(comps, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
