"""Recompute every registry-derived number quoted in literature_review.md, with the definitions the review states.

    PYTHONIOENCODING=utf-8 python scripts/litreview_stats.py [--registry data/full_paper_registry.csv] [--json out.json]

Definitions (Appendix C of the review):
  included   duplicate_of empty / 'not reported' AND Modality not marked out-of-scope
  dedup      P-ref duplicate copies vs non-research markers, split on whether duplicate_of names a P-ID
  tier       leading V1..V4 token of Validation_Type, else NR
  year       first 4-digit token of Publication_Year; < 2021 = early row; none = undated
  task       leading T1..T6 token; classification = T1..T3
  domain     Category equal to exactly one of C1..C6; multi-code and uncoded values are OTHER (Figure 2 row)
  N          leading number of Sample_N_Approx, EXCLUDING rows whose number is followed by a study-count unit
             (studies/articles/publications/RCTs/trials/papers/records/reviews/experts/datasets/cohorts)
  design     agent3_extract._DESIGN_ALIASES folded, every longitudinal_* code merged into 'longitudinal'
  DL         A2+A3+A4+A5+A7; classical = A1; transformer = A4; CNN = A2; GNN = A5; recurrent = A3; LLM = A7
  XAI        XAI_Method not in {'', not reported, none, n/a, not applicable}
"""
import argparse
import collections
import csv
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import agent3_extract as a3  # noqa: E402

UNIT_WORDS = r"(studies|articles|publications|RCTs|trials|papers|records|reviews|experts|datasets|cohorts)"


def is_dup(r):
    d = (r.get("duplicate_of") or "").strip()
    return not (d == "" or d.lower() == "not reported")


def is_oos(r):
    m = (r.get("Modality") or "").lower()
    return "out-of-scope" in m or "out_of_scope" in m


def tier(r):
    m = re.match(r"(V[1-4])", (r.get("Validation_Type") or "").strip())
    return m.group(1) if m else "NR"


def year(r):
    m = re.search(r"(\d{4})", r.get("Publication_Year") or "")
    return int(m.group(1)) if m else None


def task(r):
    m = re.match(r"(T[1-6])", (r.get("Task_Type") or "").strip())
    return m.group(1) if m else "NR"


def domain(r):
    # exact single-code rule, as in make_litreview_figures.figure2: multi-code values ('C3,C5')
    # and anything else are OTHER (Figure 2's 'multi-domain or not coded' row)
    c = (r.get("Category") or "").strip().upper()
    return c if c in {"C1", "C2", "C3", "C4", "C5", "C6"} else "OTHER"


def sample_n(r):
    s = (r.get("Sample_N_Approx") or "").strip().replace(",", "")
    # a leading count, allowing the 'N= 123', '**123' and '~123' spellings used in the registry
    m = re.match(r"\s*\**\s*(?:[Nn]\s*=\s*)?[~≈]?\s*(\d+(?:\.\d+)?)\s*(\S*)", s)
    if not m:
        return None
    if re.match(UNIT_WORDS, m.group(2) or "", re.I) or re.match(r"^\d+\s*" + UNIT_WORDS, s, re.I):
        return None
    v = float(m.group(1))
    return v if v > 0 else None


def design(r):
    d = (r.get("study_design_type") or "").strip().lower()
    d = a3._DESIGN_ALIASES.get(d, d)
    if d.startswith("longitudinal"):
        d = "longitudinal"
    return d


def arch(r):
    return (r.get("Architecture_Family") or "").strip()


def has_xai(r):
    return (r.get("XAI_Method") or "").strip().lower() not in ("", "not reported", "none", "n/a", "not applicable")


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def compute(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    dups = [r for r in rows if is_dup(r)]
    p_ref = [r for r in dups if re.match(r"P-\d+", (r.get("duplicate_of") or "").strip())]
    inc = [r for r in rows if not is_dup(r) and not is_oos(r)]
    n = len(inc)
    out = {"registry_rows": len(rows), "removed_dedup": len(dups), "dedup_p_ref_copies": len(p_ref),
           "dedup_non_research": len(dups) - len(p_ref), "removed_out_of_scope": len(rows) - len(dups) - n, "included": n}
    # Table 1
    tiers = ["V1", "V2", "V3", "V4", "NR"]
    tab = collections.OrderedDict()
    for key in ["early", 2021, 2022, 2023, 2024, 2025, 2026, "undated"]:
        tab[key] = collections.Counter()
    for r in inc:
        y = year(r)
        key = "undated" if y is None else ("early" if y < 2021 else y)
        tab[key][tier(r)] += 1
    out["table1"] = {str(k): {"n": sum(c.values()), **{t: [c[t], pct(c[t], sum(c.values()))] for t in tiers}} for k, c in tab.items()}
    tot = collections.Counter(tier(r) for r in inc)
    out["table1"]["all"] = {"n": n, **{t: [tot[t], pct(tot[t], n)] for t in tiers}}
    out["early_rows"] = [(r["Paper_ID"], r["Publication_Year"], tier(r)) for r in inc if year(r) is not None and year(r) < 2021]
    out["undated_rows"] = [(r["Paper_ID"], r["Publication_Year"], tier(r)) for r in inc if year(r) is None]
    in_window = sum(1 for r in inc if year(r) is not None and 2021 <= year(r) <= 2026)
    out["share_2021_2026"] = pct(in_window, n)
    # classification subset
    cls = [r for r in inc if task(r) in ("T1", "T2", "T3")]
    ct = collections.Counter(tier(r) for r in cls)
    out["classification"] = {"n": len(cls), **{t: [ct[t], pct(ct[t], len(cls))] for t in tiers},
                             "adni": [sum(1 for r in cls if (r.get("ADNI_Dependent") or "").strip().lower() == "true"), None]}
    out["classification"]["adni"][1] = pct(out["classification"]["adni"][0], len(cls))
    out["classification"]["v4_by_year"] = {}
    for y in range(2021, 2027):
        sub = [r for r in cls if year(r) == y]
        out["classification"]["v4_by_year"][y] = [sum(1 for r in sub if tier(r) == "V4"), len(sub), pct(sum(1 for r in sub if tier(r) == "V4"), len(sub))]
    adni_all = sum(1 for r in inc if (r.get("ADNI_Dependent") or "").strip().lower() == "true")
    out["adni_corpus"] = [adni_all, pct(adni_all, n)]
    # V2 default carriers
    t6 = [r for r in inc if task(r) == "T6"]
    rct = [r for r in inc if design(r) == "randomized_controlled_trial"]
    out["v2_default_carriers"] = {"T6": [sum(1 for r in t6 if tier(r) == "V2"), len(t6)], "RCT": [sum(1 for r in rct if tier(r) == "V2"), len(rct)]}
    # shares
    tc = collections.Counter(task(r) for r in inc)
    out["task_shares"] = {t: [tc[t], pct(tc[t], n)] for t in sorted(tc)}
    dc = collections.Counter(domain(r) for r in inc)
    out["domain_shares"] = {d: [dc[d], pct(dc[d], n)] for d in sorted(dc)}
    # sample N
    ns = [sample_n(r) for r in inc]
    usable = sorted(v for v in ns if v is not None)
    excluded_units = sum(1 for r in inc if re.match(r"\s*\**\s*(?:[Nn]\s*=\s*)?[~≈]?\s*\d[\d,.]*\s*" + UNIT_WORDS, (r.get("Sample_N_Approx") or ""), re.I))
    q = statistics.quantiles(usable, n=4, method="inclusive")
    out["sample_n"] = {"n_usable": len(usable), "excluded_study_counts": excluded_units, "median": statistics.median(usable),
                       "iqr_inclusive": [q[0], q[2]]}
    # design
    dsg = collections.Counter(design(r) for r in inc)
    out["design_shares"] = {k: [dsg[k], pct(dsg[k], n)] for k in ("cross_sectional", "longitudinal", "survival_analysis", "randomized_controlled_trial", "review_meta_analysis")}
    raw = collections.Counter((r.get("study_design_type") or "").strip() for r in inc)
    out["design_raw_exact"] = {k: [raw[k], pct(raw[k], n)] for k in ("cross_sectional", "longitudinal_cohort", "survival_analysis", "randomized_controlled_trial", "review_meta_analysis")}
    out["design_alias_rows"] = sorted((r["Paper_ID"], r["study_design_type"]) for r in inc if (r.get("study_design_type") or "").strip().lower() in a3._DESIGN_ALIASES and (r.get("study_design_type") or "").strip() not in a3._DESIGN_ALIASES.values())
    # architecture
    ac = collections.Counter(arch(r) for r in inc)
    dl = {"A2", "A3", "A4", "A5", "A7"}
    out["architecture"] = {"classical_A1": [ac["A1"], pct(ac["A1"], n)], "transformer_A4": [ac["A4"], pct(ac["A4"], n)], "cnn_A2": [ac["A2"], pct(ac["A2"], n)],
                           "gnn_A5": [ac["A5"], pct(ac["A5"], n)], "recurrent_A3": [ac["A3"], pct(ac["A3"], n)], "llm_A7": [ac["A7"], pct(ac["A7"], n)],
                           "dl_share_by_year": {}}
    for y in range(2021, 2027):
        sub = [r for r in inc if year(r) == y]
        d = sum(1 for r in sub if arch(r) in dl)
        out["architecture"]["dl_share_by_year"][y] = [d, len(sub), pct(d, len(sub))]
    # XAI
    xa = sum(1 for r in inc if has_xai(r))
    xc = sum(1 for r in cls if has_xai(r))
    out["xai"] = {"corpus": [xa, pct(xa, n)], "classification": [xc, pct(xc, len(cls))]}
    # domain x task (Figure 2 cells)
    cell = collections.Counter((domain(r), task(r)) for r in inc)
    out["domain_by_task"] = {f"{d}|{t}": c for (d, t), c in sorted(cell.items())}
    out["annual_output"] = {y: sum(tab[y].values()) for y in (2021, 2022, 2023, 2024, 2025, 2026)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(ROOT / "data" / "full_paper_registry.csv"))
    ap.add_argument("--json")
    a = ap.parse_args()
    out = compute(a.registry)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, ensure_ascii=True, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "domain_by_task"}, indent=1, ensure_ascii=True, default=str))


if __name__ == "__main__":
    main()
