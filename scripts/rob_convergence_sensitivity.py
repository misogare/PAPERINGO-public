"""Does each admitted convergence survive the risk-of-bias appraisal of its supporters?

Added 2026-09-28. For every convergence admitted to the synthesis (Table A1), the supporters' final risk-of-bias
category (logs/rob_appraisal: the adjudicated judgment where the blinded second reading disagreed, otherwise the primary
one) is counted, and the convergence is flagged if fewer than three supporters remain that are not at high risk of bias
(the review's three-supporter minimum applied to the non-high set). Supporters listed in rejected_supporters are
excluded, as in the inclusion rule.

    PYTHONIOENCODING=utf-8 python scripts/rob_convergence_sensitivity.py [--table]
"""
import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROB = ROOT / "logs" / "rob_appraisal"


def final_category(pid):
    for p in (ROB / "adjudicated" / f"{pid}.json", ROB / f"{pid}.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("overall_category", "")
    return "not_appraised"


def admitted_convergences():
    t = (ROOT / "literature_review.md").read_text(encoding="utf-8") if (ROOT / "literature_review.md").exists() else ""
    if t and "**Table A1." in t:
        a = t.index("**Table A1.")
        seg = t[a:t.index("\n## ", a)]
        return [re.match(r"\| (VF-\d+)", l).group(1) for l in seg.split("\n") if l.startswith("| VF-")]
    # public release: the manuscript is not distributed; the admitted set is listed next to the appraisal records
    return (ROB / "admitted_convergences.txt").read_text(encoding="utf-8").split()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="store_true", help="print a markdown table")
    a = ap.parse_args()
    cons = json.loads((ROOT / "data" / "consensus.json").read_text(encoding="utf-8"))
    recs = cons if isinstance(cons, list) else next(v for v in cons.values() if isinstance(v, list))
    byid = {r.get("id"): r for r in recs}
    rows = []
    for vf in admitted_convergences():
        r = byid[vf]
        rej = set(r.get("rejected_supporters") or [])
        sup = [s if isinstance(s, str) else s.get("paper_id") for s in (r.get("supporting_papers") or [])]
        sup = [s for s in sup if s and s not in rej]
        cats = {s: final_category(s) for s in sup}
        c = collections.Counter(cats.values())
        non_high = [s for s, k in cats.items() if k in ("low", "some_concerns")]
        rows.append({"vf": vf, "n": len(sup), "low": c["low"], "some_concerns": c["some_concerns"], "high": c["high"],
                     "other": len(sup) - c["low"] - c["some_concerns"] - c["high"],
                     "non_high": len(non_high), "survives": len(non_high) >= 3,
                     "high_ids": sorted(s for s, k in cats.items() if k == "high"),
                     "other_ids": sorted(f"{s} ({k})" for s, k in cats.items() if k not in ("low", "some_concerns", "high"))})
    if a.table:
        print("| Convergence | Supporters | Low | Some concerns | High | Not rated | Not at high risk | Three or more remain |")
        print("|---|---|---|---|---|---|---|---|")
        for x in rows:
            print(f"| {x['vf']} | {x['n']} | {x['low']} | {x['some_concerns']} | {x['high']} | {x['other']} | {x['non_high']} | "
                  f"{'yes' if x['survives'] else '**no**'} |")
    else:
        for x in rows:
            print(json.dumps(x))
    fails = [x["vf"] for x in rows if not x["survives"]]
    print(f"\n{len(rows)} admitted convergences; {len(rows) - len(fails)} keep three or more supporters not at high risk; "
          f"below three: {fails or 'none'}")


if __name__ == "__main__":
    main()
