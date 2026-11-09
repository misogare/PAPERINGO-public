"""G-109 selected evidence map and explicit monitoring resource scenarios.

This is a literature-audit artifact, not a fitted prediction model, economic
evaluation, or resolution of the optimal-scan-interval question. Source fields
below identify extracted records and distinguish them from PDF verification.
No research ledger or human gate is modified.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0"
OUTDIR = ROOT / "logs" / "gap_close_2026-11-01"
FIGDIR = ROOT / "figures"
EXAMPLES = (
    {
        "paper": "P-1153",
        "modality": "FDG-PET deep learning",
        "measurement_timing": "Not applicable: not a serial-scan comparison",
        "prediction_horizon": "Prognostic clinical follow-up; ADNI follow-up up to nine years, not a two-year rescan interval",
        "comparison": "Addition of FDG-PET deep-learning output to clinical and SUVR features, not addition of a second scan",
        "prior_extracted_result": "Delta iAUC 0.020 (95% CI 0.007 to 0.034)",
        "result_type": "Scope-excluded comparator: added baseline model feature, not serial-scan gain",
        "uncertainty": "95% CI 0.007 to 0.034 concerns the added deep-learning output",
        "source_doi": "https://doi.org/10.1093/radadv/umae021",
        "source_pdf_page": "PDF pp. 1, 5 and 7 (Table 3)",
        "source_record": "data/full_paper_registry.csv:1137; summaries.md:165514",
        "verification_status": "Primary PDF checked by coordinating audit agent",
        "interpretation": "Excluded from serial-scan evidence; prior table misclassified a model-feature increment as longitudinal imaging benefit",
    },
    {
        "paper": "P-974",
        "modality": "Amyloid PET (florbetaben)",
        "measurement_timing": "Variable: last follow-up SUVR minus baseline SUVR; 63 of 75 had second or third PET",
        "prediction_horizon": "Clinical follow-up mean 24 months (SD 9.3; range 11.6 to 42), not a fixed scan interval",
        "comparison": "Baseline amyloid measure versus change measure, not a nested baseline-plus-change incremental model",
        "prior_extracted_result": "AUC 0.652 longitudinal versus 0.596 baseline (difference 0.056); DeLong p=0.663",
        "result_type": "Discrimination comparison; no statistically demonstrated difference",
        "uncertainty": "Baseline AUC 95% CI 0.403 to 0.790; longitudinal AUC CI 0.484 to 0.820; these are separate AUC intervals, not a paired-difference interval",
        "source_doi": "https://doi.org/10.30773/pi.2022.0014",
        "source_pdf_page": "PDF pp. 4-5 (last-follow-up change definition and ROC comparison)",
        "source_record": "data/full_paper_registry.csv:959; summaries.md:147004",
        "verification_status": "Primary PDF checked by coordinating audit agent; measurement timing is variable",
        "interpretation": "No demonstrated difference does not prove equivalence or that serial PET adds nothing; 75 MCI participants and 17 converters",
    },
    {
        "paper": "P-527",
        "modality": "Resting-state fMRI connectivity",
        "measurement_timing": "Baseline, 12 months and 24 months",
        "prediction_horizon": "Two years in registry; ascertain prediction origin and post-scan outcome window from source",
        "comparison": "Multi-time-point connectivity with CNN/LSTM and internal cross-validation",
        "prior_extracted_result": "CNN+LSTM stable-versus-progressive MCI accuracy 74.1%, 74.8%, 75.5% at baseline, 12 and 24 months",
        "result_type": "Accuracy at a selected time point; not an isolated incremental gain",
        "uncertainty": "No paired incremental-gain confidence interval supplied in registry",
        "source_doi": "https://doi.org/10.3390/ijerph19084508",
        "source_pdf_page": "PDF pp. 2-3 and 12 (Table 2)",
        "source_record": "data/full_paper_registry.csv:522; summaries.md:44704",
        "verification_status": "Primary PDF checked by coordinating audit agent",
        "interpretation": "Multiple time points are present; cannot claim every selected example used just one interval",
    },
    {
        "paper": "P-945",
        "modality": "Clinical trajectories with MRI add-on (ADNI)",
        "measurement_timing": "12 months: change features defined as (month 12 minus baseline) divided by one year",
        "prediction_horizon": "Three-year clinical follow-up, distinct from the 12-month change measurement",
        "comparison": "Baseline clinical plus longitudinal clinical model A2 versus A2 plus MRI change; not first-scan-only versus serial imaging",
        "prior_extracted_result": "NRI 0.246 (95% CI 0.021 to 0.848); IDI 0.090 with inconsistent source intervals (both cross zero)",
        "result_type": "NRI, C-index and IDI are distinct metrics; no common AUC increment",
        "uncertainty": "Abstract IDI CI -0.062 to 0.170; body/Table 3 IDI CI -0.094 to 0.209. NRI CI 0.021 to 0.848. Source inconsistency must be retained; these are different metrics",
        "source_doi": "https://doi.org/10.1038/s41598-021-96914-3",
        "source_pdf_page": "PDF p. 1 (abstract IDI interval), p. 3 (change features), p. 5 (body and Table 3)",
        "source_record": "data/full_paper_registry.csv:930; summaries.md:143362",
        "verification_status": "Primary PDF timing and model contrasts checked by coordinating audit agent; IDI interval inconsistency recorded",
        "interpretation": "Prior table mislabeled the 36-month outcome follow-up as a scan interval",
    },
    {
        "paper": "P-967",
        "modality": "Structural MRI white-matter hyperintensity change (SPRINT MIND)",
        "measurement_timing": "Baseline and 48 months in registry",
        "prediction_horizon": "Incident dementia or MCI before the 48-month follow-up MRI",
        "comparison": "White-matter hyperintensity progression associated with incident cognitive outcome",
        "prior_extracted_result": "WMH-progression Z-score OR 1.51 (95% CI 1.12 to 2.04)",
        "result_type": "Association odds ratio, not an estimate of incremental discrimination or clinical utility",
        "uncertainty": "95% CI 1.12 to 2.04 describes the odds ratio; it is not an interval for predictive gain",
        "source_doi": "https://doi.org/10.1136/svn-2021-001357",
        "source_pdf_page": "PDF p. 1 (abstract temporal definition and OR)",
        "source_record": "data/full_paper_registry.csv:952; summaries.md:146114",
        "verification_status": "Primary PDF checked by coordinating audit agent",
        "interpretation": "Outcome was observed before follow-up MRI; an association over this period is not prospective predictive gain after ordering a second scan. 433 two-scan participants and 33 events",
    },
)


def followup_months(interval_months, horizon_months=60):
    """Scheduled followups after a month-zero baseline, including the horizon."""
    if not isinstance(interval_months, int) or interval_months <= 0:
        raise ValueError("The interval must be a positive integer number of months.")
    if not isinstance(horizon_months, int) or horizon_months < 0:
        raise ValueError("The horizon must be a nonnegative integer number of months.")
    return list(range(interval_months, horizon_months + 1, interval_months))


def snapshot_previous():
    """Preserve existing artifacts once, before replacing their audited versions."""
    backup = OUTDIR / "pre_audit_v1"
    backup.mkdir(parents=True, exist_ok=True)
    paths = [OUTDIR / "G-109_table.md", OUTDIR / "G-109_interval_ledger.csv"]
    paths += [FIGDIR / f"gap109_second_scan.{ext}" for ext in ("png", "pdf")]
    for path in paths:
        destination = backup / path.name
        if path.exists() and not destination.exists():
            shutil.copy2(path, destination)


def main():
    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)
    snapshot_previous()
    rows = [dict(row, document_version=VERSION, built_at_utc=built) for row in EXAMPLES]
    with (OUTDIR / "G-109_interval_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    schedule = []
    for interval in (6, 12, 24, 48):
        months = followup_months(interval)
        schedule.append({"interval_months": interval, "followup_months": ", ".join(map(str, months)),
                         "followup_scans": len(months), "total_scans_including_baseline": 1 + len(months),
                         "document_version": VERSION, "built_at_utc": built})
    with (OUTDIR / "G-109_schedule.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(schedule[0]))
        writer.writeheader()
        writer.writerows(schedule)

    lines = ["# ACTOR-G-109: selected evidence and planned scan counts", "",
             f"Document version: {VERSION}. Built at: {built}.", "",
             "Status: candidate / pending_human_review. No gap closure or human gate is asserted.", "",
             "This is a map of five selected examples from the prior analysis, not an exhaustive corpus search.",
             "The registry fields were inspected; primary PDF checks by the coordinating audit agent and remaining uncertainties are explicitly identified per row.",
             "Quoted results are extracted claims, not newly fitted models. They must not be pooled as a single predictive-gain measure.", "",
             "| Paper | Measurement timing | Outcome horizon | Extracted result | What the result can establish | Source record |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['paper']} | {r['measurement_timing']} | {r['prediction_horizon']} | "
                     f"{r['prior_extracted_result']} | {r['result_type']} | {r['source_record']} |")
    lines += ["", "Source and uncertainty qualifications:", ""]
    for r in rows:
        lines += [f"- **{r['paper']}**: {r['interpretation']} {r['uncertainty']} "
                  f"Verification: {r['verification_status']}. DOI: {r['source_doi'] or 'not verified in this module'}. "
                  f"PDF/page: {r['source_pdf_page']}."]
    lines += ["", "## Planned resource counts", "",
              "Assumption: everyone receives a baseline scan at month 0 and attends each scheduled follow-up at integer multiples of the interval, through and including month 60.",
              "The number of additional scans is floor(60 / interval); these counts assume no dropout, event-triggered stopping or unscheduled visits.",
              "They are resource scenarios, not observed utilisation, monetary costs, benefits or clinical recommendations.", "",
              "| Interval | Follow-up months | Additional scans | Total including baseline |",
              "|---|---|---|---|"]
    for r in schedule:
        lines.append(f"| {r['interval_months']} months | {r['followup_months']} | {r['followup_scans']} | {r['total_scans_including_baseline']} |")
    lines += ["", "## What remains open", "",
              "These examples do not establish an optimal interval. P-1153 is excluded from serial-scan evidence; the four remaining examples do not form four comparable experiments. Association, a change in NRI, and an AUC point difference answer different questions.",
              "A direct evaluation needs comparable participants, modality, prediction target and threshold; a prespecified prediction origin at each landmark; and an outcome window entirely after the scans used for prediction.",
              "Split participants before fitting preprocessing and models; compare first-scan-only and serial-change models on the same held-out people, with uncertainty for their paired gain.",
              "At later landmarks, account for earlier conversion, dropout and the resulting change in who remains eligible. Evaluate clinical utility and visit burden before choosing a schedule.", ""]
    (OUTDIR / "G-109_table.md").write_text("\n".join(lines), encoding="utf-8")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    intervals = [r["interval_months"] for r in schedule]
    counts = [r["followup_scans"] for r in schedule]
    bars = ax.bar([str(i) for i in intervals], counts, color="#427a9d", width=0.6)
    for bar, count in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, count + 0.15, str(count), ha="center")
    ax.set_ylim(0, 11.5)
    ax.set_xlabel("Scheduled interval between scans (months)")
    ax.set_ylabel("Additional scans over 60 months")
    ax.set_title("G-109: planned scan counts, not an optimal interval", loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=0.2)
    fig.text(0.12, 0.075, "Month-0 baseline; all visits at interval multiples through month 60; no dropout.\nThese resource scenarios do not estimate clinical benefit or cost effectiveness.", fontsize=8)
    fig.text(0.12, 0.015, f"Version {VERSION} | {built}", fontsize=7, color="#62676e")
    fig.tight_layout(rect=(0, 0.17, 1, 1))
    for extension in ("png", "pdf"):
        fig.savefig(FIGDIR / f"gap109_second_scan.{extension}", dpi=300)
    plt.close(fig)
    print(f"G-109 v{VERSION}: {len(rows)} selected evidence examples, not an exhaustive audit.")
    print("Planned followup scan counts (month-zero baseline, inclusive 60-month horizon): 10, 5, 2, 1.")
    print("No best interval, clinical utility or gap closure inferred. Source-check qualifications retained.")
    return rows


if __name__ == "__main__":
    main()
