"""operon-pid: turn vector P&IDs into a queryable plant model and score it against the owner's lists."""

from __future__ import annotations

import argparse
from pathlib import Path

DATA = Path("data/dexpi/C08")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="operon-pid", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="extract both Covestro sheets and score them (writes reports/)")
    r.add_argument("--data", type=Path, default=DATA)
    r.add_argument("--out", type=Path, default=Path("reports"))

    e = sub.add_parser("extract", help="build the plant model for one vector PDF")
    e.add_argument("pdf", type=Path)
    e.add_argument("--out", type=Path, help="write the model JSON here instead of printing it")

    a = sub.add_parser("ask", help="ask a question about a sheet")
    a.add_argument("pdf", type=Path)
    a.add_argument("question", nargs="+")

    args = p.parse_args(argv)
    if args.cmd == "report":
        from . import report

        report.build(args.data, args.out)
        print(f"Wrote {args.out / 'EVAL.md'}, results.json, models and overlays.")
        return 0

    from . import model

    m, *_ = model.build(args.pdf)
    if args.cmd == "extract":
        if args.out:
            args.out.write_text(m.to_json())
            print(f"Wrote {args.out}")
        else:
            print(m.to_json())
        return 0

    from . import query

    print(query.ask(m, " ".join(args.question)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
