# Drafted appraisals awaiting the author's review (2026-10-23)

These four risk-of-bias appraisals were drafted on 2026-10-23 by a model reader (Claude Fable 5.1) at the author's
request, after the four studies were cited for the first time in Section 4.1.5 of the thesis (Table 4, the measures
compared with hippocampal volume on the same participants). They follow `../ROB_BRIEF.md` and the format of the
accepted records in the parent folder, and each `signalling` quotation was checked mechanically against the mapped PDF
(`quote_check` on each record).

| Study | Tool | Draft overall | Why it is cited |
|---|---|---|---|
| P-945 (Ge et al. 2021) | PROBAST | high | baseline MRI volumes add nothing to a longitudinal cognitive Cox model; their one-year change does |
| P-993 (Dang et al. 2023) | PROBAST | high | hippocampus-centred covariance network against the hippocampus alone, cohort-dependent |
| P-1066 (Padulo et al. 2023) | JBI analytical cross-sectional | some concerns | amygdala subfields smaller in converters where hippocampal volume did not differ significantly |
| P-1097 (Amland et al. 2024) | QUIPS | high (four moderate domains) | hippocampal volume's association with conversion attenuated once the MMSE was adjusted for |

They are kept in this folder and NOT in `logs/rob_appraisal/` because `scripts/make_appraisal_table.py`,
`scripts/rob_convergence_sensitivity.py` and `scripts/make_journal_figures.py` read the parent folder, and a record there
counts as an appraisal. Until the author has reviewed them, the thesis reports 193 appraised of 210 cited studies and
names these four as drafted and not counted (Sections 3.9 and 9, Appendix G).

## To accept them

1. Read each record against its PDF; change judgments where you disagree and say so in `notes`.
2. Set `review_status` to the accepted form used in the parent folder (for example "reviewed and accepted by the operator
   (2026-10-24)") and add an `operator_review_<date>` block as the other records carry.
3. Move the four files into `logs/rob_appraisal/` (keep this README here as the record of the drafting date).
4. Run, in this order: `make_appraisal_table.py --write`, `rob_convergence_sensitivity.py`, `make_journal_figures.py`
   (Figure 7 of the thesis), `evidence_freeze_check.py --write`.
5. Change the counts in Sections 3.9 and 9 and Appendix G of the thesis (193 of 210 appraised becomes 197 of 210; the
   four sentences that name the drafts go), the CANON entry "cited studies at high risk of bias" in
   `scripts/evidence_freeze_check.py`, and the supplement history line.
