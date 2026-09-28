"""Appraisal-relevant characteristics of the studies cited in the review (Appendix G).

Added 2026-09-23. The review applies no design-specific risk-of-bias instrument, and this
table is deliberately NOT one: every column is a fact already coded in the registry or
derivable from it by a stated rule, not a bias judgment. Presenting extracted facts as if
they were QUADAS-2 or PROBAST domain ratings would be exactly the kind of unearned
appraisal the review criticises elsewhere, so the judgment columns are left for a human
appraiser and the worksheet for that pass is emitted alongside the summary.

What is reported per cited study: design, the instrument that would apply to that design,
validation tier, whether validation is external, analysed sample size, ADNI dependence,
and whether the study falls in the performance-suspect set (internal-validation
discrimination at or above 0.97 under the Section 3.5 rule).

    PYTHONIOENCODING=utf-8 python scripts/make_appraisal_table.py [--write]

--write refreshes logs/appraisal_characteristics.md (summary, embedded as Appendix G) and
logs/appraisal_worksheet.csv (one row per study, judgment columns blank).
"""
import argparse
import collections
import csv
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import litreview_stats as ls  # noqa: E402

# design -> the instrument a formal appraisal would use. A mapping, not a judgment.
INSTRUMENT = [
    (r"randomi", "RoB 2 (randomised trials)"),
    (r"review|meta_analysis|meta-analysis", "ROBIS / AMSTAR 2 (evidence syntheses)"),
    (r"survival|longitudinal|cohort|multi_cohort|predictive_model|prognos", "PROBAST (prognostic models) or ROBINS-E (cohort exposures)"),
    (r"case_control|cross_sectional|cross-sectional", "QUADAS-2 (diagnostic accuracy) or ROBINS-E (associations)"),
]
# Tagged discrimination: the accuracy, AUC or F1 value in the registry's free-text Best_Metric field.
# Rewritten 2026-09-28. The earlier pattern (metric word, up to six non-digits, then any number) read the '1' of
# 'F1' in 'no accuracy, AUC, F1 ...' as a perfect score, 'miR-483' and 'pT231' as values, and so on; it put 26
# studies that report no discrimination metric at all into every row of Table C1 (81/74/63/55/47 instead of
# 59/51/39/29/20). The rules now: take the first number after the metric word within 40 characters, stopping at a
# semicolon or a confidence interval; skip numbers that are part of an identifier or an integer range; read a bare
# integer only as a percentage (50 to 100); also read the value-first forms '0.95 Acc' and '97.8% (Acc)'.
KW = re.compile(r"\b(?:Acc(?:uracy)?|AUCs?|AUROCs?|F1)\b", re.I)
NUM = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:\.\d+)?)(?![\w])(?!\.\d)(?P<tail>\s*%|-(?=\d))?")
STOP = re.compile(r";|\bC[rI]I\b|\bCI\b|95\s*%\s*C", re.I)
# rows whose field text the parser cannot read correctly, with the reason; each was read by hand on 2026-09-28
EXCEPTIONS = {
    "P-438": None,   # 'specificity 96.8% (Acc)': the value tagged Acc is a specificity; the row reports no discrimination
}
THRESHOLDS = (0.95, 0.96, 0.97, 0.98, 0.99)


def _value(s, tail):
    tail = (tail or "").strip()
    if tail == "-" and "." not in s:          # '1-3' is a class label, not a metric
        return None
    v = float(s)
    if "." in s:
        v = v / 100 if v > 1 else v
    elif tail == "%" or 50 <= v <= 100:
        v = v / 100
    else:
        return None
    return v if 0 < v <= 1 else None


def discrimination(row):
    pid = (row.get("Paper_ID") or "").strip()
    if pid in EXCEPTIONS:
        return EXCEPTIONS[pid]
    bm = row.get("Best_Metric") or ""
    vals = []
    for k in KW.finditer(bm):
        win = bm[k.end():k.end() + 40]
        st = STOP.search(win)
        if st:
            win = win[:st.start()]
        for n in NUM.finditer(win):
            v = _value(n.group(1), n.group("tail"))
            if v is not None:
                vals.append(v)
                break
        pre = re.search(r"(?<![\w.\-/])(\d{1,3}(?:\.\d+)?)\s*(%?)\s*\(?\s*$", bm[max(0, k.start() - 14):k.start()])
        if pre and ("." in pre.group(1) or pre.group(2)):
            v = _value(pre.group(1), pre.group(2))
            if v is not None:
                vals.append(v)
    return max(vals) if vals else None


def instrument(design):
    d = (design or "").lower()
    for pat, name in INSTRUMENT:
        if re.search(pat, d):
            return name
    return "not assigned (design not coded)"


def external(row):
    return (row.get("Validation_Type") or "").strip().upper().startswith("V4")


def suspect(row, thr=0.97):
    """Discrimination and validation conditions of the Section 3.5 rule (the small-sample condition is not applied)."""
    v = discrimination(row)
    return v is not None and v >= thr and not external(row)


def threshold_table(rows):
    """Table C1: studies at or above each threshold, split by coded validation."""
    lines = ["| Threshold | Studies at or above | Internal validation only | Coded as externally validated | Those studies |",
             "|---|---|---|---|---|"]
    for thr in THRESHOLDS:
        hit = [r for r in rows if (discrimination(r) or 0) >= thr]
        ext = sorted((r["Paper_ID"] for r in hit if external(r)), key=lambda x: int(x[2:]))
        label = f"{thr:.2f}" + (" (the rule)" if thr == 0.97 else "")
        lines.append(f"| {label} | {len(hit)} | {len(hit) - len(ext)} | {len(ext)} | {', '.join(ext)} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--thresholds", action="store_true", help="print Table C1 (all included studies) and exit")
    a = ap.parse_args()
    if a.thresholds:
        rows = list(csv.DictReader(io.open(ROOT / "data" / "full_paper_registry.csv",
                                           encoding="utf-8", errors="replace", newline="")))
        print(threshold_table([r for r in rows if not ls.is_dup(r) and not ls.is_oos(r)]))
        return

    review = ROOT / "literature_review.md"
    if review.exists():
        body = io.open(review, encoding="utf-8").read().split("\n## References")[0]  # Sections 1 to 8 only (the Appendix G sentence claims exactly this set)
        cited = set(re.findall(r"P-\d+", body))
    else:
        # public traceability release: the manuscript is not distributed, so the cited set is read from the released worksheet
        ws = ROOT / "logs" / "appraisal_worksheet.csv"
        cited = {r["paper_id"] for r in csv.DictReader(io.open(ws, encoding="utf-8", newline=""))}
        if a.write:
            sys.exit("--write needs the manuscript (it rebuilds the worksheet from the cited set); run without --write")
    rows = list(csv.DictReader(io.open(ROOT / "data" / "full_paper_registry.csv",
                                       encoding="utf-8", errors="replace", newline="")))
    inc = {r["Paper_ID"].strip(): r for r in rows if not ls.is_dup(r) and not ls.is_oos(r)}
    ids = sorted(cited & set(inc), key=lambda x: int(x[2:]))

    per = []
    for pid in ids:
        r = inc[pid]
        tier = (r.get("Validation_Type") or "not reported").strip()
        per.append({
            "paper_id": pid,
            "design": (r.get("study_design_type") or "not reported").strip(),
            "instrument_if_appraised": instrument(r.get("study_design_type")),
            "validation_tier": tier,
            "external_validation": "yes" if tier.upper().startswith("V4") else "no",
            "sample_n": (r.get("Sample_N_Approx") or "not reported").strip()[:40],
            "adni_dependent": (r.get("ADNI_Dependent") or "not reported").strip(),
            "performance_suspect_set": "yes" if suspect(r) else "no",
            "rob_domain_1": "", "rob_domain_2": "", "rob_domain_3": "", "rob_domain_4": "",
            "rob_overall": "", "appraiser": "", "appraisal_date": "",
        })

    byi = collections.Counter(p["instrument_if_appraised"] for p in per)
    ext = sum(1 for p in per if p["external_validation"] == "yes")
    adni = sum(1 for p in per if p["adni_dependent"].lower() in ("yes", "true", "1"))
    susp = sum(1 for p in per if p["performance_suspect_set"] == "yes")

    out = []
    out.append("**Appraisal-relevant characteristics of the cited studies.** No design-specific risk-of-bias "
               "instrument was applied to this corpus (Supplementary Table S7, item 11). What is reported here instead, for "
               f"each of the {len(per)} studies cited in Sections 1 to 8 that belong to the corpus, is the set of appraisal-relevant "
               "characteristics that are coded for every study in the registry: these are extracted facts, not "
               "bias judgments, and they are not a substitute for an appraisal. The per-study table is published "
               "as `logs/appraisal_characteristics.md` and the worksheet an appraiser would fill in, with the "
               "judgment columns left blank, as `logs/appraisal_worksheet.csv`.\n")
    out.append("| Design group | Studies | Instrument a formal appraisal would use |")
    out.append("|---|---|---|")
    for k, v in sorted(byi.items(), key=lambda kv: -kv[1]):
        out.append(f"| {k.split(' (')[0]} | {v} | {k} |")
    out.append(f"\nAcross those {len(per)} studies: {ext} ({100*ext/len(per):.0f}%) report external validation, "
               f"{adni} ({100*adni/len(per):.0f}%) are ADNI-dependent, and {susp} report internal-validation "
               "discrimination at or above 0.97, the discrimination and validation conditions of the Section 3.5 rule "
               "(the small-sample condition is not applied, as in Table C1). Those three "
               "columns carry most of the bias-relevant signal the registry can supply without a full-text appraisal "
               "pass: they speak to transportability, to dataset non-independence, and to performance validity "
               "respectively.\n")
    out.append(f"**What a scoped appraisal would take.** A formal pass does not require all {len(inc):,} studies. The "
               f"{len(per)} cited studies are the set whose bias could change a claim in this review, and they "
               "divide into four instrument groups as tabulated. The appraisal is a human reading task: each "
               "instrument requires signalling-question judgments against the full text, which is why the worksheet "
               "ships pre-filled with the coded facts and empty in every judgment column, rather than being "
               "completed automatically from the summary records.")

    text = "\n".join(out) + "\n"
    print(text)
    print(f"[worksheet rows: {len(per)}]")
    if a.write:
        io.open(ROOT / "logs" / "appraisal_characteristics.md", "w", encoding="utf-8", newline="\r\n").write(text)
        with io.open(ROOT / "logs" / "appraisal_worksheet.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(per[0].keys()))
            w.writeheader()
            w.writerows(per)
        print("wrote logs/appraisal_characteristics.md and logs/appraisal_worksheet.csv")


if __name__ == "__main__":
    main()
