# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""Minimal S-expression reader and writer for KiCad files."""

import re

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


class Sym(str):
    """An unquoted atom (keyword or number) as opposed to a quoted string."""


def parse(text):
    stack = [[]]
    for m in _TOKEN.finditer(text):
        s = m.group(0)
        if s == "(":
            stack.append([])
        elif s == ")":
            item = stack.pop()
            stack[-1].append(item)
        elif s[0] == '"':
            stack[-1].append(s[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(Sym(s))
    return stack[0][0]


def find(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def first(node, name):
    found = find(node, name)
    return found[0] if found else None


def value(node, name):
    found = first(node, name)
    return found[1] if found is not None and len(found) > 1 else None


def _atom(x):
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        if isinstance(x, float):
            s = f"{x:.4f}".rstrip("0").rstrip(".")
            return s if s not in ("", "-0") else "0"
        return str(x)
    s = str(x).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def dump(node, indent=0):
    """Writes a node; lists whose first element is a Sym become (head ...)."""
    if not isinstance(node, list):
        return _atom(node)
    pad = "\t" * indent
    simple = all(not isinstance(c, list) for c in node)
    if simple:
        return "(" + " ".join(_atom(c) for c in node) + ")"
    out = ["(" + " ".join(_atom(c) for c in node if not isinstance(c, list))]
    for c in node:
        if isinstance(c, list):
            out.append("\n" + pad + "\t" + dump(c, indent + 1))
    out.append("\n" + pad + ")")
    return "".join(out)


def S(*items):
    """Builds a node: S('at', 1, 2) -> (at 1 2). Strings that are keywords use Sym."""
    head, *rest = items
    return [Sym(head), *rest]
