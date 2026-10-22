from functools import lru_cache
from pathlib import Path

import pytest

from operon_pid import drawing, evaluate, model, query, tags, topology, truth
from operon_pid.drawing import Seg, Sheet, Text

DATA = Path(__file__).resolve().parents[1] / "data" / "dexpi" / "C08"
SHEET1 = DATA / "UER-1234567.pdf"


# --- label parsing --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, sub, tag, spec",
    [
        ("LR015.62004-80-10HB100 FC 246", None, "LR015",
         {"fluid": "62004", "dn": "80", "pn": "10", "piping_class": "HB100", "gasket": "FC", "valve_class": "246"}),
        ("LA01-LR045.10000-25-10H1200 NV 221", "LA01", "LR045",
         {"fluid": "10000", "dn": "25", "pn": "10", "piping_class": "H1200", "gasket": "NV", "valve_class": "221"}),
        ("LQ042.80153-25-10H2200 FC 216", None, "LQ042", {"fluid": "80153", "piping_class": "H2200"}),
        ("LR048", None, "LR048", {}),
    ],
)
def test_line_label(label, sub, tag, spec):
    m = tags.LINE_RE.fullmatch(label)
    assert m and m.group("sub") == sub and m.group("tag") == tag
    for k, v in spec.items():
        assert m.group(k) == v


# --- topology rules on hand-built geometry ---------------------------------------------------


def _sheet(segs, texts=()):
    return Sheet("test", "TA99", list(texts), [Seg(*s, kind=k, width=0.96) for *s, k in segs], [])


def _runs_of(sheet):
    topo = topology.trace(sheet, {"equipment": {}, "instrument": {}, "line": {}, "valve": {}})
    return sorted(sorted((s.x0, s.y0, s.x1, s.y1) for s in r.segs) for r in topo.runs)


def test_corner_joins_and_tee_branch_stays_separate():
    # A horizontal main split at x=50, with a branch going down from the split point.
    sheet = _sheet([(0, 0, 50, 0, "pipe"), (50, 0, 100, 0, "pipe"), (50, 0, 50, 40, "pipe"), (50, 40, 80, 40, "pipe")])
    runs = _runs_of(sheet)
    assert [(0, 0, 50, 0), (50, 0, 100, 0)] in runs  # straight-through pair continues
    assert [(50, 0, 50, 40), (50, 40, 80, 40)] in runs  # the branch and its corner are one run


def test_facing_ends_bridge_across_an_inline_symbol_but_offset_ends_do_not():
    sheet = _sheet([(0, 0, 40, 0, "pipe"), (60, 0, 100, 0, "pipe"), (0, 30, 40, 30, "pipe"), (60, 33, 100, 33, "pipe")])
    runs = _runs_of(sheet)
    assert [(0, 0, 40, 0), (60, 0, 100, 0)] in runs
    assert [(0, 30, 40, 30)] in runs and [(60, 33, 100, 33)] in runs


def test_label_attaches_to_the_parallel_pipe_beside_it():
    label = Text("LR015.62004-80-10HB100 FC 246", (10, -12, 90, -2), 10.2, False)
    sheet = _sheet([(0, 0, 100, 0, "pipe"), (0, 60, 100, 60, "pipe")], [label])
    found = tags.extract(sheet)
    topo = topology.trace(sheet, found)
    labelled = [r for r in topo.runs if r.labels]
    assert len(labelled) == 1 and labelled[0].segs[0].y0 == 0 and labelled[0].labels == ["TA99-LR015"]


# --- the Covestro sheet against the owner's lists ---------------------------------------------


@lru_cache
def _sheet1():
    t = truth.load(DATA).on_drawing("UER-1234567")
    m, sheet, found, topo = model.build(SHEET1)
    return t, m, found, topo


def test_answer_key_counts():
    t = truth.load(DATA)
    s1, s2 = t.on_drawing("UER-1234567"), t.on_drawing("UER-2345678")
    assert (len(s1.equipment), len(s1.instruments), len(s1.lines), len(s1.valves)) == (16, 40, 48, 74)
    assert (len(s2.equipment), len(s2.instruments), len(s2.lines), len(s2.valves)) == (4, 6, 19, 6)


def test_tags_on_sheet1():
    t, _, found, _ = _sheet1()
    scores = evaluate.score_tags(found, t)
    assert all(s.precision == 1.0 for s in scores.values())
    assert scores["equipment"].recall == 1.0 and scores["instrument"].recall == 1.0
    assert scores["line"].correct == 45 and scores["valve"].correct == 73


def test_line_specs_and_instrument_functions_match_the_lists():
    t, _, found, _ = _sheet1()
    for name, f in evaluate.score_fields(found, t).items():
        assert f["correct"] == f["checked"] > 0, (name, f["mismatches"])


def test_connections_on_sheet1():
    t, _, found, topo = _sheet1()
    c = evaluate.score_topology(found, topo, t)
    assert c["valve on line (label on its run)"]["correct"] >= 32
    ends = c["line ends at equipment"]
    assert ends["labelled run"]["found"] == ends["labelled run"]["correct"] >= 28


def test_questions():
    _, m, _, _ = _sheet1()
    assert query.ask(m, "What connects PA103 and WA010?").startswith("TA99-LR015: fluid 62004")
    assert "TA99-AB020 is on TA99-LR015" in query.ask(m, "Which line is AB020 on?")
    assert "TA99-F0026 FRC" in query.ask(m, "flow instruments")
