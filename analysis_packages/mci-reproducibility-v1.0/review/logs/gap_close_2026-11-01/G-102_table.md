# ACTOR-G-102: conditional prevalence analysis

Document version: 2.1 | Built: 2026-11-02T02:48:40+00:00 (UTC)

Status: illustrative secondary analysis; the empirical deployment gap remains open pending human review.

Two source-checked MCI diagnostic examples are shown. This is not an exhaustive corpus analysis.
The published sensitivity, specificity and operating point are held fixed while prevalence changes.
The original thresholds were not prospectively prespecified. Fixing them for this arithmetic does not remove selection optimism.

## What the three prevalences are, and are not

The three assumed prevalences are the general-population MCI prevalences the AAN evidence synthesis
reports for three age bands, from a random-effects meta-analysis of 20 Class I and 14 Class II studies
([Petersen et al., 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC5772157/)):
8.4% at ages 65-69 (95% CI 5.2-13.4), 14.8% at ages 75-79 (95% CI 10.1-21.1) and
25.2% at ages 80-84 (95% CI 16.5-36.5). The same synthesis reports 6.7% at ages 60-64,
so prevalence roughly quadruples across the bands it covers.

These are population rates: the share of everyone in that age band who meets MCI criteria, most of whom
have never been assessed. They are the correct denominator for a screening claim offered to everyone in
an age band, which is what the blood-marker papers describe. They are NOT the case mix of a memory
clinic, where people have been referred because of a complaint and the share with MCI is higher.
No study in the corpus supplies a memory-clinic denominator, so no clinic-specific predictive value is
calculated here. The 8.4-25.2% range is the published anchor; the clinic end is unidentified.

The corpus reports no age-banded MCI prevalence figure of its own. One corpus paper quotes this same
guideline rather than measuring prevalence, so the anchor is external and is labelled as such everywhere
it appears.

## Inputs checked against the original PDFs

| Paper | Index test and target | Sensitivity / specificity | Operating point | Primary source |
|---|---|---|---|---|
| P-1232 | Plasma exosomal three-lncRNA panel; MCI versus healthy controls | 0.720 / 0.607 | Not specified in the main-text operating-point passage | [Study](https://doi.org/10.1016/j.jad.2024.11.029), PDF p. 5 (journal p. 405), section 3.6 |
| P-1218 | Plasma p-tau181 impedance biosensor; MCI versus healthy controls | 0.700 / 0.700 | 11.12 pg/mL | [Study](https://doi.org/10.1016/j.aca.2023.341535), PDF/journal p. 7, section 3.5 and Figure 6e |

No original 2x2 table is claimed to have been recovered. P-1232 reports qPCR on 150 MCI and 150 controls,
with inconsistent preceding discovery-sample counts. Its 155-per-group enrollment is not used to construct
a confidence interval for its ROC operating point.

## Expected results per 1,000 screened

| Paper | Age band | Prevalence (95% CI) | TP | FN | FP | TN | PPV | NPV | FP per TP |
|---|---|---|---|---|---|---|---|---|---|
| P-1232 | ages 65-69 | 8.4% (5.2-13.4) | 60.48 | 23.52 | 359.99 | 556.01 | 0.1438 | 0.9594 | 5.95 |
| P-1232 | ages 75-79 | 14.8% (10.1-21.1) | 106.56 | 41.44 | 334.84 | 517.16 | 0.2414 | 0.9258 | 3.14 |
| P-1232 | ages 80-84 | 25.2% (16.5-36.5) | 181.44 | 70.56 | 293.96 | 454.04 | 0.3817 | 0.8655 | 1.62 |
| P-1218 | ages 65-69 | 8.4% (5.2-13.4) | 58.80 | 25.20 | 274.80 | 641.20 | 0.1763 | 0.9622 | 4.67 |
| P-1218 | ages 75-79 | 14.8% (10.1-21.1) | 103.60 | 44.40 | 255.60 | 596.40 | 0.2884 | 0.9307 | 2.47 |
| P-1218 | ages 80-84 | 25.2% (16.5-36.5) | 176.40 | 75.60 | 224.40 | 523.60 | 0.4401 | 0.8738 | 1.27 |

Calculations: TP=N*p*Se; FN=N*p*(1-Se); FP=N*(1-p)*(1-Sp); TN=N*(1-p)*Sp;
PPV=TP/(TP+FP); NPV=TN/(TN+FN); false positives per true positive=FP/TP.
Fractional cells are expected counts, not observed people. The CSV retains unrounded calculation values.

At 8.4% (ages 65-69), P-1232 gives PPV 0.1438, NPV 0.9594 and 5.95 false positives per true positive.
At 25.2% (ages 80-84) the same operating point gives PPV 0.3817, NPV 0.8655 and 1.62.
The false-alarm-to-case ratio therefore falls by a factor of 3.67 across the age bands
the guideline covers, with no change to the test. P-1218 at 8.4% gives PPV 0.1763, NPV 0.9622 and 4.67 false positives per true positive.
The values illustrate the base-rate effect. They do not measure either marker's actual performance
in a new population, and the apparent ordering of the two markers reverses on the smallest
difference in specificity, which is not estimable from the rounded operating points.

Changing prevalence alone assumes that sensitivity and specificity transport unchanged despite differences in
age, disease spectrum, comorbidity, assay handling and reference standards. That assumption needs validation.
No sampling confidence interval, prevalence uncertainty interval, net-benefit threshold or cost-effectiveness result is estimated.
A low PPV alone does not establish that triage is clinically useless or that the test has failed.

The registered closure criterion also requires a suitable study/denominator, an externally sourced target-setting prevalence
and a prespecified threshold. This exercise supplies conditional arithmetic and cannot certify full closure.
P-939 is not used as a performance benchmark: its reported 0.169 PPV concerns incident MCI-or-dementia in the
SLAS-1 development cohort, with an internally inconsistent reported sensitivity/specificity/incidence combination.
See `G-102_audit.md` for the excluded/miscoded records and limitations.

Generated by `scripts/gap_prevalence_analysis.py`.
