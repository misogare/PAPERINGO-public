"""Aggregate-only incident-MCI feasibility and impaired-not-MCI path audit.

No weights, outcome models, causal correction, patient identifiers or row-level
outputs are produced. Existing G-128 estimates and bootstraps are not changed.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from nacc_source_audit import CSV, ROOT, load_clinical

OUT = ROOT / "logs" / "nacc_independent_2026-11-03"
VERSION = "1.0"


def covariate_counts(frame):
    age = pd.to_numeric(frame.NACCAGE, errors="coerce")
    sex = pd.to_numeric(frame.NACCSEX, errors="coerce")
    education = pd.to_numeric(frame.EDUC, errors="coerce")
    genotype = pd.to_numeric(frame.NACCAPOE, errors="coerce")
    tobacco = pd.to_numeric(frame.TOBAC100, errors="coerce")
    common = age.between(50, 120) & sex.isin([1, 2]) & education.between(0, 36)
    return {"people_n": len(frame), "age_sex_education_complete_n": int(common.sum()),
            "baseline_apoe_and_covariates_complete_n": int((common & genotype.isin(range(1, 7))).sum()),
            "baseline_smoking_and_covariates_complete_n": int((common & tobacco.isin([0, 1])).sum()),
            "both_exposures_and_covariates_complete_n": int((common & genotype.isin(range(1, 7)) & tobacco.isin([0, 1])).sum())}


def main():
    visits, baseline, audit, sources = load_clinical(min_age=50, include_telephone=True)
    cn = baseline.loc[baseline.baseline_state.eq(1)].set_index("NACCID").copy()
    future = visits.loc[visits.NACCID.isin(cn.index) & visits.days.gt(0)]
    first_mci = future.loc[future.state.eq(2)].groupby("NACCID").days.min()
    first_dem = future.loc[future.state.eq(3)].groupby("NACCID").days.min()
    last = future.groupby("NACCID").days.max()
    cn["first_mci_days"] = first_mci
    cn["first_dementia_days"] = first_dem
    cn["last_mapped_diagnosis_days"] = last
    eligible = cn.first_mci_days.notna() & (cn.first_dementia_days.isna() | cn.first_mci_days.lt(cn.first_dementia_days))
    incident = cn.loc[eligible].copy()
    incident["post_mci_followup_days"] = incident.last_mapped_diagnosis_days - incident.first_mci_days
    incident["dementia_after_mci_days"] = incident.first_dementia_days - incident.first_mci_days

    def describe(frame):
        post = frame.post_mci_followup_days
        event = frame.dementia_after_mci_days
        positive = post.loc[post.gt(0)] / 365.25
        return {**covariate_counts(frame), "positive_post_mci_followup_n": int(post.gt(0).sum()),
                "no_later_mapped_diagnosis_after_mci_n": int(post.le(0).sum()),
                "first_dementia_within_four_years_after_mci_n": int(event.gt(0).mul(event.le(4 * 365.25)).sum()),
                "later_dementia_after_mci_all_available_followup_n": int(event.gt(0).sum()),
                "median_post_mci_followup_years_among_positive": float(positive.median()) if len(positive) else None}

    primary = json.loads((OUT / "G-128" / "G-128_NACC_independent_results.json").read_text(encoding="utf-8"))
    converters = cn.loc[cn.first_dementia_days.le(8 * 365.25)].copy()
    direct = converters.loc[converters.first_mci_days.isna() | converters.first_mci_days.gt(converters.first_dementia_days)]
    assert len(converters) == primary["point"]["full"]["detected_dementia_n"]
    assert len(direct) == primary["point"]["full"]["observed_direct_n"]
    # State2 must be read from the raw projection: the shared three-state series
    # deliberately excludes impaired-not-MCI diagnoses rather than mapping them.
    raw = pd.read_csv(CSV, usecols=["NACCID", "PACKET", "VISITDATE", "NACCFDYS", "NACCUDSD"],
                      dtype={"NACCID": str, "PACKET": str, "VISITDATE": str}, low_memory=False)
    assert len(raw) == sources[0]["rows"]
    raw["days"] = pd.to_numeric(raw.NACCFDYS, errors="coerce")
    date = pd.to_datetime(raw.VISITDATE, format="mixed", errors="coerce")
    state2 = raw.loc[pd.to_numeric(raw.NACCUDSD, errors="coerce").eq(2)
                    & raw.PACKET.isin(["I", "F", "I4", "IT", "T"])
                    & date.notna() & raw.days.gt(0), ["NACCID", "days"]]
    state2 = state2.join(converters.first_dementia_days, on="NACCID", how="inner")
    state2 = state2.loc[state2.days.lt(state2.first_dementia_days)]
    state2_people = pd.Index(state2.NACCID.unique())
    direct_state2_n = int(direct.index.isin(state2_people).sum())
    result = {
        "version": VERSION, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Descriptive feasibility only; NACC all clinical packets, initial CN age50-120; no weights, model fits, causal identification or gap closure; aggregate output only.",
        "sources": sources,
        "baseline_cn": covariate_counts(cn),
        "first_recorded_mci_before_dementia_all_available_followup": describe(incident),
        "first_recorded_mci_within_four_years_of_baseline": describe(incident.loc[incident.first_mci_days.le(4 * 365.25)]),
        "baseline_cn_eight_year_dementia_path_audit": {
            "full_series_converters_n": len(converters), "no_recorded_mci_before_dementia_n": len(direct),
            "converters_with_impaired_not_mci_between_baseline_and_first_dementia_n": len(state2_people),
            "no_mci_paths_with_impaired_not_mci_between_baseline_and_first_dementia_n": direct_state2_n,
            "no_mci_paths_without_recorded_impaired_not_mci_in_that_interval_n": len(direct) - direct_state2_n,
            "raw_state2_visit_rows_in_relevant_intervals_n": len(state2),
            "definition": "NACCUDSD=2 is impaired-not-MCI, strictly after initial CN and strictly before first recorded dementia within8y. No-recorded-MCI does not mean no recorded impairment or biological bypass."},
        "notes": [
            "First observed MCI is the earliest mapped MCI diagnosis after a genuine initial CN visit, before any recorded dementia; it is not latent biological onset.",
            "All-available follow-up and four-year-from-initial-MCI-entry follow-up are separate horizons; a first MCI within4y of baseline can have later follow-up beyond that baseline horizon.",
            "This incident-MCI inception cohort differs from prevalent baseline-MCI outcome models. Baseline-MCI composition weights have not been applied to these incident cases.",
            "Covariate counts use only initial-row genotype, lifetime>100-cigarette smoking history, age, sex and education; no later nonmissing clinical values are substituted. Genotype availability can itself select people.",
            "Missing observation, competing death, interval-detected onset, retention and causal assumptions remain unresolved. These counts are feasibility descriptions, not incidence risks or causal estimates."]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "incident_feasibility.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("baseline_cn", "first_recorded_mci_before_dementia_all_available_followup",
                                            "first_recorded_mci_within_four_years_of_baseline", "baseline_cn_eight_year_dementia_path_audit")}, indent=2))


if __name__ == "__main__":
    main()
