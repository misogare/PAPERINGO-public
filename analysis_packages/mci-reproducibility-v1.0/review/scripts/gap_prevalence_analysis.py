"""Conditional predictive values at published population prevalence by age.

Two source-checked MCI diagnostic operating points are held fixed while assumed
prevalence takes the three values the AAN evidence synthesis reports for the
age bands 65-69, 75-79 and 80-84 (Petersen et al. 2018). These are general
population rates, not the case mix of a memory clinic, and the arithmetic does
not show that sensitivity and specificity transport between settings.

These are conditional calculations, not deployment validation or gap closure.
No confusion-matrix counts or confidence intervals are inferred from rounded
percentages. The original study's operating point is held fixed in each scenario.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.1"
SCENARIOS = (0.084, 0.148, 0.252)
SCENARIO_META = {
    0.084: {"band": "ages 65-69", "ci": "5.2-13.4"},
    0.148: {"band": "ages 75-79", "ci": "10.1-21.1"},
    0.252: {"band": "ages 80-84", "ci": "16.5-36.5"},
}
SCENARIO_SOURCE = ("Petersen et al. 2018, AAN practice-guideline random-effects "
                   "meta-analysis, general-population prevalence")
EXAMPLES = (
    {
        "paper": "P-1232",
        "marker": "Plasma exosomal three-lncRNA panel",
        "target": "MCI versus healthy controls",
        "sensitivity": 0.720,
        "specificity": 0.607,
        "cutoff": "Not specified in the main-text operating-point passage",
        "threshold_selection": "Reported fitted-model ROC operating point; not prospectively prespecified",
        "source_doi": "https://doi.org/10.1016/j.jad.2024.11.029",
        "source_pdf": "downloads/3. Molecular, Genetic and Fluid Biomarkers/1-s2.0-S0165032724018627-main.pdf",
        "source_page": "PDF p. 5 (journal p. 405), section 3.6",
        "source_group_note": "310 enrolled (155 MCI, 155 controls); section 3.5 reports qPCR on 150 MCI and 150 controls; preceding discovery counts conflict",
    },
    {
        "paper": "P-1218",
        "marker": "Plasma p-tau181 impedance biosensor",
        "target": "MCI versus healthy controls",
        "sensitivity": 0.700,
        "specificity": 0.700,
        "cutoff": "11.12 pg/mL",
        "threshold_selection": "Source ROC-selected cutoff; not prospectively prespecified",
        "source_doi": "https://doi.org/10.1016/j.aca.2023.341535",
        "source_pdf": "downloads/3. Molecular, Genetic and Fluid Biomarkers/1-s2.0-S0003267023007560-main.pdf",
        "source_page": "PDF/journal p. 7, section 3.5 and Figure 6e",
        "source_group_note": "MCI comparison uses 20 MCI and 20 healthy controls; the separate AD comparison is excluded",
    },
)


def transport(sensitivity, specificity, prevalence):
    """Expected cells under fixed sensitivity/specificity, with Bayes PPV/NPV."""
    if not all(0 <= v <= 1 for v in (sensitivity, specificity, prevalence)):
        raise ValueError("Sensitivity, specificity and prevalence must be in [0, 1].")
    population = 1000
    tp = population * prevalence * sensitivity
    fn = population * prevalence * (1 - sensitivity)
    fp = population * (1 - prevalence) * (1 - specificity)
    tn = population * (1 - prevalence) * specificity
    return {
        "tp_per_1000": tp,
        "fn_per_1000": fn,
        "fp_per_1000": fp,
        "tn_per_1000": tn,
        "ppv": tp / (tp + fp) if tp + fp else float("nan"),
        "npv": tn / (tn + fn) if tn + fn else float("nan"),
        "false_positives_per_true_positive": fp / tp if tp else float("inf"),
    }


def build(outdir=None, figdir=None, built_at=None):
    """Write the auditable table, complete expected cells, and scenario figure."""
    outdir = Path(outdir) if outdir else ROOT / "logs" / "gap_close_2026-11-01"
    figdir = Path(figdir) if figdir else ROOT / "figures"
    outdir.mkdir(parents=True, exist_ok=True)
    figdir.mkdir(parents=True, exist_ok=True)
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for example in EXAMPLES:
        for prevalence in SCENARIOS:
            rows.append({
                "document_version": VERSION,
                "built_at_utc": built_at,
                **example,
                "scenario_prevalence": prevalence,
                "scenario_age_band": SCENARIO_META[prevalence]["band"],
                "scenario_95ci": SCENARIO_META[prevalence]["ci"],
                "scenario_basis": SCENARIO_SOURCE,
                **transport(example["sensitivity"], example["specificity"], prevalence),
            })
    with (outdir / "G-102_prevalence.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    young_1232, mid_1232, old_1232 = rows[0], rows[1], rows[2]
    young_1218 = rows[3]
    spread = (young_1232["false_positives_per_true_positive"]
              / old_1232["false_positives_per_true_positive"])

    lines = [
        "# ACTOR-G-102: conditional prevalence analysis",
        "",
        f"Document version: {VERSION} | Built: {built_at} (UTC)",
        "",
        "Status: illustrative secondary analysis; the empirical deployment gap remains open pending human review.",
        "",
        "Two source-checked MCI diagnostic examples are shown. This is not an exhaustive corpus analysis.",
        "The published sensitivity, specificity and operating point are held fixed while prevalence changes.",
        "The original thresholds were not prospectively prespecified. Fixing them for this arithmetic does not remove selection optimism.",
        "",
        "## What the three prevalences are, and are not",
        "",
        "The three assumed prevalences are the general-population MCI prevalences the AAN evidence synthesis",
        "reports for three age bands, from a random-effects meta-analysis of 20 Class I and 14 Class II studies",
        "([Petersen et al., 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC5772157/)):",
        "8.4% at ages 65-69 (95% CI 5.2-13.4), 14.8% at ages 75-79 (95% CI 10.1-21.1) and",
        "25.2% at ages 80-84 (95% CI 16.5-36.5). The same synthesis reports 6.7% at ages 60-64,",
        "so prevalence roughly quadruples across the bands it covers.",
        "",
        "These are population rates: the share of everyone in that age band who meets MCI criteria, most of whom",
        "have never been assessed. They are the correct denominator for a screening claim offered to everyone in",
        "an age band, which is what the blood-marker papers describe. They are NOT the case mix of a memory",
        "clinic, where people have been referred because of a complaint and the share with MCI is higher.",
        "No study in the corpus supplies a memory-clinic denominator, so no clinic-specific predictive value is",
        "calculated here. The 8.4-25.2% range is the published anchor; the clinic end is unidentified.",
        "",
        "The corpus reports no age-banded MCI prevalence figure of its own. One corpus paper quotes this same",
        "guideline rather than measuring prevalence, so the anchor is external and is labelled as such everywhere",
        "it appears.",
        "",
        "## Inputs checked against the original PDFs",
        "",
        "| Paper | Index test and target | Sensitivity / specificity | Operating point | Primary source |",
        "|---|---|---|---|---|",
    ]
    for example in EXAMPLES:
        lines.append(
            f"| {example['paper']} | {example['marker']}; {example['target']} | "
            f"{example['sensitivity']:.3f} / {example['specificity']:.3f} | {example['cutoff']} | "
            f"[Study]({example['source_doi']}), {example['source_page']} |"
        )
    lines += [
        "",
        "No original 2x2 table is claimed to have been recovered. P-1232 reports qPCR on 150 MCI and 150 controls,",
        "with inconsistent preceding discovery-sample counts. Its 155-per-group enrollment is not used to construct",
        "a confidence interval for its ROC operating point.",
        "",
        "## Expected results per 1,000 screened",
        "",
        "| Paper | Age band | Prevalence (95% CI) | TP | FN | FP | TN | PPV | NPV | FP per TP |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['paper']} | {row['scenario_age_band']} | {row['scenario_prevalence']:.1%} "
            f"({row['scenario_95ci']}) | {row['tp_per_1000']:.2f} | "
            f"{row['fn_per_1000']:.2f} | {row['fp_per_1000']:.2f} | {row['tn_per_1000']:.2f} | "
            f"{row['ppv']:.4f} | {row['npv']:.4f} | {row['false_positives_per_true_positive']:.2f} |"
        )
    lines += [
        "",
        "Calculations: TP=N*p*Se; FN=N*p*(1-Se); FP=N*(1-p)*(1-Sp); TN=N*(1-p)*Sp;",
        "PPV=TP/(TP+FP); NPV=TN/(TN+FN); false positives per true positive=FP/TP.",
        "Fractional cells are expected counts, not observed people. The CSV retains unrounded calculation values.",
        "",
        f"At 8.4% (ages 65-69), P-1232 gives PPV {young_1232['ppv']:.4f}, NPV {young_1232['npv']:.4f} and "
        f"{young_1232['false_positives_per_true_positive']:.2f} false positives per true positive.",
        f"At 25.2% (ages 80-84) the same operating point gives PPV {old_1232['ppv']:.4f}, "
        f"NPV {old_1232['npv']:.4f} and {old_1232['false_positives_per_true_positive']:.2f}.",
        f"The false-alarm-to-case ratio therefore falls by a factor of {spread:.2f} across the age bands",
        "the guideline covers, with no change to the test. P-1218 at 8.4% gives PPV "
        f"{young_1218['ppv']:.4f}, NPV {young_1218['npv']:.4f} and "
        f"{young_1218['false_positives_per_true_positive']:.2f} false positives per true positive.",
        "The values illustrate the base-rate effect. They do not measure either marker's actual performance",
        "in a new population, and the apparent ordering of the two markers reverses on the smallest",
        "difference in specificity, which is not estimable from the rounded operating points.",
        "",
        "Changing prevalence alone assumes that sensitivity and specificity transport unchanged despite differences in",
        "age, disease spectrum, comorbidity, assay handling and reference standards. That assumption needs validation.",
        "No sampling confidence interval, prevalence uncertainty interval, net-benefit threshold or cost-effectiveness result is estimated.",
        "A low PPV alone does not establish that triage is clinically useless or that the test has failed.",
        "",
        "The registered closure criterion also requires a suitable study/denominator, an externally sourced target-setting prevalence",
        "and a prespecified threshold. This exercise supplies conditional arithmetic and cannot certify full closure.",
        "P-939 is not used as a performance benchmark: its reported 0.169 PPV concerns incident MCI-or-dementia in the",
        "SLAS-1 development cohort, with an internally inconsistent reported sensitivity/specificity/incidence combination.",
        "See `G-102_audit.md` for the excluded/miscoded records and limitations.",
        "",
        "Generated by `scripts/gap_prevalence_analysis.py`.",
        "",
    ]
    (outdir / "G-102_table.md").write_text("\n".join(lines), encoding="utf-8")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.8))
    prevalences = [i / 1000 for i in range(1, 501)]
    colors = ("#2a78d6", "#eb6834")
    for example, color in zip(EXAMPLES, colors):
        values = [transport(example["sensitivity"], example["specificity"], p) for p in prevalences]
        for ax, key in zip(axes, ("ppv", "npv")):
            ax.plot([p * 100 for p in prevalences], [v[key] for v in values],
                    color=color, label=example["paper"], linewidth=1.7)
            ax.scatter([p * 100 for p in SCENARIOS],
                       [transport(example["sensitivity"], example["specificity"], p)[key] for p in SCENARIOS],
                       color=color, s=22, zorder=3)
    ticks = [p * 100 for p in SCENARIOS]
    ticklabels = [f"{SCENARIO_META[p]['band'].replace('ages ', '')}\n{p:.1%}" for p in SCENARIOS]
    for ax, title in zip(axes, ("Positive predictive value", "Negative predictive value")):
        ax.set_title(title, loc="left")
        ax.set_xlabel("MCI prevalence in the screened population (%)")
        ax.set_ylabel("Predictive value at that prevalence")
        ax.set_xticks(ticks)
        ax.set_xticklabels(ticklabels, fontsize=8)
        ax.set_xlim(0, 40)
        ax.set_ylim(0, 1)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.2)
    axes[0].legend(frameon=False, loc="upper left")
    fig.suptitle("G-102: predictive values at published population prevalence by age", fontsize=10)
    fig.text(0.01, 0.005,
             f"v{VERSION} | {built_at} UTC | Petersen et al. 2018 age-band prevalence; sensitivity/specificity held fixed | "
             "general population, not a memory-clinic case mix",
             fontsize=6.2)
    fig.tight_layout(rect=(0, 0.09, 1, 0.93))
    for extension in ("png", "pdf"):
        fig.savefig(figdir / f"gap102_prevalence_transport.{extension}", dpi=300)
    plt.close(fig)
    return rows


if __name__ == "__main__":
    results = build()
    print(f"G-102 v{VERSION}: {len(EXAMPLES)} verified examples, {len(results)} age-band scenario rows; "
          "deployment gap remains open.")
