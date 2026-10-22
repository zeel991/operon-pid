"""Find tagged items in a sheet's text: equipment, instrument bubbles, line numbers (with spec) and valves.

The rules use the owner's tagging scheme, which is configuration in a real deployment:
- equipment tags are the large labels (two letters + three digits, e.g. BA100, PA103);
- instrument bubbles are a function code (FRC) stacked over a loop number (F0026);
- line labels read [subprocess-]LRnnn (or LQnnn).fluid-DN-PN+piping class gasket valve class;
- valve and piping-component tags are A + letter + three digits (AB020, AH001).
Tags written as "LA01-..." belong to another subprocess; bare tags belong to the sheet's own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .drawing import Sheet, Text

LINE_RE = re.compile(
    r"(?:(?P<sub>[A-Z]{2}\d{2})-)?(?P<tag>L[A-Z]\d{3})"
    r"(?:\.(?P<fluid>\d{5})-(?P<dn>\d+)-(?P<pn>\d{2})(?P<piping_class>[A-Z0-9]+)\s*(?P<gasket>[A-Z]{2})\s*(?P<valve_class>\d{3}))?"
)
EQUIPMENT_RE = re.compile(r"[A-Z]{2}\d{3}")
VALVE_RE = re.compile(r"A[A-Z]\d{3}")
LOOP_RE = re.compile(r"[A-Z]\d{4}")
FUNCTION_RE = re.compile(r"[A-Z]{1,5}")
EQUIPMENT_MIN_SIZE = 19.0
SPEC_FIELDS = ("fluid", "dn", "pn", "piping_class", "gasket", "valve_class")


@dataclass
class Item:
    key: str  # subprocess-tag, e.g. TA99-BA100
    tag: str
    kind: str  # equipment | instrument | line | valve
    texts: list[Text]
    attrs: dict = field(default_factory=dict)


def _key(sheet: Sheet, sub: str | None, tag: str) -> str:
    return f"{sub or sheet.subprocess}-{tag}"


def extract(sheet: Sheet) -> dict[str, dict[str, Item]]:
    out: dict[str, dict[str, Item]] = {"equipment": {}, "instrument": {}, "line": {}, "valve": {}}

    def add(kind: str, key: str, tag: str, text: Text, **attrs):
        item = out[kind].get(key)
        if item is None:
            out[kind][key] = Item(key, tag, kind, [text], attrs)
        else:
            item.texts.append(text)
            for k, v in attrs.items():
                item.attrs.setdefault(k, v)

    for t in sheet.texts:
        compact = t.text.replace(" ", "")
        if t.size >= EQUIPMENT_MIN_SIZE and EQUIPMENT_RE.fullmatch(compact):
            add("equipment", _key(sheet, None, compact), compact, t)
            continue
        if m := LINE_RE.fullmatch(t.text.strip()):
            spec = {f: m.group(f) for f in SPEC_FIELDS if m.group(f)}
            add("line", _key(sheet, m.group("sub"), m.group("tag")), m.group("tag"), t, **spec)
            continue
        if VALVE_RE.fullmatch(compact):
            add("valve", _key(sheet, None, compact), compact, t)

    # Instrument bubbles: function code directly above a loop number with the same first letter.
    loops = [t for t in sheet.texts if not t.vertical and LOOP_RE.fullmatch(t.text)]
    funcs = [t for t in sheet.texts if not t.vertical and FUNCTION_RE.fullmatch(t.text)]
    for loop in loops:
        lx, ly = loop.center
        above = [
            f for f in funcs
            if f.text[0] == loop.text[0] and abs(f.center[0] - lx) < 10 and 0 < ly - f.center[1] < 16
        ]
        if above:
            func = min(above, key=lambda f: ly - f.center[1])
            add("instrument", _key(sheet, None, loop.text), loop.text, loop, function=func.text)
    return out
