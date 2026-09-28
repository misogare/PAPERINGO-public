# Adjudication criteria for the human worksheet (2026-09-19)

What each of the five record kinds means in this pipeline, and what a record must show before it is admitted.
Compiled from the rules as implemented and written down: `scripts/agent4_relational.py` (candidate gates),
`scripts/ledger_lint.py` (gate invariants), `AGENTS.md` Agent 4 and Sub-task 4C, `config/reason_taxonomy.json`
(the controlled vocabulary of rejection reasons), `config/assumption_registry.json`, `config/gold_standard_rubric.json`
and the `.claude/agents/relmine-miner.md` / `relmine-verifier.md` rulebooks. Use it beside
`human_adjudication_worksheet_2026-09-19.md`.

Verdicts: `admit` = supported by the cited studies as stated; `rescope` = supported only in a narrower form
(say which in the note); `reject` = not supported, or one of the false-positive classes below.

## Rules that apply to every item

1. Read the block (`## Paper N:` in summaries.md) and, where the block is thin, the PDF. The record must be true of what
   the paper reports, not of what a keyword suggests. Quoted numbers must be in the block or PDF, verbatim.
2. The block must be about the cited paper. Blocks have been found describing a different paper (P-109/P-110,
   P-318..P-327), fabricated (P-369, P-883) or truncated raw extracts (P-151). If the block does not match the PDF, reject
   and say so in the note.
3. Watch negation prose: "no SHAP", "no accuracy, AUC, F1", "not associated" have been coded as the positive.
4. Papers that never anchor a record: performance-suspect studies (> 0.97 accuracy/AUC on internal validation),
   duplicate copies of another corpus entry, commentaries and editorials, conference-abstract slices, protocols without
   results, organisation reports. Two records from one source (same trial, commentary on a paper, two editions, two
   summaries of one paper, same research group and cohort) count as ONE study.
5. A study's own limitation is not a corpus-level absence, an author's speculation is not a finding, and a novelty-field
   assertion without a statistic is not a measured result.

## Conflict (C-)

Definition: two comparable studies whose reported results on the same problem disagree, and the disagreement survives
condition normalisation.

Admit only if all of these hold:
- Same task and the same construct measured on both sides (Task_Type equal; same conversion stage, e.g. MCI-to-AD on
  both sides, not SCD-to-MCI against MCI-to-AD; same population class, general versus disease-specific).
- Same data family (both ADNI, both NACC, or both single-centre of the same kind), same modality subtype (structural
  MRI only is not MRI+PET+CSF; EEG-only is not EEG+HRV), same design class (longitudinal versus cross-sectional), and
  validation tiers within two steps.
- Commensurable metrics: the same metric type on both sides (AUC versus AUC, accuracy versus accuracy). A concordance
  index is not an ROC-AUC; a subgroup result is not a whole-cohort result; an internal-validation figure is not an
  external-validation figure.
- The numeric gap exceeds the tier threshold used at generation (0.15 for V1, 0.08 for V2, 0.05 for V3, 0.03 for V4,
  the stricter of the pair), or the disagreement is at claim level (opposite conclusions on the same question).
- Directions are actually opposite, and the two records are independent evidence (not the same dataset edition
  analysed twice).
- A deliberate mismatch is acceptable only when the record says so and the disagreement IS about that mismatch
  (C-232 crosses cohorts because the claim is that rankings do not transfer).

Reject with the corresponding reason when the pair dissolves: DESIGN_MISMATCH, POPULATION_SPECIFICITY,
TASK_SUBSTAGE_MISMATCH, MODALITY_SUBTYPE_MISMATCH, METRIC_TYPE_MISMATCH, GAP_BELOW_THRESHOLD, DUPLICATE_PAPER,
PREDICTION_HORIZON_MISMATCH (1-year versus 5-year conversion), or when a third study's bridging evidence resolves it
(partially resolved) or the quantities are simply incommensurable.

Rescope when the disagreement is real but narrower than stated (for example only one endpoint, or only at one tier).

## Consensus / verified finding (VF-)

Definition: a directional claim that at least three independent studies each support as stated.

Admit only if:
- At least three supporters survive your reading. Independent means non-overlapping author groups, distinct datasets
  (at least one non-ADNI), and distinct method families; siblings from one group or one cohort count as one supporter.
- Each surviving supporter reports the finding in the SAME direction, with the design the record requires (a
  cross-sectional record cannot be supported by a study that did not compare the groups; a longitudinal record needs a
  follow-up) and in the modality the record names. Reduced-connectivity evidence does not support a
  compensation-increase claim.
- Each supporter shares the statement's distinctive content (its anchor term: olfactory, depression severity,
  dual-task cost, and so on). Sharing an architecture family or a task code is not support.
- The statement carries its scope ("in studies that run their own unimodal comparison"); the supporters fit that scope.

Reject a supporter that only mentions the topic, reports the opposite direction, is a review restating others, or is a
duplicate or flagged non-evidence paper. If fewer than three remain, the record fails (reject, or rescope if the
narrower statement still has three).

Rescope when the claim holds only in a narrower form (group-level, not per-subject; one substage; one population).

## Complementary finding (CF-)

Definition: a pair of independent studies whose joint reading supports one specific insight that neither paper states on
its own.

Admit only if:
- The combined insight is a specific sentence naming what the two establish together, and it is true of BOTH blocks.
- Neither paper already states the combined insight alone (CF-282 was demoted because one of its two studies stated
  it by itself).
- The two studies are independent (not one group, one cohort, one trial) and neither is a duplicate or a flagged
  non-evidence paper.
- The finding fields for A and B are intact (not truncated mid-clause, not raw PDF spillover) and faithful to the blocks.

Reject when the insight is template text ("providing a more complete picture", "complementary evidence", "together
they strengthen", "holistic view"), a mechanical concatenation of the two findings, a pairing justified only by
shared surface attributes (both cross-sectional, both T4, both East Asian) across incompatible constructs, or a
"clinical implication" that is really a method suggestion.

Rescope when the joint insight holds but the record overstates it (for example a within-study comparison is
claimed where one side is only an interpretive extension).

## Invalidated assumption (IA-)

Definition: a prior belief actually held by the field that a corpus study's evidence contradicts (refuted) or shows
to fail in a stated setting (bounded).

Admit only if:
- The prior belief is real: it is registered in `config/assumption_registry.json` (KA-xx), or the source paper or
  another corpus paper states it as the standing view. A belief the miner constructed, or a question the source frames
  as unclear or equipoise, is a STRAWMAN_PRIOR_BELIEF.
- The block, and the PDF where needed, really reports evidence against that belief, with a statistic (effect, p,
  correction status). A qualitative clause with no statistic is UNQUANTIFIED_REVERSAL_CLAUSE.
- The cited paper is about the right domain and modality, and the evidence is the paper's own measured result, not a
  model-fitted value at chosen covariates, not a derived rank inheriting a flagged artefact, not a result that fails the
  source's own multiple-comparison correction.
- The authors themselves claim the result. If the source lists it as an open problem, speculation or a primary
  limitation, it is SOURCE_REFUSES_CONCLUSION or AUTHORS_DECLINED_TO_CLAIM.
- The population matches the claim: an MCI-specific reversal cannot come from an analysis pooling MCI with dementia
  (SCOPE_POOLED_POPULATION).
- The pattern is not already a known, cited exception in the field (extending it to a new population is replication,
  not a reversal: EXCEPTION_ALREADY_IN_FIELD).

Rescope when the evidence bounds the belief (fails in one cohort or setting) but the record says refuted, or when the
scope must be narrowed to the outcome actually measured. Most admitted IAs are bounded, not refuted; the record's
caveat should say which.

## Research gap (G-)

Definition: a specific absence checked against the whole corpus: no eligible study among the 1,300-odd reviewed meets the
stated criterion, at the stated date. It is corpus-relative and dated, never a claim about the wider literature.

Admit only if:
- The statement names a specific, deep absence: an untested domain shift (vendor, site, language, population), a
  ground-truth or label assumption never checked, a temporal-validity or endpoint problem, a missing clinical-utility
  demonstration, a population blind spot, a task never modelled (reversion). Gold-standard examples: multi-vendor OCT
  validation on one cohort; language-agnostic speech detection above AUC 0.80; reversion as an optimisation target;
  XAI shown to change a decision; interval-censored visits forced into binary labels; no follow-up after a computational
  diagnosis.
- The corpus check is real: `evidence_for_open_status` says what was searched and what the nearest papers do instead,
  and you cannot find a closing paper by grep. If you find one, the gap is FIELD_ALREADY_ADDRESSED (reject) or, if it
  closes only part, rescope to partially open and name the paper.
- The anchor papers (`papers_claiming_gap`) actually evidence the absence (their limitation or discussion sections say
  so, or their design shows it). A cited paper that never mentions the construct is EVIDENCE_TOPIC_DRIFT. A
  "no study does X" clause contradicted by the record's own cited papers is UNIVERSAL_NEGATIVE_FALSIFIED_BY_OWN_CITATIONS.
- There are closure criteria: what a study would have to do to close it.

Reject when the record is:
- one paper's limitations section reformatted (small N, single site, cross-sectional, retrospective, no external
  validation, larger cohort needed, no causal inference): a study weakness, not a corpus-level gap. This is the
  single-paper-limitation class the pipeline auto-rejects (G-CAND-319..335 and 194 legacy records);
- a review-level limitation (heterogeneity, publication bias, narrative format);
- a "no one combines X with Y" combination with no physiological or architectural reason why it matters;
- a restatement of an existing gap; too specific to be informative; out of scope (drug development, trial design);
- real for this corpus but well studied in the wider literature (EXISTS_IN_EXTERNAL_LITERATURE), or open only
  because the data cannot exist (DATA_SCARCITY);
- built on a manufactured contrast (two instances of the same measurement family put in opposite arms), or on an
  author's importance claim presented as a measured finding (EVIDENTIAL_OVERSTATEMENT).

Rescope when the absence is genuine but the statement is broader than the check supports, or when part of it is
closed (name the closing papers).
