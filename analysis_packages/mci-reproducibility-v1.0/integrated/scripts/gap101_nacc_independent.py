"""Independent NACC G-101 measured-composition sensitivity (aggregate outputs).

The target is the pooled enrolled initial CN+MCI sample, not population incident
MCI or an identified causal correction. Baseline exposures are never filled from
future clinical visits. Existing ADNI and earlier NACC outputs are preserved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "nacc_independent_2026-11-03" / "G-101"
VERSION = "1.0"
CSV = ROOT / "downloads" / "nacc" / "investigator_nacc75.csv"

spec = importlib.util.spec_from_file_location("adni_selection_core", ROOT / "scripts" / "gap101_independent.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def center_logistic(x, y, center, frequency=None, start=None):
    """Frequency-weighted logistic with center intercepts, using Schur IRLS.

    The tiny numerical ridge is applied to nuisance/intercept coefficients and
    slope coefficients alike. It prevents absent resampled centers from making
    the Hessian singular; its value is negligible relative to sample information.
    """
    frequency = np.ones(len(y)) if frequency is None else np.asarray(frequency, float)
    groups = int(center.max()) + 1
    ridge = 1e-6
    if start is None:
        beta = np.zeros(x.shape[1])
        n = np.bincount(center, weights=frequency, minlength=groups)
        successes = np.bincount(center, weights=frequency*y, minlength=groups)
        alpha = np.log((successes + .5) / (n - successes + .5))
    else:
        beta, alpha = (np.asarray(v).copy() for v in start)

    def objective(b, a):
        eta = x@b + a[center]
        return np.dot(frequency, np.logaddexp(0, eta) - y*eta) + ridge*(b@b+a@a)/2

    for iteration in range(60):
        eta = x@beta + alpha[center]
        p = expit(eta)
        residual = frequency*(y-p)
        variance = frequency*p*(1-p)
        gb = x.T@residual - ridge*beta
        ga = np.bincount(center, weights=residual, minlength=groups) - ridge*alpha
        if max(np.max(np.abs(gb)), np.max(np.abs(ga))) < 1e-5:
            return beta, alpha, np.clip(p, 1e-10, 1-1e-10)
        haa = np.bincount(center, weights=variance, minlength=groups) + ridge
        hab = np.column_stack([np.bincount(center, weights=variance*x[:, j], minlength=groups) for j in range(x.shape[1])])
        hbb = x.T@(variance[:, None]*x) + ridge*np.eye(x.shape[1])
        schur = hbb - hab.T@(hab/haa[:, None])
        db = np.linalg.solve(schur, gb - hab.T@(ga/haa))
        da = (ga-hab@db)/haa
        old = objective(beta, alpha)
        step = 1.
        while step > 1e-8:
            nb, na = beta+step*db, alpha+step*da
            if objective(nb, na) <= old + 1e-8:
                beta, alpha = nb, na
                break
            step /= 2
        else:
            raise RuntimeError("Center-logistic line search failed")
    raise RuntimeError("Center-logistic did not converge")


def entry_weights(probability, selected, frequency, truncation):
    prevalence = np.average(selected, weights=frequency)
    raw = prevalence/probability[selected]
    selected_frequency = frequency[selected]
    if truncation is None:
        limits = [float(raw[selected_frequency > 0].min()), float(raw[selected_frequency > 0].max())]
    else:
        # Integer resampling frequency: exactly the participant quantiles in the
        # replicated empirical sample, not quantiles across repeated visits.
        sample = np.repeat(raw, selected_frequency.astype(int))
        limits = np.quantile(sample, truncation).tolist()
    weights = raw if truncation is None else np.clip(raw, *limits)
    weights = weights/np.average(weights, weights=selected_frequency)
    return weights, limits


def smd_rows(source, selected, weights, exposure):
    rows = []
    for field in [exposure, "age", "female", "education"]:
        full = source[field].to_numpy(float)
        cases = full[selected]
        def smd(w):
            average = np.average(cases, weights=w)
            vv = np.average((cases-average)**2, weights=w)
            denominator = np.sqrt((full.var()+vv)/2)
            return float((average-full.mean())/denominator) if denominator > 0 else 0.
        rows.append({"covariate": field, "target_mean": float(full.mean()),
                     "unweighted_mci_mean": float(cases.mean()),
                     "weighted_mci_mean": float(np.average(cases, weights=weights)),
                     "smd_before": smd(np.ones(len(cases))), "smd_after": smd(weights)})
    for label in ("center", "version_group"):
        for value in sorted(source[label].unique()):
            full = source[label].eq(value).to_numpy(float)
            cases = full[selected]
            avg = np.average(cases, weights=weights)
            def distance(mean, variance):
                den = np.sqrt((full.var()+variance)/2)
                return float((mean-full.mean())/den) if den > 0 else 0.
            rows.append({"covariate": label+"_category", "category_index": int(value),
                         "target_mean": float(full.mean()),
                         "unweighted_mci_mean": float(cases.mean()), "weighted_mci_mean": float(avg),
                         "smd_before": distance(cases.mean(), cases.var()),
                         "smd_after": distance(avg, np.average((cases-avg)**2, weights=weights))})
    return rows


def assemble(raw, clean_visits, baseline, source_audit):
    """Accept common audited sources; no row-level outputs are written."""
    b = baseline.copy()
    # Common source module uses the following documented normalized fields.
    b["S"] = b["state"].eq(2).astype(int)
    b = b[b.state.isin([1, 2])].copy()
    b["age"] = pd.to_numeric(b.NACCAGE, errors="coerce").where(lambda x: x.between(50, 120))
    b["female"] = pd.to_numeric(b.NACCSEX, errors="coerce").map({1: 0., 2: 1.})
    b["education"] = pd.to_numeric(b.EDUC, errors="coerce").where(lambda x: x.between(0, 36))
    genotype = pd.to_numeric(b.NACCAPOE, errors="coerce")
    b["e4"] = genotype.map({1: 0., 2: 1., 3: 0., 4: 1., 5: 1., 6: 0.})
    tobacco = pd.to_numeric(b.TOBAC100, errors="coerce")
    b["smoke"] = tobacco.where(tobacco.isin([0, 1]))
    b["age10"] = (b.age-75)/10
    b["age10_sq"] = b.age10**2
    b["educ4"] = (b.education-16)/4
    version = pd.to_numeric(b.FORMVER, errors="coerce")
    b["version_group"] = version.where(version.isin([1, 2, 3, 3.2, 4])).replace({3.2: 3})
    b["center"] = pd.to_numeric(b.NACCADC, errors="coerce").where(lambda x: x.between(100, 9999))
    b["telephone_initial"] = b.PACKET.eq("IT").astype(float)
    last = clean_visits.groupby("NACCID").days.max()
    first_dem = clean_visits.loc[clean_visits.state.eq(3)].groupby("NACCID").days.min()
    b["followup_years"] = b.NACCID.map(last)/365.25
    b["event_years"] = b.NACCID.map(first_dem)/365.25
    return b


def analyse(source_all, exposure, label, bootstrap, seed, horizon, mode="centers"):
    needed = [exposure, "age10", "age10_sq", "female", "educ4", "center", "version_group"]
    source = source_all.dropna(subset=needed).copy()
    before_overlap = len(source)
    cells = source.groupby("center").S.agg(["sum", "count"])
    supported = cells.index[(cells["sum"] > 0) & (cells["sum"] < cells["count"])]
    source = source[source.center.isin(supported)].reset_index(drop=True)
    selected = source.S.to_numpy() == 1
    source["center"], centers = pd.factorize(source.center, sort=True)
    center = source.center.to_numpy(int)
    versions = sorted(source.version_group.unique())
    base_fields = [exposure, "age10", "age10_sq", "female", "educ4"]
    if source.telephone_initial.nunique() > 1:
        base_fields += ["telephone_initial"]
    x = source[base_fields].to_numpy(float)
    if len(versions) > 1:
        x = np.column_stack([x]+[source.version_group.eq(v).to_numpy(float) for v in versions[1:]])
    covariate_names = base_fields+["initial_UDS_version_"+str(v) for v in versions[1:]]
    frequency = np.ones(len(source))
    beta_s, alpha_s, probability = center_logistic(x, source.S.to_numpy(float), center)
    # New UDSv4 entry currently has no observed dementia events in the available
    # follow-up. A separate coefficient for that version has no finite MLE.
    # Stratifying by center AND initial version permits different baseline
    # hazards without estimating an unsupported version hazard ratio. Such
    # no-event strata contribute no partial-likelihood event information.
    xx = source[base_fields].to_numpy(float)[selected]
    version_codes = pd.Categorical(source.version_group, categories=versions).codes
    outcome_strata = center*len(versions)+version_codes
    duration = np.minimum(source.followup_years.to_numpy(float), horizon)
    event = (source.event_years.notna() & source.event_years.gt(0) & source.event_years.le(horizon)).to_numpy(int)
    duration = np.where(event == 1, source.event_years.to_numpy(float), duration)
    naive = core.cox_fit(xx, duration[selected], event[selected], outcome_strata[selected])
    weighted = {}
    primary_weights = None
    for method, limits in {"untrimmed": None, "winsor_01_99": (.01, .99), "winsor_05_95": (.05, .95)}.items():
        w, cutoffs = entry_weights(probability, selected, frequency, limits)
        betaw = core.cox_fit(xx, duration[selected], event[selected], outcome_strata[selected], w, naive)
        weighted[method] = {"hr": float(np.exp(betaw[0])), "log_hr": float(betaw[0]),
                            "hr_ratio_vs_naive": float(np.exp(betaw[0]-naive[0])),
                            "percent_hr_change": float(100*np.expm1(betaw[0]-naive[0])),
                            "effective_sample_size": float(w.sum()**2/(w@w)),
                            "raw_winsor_limits": cutoffs,
                            "normalized_weight_quantiles": np.quantile(w, [0, .01, .5, .99, 1]).tolist(),
                            "balance": smd_rows(source, selected, w, exposure)}
        if method == "winsor_01_99":
            primary_weights = w
            primary_beta = betaw
    from statsmodels.duration.hazard_regression import PHReg
    ref = PHReg(duration[selected], xx, status=event[selected], strata=outcome_strata[selected], ties="breslow").fit(disp=0).params
    difference = float(np.max(np.abs(ref-naive)))
    assert difference < 1e-4, difference
    # Check efficient center-intercept selection likelihood against an independent
    # dense statsmodels logistic at the actual point estimate.
    from scipy.optimize import minimize
    z = np.column_stack([x, np.eye(len(centers))[center]])
    starting = np.r_[beta_s, alpha_s]
    yy = source.S.to_numpy(float)
    def ref_obj(b):
        eta = z@b
        return np.sum(np.logaddexp(0, eta)-yy*eta)+1e-6*(b@b)/2, z.T@(expit(eta)-yy)+1e-6*b
    ref_logit = minimize(ref_obj, starting, jac=True, method="BFGS", options={"gtol": 1e-6, "maxiter": 100})
    selection_difference = float(np.max(np.abs(ref_logit.x-starting)))
    assert selection_difference < 1e-4, selection_difference
    del z

    rng = np.random.default_rng(seed)
    draws = []
    failures = 0
    start = time.monotonic()
    group_indices = [np.flatnonzero(center == j) for j in range(len(centers))]
    for rep in range(bootstrap):
        if mode == "centers":
            multiplicity = rng.multinomial(len(centers), np.ones(len(centers))/len(centers))
            freq = multiplicity[center].astype(float)
        else:
            freq = np.zeros(len(source))
            for idx in group_indices:
                freq[idx] = rng.multinomial(len(idx), np.ones(len(idx))/len(idx))
        try:
            bs, als, ps = center_logistic(x, yy, center, freq, (beta_s, alpha_s))
            bn = core.cox_fit(xx, duration[selected], event[selected], outcome_strata[selected], freq[selected], naive)
            bw, _ = entry_weights(ps, selected, freq, (.01, .99))
            bc = core.cox_fit(xx, duration[selected], event[selected], outcome_strata[selected], freq[selected]*bw, primary_beta)
            draws.append([bs[0], bn[0], bc[0], bc[0]-bn[0]])
        except (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError):
            failures += 1
        if (rep+1) % 25 == 0:
            print(f"{exposure}: {mode} bootstrap {rep+1}/{bootstrap}, {len(draws)} successful, {time.monotonic()-start:.1f}s", flush=True)
    if len(draws) < max(20, .8*bootstrap):
        raise RuntimeError("Too few successful full-stage bootstrap draws")
    arr = np.asarray(draws)
    ci = np.quantile(arr, [.025, .975], axis=0)
    weighted["winsor_01_99"].update(bootstrap_ci=np.exp(ci[:, 2]).tolist(),
                                    paired_log_hr_difference_ci=ci[:, 3].tolist(),
                                    hr_ratio_vs_naive_ci=np.exp(ci[:, 3]).tolist())
    return {"exposure": exposure, "label": label, "source_n": len(source),
            "source_cn_n": int((~selected).sum()), "baseline_mci_n": int(selected.sum()),
            "mci_positive_followup_n": int((source.loc[selected, "followup_years"] > 0).sum()),
            "zero_followup_censored_n": int((source.loc[selected, "followup_years"] <= 0).sum()),
            "dementia_events": int(event[selected].sum()),
            "observed_risk_person_years": float(duration[selected].sum()),
            "exposed_source_n": int(source[exposure].sum()), "exposed_mci_n": int(source.loc[selected, exposure].sum()),
            "overlap_excluded_people_n": before_overlap-len(source), "centers_n": len(centers),
            "initial_uds_versions": [float(v) for v in versions], "horizon_years": horizon,
            "selection_covariates": covariate_names+["center_fixed_intercepts"],
            "outcome_covariates": base_fields, "outcome_strata": "baseline ADRC crossed with initial UDS major version",
            "baseline_mci_version_counts_and_events": [{"initial_version":float(v),"people":int(np.sum(selected & source.version_group.eq(v).to_numpy())),"events":int(event[selected & source.version_group.eq(v).to_numpy()].sum()),"positive_followup_people":int(np.sum(selected & source.version_group.eq(v).to_numpy() & source.followup_years.gt(0).to_numpy()))} for v in versions],
            "baseline_mci_phone_counts": [{"mode":"telephone initial" if phone else "standard initial", "people":int(np.sum(selected & source.telephone_initial.eq(phone).to_numpy())), "positive_followup_people":int(np.sum(selected & source.telephone_initial.eq(phone).to_numpy() & source.followup_years.gt(0).to_numpy())), "events":int(event[selected & source.telephone_initial.eq(phone).to_numpy()].sum()), "event_centers":int(source.loc[selected & source.telephone_initial.eq(phone).to_numpy() & (event == 1),"center"].nunique())} for phone in (0,1)],
            "selection_membership_or": float(np.exp(beta_s[0])), "selection_bootstrap_ci": np.exp(ci[:, 0]).tolist(),
            "naive_hr": float(np.exp(naive[0])), "naive_bootstrap_ci": np.exp(ci[:, 1]).tolist(),
            "weighted": weighted,
            "selection_probability_quantiles_mci": np.quantile(probability[selected], [0, .01, .5, .99, 1]).tolist(),
            "cox_vs_statsmodels_max_abs_coefficient_difference": difference,
            "profile_logit_vs_dense_max_abs_coefficient_difference": selection_difference,
            "bootstrap": {"requested": bootstrap, "successful": len(draws), "failed": failures,
                          "seed": seed, "unit": mode, "centers_in_resampling_frame": len(centers),
                          "both_stages_refitted": True, "seconds": time.monotonic()-start}}


def save_outputs(result):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"G-101_NACC_independent_results.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    rows, balances = [], []
    for x in result["exposures"]:
        w = x["weighted"]["winsor_01_99"]
        rows.append({"exposure": x["label"], "source_cn_n": x["source_cn_n"], "baseline_mci_n": x["baseline_mci_n"],
                     "events": x["dementia_events"], "naive_hr": x["naive_hr"],
                     "naive_ci_low": x["naive_bootstrap_ci"][0], "naive_ci_high": x["naive_bootstrap_ci"][1],
                     "weighted_hr": w["hr"], "weighted_ci_low": w["bootstrap_ci"][0], "weighted_ci_high": w["bootstrap_ci"][1],
                     "paired_hr_ratio": w["hr_ratio_vs_naive"], "paired_ratio_ci_low": w["hr_ratio_vs_naive_ci"][0], "paired_ratio_ci_high": w["hr_ratio_vs_naive_ci"][1],
                     "ess": w["effective_sample_size"], "version": VERSION, "built_at_utc": result["built_at_utc"]})
        balances.extend({"exposure": x["label"], **v} for v in w["balance"])
    pd.DataFrame(rows).to_csv(OUT/"G-101_NACC_estimates.csv", index=False)
    pd.DataFrame(balances).to_csv(OUT/"G-101_NACC_balance.csv", index=False)
    lines = ["# G-101: independent NACC measured-composition sensitivity", "",
             f"Version {VERSION} | Built {result['built_at_utc']} | Candidate, pending human review", "",
             "This experiment standardizes measured characteristics within the enrolled NACC CN+MCI sample. It does not identify a causal correction for index-event bias or estimate population MCI incidence. Only aggregate outputs are saved. Earlier ADNI and NACC analyses remain intact.", "",
             "## Definition", "",
             "Baseline is the common audited initial visit at NACCVNUM=1 and NACCFDYS=0, with baseline age 50–120. Clinical diagnosis codes are harmonized from NACC 1=normal,3=MCI,4=dementia; impaired-not-MCI and behavioral-only impairment are not recoded as MCI. All valid clinician-diagnosis packets, including telephone initial/follow-up packets, enter the primary stream. This preserves the most complete recorded diagnosis series; it is not a series of uniform in-person batteries.", "",
             "Only baseline-MCI people enter the progression model. The endpoint is first recorded dementia during four years, regardless of an intermediate CN diagnosis, with censoring at the last known primary-stream diagnosis or four years. A person contributes at most one event; baseline-only participants are censored at zero. Clinical exposures/covariates come from the initial row, never the first later nonmissing row. APOE is a derived genotype, and its availability can itself select participants.", "",
             "Selection models use the exposure, age and age squared, recorded sex, years of education, initial UDS major version, initial telephone-packet status if it varies, and center intercepts. Outcome models adjust the exposure, age terms, sex, education and initial telephone status, and stratify baseline hazards jointly by initial ADRC and initial UDS major version. This conditions on version without trying to estimate a UDSv4 hazard ratio when its entrants currently have no dementia events. No-event strata contribute no event information to the partial likelihood. UDS 3.2 is grouped with version 3. Center cells lacking either CN or MCI are removed from that exposure's target because of empirical positivity failure. NACC's enrolled diagnostic proportions are not population probabilities.", "",
             "Weights use the enrolled MCI fraction divided by the fitted MCI-entry probability. The primary weights are winsorized at participant-level 1st/99th percentiles and normalized to mean one. Untrimmed and 5th/95th sensitivities are retained. The primary bootstrap resamples whole centers, refits selection, weight cutoffs and both outcome models, and calculates the paired log-HR difference within each draw. It allows dependence within centers; centers are still a selected research sample, not a random population frame.", "",
             "## Results", "",
             "| Exposure | CN reference | Baseline MCI | Dementia events | Ordinary adjusted HR (95% interval) | Weighted HR (95% interval) | Weighted/ordinary HR (paired 95% interval) |", "|---|---:|---:|---:|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['exposure']} | {r['source_cn_n']} | {r['baseline_mci_n']} | {r['events']} | {r['naive_hr']:.3f} ({r['naive_ci_low']:.3f}–{r['naive_ci_high']:.3f}) | {r['weighted_hr']:.3f} ({r['weighted_ci_low']:.3f}–{r['weighted_ci_high']:.3f}) | {r['paired_hr_ratio']:.3f} ({r['paired_ratio_ci_low']:.3f}–{r['paired_ratio_ci_high']:.3f}) |")
    lines += ["", "## Checks and limits", ""]
    for x in result["exposures"]:
        lines += [f"### {x['label']}", "", f"- {x['centers_n']} centers; {x['overlap_excluded_people_n']} people excluded for centers lacking both diagnostic groups after complete-case filtering.",
                  f"- MCI with positive follow-up: {x['mci_positive_followup_n']}; censored at baseline: {x['zero_followup_censored_n']}.",
                  f"- Bootstrap: {x['bootstrap']['successful']}/{x['bootstrap']['requested']} successful {x['bootstrap']['unit']} draws; seed {x['bootstrap']['seed']}.",
                  f"- Unweighted Cox agrees with independent statsmodels PHReg to {x['cox_vs_statsmodels_max_abs_coefficient_difference']:.3g} maximum coefficient difference. The center-profiled logistic solution agrees with a dense penalized optimizer to {x['profile_logit_vs_dense_max_abs_coefficient_difference']:.3g}.", "",
                  "| Weight rule | HR | Change from ordinary HR | ESS | Maximum absolute measured SMD |", "|---|---:|---:|---:|---:|"]
        for method, w in x["weighted"].items():
            lines.append(f"| {method} | {w['hr']:.3f} | {w['percent_hr_change']:.2f}% | {w['effective_sample_size']:.1f} | {max(abs(v['smd_after']) for v in w['balance']):.3f} |")
        lines += ["", "| Initial UDS major version | Baseline MCI | Positive follow-up | Dementia events within four years |", "|---|---:|---:|---:|"]
        for v in x["baseline_mci_version_counts_and_events"]:
            lines.append(f"| {v['initial_version']:g} | {v['people']} | {v['positive_followup_people']} | {v['events']} |")
        if "baseline_mci_phone_counts" in x:
            lines += ["", "| Initial packet mode | Baseline MCI | Positive follow-up | Dementia events within four years |", "|---|---:|---:|---:|"]
            for v in x["baseline_mci_phone_counts"]:
                lines.append(f"| {v['mode']} | {v['people']} | {v['positive_followup_people']} | {v['events']} |")
    lines += ["", "Measured balance is approximate, not exact. The full balance CSV includes center and initial-version categories as well as the four main fields shown in the figure. Maximum absolute standardized mean differences after primary weighting are " + "; ".join(f"{x['label']}: {max(abs(v['smd_after']) for v in x['weighted']['winsor_01_99']['balance']):.3f}" for x in result['exposures']) + ". Residual imbalance remains and is not evidence that all measured selection causes have been controlled.", "",
              "A hazard ratio is a modeled diagnosis rate ratio, not a four-year risk. The smoking exposure is TOBAC100, more than 100 cigarettes in life, not current smoking and not exactly ADNI's medical-history flag. Missing, unknown and structurally unavailable fields stay missing. Genotype availability and complete-case filtering may select participants.", "",
              "Reweighting the same measured covariates already adjusted in both conditional outcome models may leave coefficients similar. A small change is not evidence that collider bias is absent, and a large change would not establish that a published null was an artefact. Unmeasured common causes, diagnostic error, survivor selection, informative visits and dropout remain. Dementia is recorded at visits, not at unobserved biological onset. This first-diagnosis Cox analysis does not model death as a competing event or assume freedom from dementia after the last observed diagnosis.", "",
              "The original NACC script uses the flawed earlier ADNI per-visit logistic estimator, mixes incident/prevalent MCI, allows repeated dementia steps, fills entry exposures using later visits, calls IT in-person despite its telephone-initial definition, and resolves conflicting same-day diagnoses toward worse status. Its E-value and 'corrected' labels do not establish a causal correction. Those results are preserved but are not the primary estimates here.", "",
              "No original published null (including cognitive-score slopes or sex-by-APOE interaction) is claimed to be reproduced, corrected or overturned by these main-effect dementia models. G-101 causal identification remains open.", "",
              "The NACC and ADNI runs use separate databases; participant overlap across those databases was not assessed. They also use different covariate adjustment/stratification and bootstrap units. No formal between-database difference is estimated, and this is not evidence of a verified person-disjoint replication.", "",
              "Sources: [NACC UDS RDD, April 2026](https://files.alz.washington.edu/documentation/uds4-rdd.pdf), [NACC genetics RDD](https://files.alz.washington.edu/documentation/rdd-genetic-data.pdf), [Mitchell et al. 2023](https://doi.org/10.1371/journal.pgen.1010596).", "",
              "![NACC ordinary and measured-composition-weighted progression associations](G-101_NACC_effects.png)", "",
              "Figure 1. Adjusted hazard ratios for first recorded dementia within four years among baseline-MCI participants. Whiskers are 95% full-stage center-bootstrap intervals. Different exposure-specific complete-case source cohorts are used. These weights do not identify a general collider-bias correction.", "",
              "![Recorded baseline covariate balance](G-101_NACC_balance.png)", "",
              "Figure 2. Main baseline covariates before/after weighting relative to each exposure-specific pooled enrolled reference. Complete balance data, including center and UDS-version categories, are in CSV/JSON. Balance on recorded fields does not identify unmeasured selection causes.", ""]
    (OUT/"G-101_NACC_independent.md").write_text("\n".join(lines), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, r in enumerate(rows):
        for shift, prefix, color, label in [( .12,"naive","#2466ad","Ordinary adjusted"),(-.12,"weighted","#d96b25","Measured composition weighted")]:
            value = r[prefix+"_hr"]
            ax.errorbar(value, i+shift, xerr=[[value-r[prefix+"_ci_low"]],[r[prefix+"_ci_high"]-value]], fmt="o", color=color, capsize=3, label=label if i == 0 else None)
    ax.set_yticks(range(len(rows)), [r["exposure"] for r in rows])
    ax.axvline(1, color="#777", ls="--", lw=1)
    ax.set_xscale("log")
    ax.set_xticks([.9,1,1.2,1.4,1.6,1.8],["0.9","1","1.2","1.4","1.6","1.8"])
    ax.minorticks_off()
    ax.set_xlabel("First recorded dementia hazard ratio; center-bootstrap 95% interval")
    ax.set_title("NACC baseline MCI: measured-composition sensitivity", loc="left")
    ax.legend(frameon=False); ax.spines[["top","right"]].set_visible(False)
    fig.text(.01,.005,f"v{VERSION} | {result['built_at_utc']} | Four-year endpoint; not an identified collider correction",fontsize=7)
    fig.tight_layout(rect=(0,.06,1,1))
    for ext in ("png","pdf"): fig.savefig(OUT/("G-101_NACC_effects."+ext),dpi=250)
    plt.close(fig)
    fig, axes = plt.subplots(1,len(rows),figsize=(10,4),sharex=True)
    for ax,x in zip(np.atleast_1d(axes),result["exposures"]):
        b=x["weighted"]["winsor_01_99"]["balance"][:4]
        yy=np.arange(len(b))
        ax.scatter([v["smd_before"] for v in b],yy+.08,label="Before",color="#2466ad")
        ax.scatter([v["smd_after"] for v in b],yy-.08,label="After",color="#d96b25")
        display={"e4":"APOE e4 carrier","smoke":"Lifetime smoking","age":"Age","female":"Recorded female","education":"Education"}
        ax.set_yticks(yy,[display.get(v["covariate"],v["covariate"]) for v in b]);ax.axvline(0,color="#888",lw=.7)
        ax.set_title(x["label"]);ax.set_xlabel("Standardized mean difference");ax.spines[["top","right"]].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle("Measured covariate balance within the enrolled NACC reference")
    fig.text(.01,.005,f"v{VERSION} | {result['built_at_utc']} | Unmeasured causes remain outside these weights",fontsize=7)
    fig.tight_layout(rect=(0,.06,1,.94))
    for ext in ("png","pdf"):fig.savefig(OUT/("G-101_NACC_balance."+ext),dpi=250)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap",type=int,default=1000)
    parser.add_argument("--seed",type=int,default=20261103)
    parser.add_argument("--horizon",type=float,default=4)
    parser.add_argument("--mode",choices=["centers","participants_within_centers"],default="centers")
    args=parser.parse_args()
    if args.bootstrap < 20:raise ValueError("Use at least 20 bootstrap draws, with 1000 for production")
    # Shared source cleaning is supplied by nacc_source_audit.py.
    import nacc_source_audit as source
    visits, baseline, audit, provenance = source.load_clinical(min_age=50,include_telephone=True)
    b=assemble(None,visits,baseline,audit)
    result={"version":VERSION,"built_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope":"NACC only; aggregate measured-composition sensitivity; no causal closure or human gate", "source":provenance,"cohort_audit":audit,"exposures":[]}
    print("Baseline source",{"cn":int(b.S.eq(0).sum()),"mci":int(b.S.eq(1).sum())},flush=True)
    for i,(exposure,label) in enumerate([("e4","APOE e4 carrier"),("smoke","Ever >100 cigarettes (TOBAC100)")]):
        begin=time.monotonic()
        x=analyse(b,exposure,label,args.bootstrap,args.seed+i,args.horizon,args.mode)
        result["exposures"].append(x)
        print(json.dumps({"exposure":exposure,"mci_n":x['baseline_mci_n'],"events":x['dementia_events'],"naive_hr":x['naive_hr'],"weighted_hr":x['weighted']['winsor_01_99']['hr'],"seconds":time.monotonic()-begin}),flush=True)
    result['built_at_utc']=datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_outputs(result)
    print("Wrote aggregate NACC G-101 outputs",flush=True)


if __name__ == "__main__":
    main()
