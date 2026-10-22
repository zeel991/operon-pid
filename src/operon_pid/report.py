"""Build both Covestro sheets, score them against the owner's lists, and write reports/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from . import evaluate, model, truth

SHEETS = ("UER-1234567", "UER-2345678")


def _color(s: str) -> tuple[float, float, float]:
    h = hashlib.md5(s.encode()).digest()
    return (h[0] / 255 * 0.75, h[1] / 255 * 0.75, h[2] / 255 * 0.75)


def overlay(pdf: Path, topo, out: Path, dpi: int = 60, clip: tuple[float, float, float, float] | None = None) -> Path:
    """Traced runs drawn over the sheet: one colour per line, dashed where the label was inherited."""
    doc = pymupdf.open(pdf)
    page = doc[0]
    for r in topo.runs:
        lab = r.labels[0] if r.labels else r.inherited
        if not lab:
            continue
        for s in r.segs:
            page.draw_line((s.x0, s.y0), (s.x1, s.y1), color=_color(lab), width=5 if r.labels else 3,
                           dashes=None if r.labels else "[6 4] 0", stroke_opacity=0.75)
    for b in topo.valve_box.values():
        page.draw_rect(b, color=(0, 0.45, 1), width=1.2)
    page.get_pixmap(dpi=dpi, clip=pymupdf.Rect(*clip) if clip else None).save(out)
    return out


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.0%})" if b else "n/a"


def build(data: Path, out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    t = truth.load(data)
    results, md = {}, []
    for name in SHEETS:
        pdf = data / f"{name}.pdf"
        m, sheet, found, topo = model.build(pdf)
        tr = t.on_drawing(name)
        tag_scores = evaluate.score_tags(found, tr)
        fields = evaluate.score_fields(found, tr)
        conn = evaluate.score_topology(found, topo, tr)
        (out / f"{name}.model.json").write_text(m.to_json())
        overlay(pdf, topo, out / f"{name}-overlay.png")
        if name == SHEETS[0]:  # close-up around PA103, BA101 and WA006/WA010 for the README
            overlay(pdf, topo, out / f"{name}-zoom.png", dpi=110, clip=(1100, 560, 1800, 1000))
        results[name] = {
            "tags": {k: {**vars(s), "precision": s.precision, "recall": s.recall} for k, s in tag_scores.items()},
            "fields": fields,
            "connections": conn,
        }

        md.append(f"## {name} (subprocess {sheet.subprocess})\n")
        md.append("| Item | In the list for this sheet | Found | Correct | Precision | Recall |")
        md.append("|---|---|---|---|---|---|")
        for k, s in tag_scores.items():
            md.append(f"| {k} | {s.expected} | {s.found} | {s.correct} | {s.precision:.0%} | {s.recall:.0%} |")
        md.append("")
        page_words = [w[4] for w in pymupdf.open(pdf)[0].get_text("words")]

        def labelled(key: str) -> bool:
            sub, tag = key.split("-", 1)
            return any(f"{sub}-{tag}" in w or (sub == sheet.subprocess and w.split(".")[0] == tag) for w in page_words)

        missed = [k for s in tag_scores.values() for k in s.missed]
        absent = [k for k in missed if not labelled(k)]
        present = [k for k in missed if labelled(k)]
        if absent:
            md.append("Missed, and the tag is not written anywhere on the page: " + ", ".join(absent) + "\n")
        if present:
            md.append("Missed although the tag is written on the page: " + ", ".join(present) + "\n")
        md.append("| Field | Correct |")
        md.append("|---|---|")
        for k, f in fields.items():
            md.append(f"| {k} | {_pct(f['correct'], f['checked'])} |")
        md.append("")
        lab, inh, unres = (conn["valve on line (label on its run)"], conn["valve on line (inherited)"],
                           conn["valve on line (unresolved)"])
        ends = conn["line ends at equipment"]
        md.append("| Connection | Result |")
        md.append("|---|---|")
        md.append(f"| Valve on line, label on its own run | {_pct(lab['correct'], lab['checked'])} |")
        md.append(f"| Valve on line, inherited from parent run | {_pct(inh['correct'], inh['checked'])} |")
        md.append(f"| Valve on line, not resolved | {unres['count']} |")
        got = ends["labelled run"]["correct"] + ends["branch run"]["correct"]
        found_n = ends["labelled run"]["found"] + ends["branch run"]["found"]
        md.append(f"| Line ends at equipment: found of expected | {_pct(got, ends['expected'])} |")
        md.append(f"| Line ends at equipment: correct of reported | {_pct(got, found_n)} |")
        md.append(f"| ...from labelled runs | {_pct(ends['labelled run']['correct'], ends['labelled run']['found'])} correct |")
        md.append(f"| ...through branch runs | {_pct(ends['branch run']['correct'], ends['branch run']['found'])} correct |")
        md.append(f"| Links on lines whose list entry has no From/To (not checkable) | {len(ends['not checkable (list has no From/To)'])} |")
        md.append("")
        wrong = lab["mismatches"] + inh["mismatches"] + ends["labelled run"]["wrong"] + ends["branch run"]["wrong"]
        if wrong:
            md.append("Wrong: " + "; ".join(wrong) + "\n")
        md.append(f"![Traced runs on {name}]({name}-overlay.png)\n")

    (out / "results.json").write_text(json.dumps(results, indent=1))
    text = (
        "# Extraction results: Covestro C08 P&IDs (DEXPI test cases)\n\n"
        "Scored against Covestro's own equipment, instrument, line and valve lists exported from the same "
        "SmartPlant model. Generated by `operon-pid report`.\n\n" + "\n".join(md)
    )
    (out / "EVAL.md").write_text(text)
    return text
