# Round 18: challenging the adjudicated contradiction verdicts

Scratch root (ROOT): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r18/conf`
Repository (read-only for you): `C:/Users/aryas/Downloads/PAPERINGO`

You are an adversarial second reader. A first model reader has adjudicated each record assigned to you
under the rule in ROOT/CONFLICT_BRIEF.md (read it in full first; the rule, the reading of
"incompatible", the gates, the performance-validity exclusion and the five outcome values are binding
and unchanged). Their verdict is in ROOT/out/<C-id>.json.

Your job is to try to overturn each verdict, in the direction that matters:
- If the verdict is `genuine`: try to show the pair is NOT a genuine contradiction (a difference in the
  quantity measured, a gate that fails, a compatible reading of the numbers, a performance-validity
  case, a misread PDF).
- If the verdict is anything else: try to show the pair IS a genuine contradiction, or a different
  outcome than the one given. An external critic suspects the review's gate is too strict ("none of
  201 is genuine is a finding about the gate"). Take that seriously: look for a comparable quantity on
  which the two studies' results are truly incompatible, including one the ledger record did not name;
  check whether the first reader over-applied a rule, treated a significance difference as a quantity
  difference, or dismissed a gate mismatch that could reasonably be waived on the record.

Read both source documents IN FULL yourself (ROOT/pdf/<P-id>.txt, page markers `=== page N ===`); do
not rely on the first reader's quotes; verify each quote they give against the text. You may Grep
`C:/Users/aryas/Downloads/PAPERINGO/summaries.md` for bridging studies.

Then decide honestly. Upholding is the right answer when the first reader is right; overturning only
counts if the rule, applied as written, requires it.

Write one file per record: ROOT/challenge/<C-id>.json, with the Write tool (never shell heredocs). Do
not modify any repository file.

```json
{
  "id": "C-xxx",
  "reader": "challenger",
  "first_reader_verdict": "...",
  "quotes_checked": [{"paper": "P-..", "page": 3, "quote": "...", "found_verbatim": true}],
  "strongest_case_against_the_verdict": "the best argument you could build for a different outcome",
  "why_it_does_or_does_not_succeed": "...",
  "challenge_result": "upheld|overturned|modified",
  "proposed_verdict": "false_positive|documented_incommensurability|partially_resolved_candidate|genuine|unresolved",
  "grounds": ["one sentence per ground"],
  "evidence_quotes": [{"paper": "P-..", "page": 3, "quote": "verbatim"}],
  "corrections_to_first_reader": ["factual errors in the first reader's file, if any"],
  "confidence": "high|medium|low"
}
```

`modified` means the outcome value stays but the grounds must change materially (say how). When done,
reply with one line per record: `C-id: upheld|overturned|modified -> verdict - one-clause reason`.
