# Round 18: blinded audit of a random registry sample against the source PDFs

Scratch root (called ROOT below):
`C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r18/audit`
Repository (read-only for you): `C:/Users/aryas/Downloads/PAPERINGO`

## Purpose

The review's census statistics (task mix, research domain, validation tier, ADNI dependence,
publication-year trends, sample sizes) are computed from the coded registry
`data/full_paper_registry.csv` over 1,318 included studies. The 167 studies the review cites were
audited record by record; the other 1,151 were never checked against their source documents. A
random sample of 60 of those 1,151 (seed 20260927) is being re-coded from the PDFs to measure the
registry's error rate. You audit 10 of them. You do NOT write to any repository file; write only your
output files under ROOT/out/, using the Write tool (never shell heredocs).

## Procedure: code blind first, then compare

1. Open ROOT/packs/pack<K>_blind.json (K is your pack number). For each study it gives the P-ID and
   the path of the full PDF text (PyMuPDF extract; page markers `=== page N ===`).
2. For each study, FIRST check the PDF is a research paper matching `title_in_pdf_map`; then read
   enough of it (abstract, methods, results, and tables; the whole paper where needed) to code the
   six fields below YOURSELF, from the PDF alone. Do not open the registry, summaries.md or the codes
   file before you have written down your own codes for all ten studies (record them in the
   `blind_codes` object of each output file).
3. THEN open ROOT/codes/pack<K>_registry_codes.json and compare field by field. For every
   disagreement, re-read the relevant passage and the definition, and judge:
   - `match`: the registry code agrees with the PDF (identical to yours, or your code was the slip);
   - `defensible`: the PDF honestly supports more than one code under the definitions and the
     registry's is one of them (say what the two readings are);
   - `error`: the PDF contradicts the registry code under the definitions (give the correct code
     and a verbatim quote with page number).
   Your own blind code is not automatically right; the question is only whether the registry code is
   supportable from the PDF under the definitions.

## Field definitions (config/extraction_template.json, quoted)

**Task_Type** (one of T1-T6).
T1 = MCI vs HC current-state DISCRIMINATION; T2 = pMCI vs sMCI conversion prediction; T3 = multi-class
staging HC/EMCI/LMCI/AD; T4 = subtype/biomarker/correlational; T5 = MCI reversion prediction;
T6 = clinical/population/review/descriptive.
Deciding test for T1 vs T4: "WHETHER DISCRIMINATION IS QUANTIFIED, NOT WHETHER A HEALTHY CONTROL
GROUP EXISTS. Assign T1 only when the study fits or evaluates a classifier, a diagnostic model or a
cut-point and reports (or would report) a discrimination figure - accuracy, AUC, F1,
sensitivity/specificity or kappa. A study that merely compares group MEANS between MCI and controls
(t-test, ANOVA, effect size, correlation, odds ratio, 'significant group difference') is T4, however
central the MCI-vs-HC contrast is to its argument." "T2: map to T2 ONLY if the task is explicitly
progressive vs stable MCI or MCI-to-AD conversion; do not map general MCI detection to T2." "T5:
reversion-to-normal PREDICTION only. Never use T5 for interventions, trials or reviews."
Reviews, meta-analyses, trials, epidemiology and descriptive clinical studies are T6 unless their
core result is one of T1-T5.

**Category** (research data domain; may list several, e.g. `C1;C5`).
C1 = Neuroimaging (MRI/fMRI/DTI/PET); C2 = Electrophysiology (EEG/MEG/ERP/fNIRS); C3 = Molecular/Fluid
(CSF/plasma/genomics/omics); C4 = Behavioural/Digital (speech/language/gait/eye/handwriting/sensors);
C5 = Clinical/Population (EHR/surveys/neuropsych/cohorts/epidemiology); C6 = Peripheral/Indirect
markers (retinal/oculomics and other non-CNS scans linked to MCI). The method (e.g. deep learning)
does not decide the category; the data domain does. Judge `error` when the study's primary data
domain is missing from the code, or a listed domain has no basis in the paper; judge `defensible`
when the code omits a secondary domain or lists one the paper uses only marginally.

**Validation_Type** (V1-V4).
V1 = LOOCV small-n (<100); V2 = k-fold CV same dataset, no separate test set; V3 = k-fold + held-out
test partition; V4 = external institutional validation (different institution or country).
"If paper says 'train/test split' without external site, assign V3. If same dataset different fold,
assign V2. Absence of reporting = V2 not V4." Convention: a study that fits no predictive model
(group comparison, association, trial, review) is coded V2; for such a study V2 is `match`, and
V1/V3/V4 is an `error` unless the paper really validates a model. Note in your output whether the
study fits/evaluates a predictive or classification model (`is_model_study`), because the review's
validation statistics are computed over classification studies only.
V4 requires a test set from a different institution or country than the training data; a later
phase of the same consortium (e.g. ADNI-3 after ADNI-1/2) is not an external institution.

**Sample_N_Approx** (total N of human participants analysed; per-group sums allowed).
Compare the NUMBER. The registry string may carry commentary; judge only whether its total is right.
`match` if the total agrees with the paper's analysed N (or its enrolled N, when the registry says
which); `defensible` if the paper reports several Ns (enrolled vs analysed, discovery vs validation)
and the registry uses one of them; `error` if the number is not a figure the paper supports. For
reviews/meta-analyses the N may be a count of studies or pooled participants. `NR`/`Not reported` is
`match` only if the paper really reports no N.

**Publication_Year**: year of publication, online-first date acceptable when print is later. Either
the online or the issue year is `match`; a year that is neither is `error`.

**ADNI_Dependent** (true/false): "True if ADNI (any phase) is the primary or sole dataset. False if
ADNI is used only for secondary comparison." A study that does not use ADNI is false.

## Output: one file per study, ROOT/out/<P-id>.json

```json
{
  "paper": "P-..",
  "pack": K,
  "pdf_is_right_paper": true,
  "title_as_printed": "...",
  "is_model_study": true,
  "blind_codes": {"Task_Type": "T4", "Category": "C3", "Validation_Type": "V2", "Sample_N": "153 (91 EMCI + 62 NC)", "Publication_Year": "2023", "ADNI_Dependent": "false"},
  "registry_codes": {"Task_Type": "...", "Category": "...", "Validation_Type": "...", "Sample_N_Approx": "...", "Publication_Year": "...", "ADNI_Dependent": "..."},
  "judgement": {
    "Task_Type": {"verdict": "match|defensible|error", "correct_code": "T4", "reason": "...", "quote": "verbatim", "page": 2},
    "Category": {...}, "Validation_Type": {...}, "Sample_N_Approx": {...}, "Publication_Year": {...}, "ADNI_Dependent": {...}
  },
  "notes": "anything else wrong in the coded row that bears on the census statistics (optional)"
}
```

`quote`/`page` are required for every `error` and `defensible` verdict and optional for `match`.
When done, reply with one line per study: `P-id: fields in error (or none)`.
