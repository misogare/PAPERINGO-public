"""Shared, aggregate-audited clinical source for the independent NACC runs.

Reads the entire projected Quick Access CSV to EOF. Participant records remain
in memory; outputs contain only aggregate counts and hashes. No signed download
links, credentials, manuscript edits or human-gate decisions are written.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "downloads" / "nacc" / "investigator_nacc75.csv"
OUT = ROOT / "logs" / "nacc_independent_2026-11-03"
VERSION = "1.0"
COLS = ["NACCID", "NACCADC", "PACKET", "FORMVER", "VISITDATE", "NACCVNUM",
        "NACCUDSD", "NACCAGE", "NACCSEX", "EDUC", "NACCAPOE", "NACCNE4S",
        "TOBAC100", "HEARING", "HEARAID", "DIABETES", "DIABTYPE", "SMOKYRS",
        "PACKSPER", "NACCFDYS", "NACCDAYS", "NACCDIED", "NACCYOD",
        "NACCINT", "MODEA1", "MODEB1", "NACCAVST"]
STATE_MAP = {1: 1, 3: 2, 4: 3}


def file_hash(path):
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def frequency(series):
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}


def load_clinical(min_age=50, include_telephone=True):
    raw = pd.read_csv(CSV, usecols=COLS, low_memory=False,
                      dtype={"NACCID": str, "PACKET": str, "VISITDATE": str})
    sources = [{"path": CSV.relative_to(ROOT).as_posix(), "sha256": file_hash(CSV),
                "bytes": CSV.stat().st_size, "rows": len(raw),
                "projected_columns": COLS, "freeze": "September 2026 (provider email)"}]
    audit = {"raw_rows_read_to_eof": len(raw),
             "raw_people": int(raw.NACCID.nunique()), "raw_centers": int(raw.NACCADC.nunique()),
             "raw_packet_counts": frequency(raw.PACKET),
             "raw_diagnosis_counts": frequency(raw.NACCUDSD),
             "minimum_baseline_age": min_age, "maximum_valid_age": 120,
             "include_telephone_diagnoses": include_telephone}
    raw["date"] = pd.to_datetime(raw.VISITDATE, format="mixed", errors="coerce")
    raw["days"] = pd.to_numeric(raw.NACCFDYS, errors="coerce")
    raw["state"] = pd.to_numeric(raw.NACCUDSD, errors="coerce").map(STATE_MAP)
    audit["missing_visit_dates"] = int(raw.date.isna().sum())
    audit["missing_elapsed_days"] = int(raw.days.isna().sum())
    audit["negative_elapsed_days"] = int(raw.days.lt(0).sum())
    audit["initial_rows_raw"] = int(raw.NACCVNUM.eq(1).sum())
    initial_raw = raw.loc[raw.NACCVNUM.eq(1)].copy()
    audit["initial_diagnosis_counts_before_exclusions"] = frequency(initial_raw.NACCUDSD)
    audit["initial_packet_counts_before_exclusions"] = frequency(initial_raw.PACKET)
    audit["initial_age_below_minimum_people"] = int(initial_raw.NACCAGE.lt(min_age).sum())
    audit["initial_age_below_minimum_by_state"] = frequency(initial_raw.loc[initial_raw.NACCAGE.lt(min_age), "NACCUDSD"])
    audit["initial_elapsed_not_zero"] = int(initial_raw.days.ne(0).sum())
    if audit["initial_elapsed_not_zero"]:
        raise ValueError("Initial visit elapsed days are not zero: resolve before analysis")
    if initial_raw.NACCID.duplicated().any():
        raise ValueError("More than one NACCVNUM=1 per participant: resolve before analysis")
    # All clinical packets are primary; the optional stream restriction is a
    # selected-sample sensitivity, not proof of in-person assessment in UDSv4.
    permitted = {"I", "F", "I4", "IT", "T"} if include_telephone else {"I", "F", "I4"}
    audit["unrecognized_packet_rows"] = int((~raw.PACKET.isin({"I", "F", "I4", "IT", "T"})).sum())
    if audit["unrecognized_packet_rows"]:
        raise ValueError("Unrecognized packet code: inspect official definition before proceeding")
    valid = raw.loc[raw.NACCID.notna() & raw.date.notna() & raw.days.ge(0)
                    & raw.PACKET.isin(permitted) & raw.state.notna()].copy()
    audit["valid_mapped_rows_before_same_day_resolution"] = len(valid)
    conflicts = valid.groupby(["NACCID", "days"]).state.nunique()
    bad = conflicts[conflicts.gt(1)].index
    mask = pd.MultiIndex.from_frame(valid[["NACCID", "days"]]).isin(bad)
    audit["conflicting_person_days_removed"] = len(bad)
    audit["conflicting_rows_removed"] = int(mask.sum())
    valid = valid.loc[~mask].copy()
    # Preserve the actual initial visit BEFORE collapsing duplicate same-day rows.
    baseline = valid.loc[valid.NACCVNUM.eq(1)].copy()
    baseline = baseline.loc[baseline.NACCAGE.between(min_age, 120)
                            & baseline.NACCADC.between(100, 9999)].copy()
    baseline["baseline_date"] = baseline.date
    baseline["baseline_state"] = baseline.state.astype(int)
    baseline["baseline_days"] = baseline.days
    visits = valid.sort_values(["NACCID", "days", "NACCVNUM"]).drop_duplicates(["NACCID", "days"])
    audit["identical_person_day_rows_collapsed"] = len(valid) - len(visits)
    audit["clean_mapped_visit_rows_all_ages"] = len(visits)
    audit["eligible_initial_people_all_three_states"] = len(baseline)
    audit["eligible_initial_state_counts"] = frequency(baseline.baseline_state)
    audit["eligible_initial_centers"] = int(baseline.NACCADC.nunique())
    after = visits.merge(baseline[["NACCID", "baseline_date"]], on="NACCID", how="inner")
    audit["elapsed_days_disagree_with_visitdate_difference"] = int(
        (after.days - (after.date - after.baseline_date).dt.days).abs().gt(1e-8).sum())
    audit["participants_with_multiple_center_codes"] = int(raw.groupby("NACCID").NACCADC.nunique().gt(1).sum())
    audit["apoe_participants_with_conflicting_valid_genotypes"] = int(
        raw.loc[raw.NACCAPOE.isin(range(1, 7))].groupby("NACCID").NACCAPOE.nunique().gt(1).sum())
    last = after.groupby("NACCID").days.max()
    audit["initial_people_with_any_later_mapped_diagnosis"] = {
        str(state): int(baseline.loc[baseline.baseline_state.eq(state), "NACCID"].map(last).gt(0).sum())
        for state in (1, 2, 3)}
    audit["initial_baseline_covariate_counts"] = {
        "smoking_codes": frequency(baseline.TOBAC100),
        "genotype_codes": frequency(baseline.NACCAPOE),
        "hearing_codes": frequency(baseline.HEARING),
        "diabetes_codes": frequency(baseline.DIABETES),
        "diabetes_type_codes": frequency(baseline.DIABTYPE),
        "sex_codes": frequency(baseline.NACCSEX),
        "initial_packet_codes": frequency(baseline.PACKET),
    }
    audit["known_death_status_codes_all_people"] = frequency(raw.drop_duplicates("NACCID").NACCDIED)
    audit["rules"] = {
        "baseline": "Actual NACCVNUM=1, valid initial state, baseline age 50-120 in primary; do not substitute a later usable diagnosis.",
        "clock": "NACCFDYS is per-visit elapsed days since initial; NACCDAYS is not used as the longitudinal clock.",
        "states": "NACCUDSD 1/3/4 mapped to CN/MCI/dementia 1/2/3. Code2 impaired-not-MCI and code8 behavioral-only impairment are excluded, not relabelled.",
        "mode": "All valid clinical diagnosis packets primary. IT/T are legacy telephone packets; UDSv4 standard packets may also contain remote forms.",
        "death": "NACCDIED=0 combines not-known-deceased with unknown; NACCYOD is year only. Do not manufacture an exact death date or count all code0 as confirmed alive.",
        "genotype": "NACCAPOE1..6 valid; carrier codes2/4/5. NACCNE4S0..2 valid counts;9 missing. Genotype fields are cross-sectional and may have been ascertained after entry.",
        "smoking": "TOBAC100 is lifetime >100 cigarettes, not current smoking. Codes0/1 valid;9/-4 missing.",
        "hearing": "HEARING0 means hearing not functionally normal without aids;1 means normal;9/-4 missing.",
        "diabetes": "DIABETES0 absent,1 recent/active,2 remote/inactive; type2 needs DIABTYPE2 and is not interchangeable with all diabetes.",
    }
    return visits.reset_index(drop=True), baseline.reset_index(drop=True), audit, sources


def main():
    _, _, audit, sources = load_clinical()
    csf_path = ROOT / "downloads" / "nacc" / "investigator_fcsf_nacc75.csv"
    csf = pd.read_csv(csf_path, usecols=["NACCID"], dtype=str)
    documentation = []
    for name in ("uds4-rdd.csv", "rdd-gen.csv", "data-freeze-read-me.pdf", "uds4-rdd.pdf"):
        path = ROOT / "downloads" / "nacc_documentation" / name
        documentation.append({"file": path.name, "sha256": file_hash(path),
                              "url": "https://files.alz.washington.edu/documentation/" + name})
    result = {"version": VERSION, "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "scope": "NACC September2026 Quick Access; aggregate-only audit; all work local; no human gate or gap closure.",
              "sources": sources, "documentation": documentation, "audit": audit,
              "csf_inventory": {"file": csf_path.name, "sha256": file_hash(csf_path),
                                "rows": len(csf), "people": int(csf.NACCID.nunique()),
                                "used_in_G128_or_G101": False},
              "provider_notes": ["Files include all visits at the September2026 freeze.",
                                 "Variable calculations changed in March2026 and later freezes; current dictionary used.",
                                 "Non-commercial research use. Publication/presentation needs NACC acknowledgment and submission/checklist review; no publication or messages sent by this analysis.",
                                 "Download links already used by the researcher; no signed links or access credentials stored in analysis outputs."]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "source_audit.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("raw_rows_read_to_eof", "raw_people", "raw_centers", "eligible_initial_state_counts", "conflicting_person_days_removed", "elapsed_days_disagree_with_visitdate_difference")}, indent=2))
    print("Wrote aggregate source audit; no participant records exported.")


if __name__ == "__main__":
    main()
