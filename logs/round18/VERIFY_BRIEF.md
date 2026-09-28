# Round 18: verifying the registry-audit error calls

Scratch root (ROOT): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r18/audit`

A first model reader audited coded registry rows against their source PDFs under ROOT/AUDIT_BRIEF.md
(read it in full: the field definitions and the match / defensible / error scale are binding). Their
results are in ROOT/out/<P-id>.json. You are the second reader for the studies assigned to you. For
every field the first reader judged `error` or `defensible`, decide independently whether the
registry code is supportable from the PDF under the definitions.

- Read the PDF text yourself (the path is in ROOT/sample_with_text.json under `pdf_text`, or, where the
  first reader's file names a `located_source` because the mapped PDF was the wrong paper, extract that
  PDF: the corrected map is `C:/Users/aryas/Downloads/PAPERINGO/data/paper_pdf_map.json`; use PyMuPDF
  (`import fitz`) from `C:/Users/aryas/Downloads/PAPERINGO/.venv/Scripts/python.exe` if you need to
  extract text, writing any helper script with the Write tool into ROOT/tmp/ and deleting it after).
- Check the first reader's quote is verbatim on the stated page.
- Be symmetric: overturn an `error` that is really `defensible` or `match`, and upgrade a
  `defensible` that the definitions actually rule out to `error`.
- For `error`, state the correct code.

Write one file per study: ROOT/verify/<P-id>.json, with the Write tool (never shell heredocs). Do not
modify any repository file.

```json
{
  "paper": "P-..",
  "pdf_used": "path of the text or PDF you read",
  "fields": {
    "Task_Type": {"first_verdict": "error", "final_verdict": "error|defensible|match", "correct_code": "T4", "reason": "...", "quote": "verbatim", "page": 1, "first_quote_verbatim": true}
  }
}
```

Include only the fields the first reader judged `error` or `defensible`. When done, reply with one
line per study: `P-id: field first->final (correct code)`.
