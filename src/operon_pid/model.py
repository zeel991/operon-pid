"""The plant model built from one sheet: equipment, instruments, lines with specs and ends, valves on lines.

Every connection records how it was found: "label" when the line's own label sits on the run,
"branch" when it was inherited from the run the branch leaves. Answers carry that provenance.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import drawing, tags, topology


@dataclass
class Model:
    sheet: str
    subprocess: str
    equipment: dict[str, dict] = field(default_factory=dict)
    instruments: dict[str, dict] = field(default_factory=dict)
    lines: dict[str, dict] = field(default_factory=dict)
    valves: dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> Model:
        return cls(**json.loads(text))


def build(pdf: Path) -> tuple[Model, drawing.Sheet, dict, topology.Topology]:
    sheet = drawing.load(pdf)
    found = tags.extract(sheet)
    topo = topology.trace(sheet, found)
    m = Model(sheet.name, sheet.subprocess)
    for k, it in found["equipment"].items():
        m.equipment[k] = {"tag": it.tag, "box": [round(v, 1) for v in topo.equipment_box.get(k, it.texts[0].box)]}
    for k, it in found["instrument"].items():
        m.instruments[k] = {"loop": it.tag, "function": it.attrs.get("function", "")}
    for k, it in found["line"].items():
        m.lines[k] = {
            "tag": it.tag,
            **{f: it.attrs[f] for f in tags.SPEC_FIELDS if f in it.attrs},
            "ends": sorted(topo.line_ends.get(k, ())),
            "branch_ends": sorted(topo.branch_ends.get(k, ())),
        }
    for k, it in found["valve"].items():
        m.valves[k] = {"tag": it.tag, "line": topo.valve_line.get(k, ""), "found_by": topo.valve_how.get(k, "")}
    return m, sheet, found, topo
