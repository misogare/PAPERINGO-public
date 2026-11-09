"""Verify saved ADNI analyses and build an aggregate report for discussion.

Does not refit production bootstraps, export participant records, or edit a
manuscript, research ledger or human gate. Run from the project virtualenv.
"""

from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import platform
from zoneinfo import ZoneInfo

import matplotlib
import numpy as np
import pandas as pd
import scipy
import statsmodels

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "adni_independent_2026-11-02"
VERSION = "1.0"


def read_json(relative):
    return json.loads((OUT / relative).read_text(encoding="utf-8"))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def verify(source, g101, g128, literal, numerical):
    module101 = load_script("gap101_independent")
    module128 = load_script("gap128_independent")
    tables, current_sources = module101.read_sources()
    expected = {x["archive"]: x["csv_sha256"] for x in source["sources"]}
    assert {x["archive"]: x["csv_sha256"] for x in current_sources} == expected
    assert {x["archive"]: x["csv_sha256"] for x in g101["inputs"]} == expected
    for x in g128["source_files"] + literal["source_files"]:
        assert x["sha256"] == expected[x["archive"]]
    _, baseline, audit = module101.prepare(tables)
    cohorts, counts, _, _ = module128.load_cohorts()
    assert audit == g101["cohort_audit"]
    assert counts == g128["cohort_audit"] == literal["cohort_audit"]
    assert set(baseline.loc[baseline.S.eq(1), "RID"]) == set(cohorts["baseline_mci"])
    assert set(baseline.loc[baseline.S.eq(0), "RID"]) == set(cohorts["baseline_cn"])
    assert counts["baseline_mci"]["n"] == source["counts"]["baseline_state_counts"]["2"] - audit["prior_observed_dementia_excluded"]
    assert numerical["all_checks_passed"]
    for x in numerical["analyses"]:
        assert digest(ROOT / x["script"]) == x["sha256"], "Re-run numerical validation after code changes"
    assert g128["bootstrap"]["requested"] == 2000
    assert all(x["success"] for x in g128["point"].values())
    assert all(x["success"] for x in literal["point"].values())
    for exposure in g101["exposures"]:
        assert exposure["bootstrap"]["requested"] == exposure["bootstrap"]["successful"] == 1000
        assert exposure["cox_validation_max_abs_coefficient_difference"] < 1e-4
    return {"source_hashes_match_current_archives": True,
            "saved_and_current_cohort_audits_match": True,
            "same_individual_baseline_cohorts_across_analyses": True,
            "numerical_validation_matches_current_scripts": True,
            "production_bootstrap_counts_verified": True,
            "scope": "Provenance and numerical checks only; no human gate or scientific closure."}


def build_report(source, g101, g128, literal, numerical, stamp):
    c = source["counts"]
    p = g128["point"]
    names = {"full": "All recorded visits", "two_year_mask": "Two-year grid + final visit", "four_year_mask": "Four-year grid + final visit"}
    changes = {(x["schedule"], x["metric"]): x for x in g128["paired_differences"]}
    r = changes[("four_year_mask", "observed_reversion_cif_4y")]
    d = changes[("four_year_mask", "observed_direct_fixed_converter_pct")]
    intensity = changes[("four_year_mask", "mci_dementia_intensity_per_100py")]
    text = ["# ADNI experiments: G-128 and G-101", "",
            f"Version {VERSION} | Report built {stamp} | ADNI only | Results for discussion, pending human review", "",
            "Both experiments have been run independently using the downloaded ADNI tables. The earlier analysis had errors in its time scale, cohort definitions and event counting, so its estimates should not be used as the final results. The replacement scripts, tables and figures are saved beside this report. NACC has not been used.", "",
            "## What was in the download", "",
            "| Count | People or records |", "|---|---:|",
            f"| Valid unique dated diagnosis records after cleaning | {c['clean_unique_diagnosis_dates']:,} records |",
            f"| People with an enrolled clinical baseline diagnosis | {c['confirmed_clinical_baseline_people']:,} people |",
            f"| MCI at clinical baseline | {c['baseline_state_counts']['2']:,} people |",
            f"| Baseline MCI eligible for a first-dementia analysis | {g128['cohort_audit']['baseline_mci']['n']:,} people |",
            f"| Eligible baseline MCI with a later diagnosis within eight years | {g128['cohort_audit']['baseline_mci']['with_followup']:,} people |",
            f"| Cognitively normal at clinical baseline | {c['baseline_state_counts']['1']:,} people |",
            f"| People who ever had an MCI diagnosis | {c['ever_MCI_people']:,} people |",
            f"| Medical-history smoking flags marked yes | {c['smoking_yes_records']:,} records, from {c['smoking_unique_people_any_yes']:,} different people |", "",
            "Two baseline-MCI participants had an earlier recorded dementia diagnosis, so they were excluded from a first-dementia analysis. Baseline means an explicit dated clinical baseline record confirmed in REGISTRY for the same phase. A screening visit, an eventual MCI diagnosis and MCI at baseline are different things. Same-day duplicate diagnoses were collapsed after preserving the baseline flag; two conflicting person-dates were excluded rather than choosing the worse diagnosis. No missing visit date was invented.", "",
            f"The overlap of ever-CN and ever-MCI contains {c['ever_CN_and_MCI_people']} people: {c['CN_before_first_MCI_among_overlap']} were recorded CN before their first MCI and {c['MCI_before_first_CN_among_overlap']} were recorded MCI first. That overlap cannot be called a converter count. The earlier claim of 1,228 smokers also counted records, not unique people.", "",
            "The outcome used here is a recorded clinical dementia diagnosis. ADNI codes DIAGNOSIS=1 as cognitively unimpaired, 2 as MCI and 3 as dementia; code 3 alone does not establish biomarker-confirmed Alzheimer's disease. [Official ADNI diagnosis definitions](https://adni.loni.usc.edu/quick-start-guide-asset/diagnostic.html).", "",
            "## G-128: does the visit schedule change the apparent path?", "",
            "We kept the same baseline participants and read each person's diagnosis series three ways. The main comparison keeps all recorded visits, or keeps visits near a two-year or four-year grid while also keeping the person's original final visit. Each person's clock starts at their own baseline. The source window ends at eight years.", "",
            "Keeping the final visit helps separate loss of intermediate information from loss of follow-up. It also means this is a test of masking recorded visits, not an estimate of what a clinic would achieve by scheduling visits every four years. All probabilities in the following table concern participants with MCI at baseline.", "",
            "| Main comparison | First recorded reversion before dementia by year 4 | Model probability of being CN at year 4 | Model probability of dementia at year 4 |", "|---|---:|---:|---:|"]
    for name, x in p.items():
        text.append(f"| {names[name]} | {100*x['observed_reversion_cif_4y']:.2f}% | {100*x['occupation_cn_from_mci_4y']:.2f}% | {100*x['occupation_dementia_from_mci_4y']:.2f}% |")
    text += ["", f"At four years, the four-year mask reduced estimated detection of a first reversion by {-100*r['difference_mask_minus_full']:.2f} percentage points (paired 95% bootstrap interval for the reduction: {-100*r['ci_high']:.2f} to {-100*r['ci_low']:.2f}). The first column concerns the first observed CN diagnosis before observed dementia, allowing for censoring and the competing event. It is not a simple percentage of all participants. The other columns come from a continuous-time model of the diagnosis states; being CN at year four is different from ever having reverted.", "",
             "The direction depends on the time point. By eight years, the first-reversion estimate is 16.23% with all visits and 20.58% with the four-year mask. Masking changes detection times, competing-event order and who remains at risk. These results do not show that less frequent observation always lowers reversion, or that it changes biological recovery.", "",
             "For the baseline-CN group, the eight-year window contains 21 people with a dementia diagnosis in the full record. Five had no post-baseline MCI diagnosis before dementia. The two-year mask classifies nine this way. The four-year mask classifies 14 this way and misses one of the original dementia cases entirely.", "",
             "| Apparent direct path, up to eight years | Direct / detected dementia | Direct / original 21 converters | Original converters missed |", "|---|---:|---:|---:|"]
    for name, x in p.items():
        text.append(f"| {names[name]} | {x['observed_direct_n']:.0f}/{x['detected_dementia_n']:.0f} | {x['observed_direct_n']:.0f}/{x['full_series_converter_reference_n']:.0f} | {x['missed_full_series_converters_n']:.0f} |")
    text += ["", f"On the fixed 21-person reference, the apparent direct-path share rises from 23.81% to 66.67%, a change of {d['difference_mask_minus_full']:.2f} percentage points (95% paired interval {d['ci_low']:.2f} to {d['ci_high']:.2f}). Using only detected dementia cases gives 14/20 = 70%; that different denominator must be stated. This is a small converter group, and the full record can itself miss intermediate MCI. Four baseline-CN participants had MCI recorded before baseline; a separate sensitivity excluding them is in the full results.", "",
             f"The model's MCI-to-dementia intensity changes only modestly: {p['full']['mci_dementia_intensity_per_100py']:.3f} to {p['four_year_mask']['mci_dementia_intensity_per_100py']:.3f} transitions per 100 MCI person-years. The paired difference is {intensity['difference_mask_minus_full']:.3f} ({intensity['ci_low']:.3f} to {intensity['ci_high']:.3f}). This is model dependent. The model's direct CN-to-dementia rate reaches its numerical lower bound in every schedule; that is not evidence that biological bypass cannot occur.", "",
             "![G-128 visit masking, diagnosis-state model and apparent direct paths](G-128/G-128_independent.png)", "",
             "Figure 1. Left: first recorded reversion before dementia, at observed visit dates. Middle: model probability of being CN at each time, given baseline MCI. Right: apparent direct paths among dementia cases detected within eight years. The same 1,285 baseline-MCI and 1,195 baseline-CN participants enter each main comparison. Whiskers are participant-bootstrap 95% intervals; the right-panel denominator changes from 21 to 20 under the four-year mask.", "",
             "### Thinning without keeping the final visit", "",
             "We also ran the more literal baseline-plus-grid version. It keeps baseline and only actual visits within six months of each grid point. It does not force retention of the original final visit. The source people stay the same, but many are left with baseline only and contribute no observed follow-up.", "",
             "| Schedule | MCI with retained follow-up / 1,285 | First recorded reversion by year 4 | Direct / detected dementia by year 8 | Original 21 converters missed |", "|---|---:|---:|---:|---:|"]
    for name, x in literal["point"].items():
        label = "All recorded visits" if name == "full" else name.replace("_mask", " grid only").replace("_", " ").capitalize()
        text.append(f"| {label} | {x['followup']['baseline_mci']['people_with_retained_followup_n']:,} | {100*x['observed_reversion_cif_4y']:.2f}% | {x['observed_direct_n']:.0f}/{x['detected_dementia_n']:.0f} | {x['missed_full_series_converters_n']:.0f} |")
    text += ["", "Under the four-year grid, 803 MCI participants have no retained follow-up. All six detected dementia cases in the CN group look direct, but 15 of the original 21 converters disappear. Quoting '100% direct conversion' alone would therefore be misleading. These are descriptive point estimates without another bootstrap; changed follow-up and possibly informative censoring limit their interpretation.", "",
             "The baseline-MCI sample exceeds 1,000. Observed visit coverage is uneven: only 597 MCI participants have every observed follow-up gap within 400 days. A pooled median gap cannot establish complete annual observation for every person. Death is not fully ascertained, diagnoses may be misclassified, and the state model assumes a time-homogeneous Markov process and non-informative observation. The experiment establishes sensitivity to the recorded schedule in ADNI; it does not recover each person's unobserved biological path. The panel likelihood follows the approach described by [Jackson (2011)](https://www.jstatsoft.org/article/view/v038i08).", "",
             "## G-101: does rebalancing the enrolled groups change progression estimates?", "",
             "This experiment used APOE e4 carrier status and the pre-baseline smoking-history flag. We estimated the rate of a first recorded dementia diagnosis during four years among people with MCI at baseline. The ordinary model already adjusts for age, recorded sex, education and study phase. We then reweighted the MCI group toward the recorded characteristics of the pooled enrolled ADNI CN+MCI group and fitted the adjusted model again.", "",
             "| Exposure | Baseline MCI / dementia events | Ordinary adjusted HR (95% interval) | Weighted adjusted HR (95% interval) | Weighted / ordinary HR (paired 95% interval) |", "|---|---:|---|---|---|"]
    for x in g101["exposures"]:
        w = x["weighted"]["winsor_01_99"]
        lo, hi = x["naive_bootstrap_ci"]
        wl, wh = w["bootstrap_ci"]
        rl, rh = w["hr_ratio_vs_naive_ci"]
        text.append(f"| {x['label']} | {x['prevalent_mci_n']:,} / {x['first_dementia_events_within_horizon']} | {x['naive_hr']:.3f} ({lo:.3f}–{hi:.3f}) | {w['hr']:.3f} ({wl:.3f}–{wh:.3f}) | {w['hr']/x['naive_hr']:.3f} ({rl:.3f}–{rh:.3f}) |")
    text += ["", "A hazard ratio above one means a higher estimated diagnosis rate relative to the unexposed group under this model. It is not a four-year probability. Both paired change intervals include one: this analysis gives no clear evidence that these measured-composition weights change the adjusted association. The smoking intervals also include one, which is not proof that smoking has no effect.", "",
             "The important limit is that these weights describe who entered the already selected ADNI CN and MCI groups. They are not population probabilities of developing MCI. They cannot remove selection through unmeasured causes. Because both outcome models already adjust for the same recorded covariates, similar coefficients can also be expected under a correctly specified conditional model. Neither a small nor a large change identifies collider bias by itself. Disease-based selection can distort an association in either direction; a drift toward zero is not guaranteed. [Mitchell et al. (2023)](https://doi.org/10.1371/journal.pgen.1010596) discuss the assumptions and limits of approaches to this problem.", "",
             "![G-101 progression associations before and after measured-composition weighting](G-101/G-101_independent_effects.png)", "",
             "Figure 2. Adjusted hazard ratios for first recorded clinical dementia during four years. The weights are standardized to the pooled enrolled ADNI reference and winsorized at participant-level 1st and 99th percentiles. Each exposure has 1,000 full two-stage bootstrap replicates, refitting the weights and both outcome models. Different exposure-specific complete-case samples are used. APOE has 1,137 MCI participants with positive follow-up; smoking history has 825. The others are censored at baseline and do not count as four-year non-cases.", "",
             "![Balance of recorded ADNI entry characteristics](G-101/G-101_independent_balance.png)", "",
             "Figure 3. Weighting brings the measured baseline characteristics closer to the pooled enrolled ADNI reference. Good balance on these fields does not establish balance on unmeasured causes or causal identification.", "",
             "The smoking flag describes medical history, not necessarily current smoking or lifetime dose. MH9ENDO describes endocrine/metabolic history and was not treated as type-2 diabetes. Missing exposure and demographic values were not converted to negative exposures or male sex. The available baseline-CN series contains 183 observed incident-MCI cases before dementia, with 37 later dementia diagnoses over all available follow-up; this is a feasibility count, not a validated incident-MCI weighting analysis. Death, informative dropout and genotype availability remain concerns. These models do not reproduce P-934's cognitive outcomes or P-984's sex-by-APOE interaction. G-101's causal question remains open.", "",
             "## Why the earlier estimates should be replaced", "",
             "| Earlier implementation | Why it changes the answer | Replacement |", "|---|---|---|",
             "| G-128 measured time from the first date in the whole download | A two-year estimate was not two years from each person's baseline | Individual baseline clocks |",
             "| G-128 changed who remained in each schedule | Schedule differences also reflected different people and follow-up | Fixed source cohorts; retained follow-up and missed cases reported |",
             "| G-128 divided MCI transitions by time in all diagnosis states | Denominator did not represent time at risk in MCI | Panel transition model using actual intervals |",
             "| G-101 mixed baseline-MCI recruits and CN recruits who later had MCI | Weights and outcome rows represented different selection events | Baseline-MCI progression analysis; incident cases separate |",
             "| G-101 could count dementia more than once and use later medical history | Endpoint and exposure timing differed from the stated design | First observed dementia; pre-baseline covariates |",
             "| G-101 used per-visit odds and a generic E-value | Neither is an identified correction for disease-entry collider bias | Clearly bounded measured-composition sensitivity |", "",
             "The new estimates also use explicitly defined cohorts and horizons, so differences from the old numbers cannot all be attributed to one isolated bug. The earlier scripts and results are preserved for comparison.", "",
             "## What can be said in the meeting", "",
             "> G-128: We read the same ADNI records at different intervals. That changed when reversions were detected and made more observed paths look as though they skipped MCI. The size and direction depend on the time point and on whether follow-up is retained. We have measured a recording effect; we have not reconstructed the true disease path.", "",
             "> G-101: We compared progression estimates before and after rebalancing recorded entry characteristics. APOE and smoking-history estimates changed little. This does not tell us that selection bias is absent, because the weights only address measured differences within ADNI. A causal correction needs a more explicit selection model and stronger data or assumptions.", "",
             "NACC would provide a separate cohort for replication and potentially more incident cases. Adding it alone would not solve causal identification. No research gap is marked closed by these experiments, and no manuscript or human gate was changed.", "",
             "## Validation and reproduction", "",
             f"G-128 used 2,000 paired participant bootstrap samples, stratified by baseline diagnosis. Model-fit failures were {g128['bootstrap']['fit_failures']}; observed-event contrasts use all 2,000 draws and model contrasts omit only the failed paired fits. G-101 had 1,000 successful two-stage bootstrap fits for each exposure. Its independent unweighted Cox implementation matches statsmodels coefficients to about 10^-9.", "",
             f"Numerical checks covered 1,640 transition matrices against scipy.linalg.expm (maximum absolute difference {numerical['ctmc_transition_matrix']['maximum_absolute_difference_from_scipy_expm']:.2g}), recovery of known rates from 20,000 simulated people (maximum relative error {100*numerical['ctmc_known_generator_recovery']['maximum_absolute_relative_error']:.2f}%), and weighted Cox estimates against replicated rows and statsmodels. These checks validate implementation, not causal assumptions or confidence-interval coverage. Current source hashes and participant-level baseline membership agree across the independent analyses; only aggregate checks are exported.", "",
             "```powershell", ".venv\\Scripts\\python.exe -X utf8 scripts/adni_source_audit.py", ".venv\\Scripts\\python.exe -X utf8 scripts/gap128_independent.py --reps 2000 --seed 20261102", ".venv\\Scripts\\python.exe -X utf8 scripts/gap101_independent.py --bootstrap 1000 --seed 20261102 --horizon 4", ".venv\\Scripts\\python.exe -X utf8 scripts/adni_analysis_validation.py", ".venv\\Scripts\\python.exe -X utf8 scripts/adni_independent_report.py", "```", "",
             "To rebuild only the additional grid sensitivity, use `scripts/gap128_independent.py --literal-grid-only`; this preserves the production bootstrap results.", "",
             "Detailed files: [G-128 methods and uncertainty](G-128/G-128_independent.md), [grid-only sensitivity](G-128/G-128_literal_grid.md), [G-101 methods and uncertainty](G-101/G-101_independent_report.md), [source audit](source_audit.json), [numerical checks](synthetic_validation.json), [provenance verification](verification.json), [artifact hashes](artifact_manifest.json). All three figures also have PDF versions beside their PNG files. The directory date records the original 2 November run; this report and the additional checks were completed on 3 November 2026 in Sydney.", ""]
    return "\n".join(text)


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = read_json("source_audit.json")
    g101 = read_json("G-101/G-101_independent_results.json")
    g128 = read_json("G-128/G-128_results.json")
    literal = read_json("G-128/G-128_literal_grid_results.json")
    numerical = read_json("synthetic_validation.json")
    verification = verify(source, g101, g128, literal, numerical)
    verification.update(version=VERSION, built_at_utc=stamp,
                        completed_sydney=datetime.now(ZoneInfo("Australia/Sydney")).isoformat(timespec="seconds"))
    (OUT / "verification.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    (OUT / "RESULTS.md").write_text(build_report(source, g101, g128, literal, numerical, stamp), encoding="utf-8")
    scripts = [ROOT / "scripts" / x for x in ("adni_source_audit.py", "gap101_independent.py", "gap128_independent.py", "adni_analysis_validation.py", "adni_independent_report.py")]
    artifacts = scripts + [p for p in OUT.rglob("*") if p.is_file() and p.name != "artifact_manifest.json"]
    preserved = [ROOT / "scripts" / "gap101_selection_bias.py", ROOT / "scripts" / "gap128_schedule_thinning.py"]
    preserved += [p for p in (ROOT / "logs" / "gap_close_2026-11-02").rglob("*") if p.is_file()]
    manifest = {"version": VERSION, "built_at_utc": stamp,
                "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "statsmodels": statsmodels.__version__, "matplotlib": matplotlib.__version__},
                "artifacts": [{"path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in sorted(artifacts)],
                "earlier_analysis_files_preserved": [{"path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in sorted(preserved)]}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verification, indent=2))
    print("Wrote aggregate RESULTS.md, verification.json and artifact_manifest.json")


if __name__ == "__main__":
    main()
