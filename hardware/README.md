# Hardware

> **Revision 0.1: first complete draft, not for fabrication.** The schematic passes KiCad's ERC;
> the PCB is placed and autorouted and needs review and hand work (see "Open items").

The carrier is described in code and generated into a KiCad project, starting from Raspberry Pi's
CM5 IO Board (design, decisions and requirements: weft-design
[docs/22](https://github.com/theweft/weft-design/blob/main/docs/22-cm5-lab-carrier.md)).

| Path | What |
|---|---|
| `gen/carrier.py` | The circuit: the IO Board's circuit, the changes to it, and the new blocks (power, supervisor, USB, GPIO path, control, ESP32, headers) |
| `gen/ioboard.json` | The IO Board circuit as data, imported once from its netlist (`gen/import_ioboard.py`) |
| `gen/build.py`, `gen/kicad.py`, `gen/circuit.py` | Writes the schematic: one sheet per block, one framed cluster per chip (passives wired to their pin, decoupling below, power symbols) |
| `gen/mklib.py` | Symbols and footprints missing from KiCad's libraries (`lib/weft.*`): TPS25947 eFuse |
| `gen/pcb.py` | The PCB: the IO Board layout, netlist applied, outline extended 110 mm to the right, new parts placed by block, the 5 V power path as zones |
| `gen/route.py` | Routing with Freerouting; the IO Board's tracks stay locked |
| `base/` | The IO Board PCB (Raspberry Pi) |
| `kicad/` | The generated KiCad project (do not edit by hand; change the code and regenerate) |

```sh
nix develop          # KiCad 10, Freerouting, Python with KiCad's pcbnew
just schematic       # generate sheets, ERC
just route           # place and route the PCB into kicad/ (takes a while)
just drc             # design rule check
```

## What changed from the IO Board

- **Power:** USB-C PD negotiated by an STUSB4500 (5 V, 5 A); 5 mΩ shunt and INA228; TPS25947 eFuse
  (5.5 A limit, OVLO 5.9 V) that the supervisor switches; discharge MOSFET for real power cuts.
  The CM5's CC pins go to a header, not the connector. The power connector moved to the extension;
  the CM5's 5 V reaches the IO Board's 5 V copper through inner layer 2.
- **GPIO_VREF** from a TPS2116 mux: 3.3 V by default, 1.8 V when the supervisor pulls PR1 low.
- **USB 3 VBUS, M.2 power, RTC battery** can be cut by the supervisor (series resistor on the CM5's
  enable, TPS22919 on the battery).
- **Ethernet** pairs pass a TS3L501E switch (disconnect for network partition tests).
- **The CM5's USB 2.0 port** goes to the lab hub or to the ESP32-S3 (FSUSB42 mux).

## New blocks

- **Supervisor:** RP2350B, 16 Mbit flash, 12 MHz crystal, SWD on the Raspberry Pi 3-pin JST-SH debug connector (the Debug Probe stays plugged in),
  BOOTSEL and RESET buttons. GPIO0..27 face the CM5's GPIO0..27 one to one.
- **GPIO path:** four LSF0108 for the 28 GPIOs (RefA = GPIO_VREF, so 3.3 V and 1.8 V banks), two
  for status pins; all off unless the supervisor enables them *and* the CM5's 3.3 V is up (R5).
- **Control:** two TCA9555 expanders; twelve open-drain outputs (74LVC3G07) for PMIC_EN,
  PWR_BUT, nRPIBOOT, EEPROM_nWP, WL/BT disable, USB OTG ID, the power kills, the 1.8 V select and
  the Ethernet switch; HDMI0 monitor emulation (DDC via TCA9517, hot plug via a PMOS).
- **USB:** lab USB-C into a USB2514B hub: supervisor, CM5 USB (via the mux), two USB-A ports (HDMI
  capture stick and a spare).
- **ESP32-S3-WROOM-1:** USB device towards the CM5, Wi-Fi/BLE partner, programmed over UART by
  the supervisor.
- **Headers:** every CM5 pin that has no connector ends at the 40-pin HAT header, the IO Board's
  J2/J3, a 2x20 control header or a 2x10 SD and power header.

## Open items (revision 0.1)

- Review the autorouted PCB; route the Ethernet pairs through the switch as 100 Ω pairs by hand.
- 5 V path: only a few vias join the new inner-layer 5 V to the IO Board's 5 V copper; check the
  current capacity and add copper.
- No current shunts in the CM5's 3.3 V and 1.8 V outputs yet (the INA3221 reads their voltages).
- Pogo pins for the module's test points: positions to be measured on a real CM5 (docs/22 §3.4).
- The TPS25947 footprint (`lib/weft.pretty`) is drawn from the datasheet's land pattern: check it.
- STUSB4500 NVM: program PDO1 to 5 V/5 A in bring-up.
- Schematic: chip pins still end in net labels (no wires between chips); search a net name to follow it.
