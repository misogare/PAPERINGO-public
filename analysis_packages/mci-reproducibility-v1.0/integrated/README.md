# Reproducibility materials

Version 1.0 | Built 9 November 2026 | Prepared locally; not a public repository accession

Authors: Seyedaria Shobeiri and Yuan Miao, Victoria University.

This package contains project code, source-checked published-input tables and/or cohort-level estimates. It contains no participant records, source PDFs, extracted publication text, manuscripts, credentials or signed data-download links. The manuscript itself is not a preprint deposited through this archive. `MANIFEST.json` records every included file and its SHA-256 digest. `CITATION.cff` identifies this package version without inventing a DOI.

## Verify without controlled data

Extract the archive and run from its root:

```text
python scripts/verify_reproducibility_package.py
```

This standard-library check verifies the hashes and selected reported estimates, recomputing Bayes and scan-count arithmetic where present. It does not refit cohort models or validate their scientific assumptions. Saved results can be inspected without installing scientific packages. `requirements.txt` pins the analysis packages available in the authors' environment; `environment.json` records the Python version and full package versions. Scripts derive file locations from this package root.

## Version and release identity

These are newly assembled local materials, distinct from the existing public core-review archive. The old core-review DOI must not be reused as the accession for these files. A public deposit needs its own version identifier and permanent URL, followed by the corresponding data-availability update. No network requests, uploads or publication occur during package construction or verification.

Software retains the repository's MIT licence; project-created content retains the content licence supplied here. Neither licence grants access to ADNI/NACC records or copyright in the source journal articles. Cohort-provider access and data-use conditions remain applicable to underlying records.

## Published-evidence calculations

`logs/gap_close_2026-11-01/` supplies seven tables describing selected EEG protocol comparisons, two diagnostic operating points at three population prevalences, five imaging source checks, and four monitoring burdens. These are published-input interpretations. There is no original EEG architecture reproduction or learned optimum rescan interval.

After installing the packages, the calculation scripts can be run from a working copy:

```text
python scripts/gap_close_eeg_leakage.py
python scripts/gap_prevalence_analysis.py
python scripts/gap_scan_interval_analysis.py
```

These scripts require no participant records or copyrighted source PDFs. PDF filenames/page references are provenance pointers only. Saved baseline tables are included; running scripts updates the working copy and its build timestamps. Verify the untouched archive before making a working copy for reruns.

## ADNI/NACC estimates and controlled-data reproduction

`logs/adni_independent_2026-11-02/` and `logs/nacc_independent_2026-11-03/` contain saved estimates, paired contrasts, weight/balance diagnostics, grid sensitivities, source-file hashes and numerical checks. JSON is the full-precision source; CSV tables facilitate inspection. There are no visit-level or participant-level rows. Source audits give aggregate eligibility accounting and input specifications. `figures_frontiers/` contains the two cohort-result figures.

To rerun the participant-level models, obtain access independently from https://adni.loni.usc.edu and https://naccdata.org. Place the authorized ADNI clinical ZIP exports in `downloads/adni/` and the NACC September 2026 Quick-Access clinical CSV in `downloads/nacc/investigator_nacc75.csv` in a working copy. The five ADNI archive names expected by `gap101_independent.py` are Diagnostic Summary, Registry, Subject Demographics, ApoE Genotyping - Results, and Medical History, with the phase suffixes specified in that script. No MRI/PET images are required. The NACC CSF inventory is an ancillary source-audit check, not an input to the four main models. A newer provider freeze may produce different estimates; input hashes identify the analyzed versions.

The original bootstrap settings were:

```text
python scripts/gap128_independent.py --reps 2000 --seed 20261102
python scripts/gap128_independent.py --literal-grid-only
python scripts/gap101_independent.py --bootstrap 1000 --seed 20261102
python scripts/gap128_nacc_independent.py --reps 2000 --participant-reps 200 --seed 20261103
python scripts/gap101_nacc_independent.py --bootstrap 1000 --seed 20261103 --mode centers
python scripts/nacc_incident_feasibility.py
```

Production reruns can take substantial time and write new results. Use a working copy, retain the frozen archive, and compare definitions, seeds and input hashes. Synthetic implementation checks can be run without provider data using `adni_analysis_validation.py` and `nacc_analysis_validation.py`; run those in a working copy too because they write reports. Source-audit and independent-report scripts that validate the original archives require those controlled files. Saved aggregate consistency is verified by the read-only command above.

The observation experiment masks existing diagnoses and preserves the final observation in its primary comparison; it is not a scheduling trial. First-reversion detection is separate from Markov state occupation. Composition weights target each exposure-specific enrolled CN+MCI reference and do not identify a causal correction for index-event bias. ADNI and NACC analyses are separate and do not establish person-level independence across databases.

## Additional integrated-manuscript checks

`logs/reviewer_revision_2026-11-09/` supplies the numerical contradiction-threshold replay, point-estimate Markov boundary refits, and weight-distribution summaries. `data/` and `config/` contain only published-study metadata and the matching review rules needed for the threshold replay. They are not participant data.

```text
python scripts/reviewer_contradiction_sensitivity.py
python scripts/reviewer_markov_sensitivity.py
```

The first command requires no provider records. The second requires the same authorized source records as the cohort analyses. Ten pairs additionally pass a uniform 0.05 screen; that does not classify them as genuine contradictions. Boundary refits leave observed detection curves unchanged. These supplementary checks belong to the integrated manuscript and do not imply that all are reported in either Frontiers derivative.
