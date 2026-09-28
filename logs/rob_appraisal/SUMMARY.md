# Risk-of-bias appraisal of the cited studies: summary (2026-09-28)

Operator delegation, 2026-09-28. The appraisal was made by model readers from the source documents under the review
adjudicator's delegation; every record is model-produced; the operator reviewed the judgments on 2026-09-28 and accepted them (`operator_review_2026_09_28` on each record).
Brief: `ROB_BRIEF.md`. Per-study records: `P-nnn.json` (primary reading), `second/P-nnn.json` (blinded second reading).
The judgments are merged into `logs/appraisal_worksheet.csv` by `scripts/make_appraisal_table.py --write`, and the
convergence analysis is `scripts/rob_convergence_sensitivity.py`.

## Scope

The 177 corpus studies cited in Sections 1 to 8 of the synthesis, plus the five supporters of admitted convergences that
the text does not cite (P-83, P-337, P-624, P-687, P-1046): 182 studies, all with a verified source document. For each
study the reader appraised the result the synthesis cites, with the tool that fits the question that result answers.

## Tools chosen (182 studies)

| Tool | Studies | Low | Some concerns | High | Not applicable |
|---|---|---|---|---|---|
| PROBAST (with PROBAST+AI considerations) | 71 | 0 | 0 | 71 | |
| JBI analytical cross-sectional | 37 | 0 | 16 | 21 | |
| QUIPS | 20 | 0 | 2 | 18 | |
| ROBIS | 20 | 0 | 0 | 20 | |
| JBI prevalence | 13 | 0 | 6 | 7 | |
| ROBINS-E | 7 | 0 | 0 | 7 | |
| RoB 2 | 5 | 0 | 2 | 3 | |
| QUADAS-2 | 5 | 0 | 0 | 5 | |
| not applicable | 4 | | | | 4 |

Of the 177 cited studies: 148 high, 25 some concerns, 4 not applicable, none low. No document mismatch was found.

What drives the high ratings: for PROBAST, the analysis domain was high in all 71 studies (small numbers of outcome
events, internal validation only, no calibration, and in several studies data leakage from segment-level or image-level
splits, feature selection on the full sample or oversampling before the split); participants (38) and outcome (28) were
the next most frequent. For ROBIS, the synthesis-and-findings domain was high in 18 of 20. For the JBI and QUIPS
appraisals, the most frequent concerns were unaddressed confounding and the statistical analysis. The rule that turns a
JBI checklist into an overall rating (high if confounders are not identified or addressed, or if the outcome measurement
or the analysis is inappropriate) is this review's, stated in the brief.

## Reliability

- Blinded second reading of a random 25% sample (44 studies, seed 20260928): the overall category agreed for 44 of 44
  (kappa 1.00). This reflects the tools' rules, under which one high domain makes the overall rating high, and most studies
  had at least one. Domain-level agreement where both readers chose the same tool: 187 of 214 domain judgments (87%). The
  tool differed for two studies (P-955, P-1094), with the same overall category under both; the primary reader's tool was
  kept and the reason recorded on the record.
- Quotations: of 3,590 verbatim quotations in the primary and second readings, a mechanical check located 3,382 (94.2%) in
  the extracted text; spot checks of the remainder found them verbatim but split by page or column breaks or soft hyphens
  in the extraction. A further 67 entries were reader annotations (what was searched for) rather than quotations.

## Convergences

`scripts/rob_convergence_sensitivity.py`: of the 22 admitted convergences, one (VF-29) keeps three or more supporters not
at high risk of bias; the other 21 rest wholly or mostly on supporters at high risk, and none of the supporters is at low
risk. The convergences therefore describe where independent studies agree, not a certainty of effect.

## Citation-accuracy notes raised by the readers

Readers were asked to flag any way the synthesis's citing sentence misstates the document; each flagged sentence was then
checked against the source document and corrected where it was wrong (recorded in Supplementary Note S6 of the synthesis).
