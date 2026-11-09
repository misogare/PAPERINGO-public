"""Build the local aggregate NACC report and descriptive ADNI comparison."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "nacc_independent_2026-11-03"
ADNI = ROOT / "logs" / "adni_independent_2026-11-02"
VERSION = "1.0"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    value = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8*1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify(source, a128, n128, n101, numerical):
    expected = source["sources"][0]
    assert digest(ROOT / expected["path"]) == expected["sha256"]
    assert digest(ROOT / "downloads/nacc" / source["csf_inventory"]["file"]) == source["csf_inventory"]["sha256"]
    assert n128["source_files"] == n101["source"] == source["sources"]
    assert n128["source_audit"] == n101["cohort_audit"] == source["audit"]
    assert n128["cohort_audit"]["baseline_mci"]["source_n"] == source["audit"]["eligible_initial_state_counts"]["2"]
    assert n128["cohort_audit"]["baseline_cn"]["source_n"] == source["audit"]["eligible_initial_state_counts"]["1"]
    assert n128["bootstrap"]["requested"] == 2000 and not n128["pilot"]
    assert all(x["success"] for x in n128["point"].values())
    assert all(e["bootstrap"]["requested"] == e["bootstrap"]["successful"] == 1000 for e in n101["exposures"])
    assert numerical["all_checks_passed"]
    for item in numerical["analysis_scripts"] + numerical["reused_core_scripts"]:
        assert digest(ROOT / item["script"]) == item["sha256"], "Refresh numerical validation after code changes"
    for item in read(ADNI / "artifact_manifest.json")["artifacts"]:
        assert digest(ROOT / item["path"]) == item["sha256"], "Existing ADNI artifact changed"
    for db in (a128, n128):
        for point in db["point"].values():
            assert point["source_cohort_mci_n"] == db["point"]["full"]["source_cohort_mci_n"]
            assert point["source_cohort_cn_n"] == db["point"]["full"]["source_cohort_cn_n"]
            assert point["observed_direct_n"] <= point["detected_dementia_n"] <= point["full_series_converter_reference_n"]
    return {"source_hashes_and_cross_analysis_audits_match": True,
            "fixed_source_cohorts_and_converter_denominators_checked": True,
            "production_bootstrap_counts_verified": True,
            "numerical_checks_pass_and_match_current_scripts": True,
            "all_ADNI_artifact_hashes_preserved": True,
            "scope": "Numerical/provenance verification only; no human gate or scientific gap closure."}


def comparison_figure(a128, n128, stamp):
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.8))
    labels = {"full": "All recorded visits", "two_year_mask": "2-year grid + last visit", "four_year_mask": "4-year grid + last visit"}
    colors = ["#2a78d6", "#e7792c", "#149b75"]
    rows = []
    for j, (name, db) in enumerate((("ADNI", a128), ("NACC", n128))):
        estimates = db["estimates"] if name == "ADNI" else db["bootstrap"]["estimates"]
        index = {(x["schedule"], x["metric"]): x for x in estimates}
        for i, (schedule, point) in enumerate(db["point"].items()):
            x = j + (i-1)*.23
            for ax, metric, factor in ((axes[0], "observed_reversion_cif_4y", 100), (axes[1], "observed_direct_fixed_converter_pct", 1)):
                e = index[(schedule, metric)]
                y, lo, hi = (e[k]*factor for k in ("estimate", "ci_low", "ci_high"))
                ax.bar(x, y, .21, color=colors[i], label=labels[schedule] if j == 0 else None)
                ax.errorbar(x, y, yerr=[[max(0,y-lo)], [max(0,hi-y)]], fmt="none", capsize=3, color="#333")
            rows.append({"database": name, "schedule": schedule,
                         "baseline_mci_n": point["source_cohort_mci_n"],
                         "reversion_cif_4y_pct": 100*point["observed_reversion_cif_4y"],
                         "direct_n": point["observed_direct_n"],
                         "fixed_converter_n": point["full_series_converter_reference_n"],
                         "detected_dementia_n": point["detected_dementia_n"],
                         "missed_converter_n": point["missed_full_series_converters_n"]})
    for ax in axes:
        ax.set_xticks([0,1], ["ADNI", "NACC"])
        ax.spines[["top","right"]].set_visible(False)
        ax.grid(axis="y", alpha=.18); ax.set_axisbelow(True)
    axes[0].set(title="First recorded reversion by year four", ylabel="Competing-event detection estimate (%)")
    axes[1].set(title="No recorded MCI before dementia by year eight", ylabel="Share of original full-series converters (%)")
    axes[1].set_xticklabels(["ADNI: 21 original converters", "NACC: 845 original converters"], fontsize=9)
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(.5,.91), ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Visit masking in two clinical databases", fontsize=13, fontweight="bold")
    fig.text(.01,.015,f"v{VERSION} | {stamp} | 95% intervals: participant bootstrap in ADNI; center bootstrap in NACC. Descriptive comparison, no pooled inference.",fontsize=7)
    fig.tight_layout(rect=(0,.08,1,.84))
    for ext in ("png","pdf"):
        fig.savefig(OUT / ("ADNI_NACC_schedule_comparison."+ext), dpi=300)
    plt.close(fig)
    pd.DataFrame(rows).to_csv(OUT / "G-128_ADNI_NACC_comparison.csv", index=False)


def write_report(source, a128, a101, n128, n101, feasibility, stamp):
    audit = source["audit"]
    c = n128["cohort_audit"]
    changes = {(x["schedule"],x["metric"]):x for x in n128["bootstrap"]["paired_differences"]}
    rev = changes[("four_year_mask","observed_reversion_cif_4y")]
    direct = changes[("four_year_mask","observed_direct_fixed_converter_pct")]
    path = feasibility["baseline_cn_eight_year_dementia_path_audit"]
    incident = feasibility["first_recorded_mci_before_dementia_all_available_followup"]
    early = feasibility["first_recorded_mci_within_four_years_of_baseline"]
    lines = ["# NACC replication and comparison with ADNI", "",
             f"Version {VERSION} | Built {stamp} | September 2026 NACC freeze | Local research results, pending human review", "",
             "Both downloaded files are readable, and the NACC versions of G-128 and the G-101 measured-composition sensitivity have now been run. The clinical/genetics file supplies these experiments. The separate CSF file is not used. The earlier NACC scripts repeat errors from the first ADNI implementation, so the corrected runs are saved in this separate directory.", "",
             "## The files and the actual baseline sample", "",
             "| Item | Verified amount |", "|---|---:|",
             f"| Main UDS/neuropathology/genetics file | {audit['raw_rows_read_to_eof']:,} visit records; {audit['raw_people']:,} people; {audit['raw_centers']} centres |",
             f"| Separate CSF file | {source['csf_inventory']['rows']:,} records; {source['csf_inventory']['people']:,} people |",
             f"| Initial MCI, age 50-120 | {c['baseline_mci']['source_n']:,} people |",
             f"| Initial MCI with a later usable diagnosis within eight years | {c['baseline_mci']['with_followup_n']:,} people |",
             f"| Initial normal cognition, age 50-120 | {c['baseline_cn']['source_n']:,} people |", "",
             "The full projected CSV was parsed to its end; it was not truncated to a sample. Initial visit means NACCVNUM=1. The clock is NACCFDYS, elapsed days from that initial visit, and it agrees with differences in the supplied visit dates. We do not substitute a later usable diagnosis for an unusable initial diagnosis. The age threshold defines a late-life sample rather than the entire NACC population.", "",
             "The official coding distinguishes normal cognition, impaired-not-MCI, MCI, dementia, and behavioural impairment without cognitive impairment. The three-state models use normal/MCI/dementia; the other diagnoses are not converted into MCI. Telephone diagnoses are retained in the primary series. IT is a telephone initial packet, not an in-person packet; newer standard packets can also contain remotely administered forms. [NACC researcher guide](https://www.naccdata.org/the-nacc-researchers-guide/), [current UDS dictionary](https://files.alz.washington.edu/documentation/uds4-rdd.pdf).", "",
             "## G-128: the calendar changes the recorded path in both databases", "",
             "Each main comparison retains the same people and their final recorded diagnosis within eight years. It then hides intermediate visits using a two- or four-year grid, accepting actual visits within six months of each grid point. This tests loss of recorded information, rather than an intervention that forces people to attend a clinic at a new interval.", "",
             "| Database | Baseline MCI | First recorded reversion by year four: all visits | Two-year mask | Four-year mask |", "|---|---:|---:|---:|---:|"]
    for name, db in (("ADNI",a128),("NACC",n128)):
        p = db["point"]
        lines.append(f"| {name} | {p['full']['source_cohort_mci_n']:,} | {100*p['full']['observed_reversion_cif_4y']:.2f}% | {100*p['two_year_mask']['observed_reversion_cif_4y']:.2f}% | {100*p['four_year_mask']['observed_reversion_cif_4y']:.2f}% |")
    lines += ["", f"In NACC, the four-year mask lowers estimated detection of a first reversion before dementia by {-100*rev['difference_mask_minus_full']:.2f} percentage points (paired 95% centre-bootstrap interval for the reduction: {-100*rev['ci_high']:.2f} to {-100*rev['ci_low']:.2f}). This is a competing-event detection estimate with censoring, not the raw proportion of every participant who recovered.", "",
              "The result depends on the horizon. By eight years, NACC estimates are 26.69% with all visits and 26.22% with the four-year mask: a -0.47-point change, with an interval from -1.79 to +1.12. ADNI's eight-year estimates move in the other direction. We therefore cannot say that longer intervals always produce less reversion, or that masking changes the disease itself.", "",
              "For the baseline-normal group, the following counts describe the eight-year window. The fixed denominator includes everyone diagnosed with dementia in that database's full recorded series.", "",
              "| Database | No recorded MCI before dementia: all visits | Two-year mask / fixed converter set | Four-year mask / fixed converter set | Full-series converters missed under four-year mask |", "|---|---:|---:|---:|---:|"]
    for name, db in (("ADNI",a128),("NACC",n128)):
        p = db["point"]; ref = p["full"]["full_series_converter_reference_n"]
        lines.append(f"| {name} | {p['full']['observed_direct_n']:.0f}/{ref:.0f} | {p['two_year_mask']['observed_direct_n']:.0f}/{ref:.0f} | {p['four_year_mask']['observed_direct_n']:.0f}/{ref:.0f} | {p['four_year_mask']['missed_full_series_converters_n']:.0f} |")
    lines += ["", f"NACC's fixed-reference no-MCI share rises by {direct['difference_mask_minus_full']:.2f} percentage points (95% paired interval {direct['ci_low']:.2f} to {direct['ci_high']:.2f}). If the denominator is changed to only dementia cases still detected by the four-year mask, the fraction is 644/808 = 79.70%; using the original converter set gives 644/845 = 76.21%. Both numerators and missed cases must be shown.", "",
              f"There is another important distinction: **{path['no_mci_paths_with_impaired_not_mci_between_baseline_and_first_dementia_n']} of NACC's {path['no_recorded_mci_before_dementia_n']} full-series no-MCI paths had a recorded impaired-not-MCI diagnosis before dementia.** 'No recorded MCI' does not mean no recorded impairment. The remaining {path['no_mci_paths_without_recorded_impaired_not_mci_in_that_interval_n']} had neither of these intermediate labels recorded, which still does not establish a biological bypass.", "",
              "![Observed schedule sensitivity in ADNI and NACC](ADNI_NACC_schedule_comparison.png)", "",
              "Figure 1. Left: first recorded CN before dementia by year four among baseline-MCI participants. Right: no recorded MCI before dementia within eight years, divided by the original full-series converter set. Whiskers are 95% bootstrap intervals: participant resampling in ADNI and centre resampling in NACC. These are separate descriptive analyses. Participant overlap across the two databases cannot be assessed here, and there is no pooled significance test.", "",
              "The NACC panel model gives a small change in MCI-to-dementia intensity, from 17.323 to 16.435 per 100 MCI person-years. Its four-year dementia probability instead rises from 41.84% to 42.92%. These are different quantities: a rate conditional on being in MCI is not the probability of reaching dementia over several years through all possible paths. The model assumes time-homogeneous Markov transitions and non-informative observation. Its direct CN-to-dementia rate hits the numerical lower bound in every schedule. Those assumptions and the boundary result limit biological interpretation.", "",
              "Removing the forced final visit gives a more literal grid sensitivity, but greatly reduces observed follow-up: only 3,267 of 12,823 MCI participants retain a later visit on the four-year grid, and 513 of the original 845 CN converters are missed. The main and grid-only experiments answer different observation questions; their results should not be mixed.", "",
              f"Visit coverage is uneven. {c['baseline_mci']['all_observed_gaps_at_most_400_days_n']:,} MCI participants have all their available gaps within 400 days, but only {c['baseline_mci']['all_gaps_within_400_days_and_followup_at_least_4y_n']} also have at least four years of follow-up. These counts describe coverage; they do not add a new eligibility condition. The full series itself can miss changes between visits. The experiment supports a bounded claim about recorded schedules, not reconstruction of true onset or proof that published direct-conversion phenotypes are wholly artefacts.", "",
              "## G-101: measured reweighting changes little in either database", "",
              "The NACC model compares ordinary and weighted associations with a first recorded dementia diagnosis within four years among people already diagnosed with MCI at baseline. The weights standardize recorded composition toward the pooled enrolled CN+MCI reference. They do not estimate population MCI-incidence probabilities.", "",
              "| Database and exposure | MCI / dementia events | Ordinary adjusted HR (95% interval) | Weighted HR (95% interval) | Weighted / ordinary HR (paired 95% interval) |", "|---|---:|---|---|---|"]
    comparison = []
    for name, db in (("ADNI",a101),("NACC",n101)):
        for x in db["exposures"]:
            w = x["weighted"]["winsor_01_99"]
            mci = x["prevalent_mci_n"] if name == "ADNI" else x["baseline_mci_n"]
            events = x["first_dementia_events_within_horizon"] if name == "ADNI" else x["dementia_events"]
            lo,hi = x["naive_bootstrap_ci"]; wl,wh = w["bootstrap_ci"]; rl,rh = w["hr_ratio_vs_naive_ci"]
            ratio = w["hr"]/x["naive_hr"]
            lines.append(f"| {name}: {x['label']} | {mci:,} / {events:,} | {x['naive_hr']:.3f} ({lo:.3f}-{hi:.3f}) | {w['hr']:.3f} ({wl:.3f}-{wh:.3f}) | {ratio:.3f} ({rl:.3f}-{rh:.3f}) |")
            comparison.append({"database":name,"exposure":x["label"],"baseline_mci_n":mci,"events":events,"ordinary_hr":x["naive_hr"],"weighted_hr":w["hr"],"paired_hr_ratio":ratio,"paired_ratio_low":rl,"paired_ratio_high":rh})
    pd.DataFrame(comparison).to_csv(OUT/"G-101_ADNI_NACC_comparison.csv",index=False)
    lines += ["", "All paired-change intervals include one. The recorded covariate weights produce no clear change in these adjusted associations. This is not proof that selection bias is absent. Both models already adjust for the same recorded variables, and unmeasured causes of entry remain uncontrolled. A hazard ratio is a modeled rate ratio, not a four-year probability. A smoking interval containing one is not proof that smoking has no effect.", "",
              "NACC smoking means more than 100 cigarettes over a lifetime; ADNI uses a medical-history flag. These exposures are not perfectly harmonized and their estimates should not be pooled. The NACC model uses age and age squared, recorded sex, education, initial telephone status, study-form version and centre information. Cox baseline hazards are stratified jointly by centre and initial UDS version. The latest version has very little follow-up and no recorded dementia events in these complete-case MCI groups; stratification avoids fitting an unsupported version hazard coefficient. Baseline-only participants are censored at zero, rather than counted as four-year non-cases.", "",
              "The NACC APOE analysis uses 43 centres and the smoking analysis 46. All 1,000 full-stage centre-bootstrap draws succeeded for each exposure; each draw refits the selection model, weight cutoffs, and both outcome models. Weights are capped at participant-level 1st/99th percentiles, with uncapped and 5th/95th sensitivities saved. Maximum remaining absolute measured imbalance is about 0.088 for APOE and 0.111 for smoking, so balance is approximate. Genotype availability, complete-case selection, attendance and dropout remain concerns.", "",
              "![NACC progression associations before and after measured-composition weighting](G-101/G-101_NACC_effects.png)", "",
              "Figure 2. NACC adjusted hazard ratios and 95% full-stage centre-bootstrap intervals for first recorded dementia. The weighting target is the enrolled research sample. This is not an identified correction for index-event bias.", "",
              "### What remains to answer the original causal question", "",
              f"The data also support a different incident-MCI design: among the {feasibility['baseline_cn']['people_n']:,} initially normal participants, {incident['people_n']:,} first have an MCI diagnosis recorded before dementia over all available follow-up. Of these, {incident['positive_post_mci_followup_n']:,} have later diagnoses and {incident['first_dementia_within_four_years_after_mci_n']} have dementia recorded within four years after that MCI visit. Restricting MCI entry to the first four baseline years gives {early['people_n']:,} entries, {early['positive_post_mci_followup_n']:,} with later diagnosis and {early['first_dementia_within_four_years_after_mci_n']} subsequent four-year dementia events. These are feasibility counts, not incidence risks or a fitted correction.", "",
              "The current weighted experiment concerns prevalent MCI at initial enrolment. It does not implement the originally proposed model of developing MCI in the initially normal arm. Applying its weights to incident MCI would mix different selection events. A further protocol must specify the exposure, target population and outcome, model observed MCI entry over time with censoring and competing events, and state which causes of both entry and progression are measured. Selection on MCI can distort an association in either direction; it does not necessarily push it toward zero. Even a better measured-entry model cannot remove bias through unmeasured common causes without additional assumptions. [Mitchell et al. (2023)](https://doi.org/10.1371/journal.pgen.1010596).", "",
              "The new models also do not reproduce the original papers' cognitive-score slopes or sex-by-APOE interaction. Their null results cannot be declared explained or overturned by this main-effect first-dementia analysis. Death status and death year/month exist in NACC, but these models do not use a death-competing state. A supplied year/month is not an exact date, and known death status does not establish cognitive status between the last diagnosis and death. We do not add an invented year of dementia-free follow-up after the final visit.", "",
              "## What this adds to the review", "",
              "> G-128: We tested the calendar directly in ADNI and NACC. Hiding intermediate visits changed the timing of recorded reversions and made more paths look as though MCI had been skipped. The effect depends on the time point and follow-up retained. This is actual reanalysis evidence about recording, not a reconstruction of the biological disease path.", "",
              "> G-101: We ran a measured-composition sensitivity in both databases. Reweighting changed APOE and smoking estimates only slightly. That does not establish that collider bias has been corrected. The incident-MCI cohort is available for a more faithful onset-based analysis, but the causal target and assumptions need to be specified first.", "",
              "The earlier NACC code used a whole-download calendar clock, per-visit odds and recurrent events, could fill entry exposures from later visits, and mislabeled telephone packets. The new analysis uses individual elapsed time, actual initial groups, first-event outcomes, centre-aware uncertainty and clearly bounded interpretation. Different cohorts and estimators also change the target, so numerical differences from the earlier output are not a controlled estimate of one isolated coding error.", "",
              "No human gate or research ledger was changed, and neither gap is automatically marked closed. Manuscripts are unchanged. All work and raw files remain local, following the instruction not to push.", "",
              "## The supplied NACC instructions and publication", "",
              "The download links were for acquiring the files; the local copies have been read directly. The September 2026 freeze is recorded in the provenance, and the current dictionaries and March-2026-onward derived-variable changes were checked. CSF assays can differ across centres and platforms, but that issue does not enter these experiments because the CSF table is not used.", "",
              "The provider supplied a non-commercial research file. For an eventual abstract, presentation or publication, retain the NACC-required acknowledgment and grant information and follow NACC's administrative submission requirements before journal/conference submission. The current NACC database grant is U24 AG072122; this acknowledges the database and does not imply that NACC directly funded this study. The full required wording is on the [NACC publication and author-checklist page](https://www.naccdata.org/publish-with-nacc-data/). No external submission or message has been sent.", "",
              "## Files and reproducibility", "",
              "The primary G-128 run has 2,000 successful paired centre-bootstrap draws per schedule, plus a 200-participant sensitivity. G-101 has 1,000 successful full-stage centre draws per exposure. Synthetic checks verify the centre-logistic optimizer against independent fits from zero, frequency-weighted cutoffs, centre/version Cox likelihoods, reused transition matrices and known-model recovery. These checks establish numerical implementation, not causal assumptions or interval coverage.", "",
              "```powershell", ".venv\\Scripts\\python.exe -X utf8 scripts/nacc_source_audit.py", ".venv\\Scripts\\python.exe -X utf8 scripts/gap128_nacc_independent.py --reps 2000 --participant-reps 200", ".venv\\Scripts\\python.exe -X utf8 scripts/gap101_nacc_independent.py --bootstrap 1000", ".venv\\Scripts\\python.exe -X utf8 scripts/nacc_incident_feasibility.py", ".venv\\Scripts\\python.exe -X utf8 scripts/nacc_analysis_validation.py", ".venv\\Scripts\\python.exe -X utf8 scripts/nacc_independent_report.py", "```", "",
              "Detailed reports and figures: [G-128](G-128/G-128_NACC_independent.md), [G-101](G-101/G-101_NACC_independent.md). Audits: [source](source_audit.json), [incident and impaired-not-MCI counts](incident_feasibility.json), [numerical validation](synthetic_validation.json), [provenance verification](verification.json), [artifact hashes](artifact_manifest.json). The comparison figure and each detailed figure also have PDF versions. Earlier ADNI results are preserved in [their original report](../adni_independent_2026-11-02/RESULTS.md).", ""]
    (OUT/"RESULTS.md").write_text("\n".join(lines),encoding="utf-8")


def main():
    source = read(OUT/"source_audit.json")
    a128 = read(ADNI/"G-128/G-128_results.json")
    a101 = read(ADNI/"G-101/G-101_independent_results.json")
    n128 = read(OUT/"G-128/G-128_NACC_independent_results.json")
    n101 = read(OUT/"G-101/G-101_NACC_independent_results.json")
    numerical = read(OUT/"synthetic_validation.json")
    feasibility = read(OUT/"incident_feasibility.json")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    checked = verify(source, a128, n128, n101, numerical)
    checked.update(version=VERSION, built_at_utc=stamp)
    (OUT/"verification.json").write_text(json.dumps(checked, indent=2)+"\n", encoding="utf-8")
    comparison_figure(a128, n128, stamp)
    write_report(source, a128, a101, n128, n101, feasibility, stamp)
    script_names = ["nacc_source_audit.py", "gap128_nacc_independent.py", "gap101_nacc_independent.py", "nacc_analysis_validation.py", "nacc_incident_feasibility.py", "nacc_independent_report.py"]
    artifacts = [ROOT/"scripts"/n for n in script_names]
    artifacts += [p for p in OUT.rglob("*") if p.is_file() and "pilot" not in p.parts and p.name != "artifact_manifest.json"]
    manifest = {"version":VERSION,"built_at_utc":stamp,
                "artifacts":[{"path":p.relative_to(ROOT).as_posix(),"sha256":digest(p)} for p in sorted(artifacts)]}
    (OUT/"artifact_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(checked,indent=2))
    print("Wrote local aggregate report, cross-database figure and artifact hashes.")


if __name__ == "__main__":
    main()
