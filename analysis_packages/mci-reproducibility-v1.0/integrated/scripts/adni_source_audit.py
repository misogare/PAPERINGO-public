"""Aggregate-only audit of the local ADNI download for G-128 and G-101.

Reads original ZIP members without exporting participant records. Baseline flags
are collected BEFORE collapsing identical same-day diagnoses: screening and
baseline can legitimately share a date. No manuscript, ledger or gate is edited.
"""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "downloads" / "adni"
OUT = ROOT / "logs" / "adni_independent_2026-11-02"


def read_table(label, prefix):
    archives = [p for p in DATA.iterdir() if p.name.startswith(label)]
    if len(archives) != 1:
        raise ValueError(f"Expected one {label} archive; found {len(archives)}")
    with ZipFile(archives[0]) as archive:
        members = [n for n in archive.namelist()
                   if Path(n).name.startswith(prefix) and n.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Ambiguous table for {prefix}: {members}")
        raw = archive.read(members[0])
    table = pd.read_csv(BytesIO(raw), dtype=str, low_memory=False)
    return table, {
        "archive": archives[0].name,
        "csv_member": members[0],
        "csv_sha256": sha256(raw).hexdigest(),
        "rows": len(table),
        "columns": table.columns.tolist(),
    }


def main():
    dx, dx_source = read_table("Diagnostic Summary [", "DXSUM_")
    registry, registry_source = read_table("Registry [", "REGISTRY_")
    demog, demog_source = read_table("Subject Demographics [", "PTDEMOG_")
    medical, medical_source = read_table("Medical History [", "MEDHIST_")
    apoe, apoe_source = read_table("ApoE Genotyping", "APOERES_")

    dx["RID"] = pd.to_numeric(dx["RID"], errors="coerce")
    dx["state"] = pd.to_numeric(dx["DIAGNOSIS"], errors="coerce")
    dx["date"] = pd.to_datetime(dx["EXAMDATE"], errors="coerce", format="mixed")
    valid_state = dx[dx["RID"].notna() & dx["state"].isin([1, 2, 3])].copy()
    valid = valid_state[valid_state["date"].notna()].copy()
    qc = pd.to_numeric(valid["HAS_QC_ERROR"], errors="coerce")
    qc_failed = int(qc.eq(1).sum())
    valid = valid[~qc.eq(1)].copy()
    multiplicity = valid.groupby(["RID", "date"])["state"].nunique()
    conflicting = multiplicity[multiplicity.gt(1)].index
    clean_raw = valid.set_index(["RID", "date"])
    clean_raw = clean_raw[~clean_raw.index.isin(conflicting)].reset_index()

    # Preserve the clinical baseline label even if a same-day screening record
    # appears first in the export. Collapse duplicate diagnosis observations later.
    baselines = (clean_raw[clean_raw["VISCODE2"].eq("bl")]
                 .sort_values(["RID", "date"]).drop_duplicates("RID"))
    before_registry = len(baselines)
    registry["RID"] = pd.to_numeric(registry["RID"], errors="coerce")
    registry["date"] = pd.to_datetime(registry["EXAMDATE"], errors="coerce", format="mixed")
    registry["phase_key"] = registry["PHASE"].fillna("").str.strip().str.upper()
    rq = pd.to_numeric(registry["HAS_QC_ERROR"], errors="coerce")
    enrolled = registry[registry["RID"].notna() & registry["date"].notna()
                        & registry["VISCODE2"].eq("bl") & ~rq.eq(1)]
    enrolled_keys = set(zip(enrolled["RID"], enrolled["phase_key"]))
    phases = baselines["PHASE"].fillna("").str.strip().str.upper()
    baselines = baselines[[k in enrolled_keys for k in zip(baselines["RID"], phases)]].copy()
    clean = (clean_raw.sort_values(["RID", "date", "state"])
             .drop_duplicates(["RID", "date"]))
    baseline_dates = baselines[["RID", "date"]].rename(columns={"date": "baseline_date"})
    after = clean.merge(baseline_dates, on="RID", how="inner")
    after = after[after["date"].ge(after["baseline_date"])]
    followup = after.groupby("RID")["date"].max()
    baselines["followup_days"] = (
        baselines["RID"].map(followup) - baselines["date"]
    ).dt.total_seconds() / 86400

    cn = set(clean.loc[clean["state"].eq(1), "RID"])
    mci = set(clean.loc[clean["state"].eq(2), "RID"])
    cn_first = mci_first = 0
    for _, person in clean.groupby("RID", sort=False):
        seq = person["state"].tolist()
        if 1 in seq and 2 in seq:
            if seq.index(1) < seq.index(2):
                cn_first += 1
            else:
                mci_first += 1

    counts = {
        "raw_diagnosis_rows": len(dx),
        "valid_state_rows_before_date_filter": len(valid_state),
        "missing_date_valid_state_rows": int(valid_state["date"].isna().sum()),
        "failed_QC_rows_removed": qc_failed,
        "registry_failed_QC_records": int(rq.eq(1).sum()),
        "registry_dated_baseline_failed_QC_records": int(
            (rq.eq(1) & registry["date"].notna()
             & registry["VISCODE2"].eq("bl")).sum()),
        "conflicting_same_person_dates_removed": len(conflicting),
        "clean_unique_diagnosis_dates": len(clean),
        "clean_people_with_diagnosis": clean["RID"].nunique(),
        "clinical_baseline_before_registry_confirmation": before_registry,
        "confirmed_clinical_baseline_people": len(baselines),
        "baseline_state_counts": {
            str(int(k)): int(v) for k, v in baselines["state"].value_counts().sort_index().items()
        },
        "baseline_MCI_with_later_diagnosis": int(
            (baselines["state"].eq(2) & baselines["followup_days"].gt(0)).sum()),
        "baseline_CN_with_later_diagnosis": int(
            (baselines["state"].eq(1) & baselines["followup_days"].gt(0)).sum()),
        "ever_CN_people": len(cn),
        "ever_MCI_people": len(mci),
        "ever_CN_and_MCI_people": len(cn & mci),
        "CN_before_first_MCI_among_overlap": cn_first,
        "MCI_before_first_CN_among_overlap": mci_first,
        "source_genotyped_people": apoe["RID"].nunique(),
        "source_medical_history_people": medical["RID"].nunique(),
        "smoking_yes_records": int(medical["MH16SMOK"].eq("1").sum()),
        "smoking_unique_people_any_yes": medical.loc[medical["MH16SMOK"].eq("1"), "RID"].nunique(),
    }
    report = {
        "version": "1.2",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "ADNI only; aggregate source audit; no participant records exported",
        "baseline_rule": "Earliest dated clinical bl diagnosis, before duplicate-date collapse; enrollment confirmed by RID and PHASE in a dated REGISTRY baseline. Do not require later active status.",
        "diagnosis_rule": "Codes 1/2/3 only; missing dates excluded; explicit QC=1 removed; conflicting same-day diagnoses dropped rather than assigning worse state; exact duplicate same-day states collapsed.",
        "diagnosis_meaning": {"1": "cognitively unimpaired", "2": "MCI", "3": "clinical dementia, not automatically biomarker-confirmed AD"},
        "sources": [dx_source, registry_source, demog_source, medical_source, apoe_source],
        "counts": counts,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "source_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=2))
    print("Wrote aggregate source_audit.json; no participant rows exported.")


if __name__ == "__main__":
    main()
