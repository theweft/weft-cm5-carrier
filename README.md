<h1><img src="https://theweft.org/brand/repos/weft-cm5-carrier.svg" alt="" width="44" height="44" align="absmiddle"> weft-cm5-carrier</h1>

> **Status: planning.** Requirements and architecture are written; no schematic or layout yet.

A carrier board for the Raspberry Pi Compute Module 5 that exposes every CM5 signal and lets an
agent work on the module without a person present. Part of [weft](https://theweft.org).

- **Every signal reachable:** each of the CM5's 200 pins ends at a labelled header, a test point
  or its native connector (HDMI, USB 3, M.2, MIPI, Ethernet). Pogo pins under the module reach
  its own test points too: the debug UART (boot console, and SWD to the SoC when enabled with
  `enable_jtag_gpio=1`), RUN and nRESET_OUT. The U.FL antenna goes to a connector on the base.
- **A supervisor that handles everything a microcontroller can** (RP2350B, one USB-C to the PC):
  - cuts and restores the CM5's power (real power cuts) and measures every rail;
  - drives the boot and control pins in the order the datasheet requires;
  - reaches all 28 GPIOs individually, for consoles, peripheral emulation, a logic analyser and
    fault injection;
  - flashes the CM5 over rpiboot or by swapping its SD card to the PC;
  - emulates a monitor and captures the HDMI picture;
  - disconnects Ethernet for network partition tests.
- **The carrier negotiates USB-C PD itself**, so it can take the CM5 off its supply without
  losing the contract.
- **A base plate**, with all cables entering at one edge, so the board sits still on a table.

Design, decisions and open questions:
[weft-design docs/22](https://github.com/theweft/weft-design/blob/main/docs/22-cm5-lab-carrier.md).
It starts from Raspberry Pi's CM5 IO Board design files (their licence permits reuse); the new
blocks are designed as code and checked in CI by weft's board tools.

## Layout

| Path | What | Licence |
|---|---|---|
| `hardware/` | Schematic sources and KiCad project | CERN-OHL-P-2.0 |
| `firmware/` | Supervisor firmware (Rust) | MIT |
| `mechanical/` | Base plate and cover | CERN-OHL-P-2.0 |

## Development

`nix develop` (or direnv) provides the tools; `just ci` runs everything CI runs.

## Licence

Hardware design files: [CERN-OHL-P-2.0](LICENSES/CERN-OHL-P-2.0.txt). Firmware, scripts and
documentation: [MIT](LICENSES/MIT.txt). [REUSE.toml](REUSE.toml) says which applies to which file.
