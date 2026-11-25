# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Routes the placed board with Freerouting and fills the zones.

    python3 route.py <placed.kicad_pcb> <out.kicad_pcb> [passes]

The IO Board's own tracks are locked first, so Freerouting keeps them (its impedance-controlled
pairs stay as Raspberry Pi routed them) and only connects what is new or changed.
"""

import os
import subprocess
import sys
import tempfile

import pcbnew

def main(src, out, passes="40"):
    board = pcbnew.LoadBoard(src)
    for t in board.GetTracks():
        t.SetLocked(True)
    # Freerouting sees a zone's outline as solid copper (a wall for every other net). Route without
    # the zones; they come back afterwards and KiCad fills them around the new tracks.
    zones = [board.GetArea(i) for i in range(board.GetAreaCount())]
    for z in zones:
        board.Remove(z)
    tmp = tempfile.mkdtemp(prefix="route-")
    dsn, ses = os.path.join(tmp, "board.dsn"), os.path.join(tmp, "board.ses")
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise SystemExit("DSN export failed")
    # Net classes and clearances come from the project file (.kicad_pro next to the board).
    cmd = ["freerouting", "-de", dsn, "-do", ses, "-mp", passes, "--gui.enabled=false"]
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    if not pcbnew.ImportSpecctraSES(board, ses):
        raise SystemExit("SES import failed")
    for t in board.GetTracks():
        t.SetLocked(False)
    for z in zones:
        board.Add(z)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(out, board)
    print("saved", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
