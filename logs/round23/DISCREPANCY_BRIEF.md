# Round 23: unresolved discrepancies among the contradiction candidates

**Paths.**
- Scratch root (R23): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r23`
- Repository (READ-ONLY for you): `C:/Users/aryas/Downloads/PAPERINGO`
- Source texts: `.../scratchpad/r20/pdf/<P-id>.txt` (page markers `=== page N ===`), and `.../scratchpad/r21/pdf/`.
  For a missing text, run `PYTHONIOENCODING=utf-8 python .../scratchpad/r21/pdf_text.py P-nnn` from the r21 folder.
- The PDF map is wrong for P-112, P-114, P-115, P-117 and P-174. The round-20 readings name the right documents.

Write only your output files, only with the Write tool.

## Why this round exists

The review's rule for a genuine contradiction (Appendix H.1) asks for incompatible results on the same quantity, with the
task, dataset family, validation tier and modality family matched. Under that rule none of the 201 candidates is genuine.
The review's operator has directed on 2026-09-29 that this is too strict for what the review should report. Where two
studies address the same clinical question and report results that differ, the review should report the pair as a
conflict, and name the methodological differences that might explain the gap, even when those differences are not shown
to explain it. The bar should be neither so light that any two different numbers count, nor so strict that every
possible explanation blocks the report.

This round applies that direction as a second, reported tier: an **unresolved discrepancy**. It is judged by the rule
below, identically for every candidate. The genuine-contradiction rule and its result are not changed.

## The rule for an unresolved discrepancy

A pair qualifies when ALL of the following hold.

1. **Same clinical question.** The two studies address the same task and comparison (for example MCI against controls;
   progression from MCI to AD; the association of a named factor with MCI). The population class behind the label is
   the same (for example community older adults, not Parkinson's patients against Alzheimer's-spectrum MCI). The broad
   measurement channel is the same: MRI (structural, diffusion and functional count as one channel), PET, EEG/MEG,
   speech or language, gait or motor, eye or retina, fluid biomarkers, or clinical/cognitive/questionnaire data. A
   multimodal model matches when the channel being compared is shared.
   - Differences in cohort or dataset, features within the channel, architecture, sample size, validation design and
     prediction horizon are ALLOWED. Record them as candidate explanations.
2. **Same metric type**: accuracy with accuracy, AUC with AUC, hazard ratio with hazard ratio, SMD with SMD, prevalence
   with prevalence, direction of association with direction of association.
3. **Not reconciled by a matched figure.** If either paper reports the result under the other's condition (the same
   horizon, the same single-timepoint input, the same contrast, the same population subset), compare those figures. If
   the matched figures agree within uncertainty, the pair does NOT qualify: the papers' own data dissolve the gap.
4. **A difference beyond uncertainty.**
   - One study's estimate must lie outside the other's 95% interval.
   - When a study reports no interval, derive one from its evaluation-set size: a Wilson interval for accuracy (n =
     subjects in the test set, or in the cross-validation, for the contrast), and the Hanley-McNeil approximation for
     AUC when class sizes are known. Show the arithmetic.
   - For associations: opposite significant directions qualify. So do intervals that do not overlap. A significant
     estimate against a non-significant one qualifies only if the intervals exclude each other. A difference in
     statistical significance alone does not qualify, as the review already states in Section 5.2.
5. **Performance validity.** For performance comparisons, neither study meets the Section 3.5 exclusion: near-ceiling
   discrimination (0.97 or more) on internal validation with a small sample and no external cohort. Check this against
   the source, not the registry flag.
6. **The record describes the papers correctly enough.** If the claimed disagreement exists only because the ledger
   record misreads a paper, judge the figures the papers actually print. A misread figure is not a discrepancy.

If a pair qualifies, list the **candidate explanations**: the concrete differences that could produce the gap (cohort,
features, input representation, sample size, validation, horizon, label definition), each with a quote. Say that they
are not shown to be sufficient, unless a paper shows one is. If a paper shows it, the pair fails condition 3.

**Neutrality.** Do not qualify a pair in order to produce a number, and do not reject one to keep the count at zero.
The operator named five pairs they would report as conflicts: C-211, C-243, C-149, C-116 and C-83. Judge them by
exactly the same rule as every other pair. Report plainly what the source documents show for them.

## What to read for each candidate

- The candidate's source reading (the path in `R23/index.json`). It holds what each paper reports, the quantities,
  `incompatible_if_same`, the performance-validity check and verbatim quotes.
- For C-204, C-227, C-232 and C-234, read the ledger record in `data/conflicts.json` instead (Grep for `"id": "C-204"`
  and read its `operator_reclassification_2026_09_24` field).
- Open the source texts whenever the reading does not settle a condition. This is always needed for the matched-figure
  check and for any uncertainty arithmetic.

## Output: one file per candidate, `R23/conf/<C-id>.json`

```json
{
  "id": "C-xxx",
  "qualifies": "yes | no | borderline",
  "question": "the shared clinical question, or why there is none",
  "metric": "the shared metric type, or the mismatch",
  "figures": {"P-a": "value with interval (reported or derived, with arithmetic)", "P-b": "..."},
  "matched_figure_check": "which matched figure exists and whether it reconciles the pair",
  "beyond_uncertainty": true,
  "significance_only": false,
  "performance_validity": "...",
  "failed_conditions": [1, 3],
  "candidate_explanations": [{"difference": "...", "quote": "...", "paper": "P-.."}],
  "reason": "two or three sentences",
  "quotes": [{"paper": "P-..", "page": 3, "quote": "verbatim"}]
}
```

## Verification round (added after the first pass)

Condition 4 as first written ('one study's estimate lies outside the other's 95% interval') is too weak on its own. A
small study's estimate can fall outside a large study's narrow interval although the two do not differ beyond their
combined uncertainty. Every qualifying pair is therefore re-checked with one uniform test of the difference:

- z = |a - b| / sqrt(SE_a^2 + SE_b^2), on the compared figures (the matched figures where they exist).
- SE from a reported 95% interval = (upper - lower) / 3.92. For ratio measures (HR, OR, RR), work on the log scale.
- Where no interval is reported: accuracy SE = sqrt(p(1-p)/n), with n the evaluation-set size for that contrast; AUC SE
  by Hanley-McNeil from the class sizes; a proportion by the same binomial formula.
- Where only a fold standard deviation is reported, treat it as a spread across folds, not as a standard error, and
  derive the SE from n instead.
- The pair passes condition 4 only if z >= 1.96. Show the arithmetic.

Pairs that reached 'qualifies' only through the advocate also get a full skeptic check of conditions 1 to 6.
