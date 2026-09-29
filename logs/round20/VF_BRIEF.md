# Convergence supporter check against source documents (round 20)

ROOT = `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r20/vf`; source texts in `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r20/pdf/<P-id>.txt` (page markers `=== page N ===`).

The review admits 22 convergences (cross-study statements supported by at least three independent studies). Until now
their supporters were checked against the structured summary records, and against the PDFs only where an audit
prompted it. You now check each supporter against its SOURCE DOCUMENT, read in full.

For each convergence assigned to you: read ROOT/records/<VF-id>.json (the ledger statement and the form in which the
review states it, Table A1 row). For EACH supporter, read its source text in full and decide:
- `right_paper`: is the document the study the ledger means (title/design plausible)?
- `supports`: `yes` (the study itself reports a result that supports the statement as the review states it, in the Table
  A1 row), `partly` (supports a narrower or weaker form; say which), or `no` (does not report such a result, reports the
  opposite, or only cites others for it).
- `quote` + `page`: the verbatim passage that decides it (or, for `no`, what you searched for).
- `note`: population, design or scope limits that matter for the statement.
Then give `verdict` for the convergence as the review states it: `holds` (at least three supporters answer yes, from
at least two distinct datasets or method families as the record claims), `holds_narrowed` (holds only in a narrower
form: give the narrower wording), or `fails` (fewer than three supporters answer yes or partly in a way that sustains
the statement). Be as ready to fail a convergence as to uphold it; do not reason from the fact that it was admitted.

You are read-only on the repository. Write ONE file per convergence, ROOT/out/<VF-id>.json, with the Write tool:
{"id":..., "supporters":[{"paper":..., "right_paper":true/false, "supports":"yes|partly|no", "quote":..., "page":..., "note":...}],
 "yes_count":n, "verdict":"holds|holds_narrowed|fails", "narrowed_wording":"...", "reasoning":"..."}
British spelling, no em-dashes. Finish with a one-line summary per convergence as your final reply.
