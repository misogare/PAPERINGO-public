"""Independent ADNI-only G-128 audit, version 2.0.

Reads controlled ADNI diagnosis and registry archives locally; writes aggregate
results only. No participant identifiers or rows are exported. The original
experiment, manuscripts, research ledgers and human gates are not modified.

Primary analysis masks intermediate diagnoses but preserves each person's last
available diagnosis within eight years. This isolates intermediate-observation
loss with a fixed source cohort/observation endpoint; it is not an exact simulation
of a clinic that only visits every two/four years. A common four-year-visit subset
is a separate descriptive sensitivity, conditional on retained follow-up.

The panel likelihood is exp(Q * actual elapsed time), with four rates and first
observed dementia treated as absorbing. Both baseline CN and MCI cohorts estimate
the shared homogeneous generator. Reported probabilities are state occupation,
not cumulative ever-reversion. Competing-event observed first-reversion curves
are reported separately. These estimates do not identify true biological onset.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "downloads" / "adni"
OUT = ROOT / "logs" / "adni_independent_2026-11-02" / "G-128"
VERSION = "2.0"
SCHEDULES = {"full": None, "two_year_mask": 2.0, "four_year_mask": 4.0}
HORIZONS = (2.0, 4.0, 8.0)
LOG_BOUNDS = [(math.log(1e-6), math.log(2.0))] * 4


def read_archive(marker, member_prefix):
    choices = sorted(p for p in DATA.iterdir() if marker in p.name)
    if len(choices) != 1:
        raise ValueError(f"Expected one archive matching {marker}; found {len(choices)}")
    path = choices[0]
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist()
                 if Path(n).name.startswith(member_prefix) and n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Ambiguous member {member_prefix}: {names}")
        raw = archive.read(names[0])
    return pd.read_csv(io.BytesIO(raw), low_memory=False), {
        "archive": path.name, "member": names[0], "sha256": hashlib.sha256(raw).hexdigest()}


def load_cohorts(max_years=8.0):
    dx, dx_source = read_archive("Diagnostic Summary [", "DXSUM_")
    registry, registry_source = read_archive("Registry [", "REGISTRY_")
    counts = {"raw_diagnosis_rows": len(dx)}
    for frame in (dx, registry):
        frame["RID"] = pd.to_numeric(frame["RID"], errors="coerce")
        frame["date"] = pd.to_datetime(frame["EXAMDATE"], errors="coerce")
    dx["DIAGNOSIS"] = pd.to_numeric(dx["DIAGNOSIS"], errors="coerce")
    valid = dx.DIAGNOSIS.isin([1, 2, 3]) & dx.RID.notna() & dx.date.notna()
    counts["valid_state_rid_date_rows"] = int(valid.sum())
    counts["positive_qc_error_rows"] = int(pd.to_numeric(dx.HAS_QC_ERROR, errors="coerce").eq(1).sum())
    dx = dx[valid & ~pd.to_numeric(dx.HAS_QC_ERROR, errors="coerce").eq(1)].copy()
    conflicts = dx.groupby(["RID", "date"]).DIAGNOSIS.nunique()
    conflict_keys = set(conflicts.index[conflicts.gt(1)])
    counts["same_day_conflicting_dates_dropped"] = len(conflict_keys)
    counts["same_day_conflicting_rows_dropped"] = sum(
        key in conflict_keys for key in zip(dx.RID, dx.date))
    dx = dx[[key not in conflict_keys for key in zip(dx.RID, dx.date)]].copy()

    # Preserve bl labels BEFORE collapsing identical same-date diagnosis records.
    # Otherwise a coincident screening row can erase a valid baseline marker.
    baseline = dx[dx.VISCODE2.eq("bl")].sort_values(["date", "PHASE"]).drop_duplicates("RID")
    counts["valid_clinical_baselines_before_registry_confirmation"] = len(baseline)
    reg_valid = registry.VISCODE2.eq("bl") & registry.date.notna() & registry.RID.notna()
    reg_valid &= ~pd.to_numeric(registry.HAS_QC_ERROR, errors="coerce").eq(1)
    enrolled = set(zip(registry.loc[reg_valid, "RID"], registry.loc[reg_valid, "PHASE"]))
    baseline = baseline[[key in enrolled for key in zip(baseline.RID, baseline.PHASE)]]
    counts["registry_confirmed_baselines"] = len(baseline)
    counts["baseline_diagnoses"] = {str(int(k)): int(v) for k, v in baseline.DIAGNOSIS.value_counts().items()}
    before = len(dx)
    dx = dx.sort_values(["RID", "date"]).drop_duplicates(["RID", "date"])
    counts["identical_same_day_rows_collapsed"] = before - len(dx)
    counts["clean_diagnosis_rows"] = len(dx)
    counts["valid_diagnosed_subjects"] = int(dx.RID.nunique())
    grouped = dict(tuple(dx.groupby("RID")))
    cohorts = {"baseline_mci": {}, "baseline_cn": {}}
    prior_dementia = 0
    cn_prior_mci = set()
    for row in baseline.itertuples():
        state = int(row.DIAGNOSIS)
        if state not in (1, 2):
            continue
        subject = grouped[row.RID]
        if subject.loc[subject.date.lt(row.date), "DIAGNOSIS"].eq(3).any():
            prior_dementia += 1
            continue
        sub = subject[subject.date.ge(row.date)]
        years = (sub.date - row.date).dt.days.to_numpy() / 365.25
        keep = years <= max_years
        years, states = years[keep], sub.DIAGNOSIS.to_numpy(int)[keep]
        if len(years) == 0 or years[0] != 0 or states[0] != state:
            raise AssertionError("Baseline observation must be present and at time zero")
        name = "baseline_mci" if state == 2 else "baseline_cn"
        cohorts[name][int(row.RID)] = (years, states)
        if state == 1 and subject.loc[subject.date.lt(row.date), "DIAGNOSIS"].eq(2).any():
            cn_prior_mci.add(int(row.RID))
    counts["baseline_cn_mci_prior_dementia_excluded"] = prior_dementia
    for name, cohort in cohorts.items():
        subject_gaps = [np.diff(t)*365.25 for t, s in cohort.values() if len(t) > 1]
        counts[name] = {"n": len(cohort), "with_followup": len(subject_gaps),
                        "all_gaps_at_most_400_days_n": int(sum(np.max(g) <= 400 for g in subject_gaps)),
                        "all_gaps_at_most_548_days_n": int(sum(np.max(g) <= 548 for g in subject_gaps))}
    gaps, medians, maxima = [], [], []
    for cohort in cohorts.values():
        for t, s in cohort.values():
            g = np.diff(t) * 365.25
            if len(g):
                gaps.extend(g); medians.append(np.median(g)); maxima.append(np.max(g))
    counts["visit_gap_days"] = {"median": float(np.median(gaps)), "q25": float(np.quantile(gaps, .25)),
                                "q75": float(np.quantile(gaps, .75))}
    counts["individual_visit_coverage"] = {
        "subjects_with_followup": len(medians),
        "median_subject_median_gap_days": float(np.median(medians)),
        "all_gaps_at_most_400_days_n": int(np.sum(np.asarray(maxima) <= 400)),
        "all_gaps_at_most_548_days_n": int(np.sum(np.asarray(maxima) <= 548)),
        "note": "Pooled median spacing does not prove every person has annual coverage."}
    counts["dementia_then_later_non_dementia_subjects"] = 0
    for cohort in cohorts.values():
        for t, s in cohort.values():
            hit = np.flatnonzero(s == 3)
            if len(hit) and np.any(s[hit[0]+1:] != 3):
                counts["dementia_then_later_non_dementia_subjects"] += 1
    counts["baseline_cn_with_recorded_mci_before_baseline"] = len(cn_prior_mci)
    metadata = {"cn_prior_mci": np.asarray([rid in cn_prior_mci for rid in cohorts["baseline_cn"]])}
    return cohorts, counts, [dx_source, registry_source], metadata


def mask_visits(t, s, period, offset=0.0, window=0.5, preserve_terminal=True):
    """Absolute time grid, not greedy time since last retained observation."""
    if period is None:
        return t.copy(), s.copy()
    keep = {0}
    if preserve_terminal:
        keep.add(len(t) - 1)  # explicit terminal-preservation sensitivity
    first = period if offset == 0 else offset
    for target in np.arange(first, t[-1] + window + 1e-8, period):
        candidates = np.flatnonzero((t > 0) & (np.abs(t - target) <= window))
        if len(candidates):
            order = np.lexsort((t[candidates], np.abs(t[candidates] - target)))
            keep.add(int(candidates[order[0]]))
    ids = np.asarray(sorted(keep))
    return t[ids], s[ids]


def first_dementia_path(t, s):
    hit = np.flatnonzero(s == 3)
    end = int(hit[0]) + 1 if len(hit) else len(t)
    return t[:end], s[:end]


def panel_arrays(series):
    values = []; subjects = []
    for i, (t, s) in enumerate(series):
        t, s = first_dementia_path(t, s)
        for j in range(len(t) - 1):
            values.append((round(float(t[j+1]-t[j]), 12), int(s[j]), int(s[j+1])))
            subjects.append(i)
    unique, inverse = np.unique(np.asarray(values), axis=0, return_inverse=True)
    return unique, inverse, np.asarray(subjects), len(series)


def transition_probabilities(log_rates, elapsed):
    a, b, c, d = np.exp(log_rates)
    u, v = a + b, c + d
    mean = -(u + v) / 2
    delta = np.sqrt(((v-u)/2) ** 2 + a*c)
    ep = np.exp((mean + delta) * elapsed)
    em = np.exp((mean - delta) * elapsed)
    C = (ep + em) / 2
    S = (ep - em) / (2 * delta)
    p11, p12 = C + S * (-u - mean), S * a
    p21, p22 = S * c, C + S * (-v - mean)
    p = np.zeros((len(np.atleast_1d(elapsed)), 3, 3))
    p[:, 0, 0], p[:, 0, 1] = p11, p12
    p[:, 1, 0], p[:, 1, 1] = p21, p22
    p[:, 0, 2], p[:, 1, 2] = 1-p11-p12, 1-p21-p22
    p[:, 2, 2] = 1
    return p


def fit_ctmc(panel, weights=None, initial=None, multi_start=False):
    unique, inverse, subjects, n = panel
    weights = np.ones(n) if weights is None else np.asarray(weights)
    counts = np.bincount(inverse, weights=weights[subjects], minlength=len(unique))
    dt, state0, state1 = unique[:, 0], unique[:, 1].astype(int)-1, unique[:, 2].astype(int)-1
    arange = np.arange(len(unique))
    def objective(theta):
        p = transition_probabilities(theta, dt)[arange, state0, state1]
        if not np.all(np.isfinite(p)) or np.min(p) < -1e-9:
            return 1e100
        return -np.dot(counts, np.log(np.maximum(p, 1e-300)))
    starts = [np.log([.04, .003, .05, .10]) if initial is None else np.asarray(initial)]
    if multi_start:
        starts += [np.log([.10, .01, .10, .20]), np.log([.01, .001, .02, .05])]
    fits = [minimize(objective, theta, method="L-BFGS-B", bounds=LOG_BOUNDS,
                     options={"maxiter": 250, "ftol": 1e-10, "gtol": 1e-5}) for theta in starts]
    fit = min(fits, key=lambda f: f.fun)
    if not fit.success and not multi_start:
        return fit_ctmc(panel, weights, initial, True)
    rates = np.exp(fit.x)
    out = {"success": bool(fit.success), "negative_log_likelihood": float(fit.fun),
           "rates_per_year": {k: float(v) for k, v in zip(
               ["cn_mci", "cn_dementia", "mci_cn", "mci_dementia"], rates)},
           "boundary_rates": [k for k, v in zip(["cn_mci", "cn_dementia", "mci_cn", "mci_dementia"], rates)
                              if v <= 1.01e-6 or v >= 1.999]}
    probabilities = transition_probabilities(fit.x, np.asarray(HORIZONS))
    for j, h in enumerate(HORIZONS):
        out[f"occupation_cn_from_mci_{int(h)}y"] = float(probabilities[j, 1, 0])
        out[f"occupation_dementia_from_mci_{int(h)}y"] = float(probabilities[j, 1, 2])
    out["mci_dementia_intensity_per_100py"] = float(100 * rates[3])
    return out, fit.x


def observed_first_events(series, origin_state):
    records = []
    for t, s in series:
        first_type = 0; event_time = t[-1]
        if origin_state == 2:
            indices = np.flatnonzero((s != 2) & (t > 0))
            if len(indices):
                j = indices[0]; event_time = t[j]; first_type = 1 if s[j] == 1 else 2
        records.append((float(event_time), first_type))
    return np.asarray(records)


def cumulative_detection(records, horizons, weights=None):
    """Competing-event CIF at OBSERVED visit dates, not latent transition time."""
    weights = np.ones(len(records)) if weights is None else np.asarray(weights)
    times, causes = records[:, 0], records[:, 1].astype(int)
    order = np.argsort(times, kind="mergesort")
    times, causes, weights = times[order], causes[order], weights[order]
    unique, starts = np.unique(times, return_index=True)
    censor = np.add.reduceat(weights * (causes == 0), starts)
    event1 = np.add.reduceat(weights * (causes == 1), starts)
    event2 = np.add.reduceat(weights * (causes == 2), starts)
    removed = censor + event1 + event2
    risk = weights.sum() - np.r_[0, np.cumsum(removed[:-1])]
    survival, cif1, cif2 = 1.0, 0.0, 0.0
    curve = []
    for j, t in enumerate(unique):
        if risk[j] > 0:
            cif1 += survival * event1[j] / risk[j]
            cif2 += survival * event2[j] / risk[j]
            survival *= 1 - (event1[j] + event2[j]) / risk[j]
        curve.append((float(t), float(cif1), float(cif2)))
    curve = np.asarray(curve)
    result = {}
    for h in horizons:
        j = np.searchsorted(curve[:, 0], h, side="right") - 1
        result[f"observed_reversion_cif_{h:g}y"] = float(curve[j, 1]) if j >= 0 else 0.0
        result[f"observed_dementia_first_cif_{h:g}y"] = float(curve[j, 2]) if j >= 0 else 0.0
    return result, curve


def cn_path_flags(series):
    out = []
    for t, s in series:
        hit = np.flatnonzero(s == 3)
        detected = bool(len(hit))
        direct = detected and not np.any(s[:hit[0]] == 2)
        out.append((int(detected), int(direct)))
    return np.asarray(out)


def direct_metrics(flags, reference_converters, weights=None):
    weights = np.ones(len(flags)) if weights is None else np.asarray(weights)
    detected = float(np.dot(weights, flags[:, 0]))
    direct = float(np.dot(weights, flags[:, 1]))
    fixed_n = float(np.dot(weights, reference_converters))
    missed = float(np.dot(weights, reference_converters & (flags[:, 0] == 0)))
    return {"detected_dementia_n": detected, "observed_direct_n": direct,
            "direct_share_among_detected_pct": 100 * direct / detected if detected else None,
            "full_series_converter_reference_n": fixed_n,
            "missed_full_series_converters_n": missed,
            "observed_direct_fixed_converter_pct": 100 * direct / fixed_n if fixed_n else None,
            "missed_full_series_converter_pct": 100 * missed / fixed_n if fixed_n else None}


def evaluate_masks(cohorts, reps=300, seed=20261102):
    mci = list(cohorts["baseline_mci"].values())
    cn = list(cohorts["baseline_cn"].values())
    original_converters = cn_path_flags(cn)[:, 0].astype(bool)
    preparations = {}; points = {}; starts = {}
    for name, period in SCHEDULES.items():
        cm = [mask_visits(t, s, period) for t, s in mci]
        cc = [mask_visits(t, s, period) for t, s in cn]
        panel = panel_arrays(cm + cc)
        model, theta = fit_ctmc(panel, multi_start=True)
        record = observed_first_events(cm, 2)
        detection, curve = cumulative_detection(record, HORIZONS)
        flags = cn_path_flags(cc)
        points[name] = {**model, **detection, **direct_metrics(flags, original_converters),
                       "source_cohort_mci_n": len(mci), "source_cohort_cn_n": len(cn),
                       "mci_subjects_with_followup": sum(len(t) > 1 for t, s in cm),
                       "cn_subjects_with_followup": sum(len(t) > 1 for t, s in cc),
                       "retained_diagnosis_records": sum(len(t) for t, s in cm + cc),
                       "panel_intervals": len(panel[1])}
        preparations[name] = (panel, record, flags, curve)
        starts[name] = theta
    metrics = [f"occupation_cn_from_mci_{int(h)}y" for h in HORIZONS]
    metrics += [f"occupation_dementia_from_mci_{int(h)}y" for h in HORIZONS]
    metrics += [f"observed_reversion_cif_{h:g}y" for h in HORIZONS]
    metrics += ["mci_dementia_intensity_per_100py", "direct_share_among_detected_pct",
                "observed_direct_fixed_converter_pct", "missed_full_series_converter_pct"]
    draws = {name: {metric: [] for metric in metrics} for name in SCHEDULES}
    failures = {name: 0 for name in SCHEDULES}
    rng = np.random.default_rng(seed)
    for b in range(reps):
        wm = rng.multinomial(len(mci), np.ones(len(mci))/len(mci))
        wc = rng.multinomial(len(cn), np.ones(len(cn))/len(cn))
        for name in SCHEDULES:
            panel, record, flags, curve = preparations[name]
            fitted, theta = fit_ctmc(panel, np.r_[wm, wc], starts[name])
            if not fitted["success"]:
                failures[name] += 1
            observed, _ = cumulative_detection(record, HORIZONS, wm)
            direct = direct_metrics(flags, original_converters, wc)
            values = {**fitted, **observed, **direct}
            for metric in metrics:
                value = values.get(metric)
                if metric.startswith("occupation_") or metric.endswith("intensity_per_100py"):
                    if not fitted["success"]:
                        value = None
                draws[name][metric].append(value)
        if (b + 1) % 50 == 0:
            print(f"Paired bootstrap {b+1}/{reps}", flush=True)
    estimates, differences = [], []
    for name in SCHEDULES:
        for metric in metrics:
            good = np.asarray([v for v in draws[name][metric] if v is not None and np.isfinite(v)])
            lo, hi = np.quantile(good, [.025, .975]) if len(good) else (None, None)
            estimates.append({"schedule": name, "metric": metric, "estimate": points[name][metric],
                              "ci_low": None if lo is None else float(lo), "ci_high": None if hi is None else float(hi),
                              "valid_bootstrap_replicates": len(good)})
    for name in ("two_year_mask", "four_year_mask"):
        for metric in metrics:
            paired = [v-u for u, v in zip(draws["full"][metric], draws[name][metric])
                      if u is not None and v is not None and np.isfinite(u) and np.isfinite(v)]
            lo, hi = np.quantile(paired, [.025, .975]) if paired else (None, None)
            base, alt = points["full"][metric], points[name][metric]
            differences.append({"schedule": name, "reference": "full", "metric": metric,
                                "difference_mask_minus_full": None if base is None or alt is None else alt-base,
                                "ci_low": None if lo is None else float(lo), "ci_high": None if hi is None else float(hi),
                                "valid_paired_replicates": len(paired)})
    return points, preparations, estimates, differences, failures


def endpoint_subset(cohorts, target=4.0, window=.5):
    out = {"baseline_mci": {}, "baseline_cn": {}}
    intervals = []
    for name, cohort in cohorts.items():
        for rid, (t, s) in cohort.items():
            choices = np.flatnonzero(np.abs(t-target) <= window)
            if len(choices):
                choice = choices[np.lexsort((t[choices], np.abs(t[choices]-target)))[0]]
                out[name][rid] = (t[:choice+1], s[:choice+1]); intervals.append(t[choice])
    summary = {"n_by_baseline": {k: len(v) for k, v in out.items()},
               "endpoint_years_min": float(np.min(intervals)), "endpoint_years_max": float(np.max(intervals)),
               "scope": "Conditional on an observed diagnosis within 3.5-4.5 years; descriptive paired-retention subset, not population risk."}
    for name, period in SCHEDULES.items():
        subset = {k: [mask_visits(t, s, period) for t, s in v.values()] for k, v in out.items()}
        mci = subset["baseline_mci"]
        summary[name] = {"mci_observed_reversion_any_n": int(sum(np.any(s[1:] == 1) for t, s in mci)),
                         "mci_observed_dementia_any_n": int(sum(np.any(s[1:] == 3) for t, s in mci)),
                         "mci_n": len(mci), **direct_metrics(cn_path_flags(subset["baseline_cn"]),
                             cn_path_flags(list(out["baseline_cn"].values()))[:, 0].astype(bool))}
    return summary


def phase_sensitivity(cohorts):
    results = []
    mci = list(cohorts["baseline_mci"].values()); cn = list(cohorts["baseline_cn"].values())
    reference = cn_path_flags(cn)[:, 0].astype(bool)
    for period in (2.0, 4.0):
        for offset in (0.0, period/2):
            for window in (.25, .5):
                cm = [mask_visits(t, s, period, offset, window) for t, s in mci]
                cc = [mask_visits(t, s, period, offset, window) for t, s in cn]
                detection, _ = cumulative_detection(observed_first_events(cm, 2), HORIZONS)
                results.append({"period_years": period, "grid_offset_years": offset,
                                "window_years": window, **detection,
                                **direct_metrics(cn_path_flags(cc), reference)})
    return results


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)


def write_literal_grid_sensitivity(cohorts, counts, sources):
    """Descriptive baseline-plus-grid comparison; never force terminal retention.

    This changes observation duration. Baseline-only participants remain in the
    source cohort and are censored at zero for observed-event curves, while
    contributing no panel likelihood intervals. No new bootstrap is performed.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mci = list(cohorts["baseline_mci"].values())
    cn = list(cohorts["baseline_cn"].values())
    reference = cn_path_flags(cn)[:, 0].astype(bool)
    point = {}
    for name, period in SCHEDULES.items():
        cm = [mask_visits(t, s, period, preserve_terminal=False) for t, s in mci]
        cc = [mask_visits(t, s, period, preserve_terminal=False) for t, s in cn]
        panel = panel_arrays(cm + cc)
        model, _ = fit_ctmc(panel, multi_start=True)
        detection, _ = cumulative_detection(observed_first_events(cm, 2), HORIZONS)
        followup = {}
        for label, series in (("baseline_mci", cm), ("baseline_cn", cc)):
            last = np.asarray([t[-1] for t, s in series])
            followup[label] = {
                "source_people_n": len(series),
                "people_with_retained_followup_n": int(np.sum(last > 0)),
                "baseline_only_n": int(np.sum(last == 0)),
                "median_last_retained_years_all": float(np.median(last)),
                "median_last_retained_years_among_followed": float(np.median(last[last > 0])),
                "last_retained_at_least_4_years_n": int(np.sum(last >= 4)),
                "last_retained_at_least_8_years_n": int(np.sum(last >= 8)),
            }
        point[name] = {**model, **detection,
                       **direct_metrics(cn_path_flags(cc), reference),
                       "followup": followup, "panel_intervals": len(panel[1]),
                       "retained_diagnosis_records": sum(len(t) for t, s in cm + cc)}
    result = {
        "version": "1.0", "built_at_utc": stamp,
        "scope": "ADNI only; descriptive baseline-plus-grid sensitivity; no terminal retention",
        "source_files": sources, "cohort_audit": counts,
        "mask_design": "Same baseline source cohorts; baseline plus nearest actual visit within +/-0.5 years of absolute 2/4-year grid; original terminal visit is not forced; maximum source follow-up 8 years.",
        "interpretation": "Observation duration changes; some people become baseline-only. CIFs depend on independent censoring and model estimates on non-informative panel observation, neither established here. A source diagnosis outside grid windows cannot be reconstructed. No biological transition or clinic policy effect is identified.",
        "bootstrap": "None; point-estimate sensitivity accompanying the separately saved paired 2,000-bootstrap terminal-preserved primary analysis.",
        "point": point, "no_gap_closure_asserted": True,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "G-128_literal_grid_results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    rows = []
    for name, values in point.items():
        row = {"schedule": name}
        row.update({k: v for k, v in values.items()
                    if isinstance(v, (int, float, bool)) or v is None})
        for label, summary in values["followup"].items():
            row.update({f"{label}_{k}": v for k, v in summary.items()})
        rows.append(row)
    write_csv(OUT / "G-128_literal_grid_estimates.csv", rows)
    report = ["# G-128: baseline-plus-grid sensitivity", "",
              f"Version 1.0 | Built {stamp} | Descriptive, pending human review", "",
              result["mask_design"], "", result["interpretation"], "",
              "This supplement does not replace the terminal-preserved primary experiment or its 2,000 paired bootstrap replicates. No additional bootstrap was run.", "",
              "| Schedule | MCI with follow-up / source | CN with follow-up / source | MCI median last retained year (all) | 4-y first-recorded reversion CIF | Model MCI-to-dementia intensity /100 MCI person-years | Direct / detected dementia | Missed full-series dementia converters |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, values in point.items():
        fm, fc = values["followup"]["baseline_mci"], values["followup"]["baseline_cn"]
        report.append(f"| {name} | {fm['people_with_retained_followup_n']}/{fm['source_people_n']} | {fc['people_with_retained_followup_n']}/{fc['source_people_n']} | {fm['median_last_retained_years_all']:.2f} | {100*values['observed_reversion_cif_4y']:.2f}% | {values['mci_dementia_intensity_per_100py']:.3f} | {values['observed_direct_n']:.0f}/{values['detected_dementia_n']:.0f} | {values['missed_full_series_converters_n']:.0f}/{values['full_series_converter_reference_n']:.0f} |")
    report += ["", "All three point fits converged: " + str(all(x["success"] for x in point.values())) + ". Rates on their numerical bounds are listed in JSON; optimizer convergence does not establish identification or validate observation assumptions.", "",
               "Observed-event curves use diagnosis dates, censor baseline-only participants at time zero, and treat observed dementia before CN as competing. A four-year-grid CIF at four years may be based on a small, selected set still observed near that horizon. Compare retained follow-up and missed cases before interpreting differences.", ""]
    (OUT / "G-128_literal_grid.md").write_text("\n".join(report), encoding="utf-8")
    primary = OUT / "G-128_independent.md"
    if primary.exists():
        text = primary.read_text(encoding="utf-8")
        marker = "\n## Supplemental baseline-plus-grid sensitivity\n"
        text = text.split(marker)[0]
        text += marker + "\n" + f"Supplement version 1.0 built {stamp}. A separate [baseline-plus-grid analysis](G-128_literal_grid.md) retains baseline and only visits inside the absolute two/four-year grid windows, without forcing each original terminal diagnosis. The source cohorts are unchanged but observation lengths and detected events change; baseline-only people are counted explicitly. Its point estimates are descriptive and do not replace the primary 2,000-bootstrap results.\n\n"
        text += "The primary first-recorded reversion curves are horizon dependent: at four years the four-year mask gives 4.21% against 8.91% in the full series, but at eight years it gives 20.58% against 16.23%. Masking changes which competing event is recorded first and which people remain at risk. These curves must not be described as showing uniformly less reversion or a change in biological recovery.\n"
        primary.write_text(text, encoding="utf-8")
    print(json.dumps({"literal_grid": point}), flush=True)
    return result


def validate_implementation():
    from scipy.linalg import expm
    theta = np.log([.05, .01, .10, .20]); a, b, c, d = np.exp(theta)
    q = np.array([[-a-b, a, b], [c, -c-d, d], [0, 0, 0]])
    for h, result in zip([0, .5, 4, 8], transition_probabilities(theta, np.array([0, .5, 4, 8]))):
        assert np.allclose(result, expm(q*h), atol=1e-12)
        assert np.allclose(result.sum(axis=1), 1)
    t = np.array([0., 1., 2., 3., 4.]); s = np.array([1, 2, 1, 2, 3])
    tm, sm = mask_visits(t, s, 4)
    assert tm.tolist() == [0, 4] and sm.tolist() == [1, 3]
    assert cn_path_flags([(t, s)])[0].tolist() == [1, 0]
    assert cn_path_flags([(tm, sm)])[0].tolist() == [1, 1]
    short_t, short_s = mask_visits(np.array([0., 1.]), np.array([2, 1]), 4,
                                  preserve_terminal=False)
    assert short_t.tolist() == [0.] and short_s.tolist() == [2]
    example = np.array([[1, 1], [1, 2], [2, 0], [0, 0]], dtype=float)
    result, curve = cumulative_detection(example, [1, 2])
    assert np.isclose(result["observed_reversion_cif_1y"], 1/3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--literal-grid-only", action="store_true",
                        help="Run only the descriptive no-terminal-retention grid sensitivity; preserve primary bootstrap files")
    args = parser.parse_args()
    validate_implementation()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cohorts, counts, sources, metadata = load_cohorts()
    if args.literal_grid_only:
        write_literal_grid_sensitivity(cohorts, counts, sources)
        return
    print(json.dumps({"cohort": counts, "bootstrap_replicates": args.reps}), flush=True)
    start = time.time()
    points, preparations, estimates, differences, failures = evaluate_masks(cohorts, args.reps, args.seed)
    endpoint = endpoint_subset(cohorts)
    phase = phase_sensitivity(cohorts)
    prior_mci_sensitivity = {}
    cn_series = list(cohorts["baseline_cn"].values())
    ref = cn_path_flags(cn_series)[:, 0].astype(bool)
    weights = (~metadata["cn_prior_mci"]).astype(int)
    for name, period in SCHEDULES.items():
        masked = [mask_visits(t, s, period) for t, s in cn_series]
        prior_mci_sensitivity[name] = direct_metrics(cn_path_flags(masked), ref, weights)
    write_csv(OUT / "G-128_estimates.csv", estimates)
    write_csv(OUT / "G-128_paired_differences.csv", differences)
    write_csv(OUT / "G-128_grid_sensitivity.csv", phase)
    result = {"version": VERSION, "built_at_utc": stamp, "scope": "ADNI only; aggregate results; pending human review",
              "source_files": sources, "cohort_audit": counts, "max_observation_years": 8,
              "mask_design": "Absolute 2/4-year grids with +/-0.5-year windows; baseline and raw terminal diagnosis always preserved; not literal clinic policies",
              "model": "Four-rate homogeneous continuous-time Markov panel likelihood; CN and MCI reversible; first observed dementia absorbing; fitted jointly in baseline CN+MCI",
              "bootstrap": {"requested": args.reps, "seed": args.seed, "unit": "subject; stratified by baseline state; paired masks", "fit_failures": failures},
              "point": points, "estimates": estimates, "paired_differences": differences,
              "common_four_year_endpoint_sensitivity": endpoint, "grid_sensitivity": phase,
              "exclude_recorded_prebaseline_mci_direct_sensitivity": prior_mci_sensitivity,
              "seconds": round(time.time()-start, 2), "no_gap_closure_asserted": True}
    (OUT / "G-128_results.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")

    colors = {"full": "#2a78d6", "two_year_mask": "#e7792c", "four_year_mask": "#149b75"}
    labels = {"full": "Full recorded series", "two_year_mask": "2-year grid + terminal visit", "four_year_mask": "4-year grid + terminal visit"}
    estimate_index = {(x["schedule"], x["metric"]): x for x in estimates}
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    for name in SCHEDULES:
        curve = preparations[name][3]
        axes[0].step(curve[:, 0], curve[:, 1]*100, where="post", label=labels[name], color=colors[name])
        theta = np.log(list(points[name]["rates_per_year"].values()))
        grid = np.linspace(0, 8, 161)
        axes[1].plot(grid, transition_probabilities(theta, grid)[:, 1, 0]*100, color=colors[name])
        for ax, metric_prefix in ((axes[0], "observed_reversion_cif_"),
                                  (axes[1], "occupation_cn_from_mci_")):
            for horizon in HORIZONS:
                e = estimate_index[(name, f"{metric_prefix}{int(horizon)}y")]
                value = e["estimate"] * 100
                ax.errorbar(horizon, value,
                            yerr=[[max(0., value-e["ci_low"]*100)],
                                  [max(0., e["ci_high"]*100-value)]],
                            color=colors[name], fmt="o", markersize=3, capsize=2, alpha=.65)
    axes[0].set(title="First recorded reversion before dementia", xlabel="Years from clinical baseline", ylabel="Cumulative observed detection (%)", xlim=(0, 8))
    axes[0].legend(fontsize=7, frameon=False)
    axes[1].set(title="Panel-model CN state occupation", xlabel="Years from clinical baseline", ylabel="P(CN at time t | baseline MCI) (%)", xlim=(0, 8))
    names = list(SCHEDULES); vals = [points[n]["direct_share_among_detected_pct"] for n in names]
    axes[2].bar(range(3), vals, color=[colors[n] for n in names])
    axes[2].set(xticks=range(3), xticklabels=["Full", "2-y mask", "4-y mask"], ylabel="No post-baseline MCI before dementia (%)", title="Observed direct-path classification")
    for i, n in enumerate(names):
        axes[2].text(i, vals[i]+1, f"{points[n]['observed_direct_n']:.0f}/{points[n]['detected_dementia_n']:.0f}", ha="center", fontsize=9)
        e = estimate_index[(n, "direct_share_among_detected_pct")]
        axes[2].errorbar(i, vals[i], yerr=[[max(0., vals[i]-e["ci_low"])],
                                         [max(0., e["ci_high"]-vals[i])]],
                        fmt="none", color="#333333", capsize=4)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", alpha=.2); ax.set_axisbelow(True)
    fig.suptitle("G-128: independent ADNI observation-masking experiment", fontsize=12, fontweight="bold")
    fig.text(.01, .01, f"v{VERSION} | {stamp} | Whiskers: subject-bootstrap 95% intervals. Same people and last observation retained; latent onset/bypass not identified.", fontsize=7)
    fig.tight_layout(rect=(0, .05, 1, .92))
    for extension in ("png", "pdf"):
        fig.savefig(OUT / f"G-128_independent.{extension}", dpi=300)
    plt.close(fig)

    def f(value):
        return "NA" if value is None else f"{value:.3f}"
    report = ["# G-128: independent ADNI-only experiment", "", f"Version {VERSION} | Built {stamp} | Candidate, pending human review", "",
              "The earlier experiment is preserved. This script executes an independent panel-model and paired observation-masking analysis. No manuscript, registry or human gate is modified.", "",
              "## Definition fixed before estimation", "",
              "Clinical baseline is the earliest valid, dated DXSUM VISCODE2=bl diagnosis, confirmed by a dated REGISTRY baseline. Screening diagnoses do not establish baseline. Identical same-day rows are collapsed only after preserving baseline labels. Two conflicting same-day diagnosis dates are omitted instead of choosing the worse diagnosis. Positive QC-error rows are excluded; missing QC flags are not interpreted as a pass.",
              "All follow-up is measured from each person's own baseline and restricted to eight years. Baseline-only participants are counted and contribute no panel intervals. Both baseline CN and baseline MCI participants estimate one homogeneous four-rate generator. Each schedule is applied to raw diagnoses before truncating at its first observed dementia; later reversals are counted descriptively and not propagated into hidden visits.", "",
              f"- Strict baseline MCI: {counts['baseline_mci']['n']}; with later diagnosis within eight years: {counts['baseline_mci']['with_followup']}.",
              f"- Strict baseline CN: {counts['baseline_cn']['n']}; with later diagnosis within eight years: {counts['baseline_cn']['with_followup']}.",
              f"- Same-day conflicts removed: {counts['same_day_conflicting_dates_dropped']} dates; dementia followed by a later non-dementia diagnosis: {counts['dementia_then_later_non_dementia_subjects']} people.", "",
              "## What the masking comparison estimates", "",
              "The two/four-year masks retain visits near absolute baseline-anchored grid points (+/- six months) AND each participant's original terminal diagnosis. This deliberately preserves the source people, last observation time and last diagnosis rather than excluding short-follow-up participants. It does not guarantee preservation of every clinical event: a hidden dementia label followed by a later non-dementia label can disappear from the masked series. Detected and missed dementia cases are therefore reported separately against a fixed full-series converter reference. This measures loss of intermediate observations, not the result of imposing a literal two/four-year clinical policy. The four-year-visit sensitivity below instead conditions on an actual diagnosis within 3.5-4.5 years; it is selected on follow-up retention and cannot estimate population risk.", "",
              "The continuous-time Markov likelihood uses transition matrices exp(Q*dt) for actual elapsed intervals. Dementia is absorbing as a first-observed-diagnosis endpoint; CN and MCI can reverse. MCI-to-dementia intensity is the fitted q(MCI,dementia), not transitions divided by time spent in every state. Time homogeneity, the Markov assumption, error-free recorded diagnoses and non-informative observation/censoring are assumptions, not established facts. No latent biological event time is measured. Model method: [Jackson 2011](https://www.jstatsoft.org/article/view/v038i08).", "",
              "The panel-model CN curve is state occupation P(X(t)=CN | X(0)=MCI), not probability of ever reverting. A separate competing-event curve records first observed CN before first observed dementia. Its event dates are diagnosis visits, so these are detection curves, not true-onset curves.", "",
              "## Paired point estimates", "",
              "| Schedule | MCI people | CN people | 4-y observed first-reversion CIF | 4-y model CN occupation | MCI-to-dementia intensity /100 person-years | Direct / detected dementia | Direct share |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in SCHEDULES:
        x = points[name]
        report.append(f"| {name} | {x['source_cohort_mci_n']} | {x['source_cohort_cn_n']} | {100*x['observed_reversion_cif_4y']:.2f}% | {100*x['occupation_cn_from_mci_4y']:.2f}% | {x['mci_dementia_intensity_per_100py']:.3f} | {x['observed_direct_n']:.0f}/{x['detected_dementia_n']:.0f} | {f(x['direct_share_among_detected_pct'])}% |")
    report += ["", "## Changes relative to the full series", "",
               "Differences below are mask minus full; intervals are paired subject-bootstrap percentiles, stratified by baseline state. Full CSVs include all two-, four- and eight-year occupation/detection quantities, each marginal interval and each paired contrast.", "",
               "| Mask | Quantity | Difference | 95% interval | Valid bootstrap pairs |", "|---|---|---:|---|---:|"]
    highlight = {"observed_reversion_cif_4y", "occupation_cn_from_mci_4y", "mci_dementia_intensity_per_100py", "direct_share_among_detected_pct", "observed_direct_fixed_converter_pct", "missed_full_series_converter_pct"}
    for x in differences:
        if x["metric"] in highlight:
            report.append(f"| {x['schedule']} | {x['metric']} | {f(x['difference_mask_minus_full'])} | {f(x['ci_low'])} to {f(x['ci_high'])} | {x['valid_paired_replicates']} |")
    report += ["", "Probability differences above are fractions (multiply by 100 for percentage points); direct-share differences are already percentage points; intensity differences are transitions per 100 MCI person-years in the fitted model.", "",
               "## Common four-year-visit subset and grid sensitivity", "",
               f"The endpoint-visit subset contains {endpoint['n_by_baseline']['baseline_mci']} baseline-MCI and {endpoint['n_by_baseline']['baseline_cn']} baseline-CN people. Eligibility requires a recorded visit in the four-year window and thus selects people retained in follow-up. These counts are not the same as all baseline or ever-MCI counts.",
               "Changing the grid origin and the visit window is reported in G-128_grid_sensitivity.csv. These are sensitivity results rather than additional independent experiments.", "",
               "## Audit of the earlier implementation", "",
               "1. Earlier t was date minus the earliest date in the ENTIRE dataset. Cohort construction computed each baseline but did not subtract it. Its two/four-year probabilities therefore did not refer to each participant's follow-up time.",
               "2. Earlier masks reduced the MCI cohort from 1,252 to 1,023 to 690 and changed converter denominators. Their differences combined masking with different people and follow-up lengths. Its len<3 bypass also left two-visit short intervals unthinned without stating that exception.",
               "3. Earlier event intensities divided MCI transitions by total person-time across all states, not MCI exposure. Midpoint-imputed transitions and risk sets holding the original interval-start state are not a valid interval-censored panel-data Aalen-Johansen estimator.",
               "4. The first observed diagnosis, screening arm, ever-MCI and true clinical baseline are different cohort definitions. The headline ever-MCI count does not by itself meet a baseline-MCI or complete-annual-follow-up requirement.", "",
               "## Limits and status", "",
               f"Direct-path classification concerns the post-baseline observed series. {counts['baseline_cn_with_recorded_mci_before_baseline']} baseline-CN people already had an MCI diagnosis recorded before baseline; excluding them is a separate JSON sensitivity, not silently treated as first-ever normal status.", "",
               "This is a runnable ADNI-only sensitivity experiment. It is not evidence that all clinically direct transitions are biological bypasses or artefacts. Thinning can conceal an intermediate MCI visit that exists in the full series; the full series can itself miss unobserved transitions. Death ascertainment is not complete in the available starter files and is not modelled as a competing biological state. Retention and observation may be informative. No human closure is asserted, and a stricter criterion requiring 1,000 baseline-MCI people with complete annual multi-year observation must be checked separately.",
               f"Only {counts['baseline_mci']['all_gaps_at_most_400_days_n']} MCI participants had every observed follow-up gap at most 400 days; {counts['baseline_mci']['all_gaps_at_most_548_days_n']} had every gap at most 548 days. The stricter 1,000-person complete-annual-trajectory criterion is not established. The common four-year subset has {endpoint['n_by_baseline']['baseline_mci']} baseline-MCI participants, not the full baseline-MCI count.",
               "The fitted direct CN-to-dementia generator rate is on the numerical lower bound in every schedule. This is a model-boundary result, not evidence that biological direct conversion cannot occur. Standard regular asymptotic inference for that boundary parameter is inappropriate. All interval estimates are conditional on the stated model; the modest MCI-to-dementia intensity contrast should not be presented as a robust biological effect merely because a percentile endpoint falls near zero.",
               f"Bootstrap replicates: {args.reps}; seed {args.seed}; failed fits by schedule: {failures}. Boundary rates are reported in JSON. Outcomes from original models are not overwritten.", "",
               "![Independent observation-masking experiment](G-128_independent.png)", ""]
    (OUT / "G-128_independent.md").write_text("\n".join(report), encoding="utf-8")
    write_literal_grid_sensitivity(cohorts, counts, sources)
    print(json.dumps({"finished": True, "seconds": result["seconds"], "point": points,
                      "four_year_subset": endpoint, "fit_failures": failures}), flush=True)


if __name__ == "__main__":
    main()
