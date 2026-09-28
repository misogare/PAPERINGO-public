# Round 19: mining the corpus for genuine contradictions (2026-09-27)

Operator instruction: find the genuine contradictions in the corpus, under the review's existing rule (Appendix H.1), unchanged.

## Funnel

- 1,318 included studies -> 4,198 directional claim atoms from 1,008 studies (compact summary extracts; `atoms.json`).
- First pass (topic pairing by agents + ledger leads): 28 candidates (`first_pass_candidates.json`).
- Recall pass: every pair of different studies in the same topic with opposite directions (or a significant effect against a null) and overlapping wording: 692 study pairs (`opposite_pairs.json`); judges kept 44, excluded 676.
- Screen against both summary blocks: 42 unique candidates; 32 dropped, 10 advanced.
- Adjudicated from both source documents in full: 10; genuine: 0. Near-misses re-read by an advocate: 3; all upheld as documented incommensurability.

## Adjudicated pairs

| Pair | Verdict | Deciding element |
|---|---|---|
| P-249|P-674 | documented_incommensurability | No. The element that fails is horizon and dose. |
| P-1081|P-1151 | documented_incommensurability | No. The exposure and the reference group differ in definition, and these are the elements that decide what is estimated. |
| P-671|P-986 | documented_incommensurability | No. Element by element: |
| P-1220|P-1396 | false_positive | No. In plasma both studies report valine lower in MCI than in controls. P-1396 reports FC 0.90 at FDR < 0.01 (Table 3, p.7). P-1220 lists valine among the metab |
| P-1275|P-1341 | false_positive | No. This is a difference in significance only, and the intervals overlap. P-1275's blood-NfL CI [0.32, 0.49] lies entirely inside P-1341's plasma CI [-0.36, 0.5 |
| P-249|P-680 | documented_incommensurability | Taken as numbers alone, the 95% CIs are disjoint: P-249's upper bound of 0.21 is below P-680's lower bound of 0.741. If the two estimated the same quantity, bot |
| P-517|P-529 | false_positive | No. Both results can hold at once: ALFF can be raised in the right posterior fusiform gyrus and lowered in the adjacent lateral inferior temporal gyrus. This is |
| P-517|P-774 | documented_incommensurability | No. Both results can be true at once. P-517's increase foci in ventral BA10/ACC may come from seeds other than the PCC, such as hippocampal, mPFC or ICA-compone |
| P-644|P-878 | false_positive | No. P-878's own statistics do not support the opposite direction on any of the three exposures, and read against its own data it agrees with P-644. |
| P-711|P-986 | documented_incommensurability | Not incompatible in the rule's sense. The candidate says the intervals 'exclude each other' (1.01 to 1.03 against 0.92 to 0.96), but a random-effects CI describ |

Advocate re-reads (near-misses): P-249|P-674: documented_incommensurability; P-1081|P-1151: documented_incommensurability; P-671|P-986: documented_incommensurability

Model readers only; nothing was written to the ledgers or the manuscript. Per-candidate grounds and quotes: `recall_result.json`, `final_result.json`.
