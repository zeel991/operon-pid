"""Answer key: Covestro's SmartPlant equipment, instrument, line and valve lists for the C08 P&IDs.

The lists are exported from the same SmartPlant model the drawings were printed from, so they are
an independent record of what each drawing should contain. Only cell values are read; macros never run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openpyxl
import xlrd


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows(path: Path, header: str = "Technical Item") -> list[dict[str, str]]:
    if path.suffix == ".xls":
        sheet = xlrd.open_workbook(path).sheet_by_name("Sheet1")
        raw = [[c.value for c in sheet.row(r)] for r in range(sheet.nrows)]
    else:
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False)["Sheet1"]
        raw = [list(r) for r in ws.iter_rows(values_only=True)]
    start = next(i for i, r in enumerate(raw) if any(_cell(v) == header for v in r))
    names = [_cell(h).replace("\n", " ") for h in raw[start]]
    out = []
    for r in raw[start + 1 :]:
        row = {names[i]: _cell(v) for i, v in enumerate(r) if i < len(names) and names[i]}
        if any(row.values()):
            out.append(row)
    return out


@dataclass
class Truth:
    equipment: dict[str, dict] = field(default_factory=dict)  # "TA99-BA100" -> {...}
    instruments: dict[str, dict] = field(default_factory=dict)  # "TA99-F0017" -> {function, drawing}
    lines: dict[str, dict] = field(default_factory=dict)  # "TA99-LR015" -> {spec, from, to, drawings}
    valves: dict[str, dict] = field(default_factory=dict)  # "TA99-AB020" -> {type, line, drawing}

    def on_drawing(self, drawing: str) -> Truth:
        return Truth(
            equipment={k: v for k, v in self.equipment.items() if v["drawing"] == drawing},
            instruments={k: v for k, v in self.instruments.items() if v["drawing"] == drawing},
            lines={k: v for k, v in self.lines.items() if drawing in v["drawings"]},
            valves={k: v for k, v in self.valves.items() if v["drawing"] == drawing},
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, ensure_ascii=False)


def load(folder: Path) -> Truth:
    t = Truth()
    for r in _rows(folder / "COV Equipment List.xlsm"):
        if not r.get("Technical Item"):
            continue
        key = f"{r['Subprocess']}-{r['Technical Item']}"
        t.equipment[key] = {"tag": r["Technical Item"], "type": r["Equipment Type"], "drawing": r["P&ID Name"]}

    for r in _rows(folder / "COV Instrument List.xlsm"):
        # One row per loop function (the bubble) plus rows for in-line elements (orifices, control valves).
        # Bubbles are filed as "Off-line instruments", or "System functions" for control functions like UC.
        if r.get("Instr Class") not in ("Off-line instruments", "System functions"):
            continue
        key = f"{r['Subprocess']}-{r['Technical Item']}"
        t.instruments[key] = {
            "loop": r["Technical Item"],
            "function": r["Measured Variable"] + r.get("Loop Function", ""),
            "drawing": r["PID Name"],
        }

    for r in _rows(folder / "COV Line List.xls"):
        key = f"{r['Sub-process']}-{r['Technical Item']}"
        t.lines[key] = {
            "tag": r["Technical Item"],
            "fluid": r["Fluid Code (Shorttext)"],
            "dn": r["DN List (Shorttext)"] or r["DN (Shorttext)"],
            "pn": r["Nominal Pressure"],
            "piping_class": r["Piping Classifi-cation"],
            "gasket": r["Gasket (Shorttext)"],
            "valve_class": r["Valve Class"],
            "from": r["From"].replace("\n", " "),
            "to": r["To"].replace("\n", " "),
            "drawings": r["All Drawings for the Line"].split(),
        }

    for r in _rows(folder / "COV Valve List.xlsm"):
        if r.get("Industrial Complex") == "No Itemtag!" or not r.get("Technical Item"):
            continue  # untagged fittings (flanges, blinds) carry no tag to find
        key = f"{r['Subprocess']}-{r['Technical Item']}"
        line = f"{r['Pipe Subprocess']}-{r['Pipe Technical item']}" if r.get("Pipe Technical item") else ""
        t.valves[key] = {"tag": r["Technical Item"], "type": r["Type"], "line": line, "drawing": r["Drawing Name"]}
    return t
