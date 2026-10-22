"""Read a vector P&ID (CAD-exported PDF): text with positions, and line segments classified by drawing style.

SmartPlant-style sheets draw each object class in its own style, so geometry can be sorted without
recognising symbols: process pipes are solid black, equipment red, valves olive, instrument bubbles and
signal lines green. The title block, revision box and equipment table are excluded from the drawing area.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

Box = tuple[float, float, float, float]

BLACK, RED, GREEN, OLIVE = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.5, 0.0), (0.5, 0.5, 0.0)
PIPE_WIDTHS = (0.96, 1.44, 2.88)


@dataclass(frozen=True)
class Text:
    text: str
    box: Box
    size: float
    vertical: bool

    @property
    def center(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


@dataclass(frozen=True)
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str  # pipe | equipment | valve | signal | other
    width: float

    @property
    def length(self) -> float:
        return ((self.x1 - self.x0) ** 2 + (self.y1 - self.y0) ** 2) ** 0.5


@dataclass
class Sheet:
    name: str
    subprocess: str
    texts: list[Text]
    segs: list[Seg]
    fills: list[tuple[Box, str]]  # filled shapes (valve bodies) with their kind
    excluded: list[Box] = field(default_factory=list)

    def in_area(self, x: float, y: float) -> bool:
        return not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in self.excluded)


def _kind(color, width) -> str:
    c = tuple(round(v, 2) for v in color) if color else None
    if c == BLACK and round(width, 2) in PIPE_WIDTHS:
        return "pipe"
    return {RED: "equipment", GREEN: "signal", OLIVE: "valve"}.get(c, "other")


def load(path: Path) -> Sheet:
    page = pymupdf.open(path)[0]
    W, H = page.rect.width, page.rect.height

    texts = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            s = "".join(span["text"] for span in line["spans"]).strip()
            if s:
                size = max(span["size"] for span in line["spans"])
                texts.append(Text(s, tuple(line["bbox"]), round(size, 1), abs(line["dir"][1]) > 0.5))

    # Title block, revision box and equipment table, located by their headings.
    excluded = [
        (0, 0, W, 28), (0, 0, 28, H), (W - 28, 0, W, H), (0, H - 28, W, H),  # border and grid labels
        (1758, 1292, W, H),  # title block
        (1258, 1490, W, H),  # revision box
    ]
    # Equipment tables: one per "AKZ" header row; each is as wide as its own columns.
    # Only cells in the header's own font count, so pipe labels at the same height don't widen it.
    head_texts = [t for t in texts if t.text == "AKZ" and t.box[0] < W / 2]
    heads = sorted(t.box[1] for t in head_texts)
    head_size = head_texts[0].size if head_texts else 0
    for i, y in enumerate(heads):
        row = sorted(t.center[0] for t in texts if abs(t.box[1] - y) < 4 and t.size == head_size and t.text != "AKZ")
        if len(row) < 2:
            continue
        col = row[1] - row[0]
        bottom = heads[i + 1] - 6 if i + 1 < len(heads) else H
        excluded.append((0, y - 6, row[-1] + col / 2, bottom))

    segs, fills = [], []
    for d in page.get_drawings():
        kind = _kind(d.get("color"), d.get("width") or 0)
        if d.get("fill") and not d.get("color"):
            fk = _kind(d["fill"], 0.96 if tuple(round(v, 2) for v in d["fill"]) == BLACK else 0)
            r = d["rect"]
            fills.append(((r.x0, r.y0, r.x1, r.y1), fk))
        for item in d["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                segs.append(Seg(p.x, p.y, q.x, q.y, kind, round(d.get("width") or 0, 2)))
            elif item[0] == "re":
                r = item[1]
                for (a, b), (c, e) in (
                    ((r.x0, r.y0), (r.x1, r.y0)), ((r.x1, r.y0), (r.x1, r.y1)),
                    ((r.x1, r.y1), (r.x0, r.y1)), ((r.x0, r.y1), (r.x0, r.y0)),
                ):
                    segs.append(Seg(a, b, c, e, kind, round(d.get("width") or 0, 2)))

    sub = next((m.group(1) for t in texts if (m := re.fullmatch(r"COVPIL1-V999-([A-Z]{2}\d{2})", t.text))), "")
    sheet = Sheet(path.stem, sub, [], [], [], excluded)
    sheet.texts = [t for t in texts if sheet.in_area(*t.center)]
    sheet.segs = [s for s in segs if sheet.in_area(s.x0, s.y0) and sheet.in_area(s.x1, s.y1)]
    sheet.fills = [f for f in fills if sheet.in_area((f[0][0] + f[0][2]) / 2, (f[0][1] + f[0][3]) / 2)]
    return sheet
