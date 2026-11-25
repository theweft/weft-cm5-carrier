# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Routes the placed board with Freerouting and fills the zones.

    python3 route.py <placed.kicad_pcb> <out.kicad_pcb> [passes]

The IO Board's own tracks are locked first, so Freerouting keeps them (its impedance-controlled
pairs stay as Raspberry Pi routed them) and only connects what is new or changed.
"""

import os
import re
import subprocess
import sys
import tempfile

import pcbnew

SUPPLY = {  # net -> track width (mm) for Freerouting
    "VBUS_IN": 0.6, "VBUS_SHUNT": 0.6, "+5V_CM5": 0.6, "SUP_5V": 0.5, "SUP_3V3": 0.4, "ESP_3V3": 0.5,
    "VBUS_LAB": 0.5, "USBA3_VBUS": 0.6, "USBA4_VBUS": 0.6, "+3V3_CM5": 0.4, "+1V8_CM5": 0.3,
    "GPIO_VREF": 0.3, "+1V1_SUP": 0.4, "GND": 0.4, "VBAT": 0.3, "VBAT_CELL": 0.3,
}


def _drop_from_classes(dsn, name):
    out, pos = [], 0
    for m in re.finditer(r"\(class [^(]*", dsn):
        out.append(dsn[pos:m.start()])
        out.append(re.sub(r"(?<=\s)" + re.escape(name) + r"(?=\s)", "", m.group(0)))
        pos = m.end()
    out.append(dsn[pos:])
    return "".join(out)


def add_classes(dsn):
    """Adds a Freerouting net class per supply width to the DSN text."""
    by_width = {}
    for n, w in SUPPLY.items():
        by_width.setdefault(w, []).append(n)
    # Copy the via padstack used by the default class.
    m = re.search(r"\(use_via ([^)]+)\)", dsn)
    use_via = m.group(1) if m else None
    classes = []
    for w, nets in by_width.items():
        names = " ".join(nets)
        # Remove these nets from the other classes (names may or may not be quoted).
        for n in nets:
            for q in (n, f'"{n}"'):
                dsn = _drop_from_classes(dsn, q)
        circuit = f"(circuit (use_via {use_via}))" if use_via else ""
        classes.append(f"    (class supply_{int(w * 1000)} {names}\n      {circuit}\n"
                       f"      (rule (width {w * 1000:.0f}) (clearance 200))\n    )\n")
    idx = dsn.rindex("  (wiring")
    net_end = dsn.rindex(")", 0, idx)  # end of (network ...)
    return dsn[:net_end] + "".join(classes) + dsn[net_end:]


def main(src, out, passes="40"):
    board = pcbnew.LoadBoard(src)
    for t in board.GetTracks():
        t.SetLocked(True)
    tmp = tempfile.mkdtemp(prefix="route-")
    dsn, ses = os.path.join(tmp, "board.dsn"), os.path.join(tmp, "board.ses")
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise SystemExit("DSN export failed")
    text = open(dsn, encoding="utf-8").read()
    open(dsn, "w", encoding="utf-8").write(add_classes(text))
    cmd = ["freerouting", "-de", dsn, "-do", ses, "-mp", passes, "--gui.enabled=false"]
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    if not pcbnew.ImportSpecctraSES(board, ses):
        raise SystemExit("SES import failed")
    for t in board.GetTracks():
        t.SetLocked(False)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(out, board)
    print("saved", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
