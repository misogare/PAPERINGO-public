# ACTOR-G-109: selected evidence and planned scan counts

Document version: 2.0. Built at: 2026-11-01T09:43:11+00:00.

Status: candidate / pending_human_review. No gap closure or human gate is asserted.

This is a map of five selected examples from the prior analysis, not an exhaustive corpus search.
The registry fields were inspected; primary PDF checks by the coordinating audit agent and remaining uncertainties are explicitly identified per row.
Quoted results are extracted claims, not newly fitted models. They must not be pooled as a single predictive-gain measure.

| Paper | Measurement timing | Outcome horizon | Extracted result | What the result can establish | Source record |
|---|---|---|---|---|---|
| P-1153 | Not applicable: not a serial-scan comparison | Prognostic clinical follow-up; ADNI follow-up up to nine years, not a two-year rescan interval | Delta iAUC 0.020 (95% CI 0.007 to 0.034) | Scope-excluded comparator: added baseline model feature, not serial-scan gain | data/full_paper_registry.csv:1137; summaries.md:165514 |
| P-974 | Variable: last follow-up SUVR minus baseline SUVR; 63 of 75 had second or third PET | Clinical follow-up mean 24 months (SD 9.3; range 11.6 to 42), not a fixed scan interval | AUC 0.652 longitudinal versus 0.596 baseline (difference 0.056); DeLong p=0.663 | Discrimination comparison; no statistically demonstrated difference | data/full_paper_registry.csv:959; summaries.md:147004 |
| P-527 | Baseline, 12 months and 24 months | Two years in registry; ascertain prediction origin and post-scan outcome window from source | CNN+LSTM stable-versus-progressive MCI accuracy 74.1%, 74.8%, 75.5% at baseline, 12 and 24 months | Accuracy at a selected time point; not an isolated incremental gain | data/full_paper_registry.csv:522; summaries.md:44704 |
| P-945 | 12 months: change features defined as (month 12 minus baseline) divided by one year | Three-year clinical follow-up, distinct from the 12-month change measurement | NRI 0.246 (95% CI 0.021 to 0.848); IDI 0.090 with inconsistent source intervals (both cross zero) | NRI, C-index and IDI are distinct metrics; no common AUC increment | data/full_paper_registry.csv:930; summaries.md:143362 |
| P-967 | Baseline and 48 months in registry | Incident dementia or MCI before the 48-month follow-up MRI | WMH-progression Z-score OR 1.51 (95% CI 1.12 to 2.04) | Association odds ratio, not an estimate of incremental discrimination or clinical utility | data/full_paper_registry.csv:952; summaries.md:146114 |

Source and uncertainty qualifications:

- **P-1153**: Excluded from serial-scan evidence; prior table misclassified a model-feature increment as longitudinal imaging benefit 95% CI 0.007 to 0.034 concerns the added deep-learning output Verification: Primary PDF checked by coordinating audit agent. DOI: https://doi.org/10.1093/radadv/umae021. PDF/page: PDF pp. 1, 5 and 7 (Table 3).
- **P-974**: No demonstrated difference does not prove equivalence or that serial PET adds nothing; 75 MCI participants and 17 converters Baseline AUC 95% CI 0.403 to 0.790; longitudinal AUC CI 0.484 to 0.820; these are separate AUC intervals, not a paired-difference interval Verification: Primary PDF checked by coordinating audit agent; measurement timing is variable. DOI: https://doi.org/10.30773/pi.2022.0014. PDF/page: PDF pp. 4-5 (last-follow-up change definition and ROC comparison).
- **P-527**: Multiple time points are present; cannot claim every selected example used just one interval No paired incremental-gain confidence interval supplied in registry Verification: Primary PDF checked by coordinating audit agent. DOI: https://doi.org/10.3390/ijerph19084508. PDF/page: PDF pp. 2-3 and 12 (Table 2).
- **P-945**: Prior table mislabeled the 36-month outcome follow-up as a scan interval Abstract IDI CI -0.062 to 0.170; body/Table 3 IDI CI -0.094 to 0.209. NRI CI 0.021 to 0.848. Source inconsistency must be retained; these are different metrics Verification: Primary PDF timing and model contrasts checked by coordinating audit agent; IDI interval inconsistency recorded. DOI: https://doi.org/10.1038/s41598-021-96914-3. PDF/page: PDF p. 1 (abstract IDI interval), p. 3 (change features), p. 5 (body and Table 3).
- **P-967**: Outcome was observed before follow-up MRI; an association over this period is not prospective predictive gain after ordering a second scan. 433 two-scan participants and 33 events 95% CI 1.12 to 2.04 describes the odds ratio; it is not an interval for predictive gain Verification: Primary PDF checked by coordinating audit agent. DOI: https://doi.org/10.1136/svn-2021-001357. PDF/page: PDF p. 1 (abstract temporal definition and OR).

## Planned resource counts

Assumption: everyone receives a baseline scan at month 0 and attends each scheduled follow-up at integer multiples of the interval, through and including month 60.
The number of additional scans is floor(60 / interval); these counts assume no dropout, event-triggered stopping or unscheduled visits.
They are resource scenarios, not observed utilisation, monetary costs, benefits or clinical recommendations.

| Interval | Follow-up months | Additional scans | Total including baseline |
|---|---|---|---|
| 6 months | 6, 12, 18, 24, 30, 36, 42, 48, 54, 60 | 10 | 11 |
| 12 months | 12, 24, 36, 48, 60 | 5 | 6 |
| 24 months | 24, 48 | 2 | 3 |
| 48 months | 48 | 1 | 2 |

## What remains open

These examples do not establish an optimal interval. P-1153 is excluded from serial-scan evidence; the four remaining examples do not form four comparable experiments. Association, a change in NRI, and an AUC point difference answer different questions.
A direct evaluation needs comparable participants, modality, prediction target and threshold; a prespecified prediction origin at each landmark; and an outcome window entirely after the scans used for prediction.
Split participants before fitting preprocessing and models; compare first-scan-only and serial-change models on the same held-out people, with uncertainty for their paired gain.
At later landmarks, account for earlier conversion, dropout and the resulting change in who remains eligible. Evaluate clinical utility and visit burden before choosing a schedule.
