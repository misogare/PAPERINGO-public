# Follow-ups from the human reading (2026-09-19)

Manuscript and ledger consequences of the human verdicts, to be applied after scoring (week 3 of the plan).
Verdicts themselves are in human_adjudication_worksheet_2026-09-19.csv; this file lists only what a verdict changes.

**Status 2026-09-19: APPLIED** (same day). Ledgers: CF-193 and C-80 rejected with human-adjudication provenance,
VF-01 pruned to four supporters (rejected_supporters P-51, P-66), VF-05 statement anchor corrected, IA-13 483-not-526,
VF-06/VF-08-candidate annotated with the reader admits (re-promotion pending operator), G-349/350/351 annotated as
out-of-sample human admits; ledger_lint --strict clean. literature_review.md: Section 5 now four contradictions
(C-80 moved to incommensurable), Table 2/4 and the Appendix A map updated, Section 9.3 and the limitations table now
report the human-agreement statistic, new C.4 paragraph documents the exercise. Figure 3 and the C.2 freeze counts
stay at 2026-09-15 until the single final re-freeze.

**Update later on 2026-09-19:** five census items adjudicated (C-232, CF-248, CF-254, CF-273, CF-185 all admit; item 8
CF-190 skipped at the reader's instruction); sample now 40/96, accuracy 0.88 (95% CI 0.74-0.95), kappa 0.59. Operator
decisions applied: VF-05 PROMOTED to confirmed as scoped (Table 4 now 22 records; Section 4.3 sentence added), VF-06
DROPPED, VF-08-candidate endorsed but held below the three-supporter bar (uncited). Week-4 refs rebuild: add a
reference entry for P-48, now cited by identifier in Section 4.3; a targeted third-supporter search for
VF-08-candidate (graph-vs-regional head-to-heads) would let it enter Table 4.

| Item | Record | Verdict | Consequence |
|---|---|---|---|
| 4 | CF-193 (P-82 x P-185) | reject | literature_review.md 5.2 cites the pair as a supporting comparison for C-80 ("on an identical cortical-morphometry feature substrate ... no clear margin"); the substrates differ (FreeSurfer v6.0/DKT/62 regions/5 measures vs v5.3.0/AAL/90 regions/2 measures) and the validation protocols differ (held-out test vs 20x repeated 10-fold CV), and the record's own numbers (83.3 vs 69.37) contradict "no clear margin". Remove the sentence, drop CF-193 from the Appendix A record map and Figure 3 counts, set complementary.json CF-193 to rejected with the reason, and let the C-80 "Reading" column stand on C-80's own evidence only. |
| 34 | VF-01 | admit | Supporter prune: the P-51 block contains no hippocampal/MTL content at all (historical curation flag confirmed) and P-66 only uses hippocampus/amygdala ROIs as model input; drop both from supporting_papers. Four verified supporters remain (P-50, P-52, P-99, P-595). |
| 36 | IA-13 | admit | Detail correction: the multi-ingredient arm totals 483 participants (6 trials) in the P-151 block rewritten 2026-09-15, not 526 as the record states; substantive contrast vs omega-3 N=2,293 unchanged. |
| 41 | VF-05 | admit | Statement text cites (P-243, P-48, P-320) but the anchor list and verified evidence are P-243, P-48, P-81; correct P-320 to P-81 in the record statement. |
| 8 | CF-190 | reject | APPLIED 2026-09-19: supporter P-201 breaches the performance-suspect rule (AUC 0.98 internal, N=40), and a CF cannot rest on one admissible study. complementary.json CF-190 set rejected with human provenance; Section 6 card removed; the sound P-268 single-study observation (encoding 93.10 vs resting 67.24) retained in Section 4.2; Geng [P-201] reference entry removed (orphaned); C.2/9.1 counts now 188 confirmed / 9 cited. |
| 42 | C-80 | reject | Reader rejects C-80 as incommensurable: P-82 consumes pre-computed FreeSurfer tabular ROI features, P-3 consumes image-level CNN input, so the 83.3-vs-74.61 gap confounds representation with architecture (same substrate-error family as item 4). literature_review.md 5.2 carries a C-80 row (see item 4 entry): at the apply pass, rework or drop the C-80 comparison and its Reading column, and set conflicts.json C-80 accordingly. |

## Out-of-sample reader verdicts (2026-09-19)

Recorded at the reader's instruction. NOT part of the 96-item random sample and excluded from scoring, so the sample's accuracy/kappa estimates stay unbiased; apply at the ledger level in the week-3 pass.

| Record | Reader verdict | Note |
|---|---|---|
| G-349 | admit | Harmonised primary outcome battery across non-pharmacological MCI intervention reviews (anchors P-838, P-839, P-841). |
| G-350 | admit | Harmonised East-Asian cost-effectiveness / willingness-to-pay threshold for MCI disease-modifying treatments (anchors P-1030, P-1031). |
| G-351 | admit | Integrated US claims-pathway model linking atrial fibrillation / anticoagulation exposure to incident cognitive impairment (anchors P-1028, P-798). |


## Continuation batch, 2026-09-21 (items 11, 37, 56-59, 62, 64, 70, 72, 95, 96)

Eleven reader verdicts recorded under the standing protocol (external AI reading aid disclosed per item; assistant re-verified every quoted figure against the summary blocks before recording): IA-20 reject (concurs with rejection), VF-31 admit, G-CAND-330 admit (DISAGREEMENT, see below), G-CAND-34 reject (concurs), G-07 admit, IA-39 admit, G-09 admit, ACTOR-G-034 admit, ACTOR-G-033 admit, G-01 admit, G-17 admit. Verification notes: P-368 gamma elevation + underexplored statement confirmed; VF-31 all four supporter findings confirmed in blocks; P-560 sulcal-width-beats-thickness and AUC 0.907 confirmed; P-420 ICC ranges confirmed verbatim.

APPLIED: G-CAND-330 rejected -> open with review_status=confirmed_human_reader (human-favoured application; reader note concedes the title is not a research problem but holds field limitations are gap material). Concurrence annotations added to the ten agreeing records. Figure 3 regenerated (open gaps 178 -> 179); master counts, Section 9.3, C.4, limitations table, README_public, Version B and skeleton updated. New scoring: 69 items, accuracy 0.90 (62/69; 95% CI 0.81-0.95), sensitivity 0.92, specificity 0.81, kappa 0.72 (0.50-0.90); per kind C 6/7, CF 10/12, G 20/21, IA 18/18, VF 8/11; seven disagreements.

HELD: CF-07 (item 11). The reader returned an admit rationale for a general diagnosis-vs-prognosis complementarity of P-56/P-48, not for the record stated insight (attention weights as biomarker-importance proxy; P-56 is an SVM model, which is the pipeline rejection ground the reader own text corroborates). No verdict recorded; the item is now PRIMED for this reader (pipeline verdict disclosed during the exchange) and excluded from the estimate unless re-adjudicated with the flag carried.

DECLINED (second occurrence): operator asked that the remaining blank items be filled with the pipeline verdicts on the ground of having read the records at generation time. Declined as before: generation-time exposure is not a blind reading, and copying verdicts makes the agreement statistic circular. 27 items remain blind and reserved for a second independent adjudicator.


## En-bloc confirmations, 2026-09-21 (items 60-94 range, 26 records)

The reader clarified the earlier instruction: they state the remaining records were reviewed against their sources during pipeline operation and the pipeline verdicts found correct, and instructed that this be recorded. RECORDED as reader confirmations on all 26 remaining items (verdict = pipeline verdict, note = [NONBLIND] marker with provenance). Because these judgments were formed with the pipeline verdicts visible, they are confirmations rather than blind adjudications: the scoring script now auto-excludes [NONBLIND] rows, the blinded estimate stays 69/96 (accuracy 0.90, CI 0.81-0.95, kappa 0.72), and C.4/9.3 document the split. The second-adjudicator reserve is unaffected (a second reader reads all 27 non-estimate items cold). CF-07 remains the only unverdicted item (held + primed; reader last said admit, the en-bloc instruction implies reject; awaiting one explicit word).


## CF-07 resolution, 2026-09-21

Reader rejected CF-07 on re-reading (their words: the two papers do not really match each other, different aspects), concurring with the pipeline rejection. Recorded with the [NONBLIND] marker (pipeline verdict and ground had been disclosed before this verdict) and annotated on the ledger record; outside the blinded estimate. The worksheet is now FULLY VERDICTED: 96/96 human-reviewed, 69 blind (the estimate: accuracy 0.90, CI 0.81-0.95, kappa 0.72) + 27 non-blind; the 27 remain the second-adjudicator cold-read reserve.
