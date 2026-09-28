"""Record one verdict in an adjudication worksheet without opening the CSV in a spreadsheet.

    PYTHONIOENCODING=utf-8 python scripts/record_adjudication.py ITEM VERDICT "note" [--date 2026-09-19] [--prefix human_adjudication]
    PYTHONIOENCODING=utf-8 python scripts/record_adjudication.py --show ITEM        # print one item's claim, papers and PDFs
    PYTHONIOENCODING=utf-8 python scripts/record_adjudication.py --progress         # how many items are done

VERDICT is admit, rescope or reject. The note should say what the decision rests on (block field or
PDF page). Rewrites only the reader_verdict / reader_note cells of that row; everything else is
preserved byte-for-byte as csv writes it (UTF-8, quoted fields).
"""
import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("item", nargs="?")
    ap.add_argument("verdict", nargs="?")
    ap.add_argument("note", nargs="?", default="")
    ap.add_argument("--date", default="2026-09-19")
    ap.add_argument("--prefix", default="human_adjudication")
    ap.add_argument("--show", metavar="ITEM", help="print one item in full and exit")
    ap.add_argument("--progress", action="store_true", help="count filled items and exit")
    args = ap.parse_args()
    path = ROOT / "logs" / f"{args.prefix}_worksheet_{args.date}.csv"
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    vcol = "reader_verdict" if "reader_verdict" in fields else "second_reader_verdict"
    ncol = "reader_note" if "reader_note" in fields else "second_reader_note"
    by = {r["item"]: r for r in rows}

    if args.progress:
        done = [r for r in rows if r[vcol].strip()]
        print(f"{len(done)} of {len(rows)} items have a verdict in {path.name}")
        todo = [r["item"] for r in rows if not r[vcol].strip()]
        print("next items:", ", ".join(todo[:10]) + (" ..." if len(todo) > 10 else ""))
        return
    if args.show:
        r = by.get(args.show)
        if not r:
            raise SystemExit(f"no item {args.show}")
        print(f"item {r['item']}  record {r['record']}  kind {r['kind']}")
        print("claim :", r["claim"])
        print("papers:", r["papers"])
        print("pdfs  :", r["pdfs"] or "(none in the map; look under downloads/)")
        print("verdict so far:", repr(r[vcol]), "| note:", repr(r[ncol]))
        return
    if not (args.item and args.verdict):
        ap.error("give ITEM and VERDICT, or --show ITEM, or --progress")
    v = args.verdict.strip().lower()
    if v not in ("admit", "rescope", "reject"):
        raise SystemExit("verdict must be admit, rescope or reject")
    r = by.get(args.item)
    if not r:
        raise SystemExit(f"no item {args.item}")
    r[vcol] = v
    r[ncol] = args.note
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    done = sum(1 for x in rows if x[vcol].strip())
    print(f"item {args.item} ({r['record']}): {v} recorded; {done} of {len(rows)} done")


if __name__ == "__main__":
    main()
