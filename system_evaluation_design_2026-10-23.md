# Evaluating the synthesis method against alternatives: design

Status: designed on 2026-10-23, not run. Nothing in the thesis or the article reports a result from this protocol.
Author: Aria Shobeiri (design drafted with AI assistance, as declared in the thesis, Section 2.9).

## 1. Why this is needed

Section 2.10 of the thesis measures two things about the synthesis: how consistently its single reader judged the
claim records (agreement 0.90 with the pipeline on 69 blinded records, kappa 0.72; a second reading agreed on 73%,
kappa 0.43) and how often the study coding was wrong (22 of 360 fields, 6.1%). Both are internal checks. Neither says
whether the method produces better, more complete or more trustworthy cross-study claims than the ways a researcher
would otherwise obtain them. The supervisor's comment of 2026-10-22 asks for exactly that: the system evaluated against
the alternatives, not only its coding and its adjudication.

An informal comparison against a general chat model was run in August 2026 (`questions/comparison_chatgpt_vs_system.md`,
`questions/question_report_v2.md`). It is not usable as evidence: the two arms received different question sets, the
chat transcripts were pasted incompletely, the model was unversioned and used without a login, no retrieval baseline was
run, and the model was asked corpus-scoped questions without access to the corpus, so the comparison measured access
rather than ability. The design below keeps what that pilot showed to be informative (fabricated corpus membership,
citation validity, negative controls) and removes those defects.

## 2. What is being evaluated

The unit of evaluation is the cross-study claim: a convergence, a contradiction verdict, a complementary pairing, a
bounded belief or a corpus-relative absence, as defined in Appendix H.1 of the thesis. The system under test (arm A) is
the released method at version 1.4 (doi:10.5281/zenodo.23490521): candidate generation by software under written rules,
followed by human adjudication from the source documents, with every verdict recorded.

## 3. Arms

| Arm | What it is | What it measures |
|---|---|---|
| A. The synthesis method | Ledger records and their adjudication status at the 1.4 release | The system as used |
| B. Conventional two-reader synthesis (reference standard for claim generation) | Two readers, neither the author, independently list the cross-study claims supported by a stratified slice of the corpus, applying the same written rules; disagreements resolved by discussion, producing reference set R | What a careful manual review finds in the same studies |
| C. General language model without corpus access | A pinned, versioned model given the same questions as D, zero-shot, logged in full | What general knowledge supplies without the corpus; access-confounded by design and reported as such |
| D. Plain retrieval baseline | One retrieval step (BM25 or embeddings) over the same PDFs, one generation step, no comparability rules, no adjudication | What the system adds over "search the PDFs and ask a model" |
| E. Previous reviews | Pellegrini et al. 2018, Ansart et al. 2021, Grueso and Viejo-Sobera 2021, Borchert et al. 2023, Xia et al. 2025, Vermeulen et al. 2025: their stated conclusions on the studies they share with this corpus | The historical baseline the field already has |

Arm B cannot be staffed by the author, who made every arm-A judgment; it needs two independent readers. Arms C and D
need API access and the question bank; arm E needs only reading.

## 4. Tasks and metrics

| Task | Items | Arms | Primary metric | Secondary |
|---|---|---|---|---|
| T1 Claim generation | A stratified slice of 60 included studies (20 from each of three modality groups: structural MRI, EEG/MEG, clinical and population), drawn with a fixed seed | A, D against B (reference set R) | Recall and precision of claims against R, by claim type | Claims found by A or D and missed by both readers, re-read by the readers (coverage of R itself) |
| T2 Contradiction adjudication | The 201 candidate contradiction pairs, with the author's source-read verdicts (146 incommensurable, 54 not disagreements, 1 withdrawn, 0 genuine) as reference | C and D give blind verdicts from the two papers' full texts | Agreement with the reference, kappa | Share of pairs called genuine by the arm (the false-positive rate the reference says is the main failure mode) |
| T3 Corpus-scoped questions | The 22 question sets (`questions/question_sets_v4.json`), including the negative controls | A (the question-answering interface over the ledgers), C, D | Citation validity (every cited study exists in the corpus and supports the sentence that cites it); fabricated corpus membership (a study asserted to be in the corpus that is not) | Scope errors (corpus-level counts asserted from a partial pack); correctness against the ledger key; labelled declines on negative controls |
| T4 Coverage against previous reviews | The 22 admitted convergences, the 7 bounded beliefs and the previous reviews' conclusions on the shared studies | A against E | Share of A's claims stated in any previous review; share of E's conclusions that A reproduces, bounds or contradicts | What each side has that the other lacks, listed |
| T5 Cost | Every arm | A, B, C, D | Reader hours, tokens and wall time per claim produced | |

## 5. Blinding, pre-registration and order

1. This protocol is committed to the repository before any arm is run; the question sets, the slice, the seeds and the
   model versions are frozen in that commit.
2. The readers of arm B receive the 60 studies and the written rules, not the ledgers; the claims of arms A, B and D are
   then presented to two scorers in random order without provenance.
3. Arms C and D are run once, logged in full (prompt, model version, retrieved passages, answer), and not re-run after
   seeing the scores.
4. T2 verdicts from arms C and D are scored against the recorded reference before anyone reads the arm's reasoning.

## 6. Sample size

For T2, detecting a difference between 0.90 agreement (the system's own blinded figure) and 0.75 at 80% power with a
two-sided 5% test needs about 70 paired items; all 201 candidate pairs are used. For T1, 60 studies are expected to yield
40 to 60 reference claims; recall is reported with Wilson intervals, so the estimate will be imprecise but honest. For T3
the 22 sets, with 77 scored turns in the pilot, give proportions with intervals of about plus or minus 10 points.

## 7. Analysis

Proportions with 95% Wilson intervals; kappa with bootstrap intervals; McNemar's test for paired arm comparisons on the
same items (T2, T3); per-arm fabrication rate reported as a count and a share; T4 reported as two lists, not a score.
No composite score across tasks: the tasks measure different things and a single number would hide which.

## 8. What already exists and what is missing

Exists: the question bank and scorer (`questions/`), the source-read verdicts and quotations for T2
(`logs/round18/`, `data/conflicts.json`), the ledgers and the question-answering interface for T3, the registry for drawing
the slice, and the previous reviews' conclusions as read in Section 8.2 of the thesis.

Missing: two independent readers for arm B; API access for arms C and D (keys are kept outside the repository); the
time to run and score. Until these exist, the thesis states that the method has not been evaluated against an
alternative (Section 9) and reports no result from this design.

## 9. What the result would and would not show

A favourable result would show that the method finds more of the reference claims than a plain retrieval baseline,
adjudicates contradictions closer to the source-read reference than a general model, and cites only studies that exist and
support the sentence. It would not show that the claims are true: that depends on the studies, whose risk of bias
Appendix G reports. An unfavourable result on T1 would mean that the admitted convergences are a subset of what two
readers would find, which is what Section 9 already allows for.
