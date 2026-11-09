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

## Review boundary

This is the archive for the literature-only Frontiers Review. It deliberately contains no ADNI/NACC experiment estimates or images. The corpus-wide registry, risk appraisals and comparison records remain a separate core-review resource; this archive documents the additional published-input checks, not a reproducible reconstruction of the literature search. Do not attach the cohort or integrated archive as this Review's supplement.
