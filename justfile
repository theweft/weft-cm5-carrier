# Run `just` to list recipes. CI runs `just ci` inside `nix develop`.

default:
    @just --list

# Spelling.
typos:
    typos

# Licence and copyright: REUSE compliance.
licenses:
    reuse lint

# Everything CI checks.
ci: licenses typos

# Generates the schematic (hardware/gen -> hardware/kicad) and runs KiCad's ERC.
schematic:
    cd hardware/gen && python3 build.py
    mkdir -p target
    kicad-cli sch erc --exit-code-violations --severity-all -o target/erc.rpt hardware/kicad/weft-cm5-carrier.kicad_sch

# Places the PCB: the IO Board layout plus the new parts, unrouted (target/placed.kicad_pcb).
place: schematic
    kicad-cli sch export netlist --format kicadsexpr -o target/carrier.net hardware/kicad/weft-cm5-carrier.kicad_sch
    python3 hardware/gen/pcb.py target/carrier.net target/placed.kicad_pcb
    cp hardware/kicad/weft-cm5-carrier.kicad_pro target/placed.kicad_pro

# Routes the placed PCB with Freerouting into hardware/kicad (takes a while).
route passes="30": place
    python3 hardware/gen/route.py target/placed.kicad_pcb hardware/kicad/weft-cm5-carrier.kicad_pcb {{passes}}

# KiCad's design rule check on the routed PCB.
drc:
    mkdir -p target
    kicad-cli pcb drc --schematic-parity --severity-all -o target/drc.rpt hardware/kicad/weft-cm5-carrier.kicad_pcb

# Routes several variants in parallel (different pass counts) into target/route-<passes>.kicad_pcb;
# compare them with `just compare-routes` and keep the best.
route-many *passes="3 6 12 25":
    for p in {{passes}}; do \
      cp target/placed.kicad_pro target/route-$p.kicad_pro; \
      nice -n 10 python3 hardware/gen/route.py target/placed.kicad_pcb target/route-$p.kicad_pcb $p > target/route-$p.log 2>&1 & \
    done; wait
