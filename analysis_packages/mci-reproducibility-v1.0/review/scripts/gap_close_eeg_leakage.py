"""Corrected evidence audit for ACTOR-G-026, version 2.0.

Published protocol differences describe their own experiments. They are not
correction factors for other classifiers. Four locally checked corpus examples
are recorded without pooling accuracies or claiming exhaustive EEG coverage.

Run: .venv/Scripts/python.exe scripts/gap_close_eeg_leakage.py
Writes G-026_table.md, G-026_evidence.csv, a withdrawal notice in the legacy
G-026_projection.csv, and figures/gap026_leakage.{png,pdf}.
No registry, summary, ledger, manuscript or human gate is modified.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

VERSION = "2.0"
ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "logs" / "gap_close_2026-11-01"
FIGDIR = ROOT / "figures"

CORPUS_EXAMPLES = [
    {
        "paper": "P-31", "task": "MCI vs HC", "n": 27,
        "accuracy": 99.95, "uncertainty": "uncertainty not transcribed in this audit",
        "protocol": "10-fold CV over segment-expanded data; subject grouping not reported",
        "location": "PDF p. 5 (9,720 segments); p. 7 (accuracy)",
        "url": "https://doi.org/10.1016/j.cmpb.2024.108123",
        "note": "9,720 segments are not 9,720 independent people. Original-model subject-wise reproduction remains unresolved.",
    },
    {
        "paper": "P-70", "task": "MCI vs HC", "n": 27,
        "accuracy": 95.51, "uncertainty": "SD 3.11 percentage points",
        "protocol": "5-fold CV; cited performance table does not establish subject grouping",
        "location": "PDF p. 7, Table 2",
        "url": "https://doi.org/10.1016/j.knosys.2022.108815",
        "note": "Same 27-person resting-state dataset as P-31. Original-model subject-wise reproduction remains unresolved.",
    },
    {
        "paper": "P-129", "task": "MCI vs HC", "n": 61,
        "accuracy": 90.97, "uncertainty": "SD 0.60 percentage points",
        "protocol": "Nested cross-validation with a cross-subject validation scheme",
        "location": "PDF p. 1 (abstract); p. 7, Table 2",
        "url": "https://doi.org/10.1016/j.bspc.2026.109564",
        "note": "94.76% (SD 0.78) is specificity, not accuracy. Registry Acc 94.76 is an extraction error; this audit leaves that source record unchanged.",
    },
    {
        "paper": "P-557", "task": "MCI vs healthy cognitive aging", "n": 23,
        "accuracy": 95.63, "uncertainty": "uncertainty not transcribed in this audit",
        "protocol": "Leave-one-out-subject cross-validation (LOOSCV)",
        "location": "PDF p. 5 (LOOSCV); p. 7, Table 1 (accuracy)",
        "url": "https://doi.org/10.3389/fnagi.2023.1294139",
        "note": "Earlier split-unit label was wrong. Small single-site pilot, different paradigm from resting-state P-31/P-70.",
    },
]

# Two matched architecture/cohort evaluations, with different clinical tasks.
MATCHED_COMPARISONS = [
    {
        "source": "Brookshire et al. 2024",
        "task": "AD vs subjective cognitive impairment", "n": 98,
        "segment": 99.80, "subject": 53.00,
        "uncertainty": "95% CI: segment 99.1-100.0%; subject 43.1-64.8%",
        "location": "Sections 2.2, 2.4, 3.1.1; Figure 2A; caveats in 4.4",
        "url": "https://doi.org/10.3389/fnins.2024.1373515",
        "note": "49 AD and 49 subjective-impairment participants. Different task and cohort from MCI studies; bias magnitude depends on experiment and architecture.",
    },
    {
        "source": "Tokdar et al. 2026", "task": "MCI vs HC", "n": 27,
        "segment": 97.40, "subject": 56.00,
        "uncertainty": "Across-fold SD: segment 2.2; subject 16.1 percentage points",
        "location": "Sections 2.1.1, 2.3.5, 3.1.1-3.1.2; Tables 3-4",
        "url": "https://doi.org/10.1186/s40708-026-00302-4",
        "note": "Same 27-person Isfahan dataset (16 HC, 11 MCI) as P-31/P-70; different CNN-Res-SE-BiLSTM-BiGRU architecture. Validation-selected Youden threshold; grouping covers train/validation/test.",
    },
]

BETWEEN_STUDY = {
    "source": "Miltiadous et al. 2026", "task": "AD vs cognitively normal",
    "weaker": 97.07, "strict": 82.11, "weaker_n": 19, "strict_n": 13,
    "uncertainty": "Between-study SD: Validity 3, 3.09; Validity 1, 6.93 percentage points",
    "location": "Performance on AD vs. CN; Figure 6; validity definitions in Methodology",
    "url": "https://doi.org/10.1007/s11571-026-10464-w",
    "note": "Different studies using AHEPA, not a paired model experiment. Validity 3 includes epoch splitting or non-nested feature/channel selection; all 19 are not verified subject-overlap cases.",
}


def evidence_rows(built_utc):
    rows = []
    for r in CORPUS_EXAMPLES:
        rows.append({
            "evidence_type": "corpus_example", "source": r["paper"],
            "task": r["task"], "n_subjects": r["n"], "metric": "Accuracy",
            "reported_percent": r["accuracy"], "comparator_percent": "",
            "within_study_drop_pp": "", "between_study_difference_pp": "",
            "protocol": r["protocol"], "uncertainty": r["uncertainty"],
            "source_location": r["location"], "source_url": r["url"],
            "note": r["note"], "version": VERSION, "built_utc": built_utc,
        })
    for r in MATCHED_COMPARISONS:
        rows.append({
            "evidence_type": "matched_protocol_comparison", "source": r["source"],
            "task": r["task"], "n_subjects": r["n"], "metric": "Accuracy",
            "reported_percent": r["segment"], "comparator_percent": r["subject"],
            "within_study_drop_pp": round(r["segment"] - r["subject"], 2),
            "between_study_difference_pp": "",
            "protocol": "Segment-wise versus subject-wise, same architecture/cohort",
            "uncertainty": r["uncertainty"], "source_location": r["location"],
            "source_url": r["url"], "note": r["note"],
            "version": VERSION, "built_utc": built_utc,
        })
    r = BETWEEN_STUDY
    rows.append({
        "evidence_type": "between_study_association", "source": r["source"],
        "task": r["task"], "n_subjects": "", "metric": "Mean accuracy across reports",
        "reported_percent": r["weaker"], "comparator_percent": r["strict"],
        "within_study_drop_pp": "",
        "between_study_difference_pp": round(r["weaker"] - r["strict"], 2),
        "protocol": "Validity 3 versus Validity 1; different studies, same AHEPA data",
        "uncertainty": r["uncertainty"], "source_location": r["location"],
        "source_url": r["url"], "note": r["note"],
        "version": VERSION, "built_utc": built_utc,
    })
    return rows


def validate_evidence(rows):
    """Guard against the metric and study-design errors this audit corrected."""
    by_source = {r["source"]: r for r in rows}
    assert by_source["P-129"]["reported_percent"] == 90.97
    assert "specificity" in by_source["P-129"]["note"]
    assert "LOOSCV" in by_source["P-557"]["protocol"]
    assert by_source["Brookshire et al. 2024"]["within_study_drop_pp"] == 46.8
    tokdar = by_source["Tokdar et al. 2026"]
    assert tokdar["task"] == "MCI vs HC" and tokdar["within_study_drop_pp"] == 41.4
    review = by_source["Miltiadous et al. 2026"]
    assert review["within_study_drop_pp"] == ""
    assert review["between_study_difference_pp"] == 14.96
    assert not any("projected_" in key for row in rows for key in row)


def write_outputs(rows, built_utc):
    with (OUTDIR / "G-026_evidence.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # Preserve the old link while preventing reuse of invented adjusted values.
    withdrawal = {
        "status": "withdrawn",
        "reason": "External study differences do not estimate other models' accuracy; prior metric and validation labels were also wrong.",
        "replacement": "G-026_evidence.csv and G-026_table.md",
        "version": VERSION, "built_utc": built_utc,
    }
    with (OUTDIR / "G-026_projection.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(withdrawal))
        writer.writeheader()
        writer.writerow(withdrawal)

    lines = [
        "# ACTOR-G-026: corrected EEG validation evidence audit", "",
        f"**Version {VERSION} | Built {built_utc} | Candidate audit, pending human review**", "",
        "The earlier adjusted-accuracy calculations are withdrawn. Published differences describe",
        "their own experiments and cannot be subtracted from another model's accuracy. This audit",
        "does not rerun a model, pool corpus accuracies, or change a workflow gate.", "",
        "## Four checked corpus examples", "",
        "These examples were checked against local PDFs; they are not an exhaustive EEG inventory.", "",
        "| Paper | People | Accuracy | Protocol | Source location | Audit note |",
        "|---|---:|---|---|---|---|",
    ]
    for r in CORPUS_EXAMPLES:
        lines.append(
            f"| [{r['paper']}]({r['url']}) | {r['n']} | {r['accuracy']:.2f}%; "
            f"{r['uncertainty']} | {r['protocol']} | {r['location']} | {r['note']} |")
    lines += [
        "", "## Matched protocol evaluations", "",
        "Each row compares one architecture on the same cohort under two split protocols.",
        "Subject-wise means people are separated between splits; it does not establish that",
        "the metric represents one aggregated clinical decision per person.", "",
        "| Source | Task and cohort | Segment-wise accuracy | Subject-wise accuracy | Within-study difference | Uncertainty and source |",
        "|---|---|---:|---:|---:|---|",
    ]
    for r in MATCHED_COMPARISONS:
        lines.append(
            f"| [{r['source']}]({r['url']}) | {r['task']}; N={r['n']} | "
            f"{r['segment']:.2f}% | {r['subject']:.2f}% | "
            f"{r['segment'] - r['subject']:.2f} percentage points | "
            f"{r['uncertainty']}; {r['location']} |")
    r = BETWEEN_STUDY
    lines += [
        "", "Tokdar's comparison already exists on the exact public 27-person resting-state cohort",
        "used by P-31/P-70, but uses a different architecture: neither original model is reproduced.",
        "Its uncertainty values are fold standard deviations, not confidence intervals.",
        "The cohort contains 16 HC and 11 MCI participants: majority-class accuracy is",
        f"{100 * 16 / 27:.2f}%; 50% describes uniform random binary guessing. Accuracy can fall",
        "below either baseline; neither is a permissible hard floor for inferred performance.", "",
        "## Between-study association, reported separately", "",
        f"[{r['source']}]({r['url']}) reports AD-versus-CN mean accuracy of {r['weaker']:.2f}%",
        f"in {r['weaker_n']} Validity-3 reports and {r['strict']:.2f}% in {r['strict_n']} Validity-1 reports",
        f"(difference {r['weaker'] - r['strict']:.2f} percentage points). {r['uncertainty']}.",
        f"Source: {r['location']}. {r['note']}", "",
        "This is not a measured within-model loss, lower bound, correction factor, or causal",
        "estimate of the effect of changing a split protocol.", "",
        "## What remains unresolved", "",
        "Published MCI evidence already demonstrates a substantial protocol difference on this",
        "dataset. The remaining original-model question is narrower: independently reproduce",
        "P-31 STCGRU and/or P-70 GRU, then compare segment-wise and subject-wise protocols,",
        "with data-dependent preprocessing, selection, calibration and thresholding confined",
        "to training/validation subjects. Report subject-level uncertainty and appropriate",
        "baselines. This audit completes neither replication nor the human gap-review gate.", "",
        "A cross-study maximum is not a performance bound. Cohorts, paradigms, class balances",
        "and metrics differ; an unadjusted comparison cannot isolate leakage or rule out an",
        "architectural improvement. Unstated grouping is uncertainty, not proven leakage.", "",
        "## Figure and files", "",
        "figures/gap026_leakage.png and .pdf show (a) matched protocol comparisons and",
        "(b) the separate Miltiadous between-study association. Points/bars reproduce published",
        "accuracies; no adjusted corpus accuracy is shown. Source uncertainty is tabulated",
        "above. The legacy projection CSV contains a withdrawal notice; G-026_evidence.csv",
        "holds the corrected evidence. Original v1 files are retained in pre_audit_v1/.", "",
        "Generated by scripts/gap_close_eeg_leakage.py.", "",
    ]
    (OUTDIR / "G-026_table.md").write_text("\n".join(lines), encoding="utf-8")


def figure(built_utc):
    blue, orange, grey, ink = "#2a78d6", "#eb6834", "#bfc5ca", "#222222"
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "figure.facecolor": "white", "axes.facecolor": "white",
    })
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(8.8, 3.9), gridspec_kw={"width_ratios": [1.35, 1]})
    for y, r in zip([1, 0], MATCHED_COMPARISONS):
        left.plot([r["subject"], r["segment"]], [y, y], color=grey, linewidth=2)
        left.scatter(r["segment"], y, color=blue, s=35, zorder=2,
                     label="Segment-wise" if y == 1 else None)
        left.scatter(r["subject"], y, color=orange, s=35, zorder=2,
                     label="Subject-wise" if y == 1 else None)
        for value in [r["subject"], r["segment"]]:
            left.annotate(f"{value:.1f}%", (value, y), xytext=(0, 9),
                          textcoords="offset points", ha="center", fontsize=8)
    left.set_yticks([1, 0], [
        "Brookshire 2024\nAD vs subjective impairment",
        "Tokdar 2026\nMCI vs HC; 27-person cohort"])
    left.set_xlim(35, 105)
    left.set_ylim(-0.55, 1.6)
    left.set_xlabel("Reported accuracy (%)")
    left.set_title("(a) Matched protocol evaluations", loc="left",
                   fontsize=9, fontweight="bold")
    left.legend(frameon=False, loc="upper center", ncol=2, fontsize=7)
    left.grid(axis="x", alpha=0.18)
    left.set_axisbelow(True)

    r = BETWEEN_STUDY
    bars = right.bar([0, 1], [r["weaker"], r["strict"]],
                     color=[grey, blue], width=0.55)
    right.bar_label(bars, labels=[f"{r['weaker']:.2f}%", f"{r['strict']:.2f}%"],
                    padding=4, fontsize=8)
    right.set_xticks([0, 1], ["Validity 3\n19 reports", "Validity 1\n13 reports"])
    right.set_ylim(0, 112)
    right.set_ylabel("Mean reported accuracy (%)")
    right.set_title("(b) Between-study association", loc="left",
                    fontsize=9, fontweight="bold")
    right.text(0.5, 0.06, "Miltiadous 2026; AHEPA, AD vs CN\nNot a within-model loss",
               transform=right.transAxes, ha="center", fontsize=7, color=ink)
    right.grid(axis="y", alpha=0.18)
    right.set_axisbelow(True)
    fig.suptitle("ACTOR-G-026: published EEG validation evidence",
                 fontsize=10, fontweight="bold", x=0.02, ha="left")
    fig.text(0.02, 0.015,
             f"Evidence audit v{VERSION} | {built_utc} | No corpus model rerun or accuracy adjustment",
             fontsize=6.6, color="#555555")
    fig.tight_layout(rect=(0, 0.07, 1, 0.93), w_pad=2)
    for ext in ("png", "pdf"):
        fig.savefig(FIGDIR / f"gap026_leakage.{ext}", dpi=300)
    plt.close(fig)


def main():
    built_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)
    rows = evidence_rows(built_utc)
    validate_evidence(rows)
    write_outputs(rows, built_utc)
    figure(built_utc)
    print(f"ACTOR-G-026 evidence audit v{VERSION}, built {built_utc}")
    print("Four checked corpus examples; P-129 accuracy and P-557 split labels corrected.")
    print("Matched differences: Brookshire 46.80 pp; Tokdar 41.40 pp.")
    print("Separate between-study association: Miltiadous 14.96 pp.")
    print("Legacy adjustments withdrawn. Original P-31/P-70 replication unresolved.")
    print("Wrote G-026_table.md, G-026_evidence.csv, withdrawal CSV, PNG/PDF.")


if __name__ == "__main__":
    main()
