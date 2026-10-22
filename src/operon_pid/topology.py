"""Trace pipe runs through the drawing geometry and connect tagged items to them.

- Pipes are horizontal or vertical. Diagonal strokes in pipe style are label leader lines.
- Segments meeting at a corner form one run. Where three or more meet (a tee), only the
  straight-through pair continues the run; the branch starts a new one.
- Pipes stop at the edges of in-line symbols (valves, reducers, orifices, hatching). Facing pipe
  ends on the same axis with a short gap between them are joined, and so is every pipe end that
  touches a valve symbol.
- A line label belongs to the run with a parallel segment closest beside it, or the run its
  leader line points to.
- A valve's line is the label on its run. A run end within a nozzle's length of an equipment
  outline gives the equipment the line runs from or to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .drawing import Box, Seg, Sheet, Text
from .tags import Item

SNAP = 1.2  # points; endpoints closer than this are the same node
LABEL_GAP = 12.0  # max distance from a line label to its pipe
TAG_GAP = 15.0  # max distance from a valve tag to its symbol
NOZZLE = 24.0  # max gap between a run end and an equipment outline (the nozzle stub between them)
BRIDGE = 45.0  # max gap between facing pipe ends joined across an in-line symbol
MIN_RUN = 8.0  # runs shorter than this in total are symbol marks, not pipe


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _box_of(segs: list[Seg]) -> Box:
    xs = [v for s in segs for v in (s.x0, s.x1)]
    ys = [v for s in segs for v in (s.y0, s.y1)]
    return (min(xs), min(ys), max(xs), max(ys))


def _grow(b: Box, d: float) -> Box:
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def _inside(x: float, y: float, b: Box) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _box_gap(a: Box, b: Box) -> float:
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def _point_seg(x: float, y: float, s: Seg) -> float:
    dx, dy = s.x1 - s.x0, s.y1 - s.y0
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - s.x0) * dx + (y - s.y0) * dy) / L))
    px, py = s.x0 + t * dx, s.y0 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def _clusters(segs: list[Seg], pad: float = 1.0) -> list[list[Seg]]:
    """Group segments whose bounding boxes touch: one group per drawn symbol or outline."""
    d = _DSU(len(segs))
    boxes = [_grow(_box_of([s]), pad) for s in segs]
    order = sorted(range(len(segs)), key=lambda i: boxes[i][0])
    for a_i, a in enumerate(order):
        for b in order[a_i + 1 :]:
            if boxes[b][0] > boxes[a][2]:
                break
            if boxes[b][1] <= boxes[a][3] and boxes[a][1] <= boxes[b][3]:
                d.union(a, b)
    groups: dict[int, list[Seg]] = {}
    for i, s in enumerate(segs):
        groups.setdefault(d.find(i), []).append(s)
    return list(groups.values())


def _via_leader(t: Text, leaders: list[Seg], pipes: list[Seg]) -> Seg | None:
    """A diagonal leader starting at the label box points at the pipe the label belongs to."""
    box = _grow(t.box, 4.0)
    for ld in leaders:
        for (x, y), (ox, oy) in (((ld.x0, ld.y0), (ld.x1, ld.y1)), ((ld.x1, ld.y1), (ld.x0, ld.y0))):
            if _inside(x, y, box):
                best = min(pipes, key=lambda s: _point_seg(ox, oy, s), default=None)
                if best is not None and _point_seg(ox, oy, best) <= 3.0:
                    return best
    return None


@dataclass
class Run:
    id: int
    segs: list[Seg]
    labels: list[str] = field(default_factory=list)  # line keys whose label sits on this run
    ends: list[tuple[float, float]] = field(default_factory=list)  # open ends (not joined to anything)
    inherited: str = ""  # for an unlabelled run: the label of the nearest labelled run it branches from


@dataclass
class Topology:
    runs: list[Run]
    valve_line: dict[str, str]  # valve key -> line key ("" when no label reaches its run)
    valve_how: dict[str, str]  # valve key -> "label" (label on its own run) | "inherited" | ""
    line_ends: dict[str, set[str]]  # line key -> equipment its labelled runs end on
    branch_ends: dict[str, set[str]]  # line key -> extra equipment reached only through inherited branch runs
    valve_box: dict[str, Box]
    equipment_box: dict[str, Box]


def _axis(s: Seg) -> str:
    if abs(s.x1 - s.x0) < 0.5:
        return "v"
    if abs(s.y1 - s.y0) < 0.5:
        return "h"
    return "d"


def _direction(s: Seg, x: float, y: float) -> tuple[float, float]:
    """Unit vector pointing from the node (x, y) along s, away from the node."""
    ox, oy = (s.x1, s.y1) if abs(s.x0 - x) + abs(s.y0 - y) < abs(s.x1 - x) + abs(s.y1 - y) else (s.x0, s.y0)
    dx, dy = ox - x, oy - y
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    return dx / n, dy / n


def trace(sheet: Sheet, items: dict[str, dict[str, Item]]) -> Topology:
    strokes = [s for s in sheet.segs if s.kind == "pipe" and s.length > 0.5]
    pipes = [s for s in strokes if _axis(s) != "d"]
    leaders = [s for s in strokes if _axis(s) == "d"]
    key = lambda x, y: (round(x / SNAP), round(y / SNAP))  # noqa: E731

    # 1. Runs: corners join; at a junction only the straight-through pair joins.
    dsu = _DSU(len(pipes))
    at: dict[tuple[int, int], list[tuple[int, float, float]]] = {}
    for i, s in enumerate(pipes):
        for x, y in ((s.x0, s.y0), (s.x1, s.y1)):
            at.setdefault(key(x, y), []).append((i, x, y))
    for idx in at.values():
        segs_here = list({i for i, _, _ in idx})
        if len(segs_here) == 2:
            dsu.union(segs_here[0], segs_here[1])
        elif len(segs_here) >= 3:
            _, x, y = idx[0]
            dirs = {i: _direction(pipes[i], x, y) for i in segs_here}
            for a_i, a in enumerate(segs_here):
                for b in segs_here[a_i + 1 :]:
                    if dirs[a][0] * dirs[b][0] + dirs[a][1] * dirs[b][1] < -0.99:  # opposite: straight through
                        dsu.union(a, b)

    # Open ends: endpoints used by one segment only.
    def open_ends() -> list[tuple[int, float, float]]:
        return [idx[0] for idx in at.values() if len({i for i, _, _ in idx}) == 1]

    # 2. Bridge in-line symbols: facing ends on the same axis, short gap.
    ends = open_ends()
    for a_i, (a, ax, ay) in enumerate(ends):
        da = _direction(pipes[a], ax, ay)
        for b, bx, by in ends[a_i + 1 :]:
            gap = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if gap > BRIDGE or gap < 0.5:
                continue
            if not (abs(ax - bx) < 0.6 or abs(ay - by) < 0.6):
                continue
            db = _direction(pipes[b], bx, by)
            ux, uy = (bx - ax) / gap, (by - ay) / gap  # from a's end towards b's end
            # each pipe must point away from the gap: a's body lies behind a's end
            if da[0] * ux + da[1] * uy < -0.99 and db[0] * ux + db[1] * uy > 0.99:
                dsu.union(a, b)

    # Valve symbols: olive outlines; each joins the pipe ends that touch it.
    valve_symbols = [_box_of(c) for c in _clusters([s for s in sheet.segs if s.kind == "valve"])]
    for vb in valve_symbols:
        grown = _grow(vb, SNAP)
        touching = [
            i for i, s in enumerate(pipes)
            if _inside(s.x0, s.y0, grown) or _inside(s.x1, s.y1, grown)
        ]
        for j in touching[1:]:
            dsu.union(touching[0], j)

    groups: dict[int, list[int]] = {}
    for i in range(len(pipes)):
        groups.setdefault(dsu.find(i), []).append(i)
    runs = [Run(n, [pipes[i] for i in idx]) for n, idx in enumerate(groups.values())]
    runs = [r for r in runs if sum(seg.length for seg in r.segs) >= MIN_RUN]
    run_of = {}
    for r in runs:
        for s in r.segs:
            run_of[id(s)] = r

    # Open ends: endpoints used once, not inside a valve symbol.
    for r in runs:
        count: dict[tuple[int, int], int] = {}
        pos: dict[tuple[int, int], tuple[float, float]] = {}
        for s in r.segs:
            for x, y in ((s.x0, s.y0), (s.x1, s.y1)):
                k = key(x, y)
                count[k] = count.get(k, 0) + 1
                pos[k] = (x, y)
        r.ends = [
            pos[k] for k, c in count.items()
            if c == 1 and not any(_inside(*pos[k], _grow(vb, SNAP)) for vb in valve_symbols)
        ]

    # 3. Line labels: nearest parallel pipe segment beside the label.
    for line_key, item in items["line"].items():
        for t in item.texts:
            best, best_d = None, LABEL_GAP
            for s in pipes:
                vertical = abs(s.x1 - s.x0) < 0.5
                horizontal = abs(s.y1 - s.y0) < 0.5
                if t.vertical and vertical:
                    lo, hi = sorted((s.y0, s.y1))
                    overlap = min(hi, t.box[3]) - max(lo, t.box[1])
                    d = min(abs(s.x0 - t.box[0]), abs(s.x0 - t.box[2]))
                elif not t.vertical and horizontal:
                    lo, hi = sorted((s.x0, s.x1))
                    overlap = min(hi, t.box[2]) - max(lo, t.box[0])
                    d = min(abs(s.y0 - t.box[1]), abs(s.y0 - t.box[3]))
                else:
                    continue
                if overlap > 0 and d < best_d:
                    best, best_d = s, d
            if best is None:
                best = _via_leader(t, leaders, pipes)
            if best is not None and id(best) in run_of:
                run = run_of[id(best)]
                if line_key not in run.labels:
                    run.labels.append(line_key)

    # Unlabelled runs inherit from the nearest labelled run they connect to (tee or junction),
    # only when exactly one label is that close. Kept separate from direct labels for scoring.
    adjacent: dict[int, set[int]] = {r.id: set() for r in runs}
    for a in runs:
        pts = [(x, y) for sg in a.segs for x, y in ((sg.x0, sg.y0), (sg.x1, sg.y1))]
        for b in runs:
            if b.id <= a.id:
                continue
            bb = _grow(_box_of(b.segs), SNAP)
            if any(_inside(x, y, bb) and any(_point_seg(x, y, sg) <= SNAP for sg in b.segs) for x, y in pts) or any(
                any(_point_seg(x, y, sg) <= SNAP for sg in a.segs)
                for sg2 in b.segs for x, y in ((sg2.x0, sg2.y0), (sg2.x1, sg2.y1))
            ):
                adjacent[a.id].add(b.id)
                adjacent[b.id].add(a.id)
    by_id = {r.id: r for r in runs}
    for r in runs:
        if r.labels:
            continue
        seen, frontier = {r.id}, {r.id}
        while frontier:
            frontier = {n for f in frontier for n in adjacent[f]} - seen
            seen |= frontier
            found_labels = {lab for f in frontier for lab in by_id[f].labels}
            if found_labels:
                if len(found_labels) == 1:
                    r.inherited = found_labels.pop()
                break

    # 4. Valves: tag -> nearest symbol -> runs touching the symbol -> label.
    valve_line, valve_how, valve_box = {}, {}, {}
    label_pos = {k: [t.center for t in it.texts] for k, it in items["line"].items()}
    for vkey, item in items["valve"].items():
        t = item.texts[0]
        cands = [(vb, _box_gap(t.box, vb)) for vb in valve_symbols]
        cands = [c for c in cands if c[1] <= TAG_GAP]
        if not cands:
            continue
        vb = min(cands, key=lambda c: c[1])[0]
        valve_box[vkey] = vb
        grown = _grow(vb, SNAP)
        touching = {
            run_of[id(s)].id for s in pipes
            if id(s) in run_of and (_inside(s.x0, s.y0, grown) or _inside(s.x1, s.y1, grown))
        }
        labels = [lab for r in runs if r.id in touching for lab in r.labels]
        if labels:
            cx, cy = (vb[0] + vb[2]) / 2, (vb[1] + vb[3]) / 2
            valve_line[vkey] = min(
                labels, key=lambda lab: min((px - cx) ** 2 + (py - cy) ** 2 for px, py in label_pos[lab])
            )
            valve_how[vkey] = "label"
            continue
        inherited = {by_id[i].inherited for i in touching if by_id[i].inherited}
        valve_line[vkey] = inherited.pop() if len(inherited) == 1 else ""
        valve_how[vkey] = "inherited" if valve_line[vkey] else ""

    # 5. Equipment outlines (red) and the runs that end on them.
    outlines = [c for c in _clusters([s for s in sheet.segs if s.kind == "equipment"]) if len(c) >= 3]
    equipment_box: dict[str, Box] = {}
    for ekey, item in items["equipment"].items():
        t = item.texts[0]
        best = min(outlines, key=lambda c: _box_gap(t.box, _box_of(c)), default=None)
        if best is not None and _box_gap(t.box, _box_of(best)) < 60:
            equipment_box[ekey] = _box_of(best)
    eq_segs = {k: [s for c in outlines if _box_of(c) == b for s in c] for k, b in equipment_box.items()}

    line_ends: dict[str, set[str]] = {}
    branch_ends: dict[str, set[str]] = {}
    for r in runs:
        if not r.labels and not r.inherited:
            continue
        hit = set()
        for x, y in r.ends:
            near = [
                (min(_point_seg(x, y, s) for s in segs), ekey)
                for ekey, segs in eq_segs.items()
                if _inside(x, y, _grow(equipment_box[ekey], NOZZLE))
            ]
            near = [n for n in near if n[0] <= NOZZLE]
            if near:
                hit.add(min(near)[1])
        for lab in r.labels:
            line_ends.setdefault(lab, set()).update(hit)
        if r.inherited:
            branch_ends.setdefault(r.inherited, set()).update(hit)
    for lab, eqs in branch_ends.items():
        eqs -= line_ends.get(lab, set())
    return Topology(runs, valve_line, valve_how, line_ends, branch_ends, valve_box, equipment_box)
