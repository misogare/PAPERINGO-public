# Round 21: searching the corpus for further supporters of seven thin convergences

Scratch root (ROOT): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r21`
Repository (READ-ONLY for you): `C:/Users/aryas/Downloads/PAPERINGO`

## Why this search exists

The review admits a convergence (a "VF record") only when enough independent studies support it. In round 20 each
admitted convergence's supporters were re-read from their source PDFs. Seven came out thin: VF-10, VF-15, VF-18
and VF-20 hold only in a narrower wording, and VF-23, VF-25 and VF-27 fell below three supporters. The review's
operator judges that the four narrowed ones make sense and has kept them. The problem is that they do not have enough
supporting papers. The honest remedy is to look through the whole corpus for studies that genuinely support the
statement and that the pipeline missed. A remedy that counts one sample twice, or stretches a study to fit, is not
acceptable.

**Neutrality is the whole point.** The operator's view that a statement "makes sense" is not evidence. Do not propose
or accept a paper to bring a count up to three, and do not reject one to keep it down. Judge only whether THIS study's
own data and analysis, as printed in its source document, demonstrate the statement. Finding nothing is a valid
outcome, and the review will then say so.

## The rule (literature_review.md, Appendix H.1, unchanged)

| Claim type | Evidence required | Independence required | Adjudication rule | Recorded on failure as |
|---|---|---|---|---|
| Convergence (VF record) | At least three studies each supporting the statement as written, checked against the study record rather than the abstract | Method family and dataset family both distinct across supporters; a dataset family counts once however many papers it yields | Each supporter re-read against the statement; scope stated as the union of the conditions actually demonstrated; all-ADNI exceptions declared on the record | Rejected, or admitted with a narrower statement and the scope named |

The rule has been applied as follows:
- **Support** means the study's OWN data and OWN analysis show what the statement says. Where the statement says
  "within-study" or "head-to-head", the study itself must run that comparison.
- **Syntheses.** A review or meta-analysis supports only a statement about syntheses. Citing or summarising other
  studies is not support, and a narrative review never supports a primary-evidence statement.
- **What goes with a support verdict.** Every "yes" needs a quotation from the results or tables. Answer `partly` when
  the study shows part of the statement, or shows it on a different comparator or population, and name the part it
  shows.
- **Independence.** A dataset family counts once however many papers it yields. Examples: ADNI; NACC; CHARLS;
  I-CONECT; one hospital's cohort reported in several papers. A paper that re-reports the same participants as an
  existing supporter adds no independent support (P-665 and P-687 are one Guangzhou sample of 30 MCI and 30 controls).
  Record any shared cohort, shared recruitment site or shared authors with the existing supporters.
- **Exclusions.** Registry duplicate stubs are listed in ROOT/registry_duplicates.json and appear as the
  `duplicate_of` column. Also excluded: conference abstracts with no analysable results, editorials and commentaries,
  and protocols with no results. Preprints are allowed but must be flagged.
- **Previously rejected supporters.** These are listed in the packet under rejected_supporters, with reasons in
  record_notes. Re-propose one only if its source document shows the rejection was wrong, and name the specific
  rejection ground you are answering.
- **Wrong PDFs.** The PDF map is wrong for P-112, P-114, P-115, P-117 and P-174. For every paper, check that the PDF's
  title and authors match the summaries.md block before relying on it.

## Tools

- **Summary blocks.** `summaries.md` sits at the repository root: 1,418 blocks, 31 MB, each headed
  `## Paper N: title`. It is NOT in numeric order.
  - Use Grep with `output_mode: "content"` and small context windows.
  - Print whole blocks with `PYTHONIOENCODING=utf-8 python C:/Users/aryas/Downloads/PAPERINGO/scripts/get_blocks.py 651 665`.
  - Never cut blocks with sed or awk ranges.
- **Semantic retrieval.** Run from the repository root:
  `PYTHONIOENCODING=utf-8 python scripts/paper_kb.py ask "<question>" --raw --top-k 30`.
  - It ranks chunks from summaries.md and the ledgers.
  - Its ledger chunks quote records, not papers: go to the paper's block before proposing anything.
- **Registry.** `data/full_paper_registry.csv` has columns Modality, Task_Type, Dataset, Sample_N_Approx,
  study_design_type and duplicate_of. Read it with Python csv, never by editing it.
- **Source PDF text.** Run `PYTHONIOENCODING=utf-8 python ROOT/pdf_text.py P-651 P-665` from ROOT.
  - It writes ROOT/pdf/<P-id>.txt with `=== page N ===` markers.
  - It prints the identity checks (map entry, block title, first lines of page 1).
- **Elsevier extracts** sometimes drop or mangle `<`, `>`, `=` and `±`. Decode these from the paper's own conventions,
  and say so when a verdict depends on it.

Write only under ROOT/out/ with the Write tool, never with shell heredocs. Do not modify any repository file.
