"""Score what was extracted from a drawing against the owner's lists for that drawing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tags import SPEC_FIELDS, Item
from .truth import Truth

KINDS = {"equipment": "equipment", "instrument": "instruments", "line": "lines", "valve": "valves"}


@dataclass
class Score:
    expected: int
    found: int
    correct: int
    missed: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.correct / self.found if self.found else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.expected if self.expected else 0.0


def score_tags(found: dict[str, dict[str, Item]], truth: Truth) -> dict[str, Score]:
    out = {}
    for kind, attr in KINDS.items():
        exp, got = set(getattr(truth, attr)), set(found[kind])
        out[kind] = Score(len(exp), len(got), len(exp & got), sorted(exp - got), sorted(got - exp))
    return out


def score_fields(found: dict[str, dict[str, Item]], truth: Truth) -> dict[str, dict]:
    """Field-level accuracy on items found and expected: instrument functions and line specs."""
    out: dict[str, dict] = {}
    pairs = [("instrument function", "instrument", "instruments", ("function",))]
    pairs += [(f"line {f}", "line", "lines", (f,)) for f in SPEC_FIELDS]
    for label, kind, attr, fields in pairs:
        expected = getattr(truth, attr)
        checked, right, wrong = 0, 0, []
        for key, item in found[kind].items():
            if key not in expected:
                continue
            for f in fields:
                if f not in item.attrs:
                    continue  # tag-only label (e.g. a bare "LR048"), nothing to compare
                checked += 1
                accepted = expected[key][f].split()
                if item.attrs[f] in accepted:
                    right += 1
                else:
                    wrong.append(f"{key}: drawing {item.attrs[f]!r}, list {expected[key][f]!r}")
        out[label] = {"checked": checked, "correct": right, "mismatches": wrong}
    return out


def _equipment_in(text: str, equipment: set[str]) -> set[str]:
    """Equipment keys named in a From/To cell, e.g. 'COVPIL1-V999-TA99-PA103/N2' -> TA99-PA103."""
    keys = {f"{m.group(1)}-{m.group(2)}" for m in re.finditer(r"V999-([A-Z]{2}\d{2})-([A-Z]{2}\d{3})", text)}
    return keys & equipment


def score_topology(found: dict[str, dict[str, Item]], topo, truth: Truth) -> dict[str, dict]:
    # Valve -> line, scored separately for labels on the valve's own run and inherited ones.
    tiers = {"label": [0, 0, []], "inherited": [0, 0, []]}
    unresolved = 0
    for vkey, line in topo.valve_line.items():
        expected = truth.valves.get(vkey, {}).get("line")
        if not expected:
            continue
        how = topo.valve_how.get(vkey, "")
        if not how:
            unresolved += 1
            continue
        tier = tiers[how]
        tier[0] += 1
        if line == expected:
            tier[1] += 1
        else:
            tier[2].append(f"{vkey}: traced {line}, list {expected}")
    no_symbol = sorted(k for k in found["valve"] if k not in topo.valve_box)

    # Line -> equipment, for lines found. Lines whose list entry has no From/To can't be checked.
    equipment = set(truth.equipment)
    exp_pairs: set[tuple[str, str]] = set()
    tier_pairs: dict[str, set[tuple[str, str]]] = {"labelled run": set(), "branch run": set()}
    unscorable = set()
    for lkey in found["line"]:
        t = truth.lines.get(lkey)
        if not t:
            continue
        if not (t["from"] or t["to"]):
            unscorable |= {(lkey, e) for e in topo.line_ends.get(lkey, set()) | topo.branch_ends.get(lkey, set())}
            continue
        exp_pairs |= {(lkey, e) for e in _equipment_in(t["from"] + " " + t["to"], equipment)}
        tier_pairs["labelled run"] |= {(lkey, e) for e in topo.line_ends.get(lkey, ())}
        tier_pairs["branch run"] |= {(lkey, e) for e in topo.branch_ends.get(lkey, ())}
    got_all = tier_pairs["labelled run"] | tier_pairs["branch run"]
    ends = {
        "expected": len(exp_pairs),
        "missed": sorted(f"{a} -> {b}" for a, b in exp_pairs - got_all),
        "not checkable (list has no From/To)": sorted(f"{a} -> {b}" for a, b in unscorable),
    }
    for tier, pairs in tier_pairs.items():
        ends[tier] = {
            "found": len(pairs), "correct": len(pairs & exp_pairs),
            "wrong": sorted(f"{a} -> {b}" for a, b in pairs - exp_pairs),
        }
    return {
        "valve on line (label on its run)": {
            "checked": tiers["label"][0], "correct": tiers["label"][1], "mismatches": tiers["label"][2],
        },
        "valve on line (inherited)": {
            "checked": tiers["inherited"][0], "correct": tiers["inherited"][1], "mismatches": tiers["inherited"][2],
        },
        "valve on line (unresolved)": {"count": unresolved, "no symbol matched": no_symbol},
        "line ends at equipment": ends,
    }
