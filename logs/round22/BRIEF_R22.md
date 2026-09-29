# Round 22: rewording VF-25 and VF-23 and appraising risk of bias for new supporters

**Paths.**
- Scratch root (R22): `C:/Users/aryas/AppData/Local/Temp/claude/C--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/scratchpad/r22`
- Round-21 root (R21): `.../scratchpad/r21`. It holds `pdf_text.py`, the cached source texts in `pdf/`, the packets and
  the round-21 readings in `out/`.
- Repository (READ-ONLY for you): `C:/Users/aryas/Downloads/PAPERINGO`.

Read `R21/SUPPORTER_BRIEF.md` first. Its rules, tools and neutrality requirement apply unchanged here. Write only under
`R22/out/`, and only with the Write tool.

## VF-25: the operator's scope decision (2026-09-29)

The operator decided that the bracketed list in VF-25's statement names examples, not a closed list. The statement is
reworded accordingly. The reworded statement, and the one supporters are judged against, is:

> **VF-25 (reworded).** Loading the system reveals MCI where the unloaded or static counterpart does not. Within the same
> study, a measure taken under a more demanding condition (for example dual-task or curved-path walking,
> peripheral-attention reaction time, or task-evoked responses), or a microstructural index of a structure (for example
> diffusion measures), separates MCI from a non-impaired comparator. The same measure under the simpler condition, or
> the corresponding volume, does not.

What counts as support:
- The study must run the dissociation itself, in the same participants and against a non-impaired comparator.
- The simpler-condition or static measure must be null by the study's own statistics.
- The loaded measure must separate the groups by those same statistics.
- A dissociation that holds marker by marker counts, when it is the same marker under the two conditions (for example
  velocity in straight versus curved walking).
- If the unloaded test separates the groups on other markers, record that as a caveat, not a disqualifier.
- If the static measure also separates the groups, the answer is `no`.
- If the dissociation holds only in a subgroup, answer `partly`.

## VF-23: operator-supplied revised statement (2026-09-29)

The operator supplied an external evaluator's reading of VF-23, with a revised statement. The review will adopt that
statement only in the form the corpus supports. The statement as supplied:

> EEG/MEG connectivity, graph/network, and complexity measures show promising potential for discriminating MCI and may
> reveal disease-related alterations that are not consistently detected by conventional spectral band-power measures.
> However, the current evidence does not establish that these measures universally outperform spectral band-power;
> rather, studies suggest that network- and complexity-based features can provide complementary information, with
> multi-domain combinations of spectral, complexity, and connectivity features often showing the strongest
> discrimination.

The evaluator also cited two outside syntheses:
- "a 2025 systematic review covering 124 EEG connectivity studies" (connectivity differences in most studies, most
  consistent in AD rather than MCI);
- "a 2026 systematic review of 21 wearable-EEG MCI studies" (connectivity features generally more accurate than
  spectral or slowing features, multi-domain combinations best, accuracy 46-95%).

**These two citations are NOT evidence unless they are corpus papers.** Check whether either is in the corpus: Grep the
summaries.md headers and blocks for the counts, the topic and the year. The review's house rule is corpus-scoped
wording. A claim that rests only on a paper outside the corpus cannot enter a convergence statement.

The statement is split into four clauses, each judged separately:
- **C1.** Connectivity, graph/network or complexity measures discriminate MCI from controls (group difference or
  classification) in the study's own data.
- **C2.** Within the same study, such a measure separates MCI where spectral band-power does not.
- **C3.** No corpus study establishes that these measures outperform spectral band-power. That would need a within-study,
  same-participant head-to-head of discrimination, ideally tested. Report every corpus study that runs such a
  head-to-head, and which way it came out.
- **C4.** Within the same study, a multi-domain combination (spectral plus complexity and/or connectivity) discriminates
  MCI better than any single domain.

A clause enters the adopted statement only if at least three independent studies (distinct dataset families) support it,
or, for C3, if the corpus search finds no head-to-head that establishes outperformance. Anything else is dropped or
hedged.

## Risk of bias for new supporters

Follow `C:/Users/aryas/Downloads/PAPERINGO/logs/rob_appraisal/ROB_BRIEF.md` exactly (tools, domains, categories,
verbatim quotations with pages). For the structure of a record, look at an existing one such as
`logs/rob_appraisal/P-58.json`.

These records are model readings under the operator's instruction of 2026-09-29. They have NOT been
reviewed by the operator, so do not write any review or acceptance field.

The result to appraise is the one named in `cited_result`: the result for which the study supports the convergence. The
source text is `R21/pdf/<P-id>.txt`; if it is missing, run `R21/pdf_text.py`.
