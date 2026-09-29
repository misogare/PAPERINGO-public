> Archived 2026-09-29 exactly as given to the round-20 readers on 2026-09-28. The title line was carried over from the round-18 brief; this round re-read the 173 candidates closed from summary records.

# Round 18: adjudicating the open contradiction candidates from the source documents

Scratch root (called ROOT below):
`C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r20/conf`
Repository (read-only for you): `C:/Users/aryas/Downloads/PAPERINGO`

## Your role and its limits

You are a model reader. The review's adjudicator (the human operator) has delegated this reading
on 2026-09-27 with the instruction: *"Read both source PDFs of each pair against the existing rule,
unchanged; a second agent checks any 'genuine' verdict."* You record a verdict and its grounds. You
do NOT write to any repository file (not the ledgers, not summaries.md, not the registry). Write only
your output files under ROOT/out/, using the Write tool (never shell heredocs).

The review has 201 contradiction candidates. 27 have already been adjudicated from both source documents;
the other 174 were closed at earlier adjudication from the summary records only (their current status is in
the record and in index.json as resolution_status_now). You are re-reading some of those 173 (one was withdrawn)
from both source documents in full, to see whether the verdicts hold on the sources. A current status is NOT
evidence: a false_positive or partially_resolved verdict reached from summaries may be wrong in either direction,
and a pair closed as "partially resolved by bridging evidence" must still be judged on whether the two studies
themselves meet the rule. **Neutrality is the whole point**: an external critic has objected that
"none genuine out of 201" may be a finding about the gate rather than the literature. Do not reject
a pair because similar pairs were rejected before, or because the review currently reports none
genuine; do not admit a pair to make the count non-zero. Decide only whether THIS pair meets the
rule below, on what the two source documents actually say.

## The rule, unchanged (literature_review.md, Appendix H.1)

| Claim type | Evidence required | Independence required | Adjudication rule | Recorded on failure as |
|---|---|---|---|---|
| Genuine contradiction | Two studies reporting incompatible results on the same quantity | Distinct studies; shared cohort or shared authorship recorded as non-independence | Four deterministic gates (task, dataset family, validation tier, modality family) matched or a mismatch waived on record, plus endpoint and metric-type compatibility judged at adjudication | False positive, partially resolved by bridging evidence, documented incommensurability, or unresolved |

Two statements already in the review bind how "incompatible" is read (Section 5.2):
- "a difference in statistical significance between two of them is not a data conflict";
- "reviews in this corpus often re-pool overlapping trial sets, so agreement between meta-analyses is
  not independent confirmation" (overlap is recorded as non-independence; it is not disqualifying).

How the rule has been applied to the 178 closed records (use the same reading; this is not a new rule):
- **Same quantity** = same estimand: same outcome or endpoint (including its horizon), same comparison
  (e.g. MCI vs controls, in blood), same population class behind the label, same metric type
  (a survival C-index is not a ROC-AUC; accuracy is not AUC; a hazard ratio is not a standardised
  mean difference).
- **Incompatible** = both results cannot be true of that quantity: opposite directions each supported
  by the study's own statistics, or estimates of the same quantity whose reported uncertainty excludes
  the other. NOT incompatible: significant vs non-significant in the same direction or with
  overlapping intervals; two different methods each reporting a different score where neither study
  claims the ordering the other refutes; a claim about X set against a claim about Y.
- **Gates.** Task (T1-T6), dataset family (a dataset family counts once however many papers it
  yields; e.g. ADNI), validation tier (V1-V4), modality family (e.g. structural MRI, functional MRI,
  EEG, fluid biomarkers). For two evidence syntheses, or two association studies that validate no
  model, the validation-tier gate is not applicable (count it as matched); the dataset family of a
  synthesis is the primary literature it pools. A gate mismatch can be waived only with a reason
  stated on the record; the precedent C-227 waived task/design/modality-subtype gates for a network
  meta-analysis against a randomised trial.
- **Performance-validity exclusion** (Section 2): studies reporting near-ceiling discrimination on
  internal validation with small samples (at or above 0.97 accuracy or AUC, no external cohort) are
  left out of cross-study PERFORMANCE comparisons. The pack marks `performance_suspect: true` where the
  registry's Best_Metric trips the automatic test. Check against the PDF whether the flag really
  applies (e.g. a meta-analysis quoting a marker's pooled AUC 0.97-0.98 is not an internal-validation
  classifier) and whether the candidate is a performance comparison at all. A performance comparison
  resting on a study that truly meets the flag cannot be genuine.

## Outcomes: use exactly one of these five values, no others

Apply in this order.
1. `false_positive`: not a disagreement at all. Read against the source documents, the two results
   are compatible (same direction, overlapping, significance-only difference), a claim in the record
   misreads its paper, or the record describes no disagreement (e.g. a filed note, an "architectural
   fork" in which both findings are presented as true, or a single-paper record).
2. `documented_incommensurability`: the studies report results on DIFFERENT quantities (different
   endpoint or horizon, metric type, population behind the shared label, predictor set or input
   representation, or a deterministic gate that fails with no waiver on record), so the results
   cannot be set against each other. Name the difference and quote both sides.
3. `partially_resolved_candidate`: a real disagreement on a comparable quantity that named third
   studies in the corpus largely explain, by showing the specific condition that separates the two
   results (e.g. assay platform, disease stage). "Heterogeneity" in general does not count. If the
   explanation is that the two measure different quantities, use `documented_incommensurability`.
   Find third studies with Grep on `C:/Users/aryas/Downloads/PAPERINGO/summaries.md` (headers are
   `## Paper N: title`); cite each by P-ID with a quote from its block.
4. `genuine`: incompatible results on the same quantity, gates matched (or waived on record with a
   reason you can state), endpoint and metric type compatible, performance-validity exclusion not
   triggered.
5. `unresolved`: the source documents do not settle it. Say exactly what information is missing.

## What you must read

For each record assigned to you:
- the record: ROOT/records/<C-id>.json (claims, statement, any earlier gate diagnostics and notes);
- the pack entry in ROOT/index.json (papers, roles, registry rows, suspect flags, PDF text paths);
- BOTH source documents IN FULL: the text files ROOT/pdf/<P-id>.txt (PyMuPDF extracts; page markers
  `=== page N ===`). Read methods, results, tables and limitations, not only the abstract. Papers
  named in the record beyond paper_a/paper_b are there for context (for C-262 the pair is
  P-13 x P-1128, named in its statement).
- the precedents in ROOT/precedents.json (C-80, C-204, C-227, C-232, C-234 were admitted as genuine at
  earlier adjudication and later reclassified as documented incommensurability once both source
  documents were read; the others show how false positives and bridging-evidence resolutions were
  written).

Elsevier extracts sometimes drop or mangle `<`, `>`, `=`, `±`; decode from the paper's own
conventions and say so when a verdict depends on it. First check that each PDF is the paper the
record means (title, authors, year); if a PDF is the wrong paper, say so and use `unresolved` unless
the other evidence settles it.

The ledger record's own text may be wrong (it was drafted from summary records, sometimes by
automated passes). Check every number and direction it states against the PDF and list errors in
`record_defects`.

## Output: one file per record, ROOT/out/<C-id>.json

```json
{
  "id": "C-xxx",
  "reader": "adjudicator",
  "papers_read": [{"paper": "P-..", "pdf_is_right_paper": true, "title_as_printed": "...", "read_in_full": true}],
  "what_each_reports": {"P-a": "the result on the contested quantity, with numbers", "P-b": "..."},
  "quantity_a": "the estimand P-a reports (outcome, comparison, population, metric, horizon)",
  "quantity_b": "the estimand P-b reports",
  "same_quantity": false,
  "incompatible_if_same": "would the results be incompatible if the quantities were the same? why",
  "gates": {"task": "match|mismatch|n/a: detail", "dataset_family": "...", "validation_tier": "...", "modality_family": "..."},
  "endpoint_and_metric": "compatible|incompatible: detail",
  "performance_validity": "not a performance comparison | flag does not apply because ... | flag applies because ...",
  "independence": "shared cohort/authors/pooled trials, if any",
  "bridging_evidence": [{"paper": "P-..", "quote": "...", "how_it_bears": "..."}],
  "verdict": "false_positive|documented_incommensurability|partially_resolved_candidate|genuine|unresolved",
  "grounds": ["one sentence per ground, each tied to a quote below"],
  "evidence_quotes": [{"paper": "P-..", "page": 3, "quote": "verbatim from the extract"}],
  "record_defects": ["statements in the ledger record that the PDFs contradict"],
  "confidence": "high|medium|low",
  "what_would_change_the_verdict": "..."
}
```

Keep quotes verbatim and short (one or two sentences each), with page numbers. When done, reply with
one line per record: `C-id: verdict (confidence) - one-clause reason`.
