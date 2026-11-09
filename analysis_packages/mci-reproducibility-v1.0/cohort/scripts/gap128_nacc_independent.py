"""Independent NACC G-128 observation-schedule sensitivity experiment.

Reads the source-audited NACC clinical series and reuses the numerically checked
ADNI panel-likelihood/observed-event helpers. Only aggregate results are written.
No participant records, manuscripts, ledgers or human gates are changed.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import gap128_independent as core

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "nacc_independent_2026-11-03" / "G-128"
VERSION = "1.0"
SCHEDULES = core.SCHEDULES
HORIZONS = core.HORIZONS


def build_cohorts(visits, baseline, max_years=8.0):
    """Keep the explicit initial diagnosis and source people under every mask."""
    cohorts = {"baseline_mci": {}, "baseline_cn": {}}
    centers = {"baseline_mci": [], "baseline_cn": []}
    groups = dict(tuple(visits.groupby("NACCID", sort=False)))
    audit = {"baseline_people_by_state": {}, "previous_dementia_excluded": 0}
    for state, n in baseline.baseline_state.value_counts().items():
        audit["baseline_people_by_state"][str(int(state))] = int(n)
    for row in baseline.itertuples():
        state = int(row.baseline_state)
        if state not in (1, 2):
            continue
        subject = groups[row.NACCID]
        if subject.loc[subject.days.lt(row.baseline_days), "state"].eq(3).any():
            audit["previous_dementia_excluded"] += 1
            continue
        t = (subject.days.to_numpy(float) - float(row.baseline_days)) / 365.25
        selected = (t >= 0) & (t <= max_years)
        t, states = t[selected], subject.state.to_numpy(int)[selected]
        order = np.argsort(t, kind="stable")
        t, states = t[order], states[order]
        if not len(t) or t[0] != 0 or states[0] != state:
            raise AssertionError("Every trajectory must retain its true initial diagnosis at zero")
        if len(t) > 1 and np.any(np.diff(t) <= 0):
            raise AssertionError("Source-audited diagnosis times must be strictly increasing")
        name = "baseline_mci" if state == 2 else "baseline_cn"
        cohorts[name][row.NACCID] = (t, states)
        centers[name].append(str(row.NACCADC))
    for name, cohort in cohorts.items():
        gaps = [np.diff(t) * 365.25 for t, s in cohort.values() if len(t) > 1]
        all_gaps = np.concatenate(gaps) if gaps else np.array([])
        audit[name] = {
            "source_n": len(cohort), "with_followup_n": len(gaps),
            "baseline_only_n": len(cohort) - len(gaps),
            "all_observed_gaps_at_most_400_days_n": sum(np.max(g) <= 400 + 1e-8 for g in gaps),
            "all_observed_gaps_at_most_548_days_n": sum(np.max(g) <= 548 + 1e-8 for g in gaps),
            "all_gaps_within_400_days_and_followup_at_least_4y_n": sum(
                len(t) > 1 and t[-1] >= 4 and np.max(np.diff(t)*365.25) <= 400 + 1e-8
                for t, s in cohort.values()),
            "visit_gap_median_days": float(np.median(all_gaps)) if len(all_gaps) else None,
            "visit_gap_q25_days": float(np.quantile(all_gaps, .25)) if len(all_gaps) else None,
            "visit_gap_q75_days": float(np.quantile(all_gaps, .75)) if len(all_gaps) else None,
            "centers_n": len(set(centers[name])),
        }
    audit["pooled_baseline_centers_n"] = len(set(centers["baseline_mci"] + centers["baseline_cn"]))
    audit["dementia_then_later_non_dementia_people_n"] = sum(
        bool(len(hit := np.flatnonzero(s == 3))) and np.any(s[hit[0]+1:] != 3)
        for c in cohorts.values() for t, s in c.values())
    return cohorts, centers, audit


def prepare_masks(cohorts, preserve_terminal=True):
    mci = list(cohorts["baseline_mci"].values())
    cn = list(cohorts["baseline_cn"].values())
    reference = core.cn_path_flags(cn)[:, 0].astype(bool)
    points, preparations, starts = {}, {}, {}
    for name, period in SCHEDULES.items():
        cm = [core.mask_visits(t, s, period, preserve_terminal=preserve_terminal) for t, s in mci]
        cc = [core.mask_visits(t, s, period, preserve_terminal=preserve_terminal) for t, s in cn]
        panel = core.panel_arrays(cm + cc)
        model, theta = core.fit_ctmc(panel, multi_start=True)
        if not model["success"]:
            raise RuntimeError(f"NACC point model did not converge: {name}")
        records = core.observed_first_events(cm, 2)
        observed, curve = core.cumulative_detection(records, HORIZONS)
        flags = core.cn_path_flags(cc)
        followup = {}
        for label, series in (("baseline_mci", cm), ("baseline_cn", cc)):
            last = np.array([t[-1] for t, s in series])
            followup[label] = {
                "source_people_n": len(series), "with_retained_followup_n": int(np.sum(last > 0)),
                "baseline_only_n": int(np.sum(last == 0)),
                "median_last_retained_year_all": float(np.median(last)),
                "median_last_retained_year_followed": float(np.median(last[last > 0])),
                "last_retained_at_least_four_years_n": int(np.sum(last >= 4)),
            }
        points[name] = {
            **model, **observed, **core.direct_metrics(flags, reference),
            "source_cohort_mci_n": len(mci), "source_cohort_cn_n": len(cn),
            "followup": followup,
            "retained_diagnosis_records": sum(len(t) for t, s in cm + cc),
            "panel_intervals": len(panel[1]),
        }
        preparations[name] = (panel, records, flags, curve)
        starts[name] = theta
    return points, preparations, starts, reference


def metric_names():
    names = [f"occupation_{state}_from_mci_{int(h)}y" for state in ("cn", "dementia") for h in HORIZONS]
    names += [f"observed_{kind}_{h:g}y" for kind in ("reversion_cif", "dementia_first_cif") for h in HORIZONS]
    names += ["mci_dementia_intensity_per_100py", "direct_share_among_detected_pct",
              "observed_direct_fixed_converter_pct", "missed_full_series_converter_pct"]
    return names


def paired_bootstrap(points, preparations, starts, reference, centers, reps, seed, unit="center"):
    metrics = metric_names()
    draws = {s: {m: [] for m in metrics} for s in SCHEDULES}
    failures = {s: 0 for s in SCHEDULES}
    nm, nc = len(centers["baseline_mci"]), len(centers["baseline_cn"])
    labels, group = np.unique(np.array(centers["baseline_mci"] + centers["baseline_cn"]), return_inverse=True)
    rng = np.random.default_rng(seed)
    begin = time.monotonic()
    for b in range(reps):
        if unit == "center":
            multiplicity = rng.multinomial(len(labels), np.ones(len(labels)) / len(labels))
            weights = multiplicity[group]
            wm, wc = weights[:nm], weights[nm:]
        else:
            wm = rng.multinomial(nm, np.ones(nm) / nm)
            wc = rng.multinomial(nc, np.ones(nc) / nc)
            weights = np.r_[wm, wc]
        for name in SCHEDULES:
            panel, record, flags, curve = preparations[name]
            model, _ = core.fit_ctmc(panel, weights, starts[name])
            if not model["success"]:
                failures[name] += 1
            observed, _ = core.cumulative_detection(record, HORIZONS, wm)
            direct = core.direct_metrics(flags, reference, wc)
            values = {**model, **observed, **direct}
            for metric in metrics:
                value = values.get(metric)
                if (metric.startswith("occupation_") or metric.endswith("intensity_per_100py")) and not model["success"]:
                    value = None
                draws[name][metric].append(value)
        if (b + 1) % 25 == 0:
            elapsed = time.monotonic() - begin
            print(f"{unit} paired bootstrap {b+1}/{reps}: {elapsed:.1f}s", flush=True)
    estimates, differences = [], []
    for name in SCHEDULES:
        for metric in metrics:
            good = [v for v in draws[name][metric] if v is not None and np.isfinite(v)]
            lo, hi = np.quantile(good, [.025, .975]) if good else (None, None)
            estimates.append({"schedule": name, "metric": metric, "estimate": points[name][metric],
                              "ci_low": None if lo is None else float(lo), "ci_high": None if hi is None else float(hi),
                              "valid_bootstrap_replicates": len(good)})
            if name != "full":
                paired = [v-u for u, v in zip(draws["full"][metric], draws[name][metric])
                          if u is not None and v is not None and np.isfinite(u) and np.isfinite(v)]
                lo, hi = np.quantile(paired, [.025, .975]) if paired else (None, None)
                differences.append({"schedule": name, "reference": "full", "metric": metric,
                                    "difference_mask_minus_full": points[name][metric] - points["full"][metric],
                                    "ci_low": None if lo is None else float(lo), "ci_high": None if hi is None else float(hi),
                                    "valid_paired_replicates": len(paired)})
    return {"unit": unit, "requested": reps, "seed": seed, "fit_failures": failures,
            "centers_n": len(labels), "seconds": round(time.monotonic()-begin, 2),
            "estimates": estimates, "paired_differences": differences}


def json_default(value):
    if isinstance(value, (np.integer, np.bool_)):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    raise TypeError(type(value).__name__)


def save_outputs(result, preparations, destination):
    destination.mkdir(parents=True, exist_ok=True)
    stamp = result["built_at_utc"]
    (destination / "G-128_NACC_independent_results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False, default=json_default) + "\n", encoding="utf-8")
    for filename, records in (
            ("G-128_NACC_estimates.csv", result["bootstrap"]["estimates"]),
            ("G-128_NACC_paired_differences.csv", result["bootstrap"]["paired_differences"]),
            ("G-128_NACC_grid_sensitivity.csv", result["grid_sensitivity"])):
        enriched = [dict(row, version=VERSION, built_at_utc=stamp) for row in records]
        core.write_csv(destination / filename, enriched)
    points = result["point"]
    palette = {"full": "#2a78d6", "two_year_mask": "#e7792c", "four_year_mask": "#149b75"}
    labels = {"full": "Full recorded series", "two_year_mask": "2-year grid + final visit", "four_year_mask": "4-year grid + final visit"}
    index = {(x["schedule"], x["metric"]): x for x in result["bootstrap"]["estimates"]}
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
    for name in SCHEDULES:
        curve = preparations[name][3]
        axes[0].step(curve[:, 0], curve[:, 1] * 100, where="post", color=palette[name], label=labels[name])
        theta = np.log(list(points[name]["rates_per_year"].values()))
        grid = np.linspace(0, 8, 161)
        axes[1].plot(grid, core.transition_probabilities(theta, grid)[:, 1, 0] * 100, color=palette[name])
        for ax, prefix in ((axes[0], "observed_reversion_cif_"), (axes[1], "occupation_cn_from_mci_")):
            for h in HORIZONS:
                x = index[(name, f"{prefix}{int(h)}y")]
                y = 100 * x["estimate"]
                ax.errorbar(h, y, yerr=[[max(0, y - 100*x["ci_low"])], [max(0, 100*x["ci_high"] - y)]],
                            color=palette[name], fmt="o", markersize=3, capsize=2, alpha=.65)
    axes[0].set(title="First recorded reversion before dementia", xlabel="Years from initial UDS visit", ylabel="Cumulative detected reversion (%)", xlim=(0, 8))
    axes[0].legend(fontsize=7, frameon=False)
    axes[1].set(title="Model probability of being CN", xlabel="Years from initial UDS visit", ylabel="P(CN at time t | baseline MCI) (%)", xlim=(0, 8))
    vals = [points[n]["direct_share_among_detected_pct"] for n in SCHEDULES]
    axes[2].bar(range(3), vals, color=list(palette.values()))
    axes[2].set(xticks=range(3), xticklabels=["Full", "2-y mask", "4-y mask"],
                ylabel="No recorded MCI before dementia (%)", title="Apparent direct paths within 8 years")
    for i, name in enumerate(SCHEDULES):
        x = index[(name, "direct_share_among_detected_pct")]
        axes[2].errorbar(i, vals[i], yerr=[[max(0, vals[i]-x["ci_low"])], [max(0, x["ci_high"]-vals[i])]],
                        fmt="none", color="#333", capsize=4)
        axes[2].text(i, vals[i]+1, f"{points[name]['observed_direct_n']:.0f}/{points[name]['detected_dementia_n']:.0f}", ha="center", fontsize=8)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle("G-128: independent NACC visit-masking experiment", fontsize=12, fontweight="bold")
    fig.text(.01, .01, f"v{VERSION} | {stamp} | Center-bootstrap 95% intervals. Same source people and final visit retained; observed paths are not biological trajectories.", fontsize=7)
    fig.tight_layout(rect=(0, .05, 1, .92))
    for ext in ("png", "pdf"):
        fig.savefig(destination / f"G-128_NACC_independent.{ext}", dpi=300)
    plt.close(fig)
    write_report(result, destination)


def write_report(result, destination):
    points, cohort = result["point"], result["cohort_audit"]
    report = ["# G-128: independent NACC visit-schedule experiment", "",
              f"Version {VERSION} | Built {result['built_at_utc']} | Candidate, pending human review", "",
              "This is an executed NACC replication of the corrected ADNI analysis. The original NACC script and its results are preserved. No ADNI outputs, manuscripts, research ledgers or gates are modified.", "",
              "## Source and cohort definition", "",
              "The September 2026 Quick-Access UDS file is read to its end using only required columns. Explicit initial visits (NACCVNUM=1) define baseline; the first usable later diagnosis is not substituted. Baseline age is 50–120 years, the prespecified late-life target, with the official valid-age upper bound retained. All packets carrying a usable clinical diagnosis are eligible, including telephone observations. These are not uniformly in-person neuropsychological batteries. A separate standard-packet sensitivity excludes legacy IT/T packets and conditions on a standard initial visit.", "",
              "NACCUDSD 1/3/4 map to CN/MCI/clinical dementia (analysis codes 1/2/3). Code 2 is impaired-not-MCI and code 8 is behavioral impairment without cognitive impairment; both are outside this three-state analysis. They are not assigned MCI or treated as negative tests. The official derived NACCFDYS measures days from the initial visit; the model uses NACCFDYS/365.25. Same-day conflicting diagnoses are removed rather than selecting the worse state. Baseline labels are preserved before exact duplicate collapse. [NACC UDS dictionary, April 2026](https://files.alz.washington.edu/documentation/uds4-rdd.pdf).", "",
              f"- Baseline MCI: {cohort['baseline_mci']['source_n']:,}; with later usable diagnosis within eight years: {cohort['baseline_mci']['with_followup_n']:,}.",
              f"- Baseline CN: {cohort['baseline_cn']['source_n']:,}; with later usable diagnosis within eight years: {cohort['baseline_cn']['with_followup_n']:,}.",
              f"- Baseline centers represented: {cohort['pooled_baseline_centers_n']}.",
              f"- MCI participants whose observed follow-up gaps are all within 400 days: {cohort['baseline_mci']['all_observed_gaps_at_most_400_days_n']:,}; of these, {cohort['baseline_mci']['all_gaps_within_400_days_and_followup_at_least_4y_n']:,} have at least four observed years. These are coverage descriptions, not an additional registered eligibility requirement. A median annual gap is not complete annual observation for everyone.", "",
              "## What the schedule comparison means", "",
              "The main two/four-year masks select actual visits nearest baseline-anchored grid points within six months, and retain each person's last diagnosis inside the eight-year source window. Thus the same source people and final observation are retained while intermediate observations are hidden. This is a masking sensitivity, not an identified intervention on clinic scheduling. The supplemental grid-only version does not force the final observation and reports the resulting loss of follow-up and dementia detection.", "",
              "A continuous-time homogeneous Markov panel likelihood exp(Q*elapsed) estimates CN↔MCI and CN/MCI→dementia rates jointly from both initial groups. Each masked sequence is truncated at its own first detected dementia; a dementia observation hidden by a mask is not carried into that series. Dementia is absorbing for this first-diagnosis endpoint even if a later source record reverses. First recorded CN before dementia is also estimated as a competing-event cumulative detection curve at visit dates. This differs from the model probability of currently being CN. Neither estimates the unobserved biological time of change. [Jackson 2011](https://www.jstatsoft.org/article/view/v038i08).", "",
              "## Main estimates", "",
              "All probabilities below start from baseline MCI. Direct-path counts instead concern the baseline-CN group over up to eight years.", "",
              "| Schedule | 4-y first recorded reversion | 8-y first recorded reversion | 4-y model CN occupation | 4-y model dementia occupation | MCI→dementia intensity /100 MCI person-years | Direct / detected dementia by 8y | Direct / fixed full-series converters | Missed converters |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, x in points.items():
        report.append(f"| {name} | {100*x['observed_reversion_cif_4y']:.2f}% | {100*x['observed_reversion_cif_8y']:.2f}% | {100*x['occupation_cn_from_mci_4y']:.2f}% | {100*x['occupation_dementia_from_mci_4y']:.2f}% | {x['mci_dementia_intensity_per_100py']:.3f} | {x['observed_direct_n']:.0f}/{x['detected_dementia_n']:.0f} | {x['observed_direct_n']:.0f}/{x['full_series_converter_reference_n']:.0f} | {x['missed_full_series_converters_n']:.0f} |")
    report += ["", "## Paired changes relative to all recorded visits", "",
               "Probability differences are percentage points below; the exported CSV retains probability fractions. Direct-share differences already use percentage points. Intensity changes use transitions per 100 MCI person-years. Intervals are paired center-bootstrap percentiles, resampling baseline centers and retaining all their participants with matching weights across masks and baseline groups.", "",
               "| Mask | Quantity | Mask minus full | 95% interval | Valid paired fits |", "|---|---|---:|---|---:|"]
    highlight = {"observed_reversion_cif_4y", "observed_reversion_cif_8y", "occupation_cn_from_mci_4y", "occupation_dementia_from_mci_4y", "mci_dementia_intensity_per_100py", "observed_direct_fixed_converter_pct", "missed_full_series_converter_pct"}
    for x in result["bootstrap"]["paired_differences"]:
        if x["metric"] in highlight:
            factor = 100 if x["metric"].startswith(("observed_reversion", "occupation_")) else 1
            report.append(f"| {x['schedule']} | {x['metric']} | {factor*x['difference_mask_minus_full']:.3f} | {factor*x['ci_low']:.3f} to {factor*x['ci_high']:.3f} | {x['valid_paired_replicates']} |")
    report += ["", "## Grid-only and packet sensitivities", "",
               "Grid-only thinning retains the same baseline source people, but many become baseline-only. It therefore combines masking of intermediate diagnoses with reduced observed follow-up. These point estimates have no additional bootstrap.", "",
               "| Grid-only schedule | MCI with follow-up / source | 4-y first recorded reversion | Direct / detected dementia | Missed original converters |", "|---|---:|---:|---:|---:|"]
    for name, x in result["literal_grid_point"].items():
        follow = x["followup"]["baseline_mci"]
        report.append(f"| {name} | {follow['with_retained_followup_n']:,}/{follow['source_people_n']:,} | {100*x['observed_reversion_cif_4y']:.2f}% | {x['observed_direct_n']:.0f}/{x['detected_dementia_n']:.0f} | {x['missed_full_series_converters_n']:.0f}/{x['full_series_converter_reference_n']:.0f} |")
    sensitivity = result["standard_packet_sensitivity"]
    report += ["", f"The standard-packet sensitivity has {sensitivity['cohort_audit']['baseline_mci']['source_n']:,} baseline-MCI and {sensitivity['cohort_audit']['baseline_cn']['source_n']:,} baseline-CN participants. Excluding legacy telephone packets can change source eligibility and diagnosis coverage; it is not a proven fully in-person sample because v4 integrates remote administration into standard packets. Its point results and audit are in JSON. The window/origin sensitivity is in G-128_NACC_grid_sensitivity.csv.", "",
               "## Audit of the earlier NACC implementation", "",
               "The earlier script measured follow-up from the earliest calendar date in the full download, instead of each participant's initial visit. It defined baseline after discarding unusable diagnoses, removed people without follow-up and then changed which people remained under each schedule. It imported a midpoint-imputed product-integral estimator with inconsistent risk-state handling, and divided MCI transitions by person-time across all states. Agreement between two implementations of that estimator is not evidence that the estimator is appropriate for panel-observed diagnoses. It also called IT in-person, although IT is a telephone initial packet, and labelled NACCUDSD=8 unknown. These source/design errors are corrected here; the old estimates are not reproduced as a valid target.", "",
               "## Limits and status", "",
               "This estimates how observed diagnosis paths and model summaries change when recorded information is masked. It does not establish biological bypass, latent onset, or the effect of a clinic policy. Unobserved changes can occur even in the full series. Recorded clinical dementia is not automatically biomarker-confirmed Alzheimer's disease. The first-observed-dementia endpoint does not describe all later reversals.",
               "The state model assumes time-homogeneous rates, a Markov process, adequate three-state classification and non-informative observation/censoring. NACC visit attendance, packet mix and retention can depend on impairment. Center bootstrap intervals reflect between-center dependence under that resampling scheme; they do not turn this selected research cohort into a population sample. No death-competing biological state or joint informative-visit process is fitted in this schedule experiment. Apparent reversion changes can depend on the time horizon and competing-event order; no universal direction is asserted. Center resampling does not correct these design limitations.",
               f"Primary requested center draws: {result['bootstrap']['requested']}; seed {result['bootstrap']['seed']}; failed model fits by schedule: {result['bootstrap']['fit_failures']}. Model failures are omitted only from paired model summaries; observed-event metrics retain their valid draws. Boundary rates are listed for each point model. The participant-bootstrap sensitivity, if requested, is separately retained in JSON and does not replace primary center intervals.",
               "No research gap is automatically declared closed and no human gate is passed. ADNI and NACC source analyses remain separate. Cross-database participant overlap has not been checked and cannot be excluded from these files, so separate databases do not establish independent people. No pooled cross-cohort significance test is made. A cross-cohort comparison must use the same definitions and state differing recruitment/observation settings.", "",
               "![NACC visit masking and apparent direct paths](G-128_NACC_independent.png)", "",
               "Figure. Left: first detected CN before dementia among baseline-MCI participants. Middle: model probability of being CN at time t given baseline MCI. Right: no recorded MCI before first recorded dementia among cases detected in baseline-CN participants within eight years; denominators are shown. Main masks retain original final observations. Whiskers are center-bootstrap 95% intervals.", ""]
    (destination / "G-128_NACC_independent.md").write_text("\n".join(report), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=2000)
    parser.add_argument("--participant-reps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    if args.reps < 40 or args.participant_reps < 0:
        raise ValueError("Use at least 40 primary replicates and nonnegative sensitivity replicates")
    from nacc_source_audit import load_clinical
    begin = time.monotonic()
    visits, baseline, source_audit, sources = load_clinical(min_age=50, include_telephone=True)
    cohorts, centers, cohort_audit = build_cohorts(visits, baseline)
    print(json.dumps({"cohort_audit": cohort_audit}, default=json_default), flush=True)
    points, preparations, starts, reference = prepare_masks(cohorts)
    print(json.dumps({"point": points}, default=json_default), flush=True)
    bootstrap = paired_bootstrap(points, preparations, starts, reference, centers, args.reps, args.seed)
    participant = None
    if args.participant_reps:
        participant = paired_bootstrap(points, preparations, starts, reference, centers,
                                       args.participant_reps, args.seed+1, unit="participant")
    literal, _, _, _ = prepare_masks(cohorts, preserve_terminal=False)
    standard_visits, standard_baseline, standard_source_audit, _ = load_clinical(min_age=50, include_telephone=False)
    standard, standard_centers, standard_cohort_audit = build_cohorts(standard_visits, standard_baseline)
    standard_point, _, _, _ = prepare_masks(standard)
    result = {
        "version": VERSION, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "NACC only; aggregate results; candidate pending human review", "pilot": args.pilot,
        "source_files": sources, "source_audit": source_audit, "cohort_audit": cohort_audit,
        "max_observation_years": 8, "baseline_age_range": [50, 120],
        "primary_packet_rule": "All usable clinical diagnosis packets, including telephone; explicit initial visit only",
        "mask_design": "Absolute 2/4-year grids +/-0.5 years; initial and last source diagnosis retained; not literal clinic policies",
        "point": points, "bootstrap": bootstrap, "participant_bootstrap_sensitivity": participant,
        "literal_grid_point": literal, "grid_sensitivity": core.phase_sensitivity(cohorts),
        "common_four_year_visit_sensitivity": core.endpoint_subset(cohorts),
        "standard_packet_sensitivity": {"source_audit": standard_source_audit, "cohort_audit": standard_cohort_audit,
                                        "point": standard_point},
        "total_seconds": round(time.monotonic()-begin, 2), "no_gap_closure_asserted": True,
    }
    destination = OUT / "pilot" if args.pilot else OUT
    save_outputs(result, preparations, destination)
    print(json.dumps({"finished": True, "total_seconds": result["total_seconds"],
                      "bootstrap_seconds": bootstrap["seconds"], "fit_failures": bootstrap["fit_failures"],
                      "output": destination.relative_to(ROOT).as_posix()}), flush=True)


if __name__ == "__main__":
    main()
