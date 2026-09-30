# PAPERINGO traceability materials

This repository holds the research materials behind a cross-study synthesis of mild cognitive impairment (MCI) research
built from a corpus of 1,318 studies. The synthesis itself is in preparation and is **not** included here; this
repository contains the records its claims are checked against, so that each claim can be traced to the study records
and adjudication records it rests on once the synthesis is available, and errors or omissions can be reported against a
named record.

## Identifiers

Every study in the corpus has a stable identifier `P-nnn`. Cross-study records carry ledger identifiers: `C-nnn`
(contradiction candidates), `VF-nn` (convergences; the prefix is a legacy label kept for traceability), `CF-nnn`
(complementary pairs), `IA-nn` (invalidated or bounded prior assumptions) and `G-...` (open gaps). A claim that cites
these identifiers can be looked up here directly.

## What is here

| Path | Contents |
|---|---|
| `data/full_paper_registry.csv` | The coded study registry: one row per study, with task, data domain, design, sample size, validation tier, best reported metric, ADNI dependence and duplicate/out-of-scope flags |
| `data/study_index.csv` | Study identifier, title, DOI and publication year for every registry row |
| `data/conflicts.json`, `consensus.json`, `complementary.json`, `invalidated.json`, `gaps.json` | The five claim ledgers, each record with its status, supporting studies, recorded reasons and adjudication history (including operator review statements) |
| `config/` | Extraction schema, normalisation rules and controlled vocabularies, the registry of prior beliefs, the non-independence register and the rejection-reason taxonomy |
| `logs/human_adjudication_*`, `logs/adjudication_criteria_2026-09-19.md` | The blinded self-audit of the adjudication: stratified worksheet, key, per-item notes and scoring report |
| `logs/double_adjudication_*` | The model second reading of a sample of records, with its key |
| `logs/gap108_recheck_2026-09-22.md` | The record-by-record re-check of the open-gap backlog |
| `logs/round18/` | Source-document adjudication of the open contradiction candidates (verdicts, grounds, verbatim quotations, challenges, independent second reading), the blinded registry coding audit and the targeted external-validation check |
| `logs/round19/` | The corpus-wide search for contradictions (extracted directional findings, candidate pairs, screening and full-text verdicts) |
| `logs/appraisal_worksheet.csv` | Appraisal-relevant characteristics of the cited studies, with the risk-of-bias judgment columns left blank for a human appraiser |
| `checklists/` | The PRISMA 2020 and SWiM reporting checklists, with where each item is reported in the synthesis |
| `scripts/` | The extraction and relational-analysis pipeline, the ledger inclusion rules, the lints, the audit scoring tools and the script that regenerates every registry-derived number |

Source PDFs and their text extracts are not redistributed (copyright); each study's DOI is in `data/study_index.csv`.

## Regenerating the numbers

Python 3.10 or later; the only third-party package the scripts below need is `requests` (imported by the extraction
module).

```
pip install -r requirements.txt
python scripts/litreview_stats.py            # every registry-derived statistic, printed with its definition
python scripts/make_appraisal_table.py --thresholds   # the performance-validity sensitivity table
python scripts/ledger_lint.py                # re-applies the admission rules to every ledger record
```

The acquisition and extraction steps cannot be re-executed from this repository: the corpus was harvested
interactively and the searches' hit counts were not logged, so the materials here are computationally reproducible
given the harvested corpus, not acquisition-reproducible.

## Reporting an error or omission

Open an issue naming the record (`P-nnn`, `VF-nn`, `C-nnn`, ...) and what is wrong. Corrections are applied to the
records and logged; the record history is kept.

## Licence and citation

Code: MIT (`LICENSE`). Data, configuration, logs and checklists: CC BY 4.0 (`LICENSE-CONTENT.md`).

Archived at Zenodo: https://doi.org/10.5281/zenodo.23050081 (version 1.1, which the synthesis refers to; version 1.0 is https://doi.org/10.5281/zenodo.23008250); every version: https://doi.org/10.5281/zenodo.23008249. Citation metadata is in `CITATION.cff`.
