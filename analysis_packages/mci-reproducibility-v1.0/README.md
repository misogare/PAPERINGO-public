# MCI code and aggregate-output packages

Version 1.0 | Prepared 9 November 2026

This release adds reproducibility materials for three alternative manuscript formats. It does not deposit a manuscript or claim that the ADNI/NACC paper is submitted, peer reviewed or available as a preprint.

| Package | Use | Contains |
|---|---|---|
| `review/` | Literature-only Frontiers Review | Code and seven source-checked published-input tables; no participant-derived cohort results |
| `cohort/` | ADNI/NACC Original Research | Analysis and validation scripts, aggregate estimates, paired contrasts, weighting diagnostics, source hashes and two result figures |
| `integrated/` | Alternative integrated manuscript | Both preceding sets plus the contradiction-threshold and Markov-boundary revision checks |

Downloadable ZIPs are attached to [the versioned release](https://github.com/misogare/PAPERINGO-public/releases/tag/mci-reproducibility-v1.0). Their SHA-256 digests and content manifests are recorded in `INDEX.json`. Each folder can be used as a standalone working root; its README explains the offline checks and the separate steps requiring authorized source-data access.

Run `python scripts/verify_reproducibility_package.py` inside an extracted package to check hashes and saved numerical results. This check uses only the Python standard library. Production ADNI/NACC reruns require provider access; raw records are not supplied. The code was additionally exercised in isolated working copies without provider data: published-input calculations, synthetic numerical checks and the integrated registry threshold screen passed.

The core-review archive and its existing DOI/version are separate. These new archives must not be cited as though they were contained in the earlier core release. In particular, the integrated package freezes the local registry/configuration used for its own threshold replay; it does not overwrite the repository's historical core records. The packages' `publicly_deposited` field records their state at build time, before this repository upload.

No participant identifiers, visit-level observations, medical images, EEG recordings, source-paper PDFs, extracted publication text, credentials or signed download links are included. Published-paper identifiers and source-data column names are provenance, not individual records. Source data remain governed by the providers' agreements. Existing MIT and content licences are retained; neither grants access to controlled records.

The integrated manuscript combines the review and cohort experiments. It is an alternative publication format, not a third independent study to publish alongside both Frontiers derivatives. The Review must use only the review package; the cohort/integrated packages are not its supplementary data.
