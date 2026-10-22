"""Plain-language questions over a plant model.

Rule-based: it recognises the tags in a question and a few intents. Each answer says how a
connection was found, so an engineer can tell a traced label from an inferred branch.
"""

from __future__ import annotations

import re

from .model import Model

VARIABLES = {"flow": "F", "level": "L", "pressure": "P", "temperature": "T", "analysis": "A", "position": "Y"}
HOW = {"label": "line label on its run", "inherited": "inherited from the run it branches off", "": "not traced"}


def _resolve(m: Model, tag: str) -> list[tuple[str, str]]:
    """(kind, key) for a tag as typed: 'LR015', 'TA99-LR015' or 'LA01-BA009'."""
    tag = tag.upper()
    out = []
    for kind in ("equipment", "lines", "valves", "instruments"):
        for key in getattr(m, kind):
            if key == tag or key.endswith("-" + tag):
                out.append((kind, key))
    # Prefer the sheet's own subprocess when a bare tag matches several.
    own = [o for o in out if o[1].startswith(m.subprocess + "-")]
    return own if own and "-" not in tag else out


def _line_summary(m: Model, key: str) -> str:
    ln = m.lines[key]
    spec = ", ".join(
        f"{name} {ln[f]}"
        for f, name in (("fluid", "fluid"), ("dn", "DN"), ("pn", "PN"), ("piping_class", "class"),
                        ("gasket", "gasket"), ("valve_class", "valve class"))
        if f in ln
    )
    ends = ", ".join(ln["ends"]) or "no equipment end traced"
    branch = f"; via branches also {', '.join(ln['branch_ends'])}" if ln["branch_ends"] else ""
    return f"{key}: {spec or 'no spec on its label'}. Ends at {ends}{branch}."


def _valves_on(m: Model, key: str) -> list[str]:
    return [f"{k} ({HOW[v['found_by']]})" for k, v in sorted(m.valves.items()) if v["line"] == key]


def _lines_at(m: Model, eq: str) -> list[str]:
    out = []
    for k, ln in sorted(m.lines.items()):
        if eq in ln["ends"]:
            out.append(f"{k} (line label on its run)")
        elif eq in ln["branch_ends"]:
            out.append(f"{k} (through a branch)")
    return out


def ask(m: Model, question: str) -> str:
    q = question.strip()
    tags = re.findall(r"\b(?:[A-Z]{2}\d{2}-)?(?:[A-Z]{2}\d{3}|[A-Z]\d{4})\b", q.upper())
    hits = [h for t in tags for h in _resolve(m, t)]

    # Between two pieces of equipment.
    eqs = [k for kind, k in hits if kind == "equipment"]
    if len(eqs) >= 2 and re.search(r"\bbetween\b|\bconnect", q, re.I):
        a, b = eqs[:2]
        both = [k for k, ln in m.lines.items() if {a, b} <= set(ln["ends"]) | set(ln["branch_ends"])]
        if not both:
            return f"No traced line runs between {a} and {b} on {m.sheet}."
        return "\n".join(_line_summary(m, k) for k in sorted(both))

    if hits:
        kind, key = hits[0]
        if kind == "lines":
            if re.search(r"valve|on (the )?line|what'?s on", q, re.I):
                valves = _valves_on(m, key)
                return f"Valves on {key}: " + (", ".join(valves) if valves else "none traced") + "."
            valves = _valves_on(m, key)
            return _line_summary(m, key) + f" {len(valves)} valve(s) traced on it."
        if kind == "equipment":
            lines = _lines_at(m, key)
            if not lines:
                return f"{key}: no traced line ends at it on {m.sheet}."
            return f"Lines at {key}: " + "; ".join(lines) + "."
        if kind == "valves":
            v = m.valves[key]
            if not v["line"]:
                return f"{key}: found on the drawing, but its pipe run has no line label it can be traced to."
            return f"{key} is on {v['line']} ({HOW[v['found_by']]}). " + _line_summary(m, v["line"])
        if kind == "instruments":
            i = m.instruments[key]
            return f"{key}: function {i['function']} (loop {i['loop']})."

    if fluid := re.search(r"\bfluid\s*(\d{5})|\b(\d{5})\b", q):
        code = fluid.group(1) or fluid.group(2)
        lines = sorted(k for k, ln in m.lines.items() if ln.get("fluid") == code)
        return f"Lines carrying fluid {code}: " + (", ".join(lines) if lines else "none") + "."

    for word, letter in VARIABLES.items():
        if re.search(rf"\b{word}\b", q, re.I):
            inst = sorted(f"{k} {v['function']}" for k, v in m.instruments.items() if v["function"].startswith(letter))
            return f"{word.capitalize()} instruments on {m.sheet}: " + (", ".join(inst) if inst else "none") + "."

    return (
        "Ask about a tag (a line like LR015, equipment like BA101, a valve like AB020, a loop like F0026), "
        "'what connects PA103 and WA010', 'lines carrying fluid 62004', or 'flow instruments'."
    )
