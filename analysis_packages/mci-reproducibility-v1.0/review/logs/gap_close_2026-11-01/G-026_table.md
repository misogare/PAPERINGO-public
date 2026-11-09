# ACTOR-G-026: corrected EEG validation evidence audit

**Version 2.0 | Built 2026-11-01T09:43:12Z | Candidate audit, pending human review**

The earlier adjusted-accuracy calculations are withdrawn. Published differences describe
their own experiments and cannot be subtracted from another model's accuracy. This audit
does not rerun a model, pool corpus accuracies, or change a workflow gate.

## Four checked corpus examples

These examples were checked against local PDFs; they are not an exhaustive EEG inventory.

| Paper | People | Accuracy | Protocol | Source location | Audit note |
|---|---:|---|---|---|---|
| [P-31](https://doi.org/10.1016/j.cmpb.2024.108123) | 27 | 99.95%; uncertainty not transcribed in this audit | 10-fold CV over segment-expanded data; subject grouping not reported | PDF p. 5 (9,720 segments); p. 7 (accuracy) | 9,720 segments are not 9,720 independent people. Original-model subject-wise reproduction remains unresolved. |
| [P-70](https://doi.org/10.1016/j.knosys.2022.108815) | 27 | 95.51%; SD 3.11 percentage points | 5-fold CV; cited performance table does not establish subject grouping | PDF p. 7, Table 2 | Same 27-person resting-state dataset as P-31. Original-model subject-wise reproduction remains unresolved. |
| [P-129](https://doi.org/10.1016/j.bspc.2026.109564) | 61 | 90.97%; SD 0.60 percentage points | Nested cross-validation with a cross-subject validation scheme | PDF p. 1 (abstract); p. 7, Table 2 | 94.76% (SD 0.78) is specificity, not accuracy. Registry Acc 94.76 is an extraction error; this audit leaves that source record unchanged. |
| [P-557](https://doi.org/10.3389/fnagi.2023.1294139) | 23 | 95.63%; uncertainty not transcribed in this audit | Leave-one-out-subject cross-validation (LOOSCV) | PDF p. 5 (LOOSCV); p. 7, Table 1 (accuracy) | Earlier split-unit label was wrong. Small single-site pilot, different paradigm from resting-state P-31/P-70. |

## Matched protocol evaluations

Each row compares one architecture on the same cohort under two split protocols.
Subject-wise means people are separated between splits; it does not establish that
the metric represents one aggregated clinical decision per person.

| Source | Task and cohort | Segment-wise accuracy | Subject-wise accuracy | Within-study difference | Uncertainty and source |
|---|---|---:|---:|---:|---|
| [Brookshire et al. 2024](https://doi.org/10.3389/fnins.2024.1373515) | AD vs subjective cognitive impairment; N=98 | 99.80% | 53.00% | 46.80 percentage points | 95% CI: segment 99.1-100.0%; subject 43.1-64.8%; Sections 2.2, 2.4, 3.1.1; Figure 2A; caveats in 4.4 |
| [Tokdar et al. 2026](https://doi.org/10.1186/s40708-026-00302-4) | MCI vs HC; N=27 | 97.40% | 56.00% | 41.40 percentage points | Across-fold SD: segment 2.2; subject 16.1 percentage points; Sections 2.1.1, 2.3.5, 3.1.1-3.1.2; Tables 3-4 |

Tokdar's comparison already exists on the exact public 27-person resting-state cohort
used by P-31/P-70, but uses a different architecture: neither original model is reproduced.
Its uncertainty values are fold standard deviations, not confidence intervals.
The cohort contains 16 HC and 11 MCI participants: majority-class accuracy is
59.26%; 50% describes uniform random binary guessing. Accuracy can fall
below either baseline; neither is a permissible hard floor for inferred performance.

## Between-study association, reported separately

[Miltiadous et al. 2026](https://doi.org/10.1007/s11571-026-10464-w) reports AD-versus-CN mean accuracy of 97.07%
in 19 Validity-3 reports and 82.11% in 13 Validity-1 reports
(difference 14.96 percentage points). Between-study SD: Validity 3, 3.09; Validity 1, 6.93 percentage points.
Source: Performance on AD vs. CN; Figure 6; validity definitions in Methodology. Different studies using AHEPA, not a paired model experiment. Validity 3 includes epoch splitting or non-nested feature/channel selection; all 19 are not verified subject-overlap cases.

This is not a measured within-model loss, lower bound, correction factor, or causal
estimate of the effect of changing a split protocol.

## What remains unresolved

Published MCI evidence already demonstrates a substantial protocol difference on this
dataset. The remaining original-model question is narrower: independently reproduce
P-31 STCGRU and/or P-70 GRU, then compare segment-wise and subject-wise protocols,
with data-dependent preprocessing, selection, calibration and thresholding confined
to training/validation subjects. Report subject-level uncertainty and appropriate
baselines. This audit completes neither replication nor the human gap-review gate.

A cross-study maximum is not a performance bound. Cohorts, paradigms, class balances
and metrics differ; an unadjusted comparison cannot isolate leakage or rule out an
architectural improvement. Unstated grouping is uncertainty, not proven leakage.

## Figure and files

figures/gap026_leakage.png and .pdf show (a) matched protocol comparisons and
(b) the separate Miltiadous between-study association. Points/bars reproduce published
accuracies; no adjusted corpus accuracy is shown. Source uncertainty is tabulated
above. The legacy projection CSV contains a withdrawal notice; G-026_evidence.csv
holds the corrected evidence. Original v1 files are retained in pre_audit_v1/.

Generated by scripts/gap_close_eeg_leakage.py.
