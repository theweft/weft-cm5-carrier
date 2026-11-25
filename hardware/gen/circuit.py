# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""A small circuit description language: parts, pins by name or number, nets by name."""

import collections

from kicad import Library

PASSIVE_FP = {
    "R": {"0402": "Resistor_SMD:R_0402_1005Metric", "0603": "Resistor_SMD:R_0603_1608Metric",
          "0805": "Resistor_SMD:R_0805_2012Metric", "1206": "Resistor_SMD:R_1206_3216Metric",
          "2512": "Resistor_SMD:R_2512_6332Metric"},
    "C": {"0402": "Capacitor_SMD:C_0402_1005Metric", "0603": "Capacitor_SMD:C_0603_1608Metric",
          "0805": "Capacitor_SMD:C_0805_2012Metric", "1206": "Capacitor_SMD:C_1206_3216Metric"},
}


class Part:
    def __init__(self, circuit, ref, lib_id, value, footprint, fields):
        self.circuit, self.ref, self.lib_id = circuit, ref, lib_id
        self.value = value
        self.footprint = footprint if footprint is not None else circuit.lib.default_footprint(lib_id)
        self.fields = dict(fields or {})
        self.nets = {}  # pin number -> net
        self.sheet = None
        self.dnp = False
        self.dnp_bom = False
        self._pins = circuit.lib.pins(lib_id)
        self.block = None

    def numbers(self, key):
        """Pin numbers for a pin number or name (a name may match several pins)."""
        key = str(key)
        nums = {p[0] for p in self._pins}
        if key in nums:
            return [key]
        found = sorted({p[0] for p in self._pins if p[1] == key})
        if not found:  # composite names such as GPIO40/ADC0 or SDA/SMBDATA/NON_REM1
            found = sorted({p[0] for p in self._pins if p[1].split("/")[0] == key})
        if not found:
            raise KeyError(f"{self.ref} ({self.lib_id}) has no pin {key!r}; pins: "
                           f"{sorted({(p[0], p[1]) for p in self._pins})}")
        return found

    def __setitem__(self, key, net):
        for n in self.numbers(key):
            if net is None:
                self.nets.pop(n, None)
            else:
                self.nets[n] = net

    def __getitem__(self, key):
        return self.nets.get(self.numbers(key)[0])

    def connect(self, **pins):
        for k, v in pins.items():
            self[k] = v
        return self

    def net_of(self, number):
        return self.nets.get(number)


class Circuit:
    def __init__(self):
        self.lib = Library()
        self.parts = {}
        self.counters = collections.defaultdict(int)
        self.block = None

    def _ref(self, prefix):
        self.counters[prefix] += 1
        base = self.block_base
        return f"{prefix}{base + self.counters[prefix]}"

    block_base = 100

    def start_block(self, name, base):
        self.block, self.block_base = name, base
        self.counters.clear()

    def part(self, lib_id, value=None, ref=None, prefix=None, footprint=None, **fields):
        if ref is None:
            ref = self._ref(prefix or "U")
        if ref in self.parts:
            raise ValueError(f"duplicate reference {ref}")
        p = Part(self, ref, lib_id, value or lib_id.split(":")[1], footprint, fields)
        p.block = self.block
        self.parts[ref] = p
        return p

    def R(self, value, a, b, size="0402", **fields):
        p = self.part("Device:R", value, prefix="R", footprint=PASSIVE_FP["R"][size], **fields)
        p["1"], p["2"] = a, b
        return p

    def C(self, value, a, b="GND", size="0402", **fields):
        p = self.part("Device:C", value, prefix="C", footprint=PASSIVE_FP["C"][size], **fields)
        p["1"], p["2"] = a, b
        return p

    def decouple(self, net, *values, size="0402"):
        for v in values:
            self.C(v, net, "GND", size=size if v in ("100n", "1u", "10n", "15p", "18p", "22n", "4.7n", "1n") else "0805")

    def nets(self):
        out = collections.defaultdict(list)
        for p in self.parts.values():
            for num, net in p.nets.items():
                out[net].append((p.ref, num))
        return out
