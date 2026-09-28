# Risk-of-bias appraisal of the cited studies: brief for model readers

Operator delegation, 2026-09-28. The appraisal is carried out by model readers under the review adjudicator's
delegation; every judgment is recorded as model-produced and awaits the adjudicator's review. You are READ-ONLY on the
repository: return your appraisal as the structured output; do not edit any file.

## What to appraise

For each assigned study you receive: its identifier (P-nnn), the path to the full text of its source document
(page markers `=== page N ===`), and `cited_contexts`: the sentences of the review that cite it. Risk of bias is
judged for **the result the review cites** (a study can have several results; appraise the one in `cited_contexts`;
if several are cited, appraise the principal one and name it).

1. Read the source text in full. Use only the source document as evidence (the summaries and registry are not evidence).
2. First check that the document is the study the review cites (title, authors, design, the cited numbers). If it is not,
   set `document_check.matches` to false, say what the document is, and stop: do not appraise a different paper.
3. Choose the tool by the **question the cited result answers** (rules below), and say why in one sentence.
4. Answer every signalling question of that tool with Yes / Probably yes / Probably no / No / No information (or the tool's
   own response options), each with a verbatim quotation and its page, or, for "No information", what you looked for.
5. Judge each domain, then the overall risk of bias, with a short rationale tied to the answers.

## Choosing the tool (by the question of the cited result)

| The cited result is ... | Tool |
|---|---|
| a systematic review, meta-analysis, network meta-analysis or umbrella review | ROBIS (phase 2 domains 1 to 4; phase 3 overall) |
| a randomised comparison from a trial | RoB 2 (five domains, for the cited outcome) |
| the performance of a multivariable prediction model or machine-learning classifier (diagnostic or prognostic): discrimination, calibration, ranking of models | PROBAST (Wolff et al. 2019), with the AI-specific considerations listed below from PROBAST+AI (Moons et al. 2025) |
| the accuracy of a single index test, biomarker or instrument against a reference standard | QUADAS-2 (four risk-of-bias domains, three applicability domains) |
| the association of one prognostic factor with a later outcome, not a model | QUIPS (six domains) |
| the effect of an exposure (medication, lifestyle, condition) on an outcome in an observational study, framed causally | ROBINS-E (seven domains) |
| a comparison between groups, or an association, measured at one time point (for example a biomarker level in MCI against controls) | JBI checklist for analytical cross-sectional studies (8 items); for a case-control design, the JBI case-control checklist (10 items) |
| a prevalence or incidence estimate | JBI checklist for prevalence studies (9 items) |
| from a qualitative study, commentary, protocol, narrative review, guideline, bibliometric analysis, or a methods or simulation paper with no participants | not applicable: no risk-of-bias tool fits; say why |

## Tools: domains and what to check

- **ROBIS**: 1 study eligibility criteria; 2 identification and selection of studies; 3 data collection and study appraisal;
  4 synthesis and findings (including heterogeneity, robustness, biases in primary studies addressed). Domains low / high /
  unclear; overall low / high / unclear (phase 3: were the concerns identified addressed in the interpretation?).
- **RoB 2**: 1 randomisation process; 2 deviations from intended interventions; 3 missing outcome data; 4 measurement of the
  outcome; 5 selection of the reported result. Domains and overall: low / some concerns / high (overall: low only if all
  domains low; high if any domain high or several domains have some concerns).
- **PROBAST**: 1 participants (data sources appropriate; inclusions and exclusions appropriate); 2 predictors (defined and
  assessed similarly for all; assessed without knowledge of outcome; available at the time of intended use); 3 outcome
  (determined appropriately; pre-specified or standard definition; predictors excluded from the outcome definition; defined
  and determined similarly for all; determined without knowledge of predictors; appropriate interval between predictor
  assessment and outcome); 4 analysis (reasonable number of participants with the outcome; continuous predictors handled
  appropriately; all enrolled participants included; missing data handled appropriately; selection of predictors not based
  on univariable analysis; complexities in the data accounted for; relevant performance measures evaluated, including
  calibration; overfitting, underfitting and optimism accounted for; model weights correspond to the reported results).
  AI-specific considerations to answer inside domain 4 (and domain 1 for data sources): whether the split between training
  and test data is at the participant level (no data from one person on both sides); whether preprocessing, feature
  selection, augmentation and hyperparameter tuning are done within the training folds only (no leakage); whether the test
  set is held out from all tuning; class imbalance and how it was handled; whether the reported performance is apparent,
  internally validated or externally validated. Domains low / high / unclear; overall: low only if all domains low, high if
  any domain high, otherwise unclear. If a model was developed without any external validation and every domain is low,
  consider rating overall high unless the development data set was large and internal validation was done.
- **QUADAS-2**: 1 patient selection (consecutive or random sample; case-control design avoided; inappropriate exclusions
  avoided); 2 index test (interpreted without knowledge of the reference standard; threshold pre-specified); 3 reference
  standard (likely to classify the target condition correctly; interpreted without knowledge of the index test); 4 flow and
  timing (appropriate interval; all received the same reference standard; all included in the analysis). Risk of bias and
  applicability concerns: low / high / unclear; overall: low only if all domains low, high if any high, otherwise unclear.
- **QUIPS**: 1 study participation; 2 study attrition; 3 prognostic factor measurement; 4 outcome measurement; 5 study
  confounding; 6 statistical analysis and reporting. Each low / moderate / high; overall: low if all low or at most one
  moderate; high if any high or three or more moderate; otherwise moderate (report moderate as "some concerns").
- **ROBINS-E**: 1 confounding; 2 measurement of the exposure; 3 selection of participants; 4 post-exposure interventions;
  5 missing data; 6 measurement of the outcome; 7 selection of the reported result. Each low / some concerns / high /
  very high; overall = the most severe domain judgment.
- **JBI analytical cross-sectional (8 items)**: inclusion criteria defined; subjects and setting described; exposure
  measured validly and reliably; objective standard criteria for the condition; confounders identified; strategies for
  confounders; outcomes measured validly and reliably; appropriate statistical analysis. **JBI case-control (10 items)** and
  **JBI prevalence (9 items)** as published. Items Yes / No / Unclear / Not applicable. Overall by this review's rule: high
  if any of confounder identification, confounder strategy, valid outcome measurement or appropriate analysis is "No";
  low if every applicable item is "Yes"; otherwise some concerns.

## Summary categories (used to compare tools)

Report `overall` in the tool's own terms and `overall_category` as one of: `low`, `some_concerns` (includes unclear and
moderate), `high` (includes very high), `not_applicable`, `document_mismatch`.

## Rules

- Quote verbatim, with the page. Never supply information the document does not contain; "No information" is a valid answer.
- Judge the design and conduct, not the direction or size of the result. Do not let the review's own framing in
  `cited_contexts` drive the judgment; it only tells you which result to appraise.
- If the cited context misstates what the document reports, say so in `notes` (this is recorded for correction).
- British spelling; no em-dashes.
