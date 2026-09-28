# Round 19: adjudicating mined contradiction candidates from the source documents

Scratch root (R19): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r19`
Repository (read-only for you): `C:/Users/aryas/Downloads/PAPERINGO`

## Your role

You are a model reader. The review's adjudicator (the human operator) has asked for the genuine contradictions
in a corpus of 1,318 MCI studies to be found, under the review's existing rule, unchanged. A mining pass
extracted directional findings from every study's summary record and paired studies that appear to report
incompatible results on the same quantity; a screen against the summary records kept the pair you are given.
You decide, from BOTH SOURCE DOCUMENTS read in full, whether the pair meets the rule. You do not write to any
repository file.

Neutrality matters in both directions. The review currently reports no genuine contradiction among 201
earlier candidates, and a critic suspects the rule is applied too strictly; the operator wants genuine
contradictions found if they exist. Do not reject a pair because earlier candidates failed, and do not admit a
pair to produce a result. Decide only whether THIS pair meets the rule on what the two papers actually report.

## The rule (literature_review.md, Appendix H.1, unchanged)

A genuine contradiction: two studies reporting incompatible results on the same quantity. Distinct studies
(shared cohort or authorship recorded as non-independence, not disqualifying). Four deterministic gates (task,
dataset family, validation tier, modality family) matched or a mismatch waived on record, plus endpoint and
metric-type compatibility judged at adjudication. On failure: false positive, partially resolved by bridging
evidence, documented incommensurability, or unresolved.

How the rule is read (the same reading applied to the 23 candidates adjudicated on 2026-09-27):
- **Same quantity** = same estimand: the same marker or exposure measured the same way (fluid, assay class,
  instrument, imaging measure), the same comparison or outcome and horizon, the same population class behind
  the label (clinical MCI in older adults is not PD-MCI, post-stroke MCI, MCI in diabetes or HIV unless both
  are; a cohort restricted on the exposure's indication is not the same as an unrestricted one), the same metric
  type, and an adjustment set that does not change what is estimated.
- **Incompatible** = both results cannot be true of that quantity: opposite directions each supported by the
  study's own statistics, or estimates whose reported uncertainty excludes the other. NOT incompatible:
  significant vs non-significant in the same direction or with overlapping intervals; two different models
  each reporting a different accuracy; a claim about X against a claim about Y.
- **Gates.** Task codes T1-T6; validation tier is not applicable to association studies, trials or syntheses
  (count as matched); modality family = the class of measurement. **Dataset family**: two different cohorts can
  form a genuine contradiction when the claim is about a population-level association or effect (not a model's
  performance) and both cohorts sample the same population class; the dataset-family mismatch is then waived on
  the record, and you must state the waiver reason (e.g. "population-level association; both community-dwelling
  older adults with clinically diagnosed MCI"). If the populations differ in a way that changes the estimand,
  there is no waiver: that is documented incommensurability.
- **Performance validity**: a study reporting internal-validation discrimination >= 0.97 with a small sample and
  no external cohort is excluded from performance comparisons (rarely relevant here).
- **Precedents** (documented incommensurability after both papers were read): a positive class including
  patients already diagnosed with AD vs conversion within 36 months (C-204); a network meta-analysis estimate
  against control vs a head-to-head trial (C-227); models with different predictor sets across cohorts (C-232);
  two tDCS meta-analyses in the same direction differing only in significance, pooling different instruments
  (C-234); antihypertensive use in a hypertensive-only cohort vs an unrestricted cohort (C-254).

## Outcomes (exactly one)

`genuine` | `false_positive` (not a disagreement: compatible results, significance-only difference, or a claim
that misstates its study) | `documented_incommensurability` (different quantities) |
`partially_resolved_candidate` (a real disagreement largely explained by named third corpus studies showing the
specific separating condition) | `unresolved` (the source documents do not settle it; say what is missing).

## Procedure

1. Extract both PDFs' text: `cd C:/Users/aryas/Downloads/PAPERINGO && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe R19/pdf_text.py P-A P-B`
   (writes R19/pdf/P-X.txt with page markers, and warns when the PDF may be the wrong paper; if so, check the
   summary block with `scripts/get_blocks.py N` and search `downloads/` for the right PDF by title; if none,
   use `unresolved` unless the evidence settles it).
2. Read BOTH texts in full: methods, results, tables, limitations. Locate each side's result on the contested
   quantity; quote it verbatim with the page. Check numbers, direction coding (ratios can be inverted),
   significance, the population, the measurement and the adjustment set.
3. Decide, and state the shared quantity precisely if genuine.

## Output (return as your structured result)

verdict; confidence; quantity (the shared estimand, or each side's if different); finding_a / finding_b (with
numbers and page); same_quantity (why or why not, element by element); incompatible (why or why not);
gates (task, dataset family with waiver reason if any, validation tier, modality family); independence;
evidence_quotes (paper, page, verbatim quote); record_of_errors (anything the candidate description got wrong);
manuscript_statement (only if genuine: 2-4 sentences a review could print, stating what each study found, on
what population, and the contradiction, with candidate moderators that remain open rather than dissolving it);
what_would_change_the_verdict.
