"""Score a filled adjudication worksheet against the first-reader (pipeline) key.

    PYTHONIOENCODING=utf-8 python scripts/score_double_adjudication.py [--date 2026-09-15] [--prefix double_adjudication]

Treats the reader (human or model) as the reference and the pipeline's verdict as the test. Reports,
with rescope counted as admit and separately as reject:
  - accuracy (share of records where the pipeline's verdict matches the reader's) with a 95% Wilson interval
  - sensitivity (pipeline admits | reader admits) and specificity (pipeline rejects | reader rejects), each with a Wilson interval
  - Cohen's kappa with a 95% bootstrap interval
  - accuracy per record kind with Wilson intervals
and lists every disagreement. The worksheet's verdict column may be named reader_verdict or second_reader_verdict.
"""
import argparse
import csv
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def kappa(a, b):
    n = len(a)
    if n == 0:
        return float("nan")
    cats = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion k/n."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, centre - half, centre + half


def fmt(k, n):
    p, lo, hi = wilson(k, n)
    return f"{p:.2f} ({k}/{n}; 95% CI {lo:.2f} to {hi:.2f})" if n else "n/a (0 items)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-15")
    ap.add_argument("--prefix", default="double_adjudication")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--exclude", default="", help="comma-separated item numbers to leave out (e.g. items primed before the reading)")
    args = ap.parse_args()
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    ws_rows = list(csv.DictReader(open(ROOT / "logs" / f"{args.prefix}_worksheet_{args.date}.csv", encoding="utf-8")))
    ws = {r["item"]: r for r in ws_rows}
    key = {r["item"]: r for r in csv.DictReader(open(ROOT / "logs" / f"{args.prefix}_key_{args.date}.csv", encoding="utf-8"))}
    vcol = "reader_verdict" if "reader_verdict" in ws_rows[0] else "second_reader_verdict"
    ncol = "reader_note" if "reader_note" in ws_rows[0] else "second_reader_note"
    # rows whose note carries [NONBLIND] are reader confirmations made with the pipeline
    # verdict visible; they are never part of the blinded agreement estimate
    nonblind = {i for i in ws if "[NONBLIND" in (ws[i].get(ncol) or "").upper()}
    filled = [i for i in ws if ws[i][vcol].strip() and i not in excluded and i not in nonblind]
    if not filled:
        raise SystemExit(f"no {vcol} values filled in yet")
    if nonblind:
        print(f"excluded {len(nonblind)} non-blind confirmation items ([NONBLIND] marker): "
              f"{', '.join(sorted(nonblind, key=int))}")
    if excluded:
        print(f"excluded items: {', '.join(sorted(excluded, key=int))}")
    first = [key[i]["first_reader"] for i in filled]
    second_raw = [ws[i][vcol].strip().lower() for i in filled]
    kinds = [key[i]["kind"] for i in filled]
    bad = sorted({v for v in second_raw if v not in ("admit", "rescope", "reject")})
    if bad:
        raise SystemExit(f"unrecognised verdicts: {bad}")
    print(f"{len(filled)} of {len(ws)} items scored ({args.prefix}, {args.date}); reader = reference, pipeline verdict = test")
    for label, mapping in (("rescope -> admit", {"rescope": "admit"}), ("rescope -> reject", {"rescope": "reject"})):
        second = [mapping.get(v, v) for v in second_raw]
        agree = sum(1 for x, y in zip(first, second) if x == y)
        tp = sum(1 for x, y in zip(first, second) if x == "admit" and y == "admit")
        fn = sum(1 for x, y in zip(first, second) if x == "reject" and y == "admit")
        tn = sum(1 for x, y in zip(first, second) if x == "reject" and y == "reject")
        fp = sum(1 for x, y in zip(first, second) if x == "admit" and y == "reject")
        k = kappa(first, second)
        rng = random.Random(1)
        boots = []
        for _ in range(args.boot):
            idx = [rng.randrange(len(first)) for _ in first]
            boots.append(kappa([first[i] for i in idx], [second[i] for i in idx]))
        boots = sorted(b for b in boots if b == b)
        lo, hi = (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]) if boots else (float("nan"), float("nan"))
        print(f"\n[{label}]")
        print(f"  accuracy     {fmt(agree, len(first))}")
        print(f"  sensitivity  {fmt(tp, tp + fn)}   (pipeline admits | reader admits)")
        print(f"  specificity  {fmt(tn, tn + fp)}   (pipeline rejects | reader rejects)")
        print(f"  kappa        {k:.2f} (95% bootstrap {lo:.2f} to {hi:.2f})")
        print("  per kind:")
        for kind in sorted(set(kinds)):
            sel = [j for j, kk in enumerate(kinds) if kk == kind]
            ag = sum(1 for j in sel if first[j] == second[j])
            print(f"    {kind:<3} accuracy {fmt(ag, len(sel))}")
    print("\nDisagreements (pipeline vs reader):")
    for i, f, s in zip(filled, first, second_raw):
        if f != s:
            print(f"  item {i} {key[i]['record']} ({key[i]['kind']}): pipeline={f} reader={s} | {ws[i][ncol][:160]}")


if __name__ == "__main__":
    main()
