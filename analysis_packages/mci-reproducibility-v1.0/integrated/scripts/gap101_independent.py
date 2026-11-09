"""Independent ADNI-only G-101 measured-selection sensitivity analysis.

This is not an identified correction for index-event/collider bias. It compares
the same baseline-MCI participants before/after standardizing measured entry
covariates toward a pooled enrolled ADNI CN+MCI reference. No controlled records
are exported: outputs contain aggregate numbers and figures only.

Run: .venv/Scripts/python.exe -X utf8 scripts/gap101_independent.py --bootstrap 300
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "adni_independent_2026-11-02" / "G-101"
VERSION = "1.1"
CN, MCI, DEM = 1, 2, 3
PHASES = ("ADNI1", "GO2", "ADNI3", "ADNI4")
BASE = ("exposure", "age10", "age10_sq", "female", "educ4")
ARCHIVES = {
    "DXSUM": "Diagnostic Summary [ADNI1,GO,2,3,4,TEAM].zip",
    "REGISTRY": "Registry [ADNI1,GO,2,3,4,TEAM].zip",
    "PTDEMOG": "Subject Demographics [ADNI1,GO,2,3,4,TEAM].zip",
    "APOERES": "ApoE Genotyping - Results [ADNI1,GO,2,3,4].zip",
    "MEDHIST": "Medical History [ADNI1,GO,2].zip",
}


def read_sources():
    tables, provenance = {}, []
    for key, archive in ARCHIVES.items():
        path = ROOT / "downloads" / "adni" / archive
        with zipfile.ZipFile(path) as z:
            members = [n for n in z.namelist() if Path(n).name.startswith(key + "_") and n.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"Expected one {key} CSV in archive; found {len(members)}")
            raw = z.read(members[0])
        tables[key] = pd.read_csv(io.BytesIO(raw), dtype=str, low_memory=False)
        provenance.append({"archive": archive, "member": members[0], "csv_sha256": hashlib.sha256(raw).hexdigest(), "rows": len(tables[key])})
    return tables, provenance


def numbers(series):
    return pd.to_numeric(series, errors="coerce")


def prepare(tables):
    audit = {}
    raw = tables["DXSUM"].copy()
    raw["RID"] = numbers(raw["RID"])
    raw["state"] = numbers(raw["DIAGNOSIS"])
    raw["date"] = pd.to_datetime(raw["EXAMDATE"], errors="coerce")
    failed_qc = raw.get("HAS_QC_ERROR", pd.Series(index=raw.index, dtype=str)).astype(str).str.lower().isin(["1", "true", "yes"])
    audit["raw_diagnosis_rows"] = len(raw)
    audit["raw_baseline_diagnosis_rows"] = int(((raw.VISCODE2 == "bl") & raw.state.isin([CN, MCI, DEM])).sum())
    audit["baseline_rows_without_actual_date"] = int(((raw.VISCODE2 == "bl") & raw.state.isin([CN, MCI, DEM]) & raw.date.isna()).sum())
    audit["explicit_failed_diagnosis_qc_rows_excluded"] = int(failed_qc.sum())
    valid = raw.loc[raw.RID.notna() & raw.date.notna() & raw.state.isin([CN, MCI, DEM]) & ~failed_qc].copy()
    valid["RID"] = valid.RID.astype(int)
    audit["valid_dated_diagnosis_rows"] = len(valid)
    discordant = valid.groupby(["RID", "date"]).state.nunique()
    bad_keys = discordant[discordant > 1].index
    audit["discordant_same_day_dates_excluded"] = len(bad_keys)
    keep = ~pd.MultiIndex.from_frame(valid[["RID", "date"]]).isin(bad_keys)
    valid = valid.loc[keep].copy()

    # Preserve an explicit baseline flag BEFORE collapsing identical screening/
    # baseline diagnoses occurring on the same calendar date.
    baseline = valid.loc[valid.VISCODE2.eq("bl")].sort_values(["RID", "date"]).drop_duplicates("RID", keep="first")
    audit["dated_explicit_baseline_people_before_registry"] = len(baseline)
    registry = tables["REGISTRY"].copy()
    registry["RID"] = numbers(registry["RID"])
    registry["date"] = pd.to_datetime(registry["EXAMDATE"], errors="coerce")
    failed_registry_qc = registry.get("HAS_QC_ERROR", pd.Series(index=registry.index, dtype=str)).astype(str).str.lower().isin(["1", "true", "yes"])
    audit["explicit_failed_registry_qc_rows_excluded"] = int(failed_registry_qc.sum())
    audit["explicit_failed_registry_dated_baseline_qc_rows_excluded"] = int((failed_registry_qc & registry.VISCODE2.eq("bl") & registry.RID.notna() & registry.date.notna()).sum())
    reg_bl = registry.loc[registry.VISCODE2.eq("bl") & registry.RID.notna() & registry.date.notna() & ~failed_registry_qc, ["RID", "PHASE"]].drop_duplicates()
    reg_bl["RID"] = reg_bl.RID.astype(int)
    registered_keys = pd.MultiIndex.from_frame(reg_bl)
    eligible_keys = pd.MultiIndex.from_frame(baseline[["RID", "PHASE"]])
    baseline = baseline.loc[eligible_keys.isin(registered_keys)].copy()
    audit["registered_baseline_all_diagnoses"] = len(baseline)
    audit["registered_baseline_counts"] = {str(int(k)): int(v) for k, v in baseline.state.value_counts().items()}
    dx = valid.sort_values(["RID", "date", "PHASE"]).drop_duplicates(["RID", "date"], keep="first")
    audit["unique_dated_diagnosis_records"] = len(dx)
    audit["unique_dated_diagnosis_people"] = int(dx.RID.nunique())
    baseline = baseline.loc[baseline.state.isin([CN, MCI]), ["RID", "date", "state", "PHASE"]].rename(columns={"date": "baseline_date", "state": "baseline_state", "PHASE": "entry_phase"})
    audit["baseline_cn_mci_before_prior_dementia_exclusion"] = len(baseline)
    first_dem = dx.loc[dx.state.eq(DEM)].groupby("RID").date.min().rename("first_ever_dementia_date")
    baseline = baseline.merge(first_dem, on="RID", how="left")
    prior = baseline.first_ever_dementia_date.notna() & baseline.first_ever_dementia_date.lt(baseline.baseline_date)
    audit["prior_observed_dementia_excluded"] = int(prior.sum())
    baseline = baseline.loc[~prior].copy()
    baseline["S"] = baseline.baseline_state.eq(MCI).astype(int)
    baseline["phase_group"] = baseline.entry_phase.replace({"ADNIGO": "GO2", "ADNI2": "GO2"})
    baseline = baseline.loc[baseline.phase_group.isin(PHASES)].copy()

    dm = tables["PTDEMOG"].copy()
    dm["RID"] = numbers(dm.RID)
    dm["date"] = pd.to_datetime(dm.VISDATE, errors="coerce")
    birth = pd.to_datetime(dm.PTDOB, format="%m/%Y", errors="coerce")
    birth = birth.fillna(pd.to_datetime(dm.PTDOBYY, errors="coerce"))
    dm["birth"] = birth
    sex = numbers(dm.PTGENDER)
    dm["female"] = sex.map({1: 0.0, 2: 1.0})
    education = numbers(dm.PTEDUCAT)
    dm["education"] = education.where(education.between(0, 30))
    before = dm.merge(baseline[["RID", "baseline_date"]], on="RID", how="inner")
    before = before.loc[before.date.notna() & before.date.le(before.baseline_date)]
    # Latest nonmissing pre-baseline values, field by field; no post-entry data.
    cov = before.sort_values(["RID", "date"]).groupby("RID")[["birth", "female", "education"]].last().reset_index()
    baseline = baseline.merge(cov, on="RID", how="left")
    baseline["age"] = (baseline.baseline_date - baseline.birth).dt.days / 365.25
    baseline.loc[~baseline.age.between(40, 110), "age"] = np.nan
    baseline["age10"] = (baseline.age - 75) / 10
    baseline["age10_sq"] = baseline.age10 ** 2
    baseline["educ4"] = (baseline.education - 16) / 4

    ap = tables["APOERES"].copy()
    ap["RID"] = numbers(ap.RID)
    normalized = ap.GENOTYPE.fillna("").str.replace(" ", "", regex=False)
    valid_genotype = normalized.str.fullmatch(r"[234]/[234]")
    ap["e4"] = normalized.where(valid_genotype).str.count("4").clip(upper=1)
    audit["genotype_records"] = len(ap)
    audit["invalid_or_missing_genotype_records"] = int((~valid_genotype).sum())
    conflict = ap.groupby("RID").e4.nunique()
    conflict_ids = conflict[conflict > 1].index
    audit["conflicting_genotype_participants_excluded"] = len(conflict_ids)
    ap = ap.loc[~ap.RID.isin(conflict_ids)].drop_duplicates("RID")[["RID", "e4"]]
    baseline = baseline.merge(ap, on="RID", how="left")

    mh = tables["MEDHIST"].copy()
    mh["RID"] = numbers(mh.RID)
    mh["date"] = pd.to_datetime(mh.VISDATE, errors="coerce")
    for source, dest in (("MH16SMOK", "smoke"), ("MH4CARD", "cardiac"), ("MH9ENDO", "endocrine")):
        value = numbers(mh[source])
        mh[dest] = value.where(value.isin([0, 1]))
    mh = mh.merge(baseline[["RID", "baseline_date"]], on="RID", how="inner")
    delta = (mh.date - mh.baseline_date).dt.days
    audit["medical_history_rows_after_baseline_not_used"] = int(delta.gt(0).sum())
    mh = mh.loc[delta.between(-365, 0)].sort_values(["RID", "date"]).groupby("RID")[["smoke", "cardiac", "endocrine"]].last().reset_index()
    baseline = baseline.merge(mh, on="RID", how="left")
    audit["prevalent_mci_before_covariate_exclusions"] = int(baseline.S.sum())
    audit["baseline_cn_before_covariate_exclusions"] = int((baseline.S == 0).sum())

    last = dx.groupby("RID").date.max().rename("last_diagnosis_date")
    baseline = baseline.merge(last, on="RID", how="left")
    baseline["followup_years"] = (baseline.last_diagnosis_date - baseline.baseline_date).dt.days / 365.25
    baseline["event_years"] = (baseline.first_ever_dementia_date - baseline.baseline_date).dt.days / 365.25
    # Earliest observed dementia is absorbing for this endpoint, even if a
    # later record reverts to MCI. Reversion does not remove baseline-MCI status.
    audit["mci_with_positive_diagnosis_followup"] = int(((baseline.S == 1) & (baseline.followup_years > 0)).sum())
    return dx, baseline, audit


def selection_matrix(source, exposure):
    x = source[[exposure, "age10", "age10_sq", "female", "educ4"]].to_numpy(float)
    phases = [p for p in PHASES[1:] if source.phase_group.eq(p).any()]
    extra = np.column_stack([source.phase_group.eq(p).to_numpy(float) for p in phases]) if phases else np.empty((len(source), 0))
    return np.column_stack([np.ones(len(source)), x, extra]), ["constant", exposure, "age10", "age10_sq", "female", "educ4"] + ["phase_" + p for p in phases]


def logistic_fit(x, y):
    # A negligible numerical ridge prevents infinite nuisance coefficients in
    # sparse resampled phase cells; it is refitted in every bootstrap replicate.
    ridge = 1e-6
    def objective(beta):
        eta = x @ beta
        penalty = ridge * np.dot(beta[1:], beta[1:]) / 2
        value = np.sum(np.logaddexp(0, eta) - y * eta) + penalty
        grad = x.T @ (expit(eta) - y)
        grad[1:] += ridge * beta[1:]
        return value, grad
    fit = minimize(objective, np.zeros(x.shape[1]), jac=True, method="BFGS", options={"gtol": 1e-6, "maxiter": 300})
    if not np.all(np.isfinite(fit.x)) or np.max(np.abs(objective(fit.x)[1])) > 1e-3:
        raise RuntimeError("Selection logistic did not converge")
    return fit.x, np.clip(expit(x @ fit.x), 1e-8, 1 - 1e-8)


def cox_fit(x, duration, event, strata, weights=None, start=None):
    """Weighted, phase-stratified Cox partial likelihood, Breslow ties."""
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    blocks = []
    for group in np.unique(strata):
        ix = np.flatnonzero(strata == group)
        ix = ix[np.argsort(-duration[ix], kind="stable")]
        tt, ee, xx, ww = duration[ix], event[ix], x[ix], w[ix]
        ends = np.r_[np.flatnonzero(tt[:-1] != tt[1:]), len(tt) - 1]
        starts = np.r_[0, ends[:-1] + 1]
        event_weights = np.add.reduceat(ww * ee, starts)
        active = event_weights > 0
        if active.any():
            blocks.append((xx, ww, ee, ends[active], event_weights[active]))
    if not blocks:
        raise RuntimeError("No events")
    def objective(beta):
        value, gradient = 0.0, np.zeros(x.shape[1])
        for xx, ww, ee, ends, dw in blocks:
            eta = xx @ beta
            shift = float(eta.max())
            ew = ww * np.exp(eta - shift)
            risk = np.cumsum(ew)[ends]
            risk_x = np.cumsum(ew[:, None] * xx, axis=0)[ends]
            value -= np.sum(ww * ee * eta) - np.sum(dw * (np.log(risk) + shift))
            gradient -= xx.T @ (ww * ee) - np.sum(dw[:, None] * risk_x / risk[:, None], axis=0)
        return value, gradient
    fit = minimize(objective, np.zeros(x.shape[1]) if start is None else start, jac=True, method="BFGS", options={"gtol": 1e-6, "maxiter": 300})
    if not np.all(np.isfinite(fit.x)) or np.max(np.abs(objective(fit.x)[1])) > 1e-3:
        raise RuntimeError("Cox model did not converge")
    return fit.x


def subject_weights(probability, selected, truncation):
    raw = selected.mean() / probability[selected]
    if truncation is None:
        lo, hi = float(raw.min()), float(raw.max())
        weights = raw.copy()
    else:
        lo, hi = np.quantile(raw, truncation)
        weights = np.clip(raw, lo, hi)
    weights /= weights.mean()
    return weights, raw, [float(lo), float(hi)]


def balance(source, mci, exposure, weights):
    fields = [exposure, "age", "female", "education"]
    result = []
    for field in fields + ["phase_" + p for p in PHASES]:
        if field.startswith("phase_"):
            phase = field.removeprefix("phase_")
            full = source.phase_group.eq(phase).to_numpy(float)
            case = mci.phase_group.eq(phase).to_numpy(float)
        else:
            full, case = source[field].to_numpy(float), mci[field].to_numpy(float)
        mean, v = full.mean(), full.var()
        def smd(w):
            avg = np.average(case, weights=w)
            vv = np.average((case - avg) ** 2, weights=w)
            denom = np.sqrt((v + vv) / 2)
            return float((avg - mean) / denom) if denom > 0 else 0.0
        result.append({"covariate": field, "target_mean": float(mean), "mci_mean": float(case.mean()), "weighted_mci_mean": float(np.average(case, weights=weights)), "smd_before": smd(np.ones(len(case))), "smd_after": smd(weights)})
    return result


def analyse_exposure(baseline, exposure, label, bootstrap, seed, horizon):
    needed = [exposure, "age10", "age10_sq", "female", "educ4", "phase_group"]
    source = baseline.dropna(subset=needed).copy().reset_index(drop=True)
    source = source.loc[source.phase_group.isin(PHASES)].reset_index(drop=True)
    selected = source.S.to_numpy() == 1
    mci = source.loc[selected].copy()
    xs, names = selection_matrix(source, exposure)
    beta_s, probability = logistic_fit(xs, source.S.to_numpy(float))
    xx = source[[exposure, "age10", "age10_sq", "female", "educ4"]].to_numpy(float)
    durations = np.minimum(source.followup_years.to_numpy(float), horizon)
    events = (source.event_years.notna() & source.event_years.gt(0) & source.event_years.le(horizon)).to_numpy(int)
    durations = np.where(events == 1, source.event_years.to_numpy(float), durations)
    strata = source.phase_group.to_numpy(str)
    naive = cox_fit(xx[selected], durations[selected], events[selected], strata[selected])
    methods = {"untrimmed": None, "winsor_01_99": (0.01, 0.99), "winsor_05_95": (0.05, 0.95)}
    weighted = {}
    for method, trunc in methods.items():
        weights, raw, cutoffs = subject_weights(probability, selected, trunc)
        beta = cox_fit(xx[selected], durations[selected], events[selected], strata[selected], weights, naive)
        weighted[method] = {"hr": float(np.exp(beta[0])), "log_hr": float(beta[0]), "log_hr_difference": float(beta[0] - naive[0]), "percent_hr_change": float(100 * (np.exp(beta[0] - naive[0]) - 1)), "effective_sample_size": float(weights.sum() ** 2 / np.square(weights).sum()), "normalized_weight_quantiles": {str(q): float(v) for q, v in zip([0, 0.01, 0.5, 0.99, 1], np.quantile(weights, [0, 0.01, 0.5, 0.99, 1]))}, "raw_weight_winsor_limits": cutoffs, "balance": balance(source, mci, exposure, weights)}
    # Verify the independent unweighted Cox implementation against statsmodels.
    from statsmodels.duration.hazard_regression import PHReg
    reference = PHReg(durations[selected], xx[selected], status=events[selected], strata=strata[selected], ties="breslow").fit(disp=0)
    agreement = float(np.max(np.abs(reference.params - naive)))
    if agreement > 1e-4:
        raise AssertionError(f"Independent Cox mismatch versus PHReg: {agreement}")

    rng = np.random.default_rng(seed)
    boot = []
    failures = 0
    begin = time.monotonic()
    for rep in range(bootstrap):
        index = rng.integers(0, len(source), len(source))
        ss = source.S.to_numpy()[index] == 1
        try:
            bs, ps = logistic_fit(xs[index], source.S.to_numpy(float)[index])
            bn = cox_fit(xx[index][ss], durations[index][ss], events[index][ss], strata[index][ss], start=naive)
            bw, _, _ = subject_weights(ps, ss, (0.01, 0.99))
            bc = cox_fit(xx[index][ss], durations[index][ss], events[index][ss], strata[index][ss], bw, bn)
            boot.append([bs[1], bn[0], bc[0], bc[0] - bn[0]])
        except (RuntimeError, np.linalg.LinAlgError, ValueError, FloatingPointError):
            failures += 1
        if (rep + 1) % 50 == 0:
            print(f"{exposure}: bootstrap {rep + 1}/{bootstrap}, successful {len(boot)}, elapsed {time.monotonic()-begin:.1f}s", flush=True)
    arr = np.asarray(boot)
    if len(arr) < max(30, 0.8 * bootstrap):
        raise RuntimeError(f"Too few successful full-stage bootstrap samples: {len(arr)}/{bootstrap}")
    ci = np.quantile(arr, [0.025, 0.975], axis=0)
    weighted["winsor_01_99"]["bootstrap_ci"] = np.exp(ci[:, 2]).tolist()
    weighted["winsor_01_99"]["paired_log_hr_difference_ci"] = ci[:, 3].tolist()
    weighted["winsor_01_99"]["hr_ratio_vs_naive_ci"] = np.exp(ci[:, 3]).tolist()
    return {"exposure": exposure, "label": label, "source_n": len(source), "source_cn_n": int((~selected).sum()), "prevalent_mci_n": int(selected.sum()), "mci_positive_followup_n": int((mci.followup_years > 0).sum()), "mci_zero_followup_censored_n": int((mci.followup_years <= 0).sum()), "first_dementia_events_within_horizon": int(events[selected].sum()), "observed_followup_person_years": float(durations[selected].sum()), "exposed_source_n": int(source[exposure].sum()), "exposed_mci_n": int(mci[exposure].sum()), "horizon_years": horizon, "selection_covariates": names, "outcome_covariates": [exposure, "age10", "age10_sq", "female", "educ4"], "outcome_stratified_by": "ADNI1 / ADNIGO+ADNI2 / ADNI3 / ADNI4", "selection_or": float(np.exp(beta_s[1])), "selection_bootstrap_ci": np.exp(ci[:, 0]).tolist(), "naive_hr": float(np.exp(naive[0])), "naive_bootstrap_ci": np.exp(ci[:, 1]).tolist(), "weighted": weighted, "selection_probability_quantiles_mci": {str(q): float(v) for q, v in zip([0, 0.01, 0.5, 0.99, 1], np.quantile(probability[selected], [0, 0.01, 0.5, 0.99, 1]))}, "phase_source_counts": source.groupby(["phase_group", "S"]).size().unstack(fill_value=0).to_dict(), "bootstrap": {"requested": bootstrap, "successful": len(arr), "failed": failures, "seed": seed, "resampling_unit": "participant from pooled baseline CN+MCI; selection and both outcome fits refitted each time", "seconds": time.monotonic()-begin}, "cox_validation_max_abs_coefficient_difference": agreement}


def incident_summary(dx, baseline):
    cn = baseline.loc[baseline.S.eq(0)]
    result = {"baseline_cn_n": len(cn), "observed_incident_mci_before_any_dementia_n": 0, "later_dementia_after_incident_mci_n": 0}
    for row in cn.itertuples():
        trajectory = dx.loc[dx.RID.eq(row.RID)].sort_values("date")
        history = trajectory.loc[trajectory.date.lt(row.baseline_date)]
        if history.state.isin([MCI, DEM]).any():
            continue
        future = trajectory.loc[trajectory.date.gt(row.baseline_date)]
        first_m = future.loc[future.state.eq(MCI), "date"].min()
        first_d = future.loc[future.state.eq(DEM), "date"].min()
        if pd.notna(first_m) and (pd.isna(first_d) or first_m < first_d):
            result["observed_incident_mci_before_any_dementia_n"] += 1
            if pd.notna(first_d) and first_d > first_m:
                result["later_dementia_after_incident_mci_n"] += 1
    result["note"] = "Different inception cohort/estimand from prevalent baseline MCI; no baseline-MCI selection weights applied to these incident cases. Counts use all subsequent available diagnosis visits, not only the four-year primary horizon."
    return result


def write_outputs(result):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "G-101_independent_results.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, allow_nan=False, default=lambda x: int(x) if isinstance(x, np.integer) else float(x))
    compact = []
    for r in result["exposures"]:
        q = r["weighted"]["winsor_01_99"]
        compact.append({"exposure": r["label"], "source_n": r["source_n"], "baseline_mci_n": r["prevalent_mci_n"], "events": r["first_dementia_events_within_horizon"], "naive_hr": r["naive_hr"], "naive_ci_low": r["naive_bootstrap_ci"][0], "naive_ci_high": r["naive_bootstrap_ci"][1], "weighted_hr": q["hr"], "weighted_ci_low": q["bootstrap_ci"][0], "weighted_ci_high": q["bootstrap_ci"][1], "paired_hr_ratio": q["hr"] / r["naive_hr"], "paired_hr_ratio_ci_low": q["hr_ratio_vs_naive_ci"][0], "paired_hr_ratio_ci_high": q["hr_ratio_vs_naive_ci"][1], "weighted_ess": q["effective_sample_size"], "version": VERSION, "built_at_utc": result["built_at_utc"]})
    pd.DataFrame(compact).to_csv(OUT / "G-101_independent_estimates.csv", index=False)
    balances = []
    for r in result["exposures"]:
        for row in r["weighted"]["winsor_01_99"]["balance"]:
            balances.append({"exposure": r["label"], **row})
    pd.DataFrame(balances).to_csv(OUT / "G-101_balance.csv", index=False)
    fmt = lambda x: f"{x:.3f}"
    lines = ["# G-101: independent ADNI-only measured-selection sensitivity analysis", "", f"Version {VERSION} | Built {result['built_at_utc']} | Candidate, pending human review", "", "This is an executed analysis, not an identified causal correction or a finding that collider bias is absent. No NACC data are used, and no ledger or human gate is changed.", "", "## Design fixed before the new fits", "", "- Baseline is an explicit dated DXSUM VISCODE2=bl record, confirmed by a dated REGISTRY baseline in the same phase. Baseline flags are retained before same-day deduplication; screening-only records are not substituted for baseline.", "- Identical same-day diagnoses are collapsed; discordant same-day diagnoses are excluded rather than resolving them toward the more severe state. Prior observed dementia is excluded for a first-dementia endpoint.", "- Baseline CN and baseline MCI are disjoint. Only baseline-MCI participants enter the progression model. People recruited CN who later develop MCI are described separately and are not given prevalent-MCI entry weights.", "- The endpoint is the first OBSERVED clinical dementia diagnosis after baseline, regardless of an intervening CN diagnosis. Observation ends at the last known diagnosis or four years, whichever comes first. A person contributes at most one dementia event. Participants without later diagnosis are censored at time zero; they do not count as four-year non-cases.", "- Covariates are age (linear and quadratic), recorded sex, education, and study phase. Demographic values must be recorded by baseline; smoking history is the most recent nonmissing pre-baseline flag within 365 days. Unknown sex/education/genotype stays missing, and exposure-specific complete-case counts are reported.", "- Selection logistic regression models baseline MCI versus CN in the pooled enrolled ADNI reference, with the exposure, age, sex, education and phase. Outcome models are phase-stratified Cox models with the same age/sex/education terms. The negligible logistic nuisance ridge is 1e-6; it is refitted in every bootstrap sample.", "- Stabilized inverse-entry-probability weights standardize measured baseline composition toward that pooled ADNI reference. Winsorization limits are calculated over PARTICIPANTS, not repeated visits. Untrimmed, 1st/99th and 5th/95th sensitivities are shown; 1st/99th is primary.", "- A participant bootstrap refits selection weights and both outcome models. The paired change interval is computed from the within-bootstrap log-HR difference. These are internal precision estimates for this selected ADNI sample, not population-sampling intervals.", "", "## Cohort audit", "", "```json", json.dumps(result["cohort_audit"], indent=2), "```", "", "## Side-by-side estimates", "", "Hazard ratios concern the rate of first recorded dementia over the stated follow-up. They are not the prior script's per-visit odds ratios.", "", "| Exposure | Reference CN+MCI | Baseline MCI | Dementia events | Naive adjusted HR (bootstrap 95% interval) | Measured-selection weighted HR (95% interval) | Weighted/naive HR ratio (paired 95% interval) | Weighted ESS |", "|---|---:|---:|---:|---|---|---|---:|"]
    for r in compact:
        lines.append(f"| {r['exposure']} | {r['source_n']} | {r['baseline_mci_n']} | {r['events']} | {fmt(r['naive_hr'])} ({fmt(r['naive_ci_low'])}-{fmt(r['naive_ci_high'])}) | {fmt(r['weighted_hr'])} ({fmt(r['weighted_ci_low'])}-{fmt(r['weighted_ci_high'])}) | {fmt(r['paired_hr_ratio'])} ({fmt(r['paired_hr_ratio_ci_low'])}-{fmt(r['paired_hr_ratio_ci_high'])}) | {r['weighted_ess']:.1f} |")
    lines += ["", "## Weight and model checks", ""]
    for r in result["exposures"]:
        lines += [f"### {r['label']}", "", f"- Enrolled reference: {r['source_cn_n']} CN and {r['prevalent_mci_n']} baseline MCI; {r['mci_positive_followup_n']} MCI have positive observed follow-up and {r['mci_zero_followup_censored_n']} are censored at time zero.", f"- Exposure-positive subjects: reference {r['exposed_source_n']}, MCI {r['exposed_mci_n']}.", f"- Baseline-MCI membership OR: {r['selection_or']:.3f}, bootstrap interval {r['selection_bootstrap_ci'][0]:.3f}-{r['selection_bootstrap_ci'][1]:.3f}. This is cross-sectional entry composition, not incident-MCI risk.", f"- Full two-stage bootstrap: {r['bootstrap']['successful']}/{r['bootstrap']['requested']} successful; seed {r['bootstrap']['seed']}.", f"- Independent unweighted Cox matches statsmodels PHReg to maximum absolute coefficient difference {r['cox_validation_max_abs_coefficient_difference']:.3g}.", "", "| Weight rule | Exposure HR | HR change from naive | ESS | Maximum absolute SMD after weighting |", "|---|---:|---:|---:|---:|"]
        for method, q in r["weighted"].items():
            lines.append(f"| {method} | {q['hr']:.3f} | {q['percent_hr_change']:.2f}% | {q['effective_sample_size']:.1f} | {max(abs(b['smd_after']) for b in q['balance']):.3f} |")
        lines += ["", "Selection probability and weight quantiles, phase counts, and full balance measures are retained in the JSON and balance CSV.", ""]
    lines += ["## Incident-MCI feasibility check", "", "```json", json.dumps(result["incident_mci_feasibility"], indent=2), "```", "", "## What the prior analysis got wrong", "", "- Its progression rows include baseline-CN recruits who later have MCI, while its weights model prevalent MCI at entry. Those are different selection events, so its labelled within-prevalent-MCI comparison is not the stated design.", "- It counts every MCI-to-dementia step and can continue after a first dementia diagnosis, rather than fitting a first-progression endpoint. The new analysis stops at the first observed dementia.", "- Its earliest usable diagnosis can be screening rather than enrolled baseline. Counts of ever-CN and ever-MCI overlap both because of incidence and reversion; their intersection is not automatically a count of converters.", "- It admits medical history up to one year after entry; this analysis requires pre-entry values. Its missing/unknown sex is turned into male. APOE parsing also lacks a validity guard, although every genotype in the supplied raw APOERES file is a valid 2/3/4 allele pair.", "- Its linear interval-length adjustment in a per-visit logistic regression is not the same estimator as a continuous-time hazard model. Suppressing warnings also conceals limitations of cluster covariance with frequency weights.", "- Its E-value is not a test for collider bias or a correction for it. A generic unmeasured-confounding bound cannot identify the disease-entry selection mechanism.", "", "## Interpretation limits", "", "1. ADNI is an enriched research cohort, not a random sample of the population at risk of MCI. Its CN/MCI proportions do not give population sampling or incident-MCI probabilities. The source target here is explicitly the pooled enrolled ADNI baseline sample.", "2. Weighting a handful of recorded covariates cannot remove index-event bias transmitted by unmeasured common causes, incorrect causal structure, exposure measurement error, or survivor selection. Smoking is a medical-history yes/no flag, not proven current smoking or a full lifetime dose. MH9ENDO is not type-2 diabetes; it is not analysed as that exposure. No claim that hearing impairment is absent from all ADNI data is made.", "3. Adjusted conditional HRs can remain similar after reweighting because the outcome regression already conditions on the same covariates. A small change does not establish that measured composition or collider bias is harmless, and a larger change would not prove that a published null was an artefact.", "4. Some covariates and genotypes are unavailable, and genotype assay availability can itself select participants. Genetic ancestry, vascular risk, site and all other selection causes are not comprehensively controlled. Phase grouping combines ADNIGO and ADNI2 to avoid a structurally all-MCI recruitment cell.", "5. Death is not completely ascertained by the downloaded tables. Death-competing-risk and informative dropout corrections are not estimated. Last-diagnosis censoring may be informative. Events are detected at visits, not their unobserved biological onset; the four-year analysis concerns recorded diagnosis.", "6. The outcome is recorded dementia, not the cognitive-score slopes or biomarker outcomes of P-934; APOE here is a main effect, not the sex-by-APOE interaction in P-984. None of the four original published nulls is claimed to have been reproduced, disproved or causally corrected.", "7. This is an ADNI-only sensitivity study. It does not close the causal-identification part of G-101 or substitute for independent NACC replication. No human gate is passed.", "", "Primary methodological context: [Dudbridge et al. 2019](https://doi.org/10.1038/s41467-019-09381-w) and [Mitchell et al. 2023](https://doi.org/10.1371/journal.pgen.1010596). Official definitions: [ADNI diagnosis](https://adni.loni.usc.edu/quick-start-guide-asset/diagnostic.html), [enrollment](https://adni.loni.usc.edu/quick-start-guide-asset/enrollment.html), [cohort design](https://adni.loni.usc.edu/data-samples/adni-data/study-cohort-information/).", "", "Only aggregate outputs are written; no participant identifiers or row-level data are exported.", ""]
    lines += [
        "## Figures", "",
        "![Naive and measured-selection weighted hazard ratios](G-101_independent_effects.png)", "",
        f"Figure 1. Adjusted hazard ratios for first recorded dementia within {result['exposures'][0]['horizon_years']:g} years of clinical baseline MCI. Intervals come from {result['exposures'][0]['bootstrap']['requested']:,} participant bootstrap samples that refit both stages. Both models adjust the same recorded covariates; a small change does not show that index-event bias is absent.", "",
        "![Balance of recorded characteristics before and after weighting](G-101_independent_balance.png)", "",
        "Figure 2. Recorded baseline characteristics relative to each exposure-specific pooled enrolled ADNI CN+MCI reference. Weighting improves measured balance. Unmeasured common causes and research-cohort selection remain outside this calculation.", "",
    ]
    (OUT / "G-101_independent_report.md").write_text("\n".join(lines), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    for i, r in enumerate(compact):
        for shift, key, lo, hi, color, label in ((0.12, "naive_hr", "naive_ci_low", "naive_ci_high", "#2466ad", "Naive adjusted"), (-0.12, "weighted_hr", "weighted_ci_low", "weighted_ci_high", "#d96b25", "Measured-selection weighted")):
            ax.errorbar(r[key], i + shift, xerr=[[r[key] - r[lo]], [r[hi] - r[key]]], fmt="o", color=color, capsize=3, label=label if i == 0 else None)
    ax.axvline(1, color="#777", linestyle="--", linewidth=1)
    ax.set_yticks(range(len(compact)), [r["exposure"] for r in compact])
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 3], ["1", "2", "3"])
    ax.minorticks_off()
    ax.set_xlabel("First recorded dementia hazard ratio; participant-bootstrap 95% interval")
    ax.set_title("ADNI baseline MCI: measured-selection sensitivity", loc="left")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.01, 0.01, f"v{VERSION} | {result['built_at_utc']} | Not an identified correction for collider bias", fontsize=7)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(OUT / ("G-101_independent_effects." + ext), dpi=250)
    plt.close(fig)
    fig, axes = plt.subplots(1, len(result["exposures"]), figsize=(10, 4.8), sharex=True)
    for ax, r in zip(np.atleast_1d(axes), result["exposures"]):
        rows = [b for b in r["weighted"]["winsor_01_99"]["balance"] if not (b["covariate"].startswith("phase_") and b["target_mean"] == 0)]
        yy = np.arange(len(rows))
        ax.scatter([b["smd_before"] for b in rows], yy + 0.08, color="#2466ad", label="Before weighting")
        ax.scatter([b["smd_after"] for b in rows], yy - 0.08, color="#d96b25", label="After weighting")
        ax.axvline(0, color="#888", linewidth=0.7)
        for threshold in (-0.1, 0.1):
            ax.axvline(threshold, color="#aaa", linewidth=0.7, linestyle="--")
        display = {"e4": "APOE e4", "smoke": "Smoking history", "female": "Recorded female", "education": "Education", "age": "Age", "phase_ADNI1": "ADNI1", "phase_GO2": "ADNIGO + ADNI2", "phase_ADNI3": "ADNI3", "phase_ADNI4": "ADNI4"}
        ax.set_yticks(yy, [display.get(b["covariate"], b["covariate"]) for b in rows])
        ax.set_title(r["label"])
        ax.set_xlabel("Standardized mean difference")
        ax.spines[["top", "right"]].set_visible(False)
    np.atleast_1d(axes)[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Observed entry characteristics: balance does not identify all selection bias", fontsize=10)
    fig.text(0.01, 0.005, f"v{VERSION} | {result['built_at_utc']} | Unmeasured common causes remain unadjusted", fontsize=7)
    fig.tight_layout(rect=(0, 0.06, 1, 0.94))
    for ext in ("png", "pdf"):
        fig.savefig(OUT / ("G-101_independent_balance." + ext), dpi=250)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--horizon", type=float, default=4.0)
    args = parser.parse_args()
    if args.bootstrap < 40 or args.horizon <= 0:
        raise ValueError("Use at least 40 bootstrap replicates and a positive horizon")
    tables, provenance = read_sources()
    dx, baseline, audit = prepare(tables)
    print("Strict registered baseline groups:", audit["registered_baseline_counts"], flush=True)
    results = []
    for i, (exposure, label) in enumerate((("e4", "APOE e4 carrier"), ("smoke", "Smoking-history flag"))):
        print("Starting", label, flush=True)
        r = analyse_exposure(baseline, exposure, label, args.bootstrap, args.seed + i, args.horizon)
        results.append(r)
        print(f"{label}: {r['prevalent_mci_n']} baseline MCI, {r['first_dementia_events_within_horizon']} first dementia events; naive HR {r['naive_hr']:.3f}, weighted HR {r['weighted']['winsor_01_99']['hr']:.3f}", flush=True)
    result = {"version": VERSION, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scope": "ADNI only; aggregate outputs; descriptive measured-selection sensitivity, not an identified causal correction", "inputs": provenance, "cohort_audit": audit, "incident_mci_feasibility": incident_summary(dx, baseline), "exposures": results}
    write_outputs(result)
    print("Wrote aggregate results to", OUT.relative_to(ROOT), flush=True)


if __name__ == "__main__":
    main()
