# Round 18: are these classification studies externally validated (V4)?

Scratch root (ROOT): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r18/v4`
Repository (read-only for you): `C:/Users/aryas/Downloads/PAPERINGO`

The review reports that 3.8% of its 502 classification studies validate externally. A screen of the
summary records found 25 classification studies coded V1, V2 or V3 whose validation section describes
an external or independent cohort (in one batch the summariser appears to have written "V1" for an
external test set). You decide, from each study's PDF, which validation tier the study actually reaches.
You do NOT write to any repository file; write only ROOT/out/<P-id>.json with the Write tool (never
shell heredocs).

## The codes (config/extraction_template.json, quoted) and how V4 has been applied

V1 = LOOCV small-n (<100); V2 = k-fold CV same dataset, no separate test set; V3 = k-fold + held-out test
partition; V4 = external institutional validation (different institution or country). "If paper says
'train/test split' without external site, assign V3. If same dataset different fold, assign V2."

At the review's audit of every study coded V4 (2026-09-27) the code was kept only for a model or score
that was fixed in development and then evaluated on a cohort from another institution or country that
took no part in its development. NOT V4: a held-out partition of the development data; a later wave,
phase or visit of the development cohort (e.g. ADNI-GO or ADNI-3 after ADNI-1/2); cross-validation that
pools several cohorts so the test cohort also trains the model; external validation of a single marker or
gene rather than of the model the study reports; a "validation cohort" recruited at the same institution
as the training cohort. A test set from a different consortium or institution (e.g. a model trained on
ADNI and tested on AIBL, OASIS, NACC, J-ADNI or a named hospital cohort) IS V4 when the model was fixed
before being applied to it. If a study reports several tiers, code the highest one it actually reaches.

## Procedure

For each study in your list (ROOT/pack.json gives the PDF text path, the map title and the registry codes):
1. Check the PDF is the right paper: compare its title with the study's block header in
   `C:/Users/aryas/Downloads/PAPERINGO/summaries.md` (Grep `^## Paper N:`). If it is not, search
   `downloads/` for the right PDF by title (PyMuPDF via `C:/Users/aryas/Downloads/PAPERINGO/.venv/Scripts/python.exe`,
   helper scripts written with the Write tool into ROOT/tmp/ and deleted after); if none is found, judge
   from the summary block and say so.
2. Read the methods and results sections that describe how the model was trained and tested, and code
   the tier. Quote the decisive sentence(s) verbatim with page numbers.

## Output: ROOT/out/<P-id>.json

```json
{"paper": "P-..", "pdf_is_right_paper": true, "source_read": "path or 'summary block only'",
 "registry_code": "V1", "correct_code": "V4", "changed": true,
 "training_data": "cohort(s) and institution(s)", "test_data": "cohort(s) and institution(s)",
 "model_fixed_before_external_test": true,
 "reason": "one or two sentences", "quotes": [{"page": 5, "quote": "verbatim"}], "confidence": "high|medium|low"}
```

When done, reply with one line per study: `P-id: registry -> correct (confidence)`.
