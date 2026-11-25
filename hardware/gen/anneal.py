# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Improves a placement by simulated annealing.

Moves: shift a part, rotate it by 90 degrees, or swap two parts with the same footprint.
Cost: the half-perimeter wire length (HPWL) of every net, from the real pad positions, with
weights: clocks, QSPI and USB pairs count triple; supply nets and ground not at all (pours and
planes carry them). Decoupling capacitors (a supply and ground only) are pulled towards the nearest
pin of that supply on a chip. Constraints: no overlap of the (inflated) courtyards, inside the
part's region, clear of fixed obstacles. A move that breaks a constraint is rejected.
"""

import math
import random

CRITICAL = ("SUP_X", "SUP_QSPI", "HUB_X", "HUB_D", "LAB_D", "USB2_", "ESP_USB", "SUP_USB")
SUPPLY = {"SUP_3V3", "SUP_5V", "+3V3_CM5", "+5V_CM5", "ESP_3V3", "+1V8_CM5", "VBUS_IN", "+1V1_SUP",
          "VBUS_LAB", "GPIO_VREF"}


def weight(net):
    if net == "GND":
        return 0.0
    if net in SUPPLY:  # carried by pours and wide tracks; decoupling is handled separately
        return 0.0
    if net.startswith(CRITICAL):
        return 3.0
    return 1.0


class Grid:
    """Spatial hash of rectangles for overlap tests."""

    def __init__(self, cell=2.0):
        self.cell, self.cells = cell, {}

    def _keys(self, r):
        c = self.cell
        for i in range(int(math.floor(r[0] / c)), int(math.floor(r[2] / c)) + 1):
            for j in range(int(math.floor(r[1] / c)), int(math.floor(r[3] / c)) + 1):
                yield (i, j)

    def add(self, key, r):
        for k in self._keys(r):
            self.cells.setdefault(k, {})[key] = r

    def remove(self, key, r):
        for k in self._keys(r):
            self.cells.get(k, {}).pop(key, None)

    def hits(self, r, ignore=()):
        for k in self._keys(r):
            for key, o in self.cells.get(k, {}).items():
                if key in ignore:
                    continue
                if not (r[2] <= o[0] or r[0] >= o[2] or r[3] <= o[1] or r[1] >= o[3]):
                    return True
        return False


class Part:
    def __init__(self, ref, shapes, region, x, y, rot, footprint, margin):
        self.ref, self.shapes, self.region = ref, shapes, region  # shapes[rot] = (rect, pads)
        self.x, self.y, self.rot, self.footprint, self.margin = x, y, rot, footprint, margin

    def rect(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        r = self.shapes[self.rot if rot is None else rot][0]
        m = self.margin
        return (x + r[0] - m, y + r[1] - m, x + r[2] + m, y + r[3] + m)

    def pads(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(net, x + dx, y + dy) for net, dx, dy in self.shapes[self.rot if rot is None else rot][1]]


def anneal(parts, fixed_pads, obstacles, decap_targets, moves=200_000, seed=1, log=print):
    """parts: movable Part objects; fixed_pads: [(net, x, y)]; obstacles: [rect];
    decap_targets: {ref: [(x, y)] candidate pins of its supply on chips (movable or fixed)}.
    Returns the final cost."""
    rnd = random.Random(seed)
    grid = Grid()
    for i, r in enumerate(obstacles):
        grid.add(("obs", i), r)
    for p in parts:
        grid.add(p.ref, p.rect())
    by_ref = {p.ref: p for p in parts}
    # Net membership.
    net_parts, net_fixed = {}, {}
    for p in parts:
        for net, _, _ in p.pads():
            net_parts.setdefault(net, set()).add(p.ref)
    for net, x, y in fixed_pads:
        net_fixed.setdefault(net, []).append((x, y))
    nets = {n for n in net_parts if weight(n) > 0}

    def net_cost(net):
        xs, ys = [], []
        for ref in net_parts.get(net, ()):
            for n, x, y in by_ref[ref].pads():
                if n == net:
                    xs.append(x)
                    ys.append(y)
        for x, y in net_fixed.get(net, ()):
            xs.append(x)
            ys.append(y)
        if len(xs) < 2:
            return 0.0
        return weight(net) * ((max(xs) - min(xs)) + (max(ys) - min(ys)))

    def decap_cost(p):
        targets = decap_targets.get(p.ref)
        if not targets:
            return 0.0
        tx = [(by_ref[t].x + dx, by_ref[t].y + dy) if isinstance(t, str) else (dx, dy)
              for t, dx, dy in targets]
        return 2.0 * min(abs(p.x - x) + abs(p.y - y) for x, y in tx)

    cache = {n: net_cost(n) for n in nets}
    total = sum(cache.values()) + sum(decap_cost(p) for p in parts)
    log(f"anneal: {len(parts)} parts, {len(nets)} nets, start cost {total:.0f}")

    def nets_of(*refs):
        out = set()
        for r in refs:
            out.update(n for n, _, _ in by_ref[r].pads() if n in nets)
        return out

    def decaps_touching(*refs):
        out = set(r for r in refs if r in decap_targets)
        for ref, targets in decap_targets.items():
            if any(isinstance(t, str) and t in refs for t, _, _ in targets):
                out.add(ref)
        return out

    def inside(p, r):
        reg = p.region
        return r[0] >= reg[0] and r[1] >= reg[1] and r[2] <= reg[2] and r[3] <= reg[3]

    swap_groups = {}
    for p in parts:
        swap_groups.setdefault(p.footprint, []).append(p.ref)

    t0, t1 = 8.0, 0.02
    accepted = 0
    for step in range(moves):
        temp = t0 * (t1 / t0) ** (step / moves)
        span = max(0.5, min(20.0, temp * 3))
        p = parts[rnd.randrange(len(parts))]
        kind = rnd.random()
        if kind < 0.15 and len(swap_groups[p.footprint]) > 1:
            q = by_ref[rnd.choice(swap_groups[p.footprint])]
            if q is p:
                continue
            moved = [(p, q.x, q.y, p.rot), (q, p.x, p.y, q.rot)]
        elif kind < 0.30:
            moved = [(p, p.x, p.y, (p.rot + rnd.choice((90, 180, 270))) % 360)]
        else:
            nx = round((p.x + rnd.uniform(-span, span)) * 4) / 4
            ny = round((p.y + rnd.uniform(-span, span)) * 4) / 4
            moved = [(p, nx, ny, p.rot)]
        refs = [m[0].ref for m in moved]
        ok = True
        for part, x, y, rot in moved:
            r = part.rect(x, y, rot)
            if not inside(part, r) or grid.hits(r, ignore=refs):
                ok = False
                break
        if ok and len(moved) == 2:  # the two swapped parts must not overlap each other
            ra, rb = moved[0][0].rect(*moved[0][1:]), moved[1][0].rect(*moved[1][1:])
            if not (ra[2] <= rb[0] or ra[0] >= rb[2] or ra[3] <= rb[1] or ra[1] >= rb[3]):
                ok = False
        if not ok:
            continue
        affected = nets_of(*refs)
        decaps = decaps_touching(*refs)
        before = sum(cache[n] for n in affected) + sum(decap_cost(by_ref[d]) for d in decaps)
        old = [(m[0], m[0].x, m[0].y, m[0].rot) for m in moved]
        for part, _, _, _ in moved:
            grid.remove(part.ref, part.rect())
        for part, x, y, rot in moved:
            part.x, part.y, part.rot = x, y, rot
        new_costs = {n: net_cost(n) for n in affected}
        after = sum(new_costs.values()) + sum(decap_cost(by_ref[d]) for d in decaps)
        delta = after - before
        if delta <= 0 or rnd.random() < math.exp(-delta / temp):
            cache.update(new_costs)
            total += delta
            accepted += 1
            for part, _, _, _ in moved:
                grid.add(part.ref, part.rect())
        else:
            for part, x, y, rot in old:
                part.x, part.y, part.rot = x, y, rot
            for part, _, _, _ in moved:
                grid.add(part.ref, part.rect())
        if step % 50_000 == 0 and step:
            log(f"anneal: step {step}, temperature {temp:.2f}, cost {total:.0f}, accepted {accepted}")
    log(f"anneal: end cost {total:.0f}, {accepted} of {moves} moves accepted")
    return total
