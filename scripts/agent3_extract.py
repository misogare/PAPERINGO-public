"""
agent3_extract.py — Extraction Agent
Extracts structured records from summaries.md → full_paper_registry.csv
Applies normalisation rules from extraction_normalisation_rules.json.

Runs in API mode when ANTHROPIC_API_KEY exists, otherwise uses local deterministic parsing.

=== FIXES APPLIED IN THIS VERSION ===
1. NEW: generic section parser (parse_sections + get_section) replaces ALL the
   brittle per-field regexes that searched for one exact phrase like
   "### Venue/Publisher" or "### Sample N". Those headers do not exist in the
   real summaries.md template — the actual headers are "### Venue" and there
   is NO sample-size header at all. The generic parser instead:
     a) splits the text on every actual "### ..." header line, whatever it says
     b) scores each discovered header against keyword sets (e.g. ["novelty"],
        ["venue"], ["gaps","limitations"]) and returns the best match
   This is robust to header-phrasing drift across different summarisation
   batches, instead of requiring an exact-string match per field.
2. NEW: Sample_N_Approx now extracts from FREE TEXT inside "What did the
   authors do?" / "Key methods used" (e.g. "n=32", "(n=72)", "72 older adults")
   since no dedicated header exists for it. Previously hardcoded/header-searched
   and always "not reported".
3. _local_extract() populates all 8 new fields (study_design_type, task_substage,
   population_specificity, modality_subtype, prediction_horizon_years,
   primary_metric_type, duplicate_of) with real pattern-matching logic.
4. Best_AUC values stored as percentages (e.g. "87") are normalised to decimal
   (0.87) at extraction time, so agent4's numeric gap calculations don't see a
   spurious 86-point gap against correctly-scaled papers.
5. task_substage is scoped by the paper's own Task_Type (a T1 paper cannot be
   labelled pMCI_vs_sMCI) and modality_subtype is scoped to Method_Architecture
   text only, not the full summary, so a passing mention of "EEG" in a
   literature-comparison sentence doesn't override the paper's real modality.
6. design_comparison_valid removed from REGISTRY_FIELDS — it's a pairwise
   property computed by agent4 during conflict detection, not extractable for
   a single paper.
7. _normalise_record() validates all 8 new fields against allowed value sets.
8. CSV writes are sanitised (null bytes / control chars stripped, line endings
   normalised) so malformed source text can't corrupt the registry file itself.
9. Self-improvement hook: reads config/schema_additions.json at startup and
   merges any newly-learned fields into REGISTRY_FIELDS + the extraction prompt.
"""

import csv
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("agent3_extract")

ROOT        = Path(__file__).parent.parent
DATA        = ROOT / "data"
LOGS        = ROOT / "logs"
CONFIG      = ROOT / "config"
SUMMARIES   = DATA / "summaries.md"
SUMMARIES_FALLBACK = ROOT / "summaries.md"
REGISTRY    = DATA / "full_paper_registry.csv"
EXTRACT_LOG = LOGS / "agent3_extraction_log.txt"
TEMPLATE    = CONFIG / "extraction_template.json"
NORM_RULES  = CONFIG / "extraction_normalisation_rules.json"
SCHEMA_ADDITIONS = CONFIG / "schema_additions.json"   # written by pipeline_selfimprovement.py

ANTH_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Static fields (always present) ─────────────────────────────────────────────
REGISTRY_FIELDS = [
    "Paper_ID", "Category", "Modality", "Method_Architecture",
    "Task_Type", "Best_Metric", "Best_AUC", "Dataset", "Sample_N_Approx",
    "Validation_Type", "Key_Novelty", "Primary_Limitation", "Open_Problems", "Journal",
    "Architecture_Family", "XAI_Method",
    "Publication_Year", "ADNI_Dependent",
    "study_design_type", "task_substage", "population_specificity",
    "modality_subtype", "prediction_horizon_years", "primary_metric_type",
    "duplicate_of",
    # NOTE: design_comparison_valid REMOVED — pairwise property, computed by agent4.
]

VALID_STUDY_DESIGN = {
    "cross_sectional", "longitudinal_2point", "longitudinal_multipoint",
    "future_value_forecasting", "survival_analysis", "review_meta_analysis",
    # 21 curated rows already carry randomized_controlled_trial, but it was absent
    # from this validator, so a correctly detected trial would have been reset to
    # "not reported" on write. Same input/output vocabulary drift as the metric
    # types above.
    "randomized_controlled_trial",
    # Invited commentaries, editorials and letters carry zero primary data. They
    # used to fall through to cross_sectional (the largest comparability bucket
    # Agent 4 pairs on) or be hand-coded review_meta_analysis (PAPERINGO-mb8).
    "commentary_editorial",
    # Documented-extension additions (batch P-1413..P-1423, PAPERINGO-r850):
    # these tokens were already carried by curated corpus rows (case_control x15,
    # retrospective_cohort x8) or were the only accurate label for a batch row,
    # but were absent here, so the validator flagged them out-of-vocabulary.
    # Same input/output vocabulary drift as the RCT case above.
    "case_control",
    "retrospective_cohort",
    "not reported",
}

# Case/alias variants seen in the curated registry for the same design concept
# (PAPERINGO-1wl). The validator canonicalises these instead of resetting them.
_DESIGN_ALIASES = {
    "rct": "randomized_controlled_trial",
    "randomised_controlled_trial": "randomized_controlled_trial",
    "interventional_rct": "randomized_controlled_trial",
    "randomized_clinical_trial": "randomized_controlled_trial",
    "non_research_editorial": "commentary_editorial",
    "editorial": "commentary_editorial",
    "commentary": "commentary_editorial",
    "letter_to_the_editor": "commentary_editorial",
    "correspondence": "commentary_editorial",
    "letter": "commentary_editorial",
    "randomised": "randomized_controlled_trial",
    "randomized": "randomized_controlled_trial",
    "qualitative_systematic_review": "review_meta_analysis",
    "scoping_review": "review_meta_analysis",
    "umbrella_review": "review_meta_analysis",
    "systematic_review": "review_meta_analysis",
    "meta_analysis": "review_meta_analysis",
    "narrative_review": "review_meta_analysis",
    "systematic_review_meta_analysis": "review_meta_analysis",
    "network_meta_analysis": "review_meta_analysis",
    "systematic_review_network_meta_analysis": "review_meta_analysis",
    "review": "review_meta_analysis",
    "cross_sectional_study": "cross_sectional",
    "cross_sectional_survey": "cross_sectional",
    "cross_sectional_observational": "cross_sectional",
    "survival": "survival_analysis",
    "cox_regression": "survival_analysis",
    "time_to_event": "survival_analysis",
}

# A declared out-of-vocabulary value is kept (with a note) when it is a clean
# snake_case token; only prose/blank values are reset. The curated registry
# already holds ~40 design and ~60 metric-type tokens outside the original
# enums, and resetting a declaration to "not reported" was silent information
# loss (PAPERINGO-n01u, PAPERINGO-1wl). Extending the enums themselves is an
# operator decision this code does not pre-empt.
_SOFT_VOCAB_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,60}$")

VALID_POPULATION = {
    "general", "T2DM", "sarcopenia", "chronic_pain", "occupational_exposure",
    "CHF", "PD", "ESRD", "COPD", "other_disease_specific", "not reported",
}
VALID_TASK_SUBSTAGE = {
    "SCD_to_MCI", "NC_to_MCI", "pMCI_vs_sMCI", "MCI_to_AD", "EMCI_to_AD",
    "MCI_vs_HC", "multiclass_staging", "MCI_reversion", "biomarker_correlation",
    # PAPERINGO-xrdg (operator-approved 2026-09-04): amyloid-status stratification
    # WITHIN aMCI (both arms patients, no controls). Instances: P-1282 (re-coded
    # from a false MCI_vs_HC label that let a conflict pair pass the gates); Kong
    # 2025 Clin Neurophysiol 180:2111374 pending acquisition (PAPERINGO-6q7q).
    "aMCI_Apos_vs_Aneg",
    # Documented-extension addition (batch P-1413..P-1423, PAPERINGO-r850):
    # public-awareness / willingness-to-pay surveys (P-629 live; P-1415 stubbed
    # duplicate). No cognitive-transition meaning, so it must never fall into the
    # MCI-transition buckets Agent 4's equality gates pair on.
    "awareness_survey",
    "not reported",
}
# Documented vocabulary for the modality_subtype comparator column, sourced from
# config/extraction_normalisation_rules.json ("modality_subtype_rules", added by
# the 2026-09-04 repair of the P-1048+ free-text drift: 314 distinct values in
# 330 rows made exact comparison meaningless and unrecognised values fell through
# Agent 4's bucket gate). Falls back to the detector's own tokens when the config
# section is absent, so older trees degrade to pre-2026-09-04 behaviour.
_VALID_MOD_SUBTYPE_RULES = {}
try:
    _msub_rules_raw = json.loads(NORM_RULES.read_text(encoding="utf-8")) if NORM_RULES.exists() else {}
    _VALID_MOD_SUBTYPE_RULES = _msub_rules_raw.get("modality_subtype_rules", {})
except Exception as _e:  # pragma: no cover - config problems must not kill the agent
    log.warning(f"Could not read normalisation rules for modality_subtype: {_e}")
VALID_MODALITY_SUBTYPE = (set(_VALID_MOD_SUBTYPE_RULES.get("canonical_vocabulary", []))
                          or set(_DETECTOR_MSUB_VOCAB)) | {"not reported"}
# Aliases recorded by the 2026-09-04 repair, forward direction only: read by
# _canon_modality_subtype at normalisation time and by registry_lint's
# out-of-vocabulary report. There is deliberately no reverse map.
_MSUB_KNOWN_ALIAS = dict(_VALID_MOD_SUBTYPE_RULES.get("aliases_applied_2026_09_04", {}))

# NOTE: this set is the OUTPUT validator; _VALID_METRIC_TYPES near the metric
# parser is the INPUT vocabulary. They must be kept in step. They were not: the
# parser could produce effect_size / Risk_Ratio / Correlation / descriptive, and
# then this set silently reset every one of them to "not reported" - which is how
# eight of the ten meta-analysis rows in batch P-828..P-837 lost a metric type
# that their blocks had declared correctly (PAPERINGO-3hk). The curated registry
# has contained effect_size, correlation, group_difference and qualitative for
# hundreds of rows, so the vocabulary here was behind the data, not ahead of it.
VALID_METRIC_TYPE = {
    "AUC_ROC", "Accuracy", "Balanced_Accuracy", "C_index",
    "Sensitivity_Specificity", "PPV", "Hazard_Ratio", "Odds_Ratio", "F1",
    "effect_size", "Risk_Ratio", "Correlation", "Kappa", "MMSE_change",
    "descriptive",
    # Documented-extension additions (batch P-1413..P-1423, PAPERINGO-r850):
    # prevalence x11 (survey/diagnostic-metric rows incl. P-629/P-1415) and
    # regression_coefficient x7 (MR beta and Cox-associated rows incl. P-1420)
    # were established corpus practice but missing from this enum.
    "prevalence", "regression_coefficient",
    "not reported",
}


def _canon_modality_subtype(value: str):
    """Canonical modality_subtype for a documented alias, else None. Mirrors
    _canon_design / _canon_metric_type; the table is the one written to
    config/extraction_normalisation_rules.json by the 2026-09-04 repair."""
    return _MSUB_KNOWN_ALIAS.get((value or "").strip())

# Detector vocabulary (module level so the write-time validator can fall back
# to it when the config section is missing).
_DETECTOR_MSUB_VOCAB = {
    "trimodal_MRI_PET_CSF", "structural_MRI_PET", "fNIRS", "EEG_graph_connectivity",
    "EEG_spectral", "acoustic_speech_features", "NLP_text_transcripts",
    "NLP_clinical_notes", "clinical_EHR", "driving_telemetry", "handwriting_EEG",
    "retinal_OCT", "plasma_biomarkers", "CSF_biomarkers",
    "neuropsychological_scores_only", "structural_MRI_only", "PET", "DTI",
}


# ── Self-improvement: load any newly-learned schema fields ─────────────────────
def _load_learned_schema_fields() -> dict:
    if not SCHEMA_ADDITIONS.exists():
        return {}
    try:
        learned = json.loads(SCHEMA_ADDITIONS.read_text(encoding="utf-8"))
        return learned if isinstance(learned, dict) else {}
    except Exception as e:
        log.warning(f"Failed to read schema_additions.json: {e}")
        return {}


_LEARNED_FIELDS = _load_learned_schema_fields()
for _field_name in _LEARNED_FIELDS:
    if _field_name not in REGISTRY_FIELDS:
        REGISTRY_FIELDS.append(_field_name)
        log.info(f"Agent 3: learned field '{_field_name}' added to registry schema "
                 f"from self-improvement cycle.")


def _load_config() -> tuple:
    template = json.loads(TEMPLATE.read_text()) if TEMPLATE.exists() else {}
    rules    = json.loads(NORM_RULES.read_text()) if NORM_RULES.exists() else {}
    return template, rules


def _get_existing_ids() -> set:
    if not REGISTRY.exists():
        return set()
    with open(REGISTRY, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ids = set()
        for row in reader:
            if "Paper_ID" in row:
                ids.add(row["Paper_ID"])
                continue
            for k, v in row.items():
                if isinstance(k, str) and k.lstrip("\ufeff") == "Paper_ID":
                    ids.add(v)
                    break
        return ids


# Summary blocks for duplicate PDFs, corrigenda and non-study documents (e.g. the
# ADNI procedures manual) are kept in summaries.md only for paper-number continuity.
# They carry an explicit instruction that agents 3-7 skip them, and a bracketed
# title marking them as not analysed. They hold no cohort, method or result, so the
# deterministic fallback parser fills every field with defaults and emits a row that
# looks like a real ADNI study. Detect them here rather than downstream.
_SKIP_PHRASE_RE = re.compile(r"SKIP(?:PED)?\b.{0,60}Agent\s*3|agents\s*\(3", re.IGNORECASE | re.DOTALL)
_SKIP_TITLE_RE = re.compile(
    r"^##\s*Paper\s*[^:\n]+:\s*(?:\[|Corrigendum\b)", re.IGNORECASE
)


def _skip_reason(block: str) -> str:
    """Return why this summary block must not be extracted, or '' if it should be."""
    header = block.split("\n", 1)[0]
    if _SKIP_TITLE_RE.match(header):
        return header.split(":", 1)[1].strip()[:80]
    if _SKIP_PHRASE_RE.search(block):
        return "block instructs downstream agents to skip"
    return ""


def _parse_summaries() -> dict:
    """Parse summaries.md into {paper_id: summary_text} dict."""
    source = SUMMARIES if SUMMARIES.exists() else SUMMARIES_FALLBACK
    if not source.exists():
        return {}
    content = source.read_text(encoding="utf-8", errors="replace")
    papers = {}
    blocks = re.split(r"\n(?=## Paper)", content)
    for block in blocks:
        if not block.strip():
            continue
        header_match = re.match(r"## Paper ([^:\n]+):", block)
        if header_match:
            raw_pid = header_match.group(1).strip()
            m = re.match(r"P-(\d+)$", raw_pid, re.IGNORECASE)
            if m:
                pid = f"P-{m.group(1)}"
            else:
                n = re.match(r"(\d+)$", raw_pid)
                pid = f"P-{n.group(1)}" if n else raw_pid
            papers[pid] = block
    return papers


# ── Generic section parser — replaces brittle per-field exact-phrase regexes ───

def parse_sections(text: str) -> dict:
    """
    Splits summary text on every '### <header>' line, whatever the header text
    actually says, and returns {header_text: content_until_next_header}.

    This is the fix for the Journal/Venue and Key_Novelty bugs: the previous
    code required an EXACT phrase like '### Venue/Publisher' or '### Key
    Novelty' to appear verbatim. The real template uses '### Venue' and
    '### What is the novelty of the paper?' — close but not identical, and
    different summarisation batches over a long-running project can drift in
    exact wording. Splitting generically and fuzzy-matching afterward (see
    get_section) is robust to that drift; hardcoding one exact phrase per
    field is not.
    """
    lines = text.splitlines()
    sections: dict = {}
    current_header = None
    buf: list = []
    for line in lines:
        h = re.match(r"^###\s*(.+?)\s*$", line.strip())
        if h:
            if current_header is not None:
                sections[current_header] = "\n".join(buf).strip()
            current_header = h.group(1).strip()
            buf = []
        else:
            if current_header is not None:
                buf.append(line)
    if current_header is not None:
        sections[current_header] = "\n".join(buf).strip()
    return sections


def get_section(sections: dict, keywords: list, min_overlap: int = 1) -> str | None:
    """
    Finds the section whose header best overlaps with `keywords` and returns
    its content, or None if nothing scores >= min_overlap.

    Example: get_section(sections, ["novelty"]) matches header
    "What is the novelty of the paper?" because "novelty" appears as a word
    in that header, regardless of the surrounding phrasing.
    """
    best_header, best_score = None, 0
    for header in sections:
        header_words = set(re.findall(r"[a-z]+", header.lower()))
        score = sum(1 for k in keywords if k.lower() in header_words)
        if score > best_score:
            best_score, best_header = score, header
    if best_header and best_score >= min_overlap:
        return sections[best_header]
    return None


def extract_sample_n_freetext(sections: dict) -> str:
    """
    Sample_N_Approx has NO dedicated header in the real summaries.md template
    — confirmed by inspecting the actual section list (What did the authors
    do?, Why did the authors..., What are the key components..., ..., Venue,
    DOI, Reference metadata confidence — no 'Sample N' anywhere). Sample size
    only ever appears as free text, e.g. "Participants with aMCI (n=32)...",
    "...a cohort of 72 older adults". This searches the methods-adjacent
    sections for that free-text pattern instead of a non-existent header.
    """
    search_text = " ".join(filter(None, [
        get_section(sections, ["authors", "do"]),
        get_section(sections, ["components", "method"]),
        get_section(sections, ["key", "methods"]),
    ]))
    patterns = [
        r"\bn\s*=\s*(\d+)",
        r"\(n[\s=]*(\d+)\)",
        r"(\d+)\s+(?:participants|subjects|patients|individuals|"
        r"older adults|MCI patients|controls|healthy controls)",
    ]
    for p in patterns:
        m = re.search(p, search_text, re.IGNORECASE)
        if m:
            return m.group(1)
    return "not reported"


# ── Pattern-matching helpers for the 8 new fields (local/no-API mode) ──────────

def _design_kw(low: str, keywords) -> bool:
    """True if any keyword appears in a sentence that does not DENY it.

    The design detector used to match bare substrings. P-828's block contains the
    sentence "No longitudinal outcome, no incidence, no hazard ratio, no
    time-to-event modelling.", so a cross-sectional prevalence study was coded
    survival_analysis off a phrase that was there to say the paper does NOT do it.
    This is the same negation-blindness that produced five false invalidated-
    assumption records through XAI_Method (PAPERINGO-kpj); the guard there is
    reused here rather than reinvented.
    """
    for k in keywords:
        start = 0
        while True:
            i = low.find(k, start)
            if i == -1:
                break
            if not _XAI_DENIAL.search(_sentence_at(low, i)):
                return True
            start = i + len(k)
    return False


_COMMENTARY_TITLE = re.compile(
    r"\b(?:editorial|commentary|letter to the editor|correspondence|viewpoint|"
    r"invited comment|guest editorial|perspective piece)\b", re.I,
)
_COMMENTARY_OPENING = (
    "invited commentary", "this commentary", "this editorial", "in this editorial",
    "letter to the editor", "commentary on the article", "we comment on",
    "the commentary reports no findings", "the editorial reports no",
)


def _detect_study_design_type(low: str, title_scope: str = "", title_line: str = "") -> str:
    # ORDER MATTERS: survival/forecasting cohorts are themselves longitudinal,
    # so the generic "longitudinal" catch-all must run LAST. Previously it ran
    # before survival/forecasting, collapsing those designs into
    # longitudinal_2point (149 papers over-bucketed, survival/forecasting
    # starved). Reviews go first since a review may merely *mention*
    # longitudinal studies it surveys.
    #
    # Commentaries/editorials/letters come first of all: they carry no primary
    # data, so no other design can apply, and they routinely DISCUSS reviews,
    # trials and cohorts (PAPERINGO-mb8). A bare "editorial"/"commentary" counts
    # only in the TITLE LINE - the opening of a systematic review routinely quotes
    # the journal's "editorial history" line, which is not a commentary cue.
    if title_line and _COMMENTARY_TITLE.search(title_line):
        return "commentary_editorial"
    if any(k in title_scope for k in _COMMENTARY_OPENING):
        return "commentary_editorial"
    if any(k in low for k in ["systematic review", "meta-analysis", "narrative review",
                               "review of the literature"]):
        return "review_meta_analysis"
    # A paper that announces itself as a randomised trial IS one, whatever else it
    # also is. This test used to sit BELOW the timepoint heuristic, so P-829 - a
    # triple-blinded placebo-controlled trial measured at three visits - was coded
    # longitudinal_multipoint off the phrase "time points". The trial phrasing is
    # also widened: the old list required the exact string "randomized controlled
    # trial", which P-829's own title ("A triple-blinded, randomized,
    # placebo-controlled trial") does not contain. Still judged from the title
    # scope only, because secondary analyses routinely draw participants from a
    # parent trial without themselves being one.
    if any(k in title_scope for k in ["randomised controlled trial", "randomized controlled trial",
                                       "randomised clinical trial", "randomized clinical trial",
                                       "randomised trial", "randomized trial",
                                       "placebo-controlled trial", "placebo controlled trial",
                                       "controlled clinical trial", "randomised, controlled",
                                       "randomized, controlled"]):
        return "randomized_controlled_trial"
    if _design_kw(low, ["survival", "cox regression", "cox model", "cox proportional",
                        "time-to-event", "time to conversion", "random survival forest",
                        "c-index", "concordance index", "kaplan-meier", "kaplan meier"]):
        return "survival_analysis"
    if _design_kw(low, ["future value", "forecast biomarker", "predict future",
                        "forecasting model", "time-series inference", "dynamic prognosis",
                        "forecast progression"]):
        return "future_value_forecasting"
    if _design_kw(low, ["3 timepoint", "4 timepoint", "5 timepoint", "time points",
                        "multiple visit", "repeated measure", "multi-timepoint",
                        "multi-time-point", "multiple timepoints", "trajectory model"]):
        return "longitudinal_multipoint"
    # A study that describes ITSELF as cross-sectional is cross-sectional even
    # when it draws on a longitudinal cohort. "Longitudinal" in these blocks
    # frequently describes the parent cohort (HABS-HD, CHARLS, CLHLS), not the
    # analysis, which mislabelled explicitly cross-sectional analyses.
    if any(k in low for k in ["cross-sectionally studied", "cross-sectional study",
                               "cross-sectional design", "cross-sectional analysis",
                               "cross-sectionally examined", "cross-sectional associations"]):
        return "cross_sectional"
    # Final fallback. Denial-guarded like the sets above (a block saying "no
    # longitudinal follow-up was performed" must not code longitudinal), and
    # "follow-up" only counts next to a visit/assessment/time word: an
    # interviewer's "two follow-up questions" is not a second timepoint
    # (P-857, PAPERINGO-zy6m).
    if _design_kw(low, ["longitudinal", "follow-up visit", "follow-up assessment",
                        "follow-up wave", "follow-up examination", "baseline and follow",
                        "followed up for", "followed for"]):
        return "longitudinal_2point"
    if re.search(r"follow-?up (?:period |of |at |after |over )?(?:\d+|\w+) ?(?:months?|years?|weeks?)\b", low) \
            and _design_kw(low, ["follow-up", "follow up"]):
        return "longitudinal_2point"
    return "cross_sectional"


_REFERENCE_REGION = re.compile(
    r"\n#{1,4}\s*(?:references?|reference list|bibliography)\b|\nreferences\s*\n|"
    r"\n\s*\[?1\]?\.?\s+[A-Z][a-z]+ [A-Z]{1,3}[,.].{0,80}\b(?:19|20)\d{2}\b",
    re.I,
)


def _strip_reference_region(text: str) -> str:
    """Cut a whole-block scope at the start of its reference list. Only used when a
    block has no descriptive sections to scope to; reference titles otherwise leak
    words like 'longitudinal' into the design detector (PAPERINGO-zy6m)."""
    m = _REFERENCE_REGION.search(text)
    return text[: m.start()] if m and m.start() > 200 else text


_EXPLICIT_STUDY_DESIGN = re.compile(
    r"study[ _]design[ _]type\b"
    r"(?:\s+(?:for|of|in|the|this|that|record|registry|column|field|value|paper's|"
    r"block's|own|here|should|be|recorded|remains|stays|is|therefore|thus))*"
    r"\s*(?:[:=]|\bis\b|\bas\b)"
    # The token must END the declaration: a following bare word means the block
    # is writing prose ("study_design_type: randomised, placebo-controlled trial
    # with ..." captured "randomised"; "prospective cohort ..." captured
    # "prospective"). A parenthetical or a dash-clause after the token is fine.
    r"\s*\**\s*[\"'`]?([A-Za-z][A-Za-z0-9_\-]{2,50})[\"'`]?(?=\s*[.,;:)\n(]|\s*\*|\s*$|\s+[-–—]\s)",
    re.I,
)


def _canon_design(value: str) -> str | None:
    """Case-fold and alias-normalise a study_design_type token; None if unusable."""
    tok = re.sub(r"[ \-]+", "_", (value or "").strip().strip("*\"'`").lower())
    tok = tok.strip("_.")
    if not tok:
        return None
    # "no_admissible_value_fits" and kin are a summariser's shrug, not a design.
    if re.search(r"no_admissible|none_fits|does_not_fit|not_fit|unclear|unknown", tok):
        return None
    if tok in _DESIGN_ALIASES:
        return _DESIGN_ALIASES[tok]
    if tok in ("not_reported", "nr", "not_applicable", "na", "not", "none", "no"):
        return "not reported"
    for v in VALID_STUDY_DESIGN:
        if v.lower() == tok:
            return v
    return tok if _SOFT_VOCAB_TOKEN.match(tok) else None


def _unique_declared_study_design(text: str) -> str | None:
    """The study_design_type a block declares outright, if it declares exactly one.
    Blocks in the current house style write e.g. 'study_design_type: prospective_cohort'
    (52 such declarations in summaries.md at the time of writing); a block naming two
    different values is discussing coding, not declaring its own (PAPERINGO-qlxz)."""
    if not text:
        return None
    found = set()
    for m in _EXPLICIT_STUDY_DESIGN.finditer(text):
        tok = m.group(1).strip()
        if tok.lower().split(" ")[0] in _METRIC_DECL_STOPWORDS | {"must"}:
            continue
        canon = _canon_design(tok)
        if canon:
            found.add(canon)
    return found.pop() if len(found) == 1 else None


def _detect_task_substage(low: str, task_type: str) -> str:
    """Scoped by Task_Type so a T1 paper can never be labelled pMCI_vs_sMCI etc."""
    if task_type == "T6":
        return "not reported"
    if task_type == "T5":
        return "MCI_reversion"
    if task_type == "T2":
        if any(k in low for k in ["pmci", "progressive mci", "stable mci", "smci", "p-mci", "s-mci"]):
            return "pMCI_vs_sMCI"
        if "emci" in low and ("conversion" in low or " to ad" in low):
            return "EMCI_to_AD"
        if "scd" in low and ("mci" in low or "progression" in low):
            return "SCD_to_MCI"
        return "MCI_to_AD"
    if task_type == "T1":
        return "MCI_vs_HC"
    if task_type == "T3":
        return "multiclass_staging"
    if task_type == "T4":
        return "biomarker_correlation"
    return "not reported"


# From an exclusion cue to the end of its sentence/clause. Kept deliberately
# broad on the cue side (excluded / exclusion criteria / ineligible / without a
# history of / free of / no history of) and narrow on the span (one sentence).
_EXCLUSION_CLAUSE = re.compile(
    r"(?:\bexclu(?:ded|sion|ding)\b|\bineligible\b|\bnot eligible\b|"
    r"\bwithout (?:a |any )?(?:history|diagnosis|evidence) of\b|"
    r"\bno (?:history|diagnosis|evidence) of\b|\bfree (?:of|from)\b)"
    r"[^.;\n]*[.;]?",
    re.I,
)


def _detect_population_specificity(low: str) -> str:
    """
    The population a study RECRUITED, not every condition it mentions.

    Comorbidity covariate lists, adjustment variables and descriptions of
    prior work's comparison groups all name diseases that have nothing to do
    with who was enrolled. Scanning broadly labelled an EEG study in MCI due
    to AD as T2DM (from a sentence about an earlier paper's comparison group),
    a plasma-biomarker study as ESRD (from "kidney disease" in an adjustment
    set) and a nutrition study as CHF (from "cardiac disease" in a comorbidity
    list). Callers therefore pass a narrow scope: the title plus the opening
    of "What did the authors do?", which is where the enrolled population is
    stated.

    Exclusion criteria live in exactly that scope and INVERT the meaning: P-854
    was coded PD because "individuals were excluded if they had a diagnosis of
    Parkinson's disease" (PAPERINGO-g2q4). Clauses governed by an exclusion cue
    are removed before matching.
    """
    low = _EXCLUSION_CLAUSE.sub(" ", low)
    if any(k in low for k in ["heart failure", " chf ", "chf-mci", "cardiac"]):
        return "CHF"
    if any(k in low for k in ["parkinson", "pd-mci", " pd "]):
        return "PD"
    if any(k in low for k in ["type 2 diabetes", "t2dm", "diabetic"]):
        return "T2DM"
    if "sarcopenia" in low:
        return "sarcopenia"
    if any(k in low for k in ["chronic pain", "fibromyalgia", "chronic low back pain",
                               "chronic lower back pain", "clbp"]):
        return "chronic_pain"
    if any(k in low for k in ["occupational", "miner", "pesticide", "dust exposure"]):
        return "occupational_exposure"
    if any(k in low for k in ["renal", "esrd", "kidney disease", "dialysis"]):
        return "ESRD"
    if "copd" in low or "chronic obstructive" in low:
        return "COPD"
    return "general"


def _detect_modality_subtype(low: str) -> str:
    # 2026-09-04: PET/SPECT/metabolomics/microbiome/genetics/methylation/
    # transcriptomics branches added — the C3-heavy P-1048+ zone exposed that
    # the detector could not express pure-nuclear-imaging, serum-only or
    # omics studies, which is how the free-text drift started. "pet" is now
    # word-boundary matched (a bare substring hit 'competing risks'); "oct"
    # likewise ('october', 'octogenarian' misfired to retinal_OCT).
    if all(k in low for k in ["mri", "pet", "csf"]):
        return "trimodal_MRI_PET_CSF"
    if "mri" in low and "pet" in low:
        return "structural_MRI_PET"
    if re.search(r"\bpet\b", low):
        return "PET"
    if re.search(r"\bspect\b", low):
        return "SPECT"
    if "fnirs" in low:
        return "fNIRS"
    if "eeg" in low and ("graph" in low or "connectivity" in low):
        return "EEG_graph_connectivity"
    if "eeg" in low:
        return "EEG_spectral"
    if "speech" in low and ("acoustic" in low or "prosod" in low):
        return "acoustic_speech_features"
    if "transcriptom" in low:
        return "transcriptomics"
    if any(k in low for k in ["transcript", "nlp", "language model", "clinical note"]):
        return "NLP_text_transcripts" if "transcript" in low else "NLP_clinical_notes"
    if "methylation" in low or "epigenetic" in low:
        return "DNA_methylation"
    if re.search(r"\bprs\b|polygenic|\bsnps?\b|genom", low):
        # Genetic exposures combined with MRI outcomes keep the legacy token.
        if "mri" in low:
            return "structural_MRI_plus_genetics"
        return "genetics_genomics"
    if "microbiom" in low:
        return "gut_microbiome"
    if "metabolom" in low or "lipidom" in low:
        return "blood_metabolomics"
    if "ehr" in low or "electronic health record" in low:
        return "clinical_EHR"
    if "driving" in low or "telemetry" in low:
        return "driving_telemetry"
    if "handwriting" in low:
        return "handwriting_EEG"
    if "retina" in low or re.search(r"\boct\b", low) or "fundus" in low:
        return "retinal_OCT"
    if "plasma" in low:
        return "plasma_biomarkers"
    if re.search(r"\bserum\b", low):
        return "serum_biomarkers"
    if "csf" in low and "biomarker" in low:
        return "CSF_biomarkers"
    if any(k in low for k in ["neuropsychological", "mmse", "moca", "cognitive score"]):
        return "neuropsychological_scores_only"
    if "mri" in low:
        return "structural_MRI_only"
    return "not reported"


def _detect_prediction_horizon(low: str, task_type: str = "") -> str:
    """
    Horizon over which conversion/reversion is predicted.

    Only meaningful for prediction tasks (T2 conversion, T5 reversion). It was
    previously computed for every paper from a bare "(\\d+)\\s*year" match over
    the whole summary, which harvested participant AGES ("aged 65 years",
    "80 years old") and wrote them into this column as horizons. Gate on task
    type, require an explicit horizon phrase, and reject age contexts and
    values outside a plausible follow-up range.
    """
    if task_type not in ("T2", "T5"):
        return "not reported"

    horizon_cues = (
        r"(?:follow(?:ed|-| )?up|followup|within|over|after|horizon|predict(?:ed|ing|ion)?\s+"
        r"(?:of\s+)?(?:conversion|progression|reversion)?|conversion\s+within|progression\s+within)"
    )
    for pattern, unit in [
        (horizon_cues + r"[^.]{0,40}?(\d+(?:\.\d+)?)\s*[-– ]?\s*year", "year"),
        (r"(\d+(?:\.\d+)?)\s*[-– ]?\s*year[^.]{0,30}?(?:follow(?:ed|-| )?up|horizon|conversion|progression)", "year"),
        (horizon_cues + r"[^.]{0,40}?(\d+)\s*month", "month"),
        (r"(\d+)\s*month[^.]{0,30}?(?:follow(?:ed|-| )?up|horizon|conversion|progression)", "month"),
    ]:
        for m in re.finditer(pattern, low, re.IGNORECASE):
            context = low[max(0, m.start() - 60):m.end() + 20]
            if re.search(r"\bage[ds]?\b|\bmean age\b|\byears old\b|\baged\b", context):
                continue
            val = float(m.group(1))
            if unit == "month":
                val = round(val / 12.0, 1)
            # Plausible MCI follow-up horizons only; anything larger is almost
            # certainly an age, a birth year or a recruitment span.
            if 0.1 <= val <= 20:
                return str(val)
    return "not reported"


def _detect_primary_metric_type(text: str) -> str:
    """Legacy full-text fallback, reached only when a block has no Best_Metric value
    at all. Every test is sentence-denial guarded (PAPERINGO-tz2): 'no concordance
    index ... no hazard ratio' must not derive Hazard_Ratio, and a block that merely
    enumerates the admissible vocabulary must not derive C_index."""
    low = text.lower()
    if _xai_asserted(low, r"\bauc\b|\broc\b"):
        return "AUC_ROC"
    if _xai_asserted(low, r"\bc-?index\b|\bconcordance\b"):
        return "C_index"
    if _xai_asserted(low, r"\bhazard ratio\b|\bhr\b"):
        return "Hazard_Ratio"
    if _xai_asserted(low, r"\bodds ratio\b"):
        return "Odds_Ratio"
    if _xai_asserted(low, r"\bbalanced accuracy\b|\bbacc\b"):
        return "Balanced_Accuracy"
    if _xai_asserted(low, r"\bf1\b|\bf-score\b"):
        return "F1"
    if _xai_asserted(low, r"\baccuracy\b"):
        return "Accuracy"
    return "not reported"


_VALID_METRIC_TYPES = {
    "auc_roc": "AUC_ROC",
    "accuracy": "Accuracy",
    "balanced_accuracy": "Balanced_Accuracy",
    "c_index": "C_index",
    "sensitivity_specificity": "Sensitivity_Specificity",
    "ppv": "PPV",
    "hazard_ratio": "Hazard_Ratio",
    "odds_ratio": "Odds_Ratio",
    "f1": "F1",
    "not reported": "not reported",
    "not_reported": "not reported",
    # Added for batch P-828..P-837 (closes PAPERINGO-3hk). Eight of that batch's
    # ten papers are meta-analyses whose unit of result is a pooled effect size,
    # and there was no admissible token for one, so every such row silently
    # became "not reported". The corpus has been recording effect_size on this
    # kind of row since P-642/P-698/P-699/P-705 - the vocabulary here had simply
    # never been widened to match what the curated rows already contain.
    "effect_size": "effect_size",
    "risk_ratio": "Risk_Ratio",
    "correlation": "Correlation",
    "sensitivity": "Sensitivity_Specificity",
    "specificity": "Sensitivity_Specificity",
    "concordance_index": "C_index",
    "kappa": "Kappa",
    "mmse_change": "MMSE_change",
    "descriptive": "descriptive",
    # Case/abbreviation aliases (PAPERINGO-1wl). The curated registry stores the
    # same concept under several spellings - Odds_Ratio/odds_ratio/OR, AUC_ROC/AUC,
    # C_index/c_index/C-index - so exact-match filters silently saw a fraction of
    # the evidence. New rows are canonicalised here; existing rows are never
    # rewritten (append-only).
    "auc": "AUC_ROC",
    "roc_auc": "AUC_ROC",
    "auroc": "AUC_ROC",
    "auc_delong": "AUC_ROC",
    "or": "Odds_Ratio",
    "aor": "Odds_Ratio",
    "adjusted_odds_ratio": "Odds_Ratio",
    "hr": "Hazard_Ratio",
    "ahr": "Hazard_Ratio",
    "adjusted_hazard_ratio": "Hazard_Ratio",
    "rr": "Risk_Ratio",
    "relative_risk": "Risk_Ratio",
    "cindex": "C_index",
    "c_statistic": "C_index",
    "concordance": "C_index",
    "smd": "effect_size",
    "hedges_g": "effect_size",
    "cohens_d": "effect_size",
    "cohen_d": "effect_size",
    "mean_difference": "effect_size",
    "standardised_mean_difference": "effect_size",
    "standardized_mean_difference": "effect_size",
    "sens_spec": "Sensitivity_Specificity",
    "spearman_correlation": "Correlation",
    "pearson_correlation": "Correlation",
    "correlation_coefficient": "Correlation",
    "cohens_kappa": "Kappa",
    "cohen_kappa": "Kappa",
    "regression_beta": "regression_coefficient",
    "beta": "regression_coefficient",
    "beta_coefficient": "regression_coefficient",
    "regression_coefficient": "regression_coefficient",
    "prevalence_point_estimate": "prevalence",
    "prevalence": "prevalence",
    "group_difference": "group_difference",
    "qualitative": "qualitative",
    # Operator mappings applied by hand at G3 for batch P-858..P-867 (PAPERINGO-n01u).
    "observed_expected_ratio": "descriptive",
    "descriptive_time_interval": "descriptive",
    "threshold_estimate": "descriptive",
    "descriptive_statistics": "descriptive",
    "proportion": "descriptive",
    "proportion_descriptive": "descriptive",
}


def _canon_metric_type(value: str) -> str | None:
    """Case-fold and alias-normalise a primary_metric_type token; None if unusable.
    Out-of-vocabulary but well-formed tokens (e.g. Observed_Expected_Ratio,
    Descriptive_Time_Interval) are returned as declared, snake_cased, rather than
    being dropped (PAPERINGO-n01u)."""
    raw = (value or "").strip().strip("*\"'`")
    tok = re.sub(r"[ \-]+", "_", raw.lower()).strip("_.")
    if not tok:
        return None
    if tok in ("not_reported", "nr", "not_applicable", "na", "n_a", "not", "none", "no"):
        return "not reported"
    if tok in _VALID_METRIC_TYPES:
        return _VALID_METRIC_TYPES[tok]
    for v in VALID_METRIC_TYPE:
        if v.lower() == tok:
            return v
    declared = re.sub(r"[ \-]+", "_", raw).strip("_.")
    return declared if _SOFT_VOCAB_TOKEN.match(declared) else None


# The house style states the intended value outright, e.g.
#   "NR. primary_metric_type for the registry is Hazard_Ratio, ..."
#   "NR. primary_metric_type for the registry: not reported. ..."
#   "**primary_metric_type: Observed_Expected_Ratio**"
# Honour that declaration before any pattern sniffing (PAPERINGO-4oy). The
# captured token is open (any identifier), not a fixed alternation: a fixed
# alternation could not even SEE an out-of-vocabulary declaration, which is how
# six of ten declarations in batch P-858..P-867 were dropped (PAPERINGO-n01u).
# Between the label and the separator only filler words are allowed. A lazy
# "anything up to 40 chars" gap let "primary_metric_type correlation: their
# endpoints ..." (a block discussing ANOTHER paper's coding) capture "their".
_DECL_FILLER = (
    r"(?:\s+(?:for|of|in|the|this|that|record|registry|column|field|value|paper's|"
    r"block's|own|here|should|be|recorded|remains|stays|is|therefore|thus))*"
)
_EXPLICIT_METRIC_TYPE = re.compile(
    r"primary[ _]metric[ _]type\b" + _DECL_FILLER + r"\s*(?:[:=]|\bis\b|\bas\b)"
    r"\s*\**\s*[\"'`]?([A-Za-z][A-Za-z0-9_\-]{1,40}(?: (?:ratio|specificity|accuracy|index|size|change|reported|difference|coefficient|estimate|interval))?)"
    r"[\"'`]?(?=[.,;:)\n]|\s*\*|\s|$)",
    re.I,
)
_METRIC_DECL_STOPWORDS = {"for", "the", "is", "of", "in", "should", "be", "recorded",
                          "as", "registry", "value", "column", "field", "this", "a", "an",
                          "well", "such", "any", "also", "only", "either", "both", "same",
                          "one", "that", "which", "it", "its"}


def _declared_metric_type(text: str) -> str | None:
    """Return the primary_metric_type the block declares outright, if it declares one.

    Pattern sniffing over Best_Metric prose is negation-blind: an unanchored search
    for 'hazard' or 'c.?index' fires on a sentence that DENIES the metric, e.g.
    'no concordance index ... and no hazard ratio ... belongs to this record', or on
    a block that merely enumerates the admissible vocabulary. Four of ten blocks in
    batch P-778..P-787 were mis-typed that way (P-780 and P-786 -> Hazard_Ratio,
    P-784 and P-787 -> C_index) while every one of them reports no metric at all.
    """
    if not text:
        return None
    for m in _EXPLICIT_METRIC_TYPE.finditer(text):
        tok = m.group(1).strip()
        if tok.lower() in _METRIC_DECL_STOPWORDS:
            continue
        return _canon_metric_type(tok)
    return None


def _unique_declared_metric_type(text: str) -> str | None:
    """Return the metric type a block declares, but only if it declares exactly one.

    A block that names two different primary_metric_type values is discussing
    coding rather than declaring its own, so it is not a usable declaration.
    """
    if not text:
        return None
    found = set()
    for m in _EXPLICIT_METRIC_TYPE.finditer(text):
        tok = m.group(1).strip()
        if tok.lower() in _METRIC_DECL_STOPWORDS:
            continue
        value = _canon_metric_type(tok)
        if value is not None:
            found.add(value)
    if len(found) == 1:
        return found.pop()
    return None


def _metric_type_from_best_metric(best_metric: str, full_text_fallback: str) -> str:
    """Derive from THIS paper's own Best_Metric field, not a blind full-text scan."""
    bm = (best_metric or "").strip()

    declared = _declared_metric_type(bm)
    if declared is not None:
        return declared

    # Best_Metric is now stored as the block's short "Assigned:" value, which means
    # the block's OWN "primary_metric_type: <token>" line - which sits further down
    # the same section - is no longer inside `bm`. Consult it here before any
    # sniffing. It requires the literal "primary_metric_type" label, so it cannot
    # fire on a metric name a block merely quotes while explaining it has none.
    #
    # But the label alone is not sufficient: P-817's block writes
    # "primary_metric_type C_index. Two contrasts matter for coding the present
    # paper" while discussing how a DIFFERENT paper would be coded, and names
    # Hazard_Ratio in the same breath. Taking the first match there would overwrite
    # a correct Accuracy with C_index. So the block-level declaration counts only
    # when the whole block agrees with itself - exactly one distinct value.
    declared = _unique_declared_metric_type(full_text_fallback)
    if declared is not None:
        return declared

    # An explicitly declared "NR" is trustworthy now that Best_Metric is parsed
    # from the block's own "Assigned:" line, so it must NOT fall through to the
    # blind full-text scan below: that scan reads the AUCs a block quotes while
    # EXPLAINING why it has none, and stamped AUC_ROC on all seven metric-free
    # rows of batch P-818..P-827.
    if _is_not_reported(bm) or bm.strip().upper() == "NR":
        return "not reported"

    if bm and bm.lower() not in ("not reported", "nr", ""):
        prefix_map = [
            (r"^auc\b", "AUC_ROC"),
            (r"^sroc\b", "AUC_ROC"),
            (r"^bacc\b", "Balanced_Accuracy"),
            (r"^acc\b", "Accuracy"),
            (r"^f1\b", "F1"),
            (r"^kappa\b", "not reported"),
            (r"^sens", "Sensitivity_Specificity"),
            (r"^spec", "Sensitivity_Specificity"),
            # Pooled estimates from meta-analyses. Anchored at the start of the
            # value so they read the estimator the block chose, not a number it
            # merely mentions. "OR"/"RR"/"HR" only ever appear abbreviated in
            # this position; spelled-out forms are kept below for prose values.
            (r"^smd\b", "effect_size"),
            (r"^md\b", "effect_size"),
            (r"^wmd\b", "effect_size"),
            (r"^hedges", "effect_size"),
            (r"^cohen", "effect_size"),
            (r"^g\b", "effect_size"),
            (r"^d\b", "effect_size"),
            (r"^or\b", "Odds_Ratio"),
            (r"^rr\b", "Risk_Ratio"),
            (r"^hr\b", "Hazard_Ratio"),
            (r"^r\b", "Correlation"),
            (r"c.?index", "C_index"),
            (r"hazard", "Hazard_Ratio"),
            (r"odds", "Odds_Ratio"),
            (r"risk ratio|relative risk", "Risk_Ratio"),
            (r"standardi[sz]ed mean difference|mean difference", "effect_size"),
            # House style tags a metric value-first - "0.84 AUC", "0.78 Acc",
            # "0.71 F1" - which the ^-anchored patterns above cannot see. These
            # unanchored forms are last so a more specific estimator always wins,
            # and they are safe because they run against the short declared
            # Best_Metric value, never against block prose.
            (r"\bauc\b", "AUC_ROC"),
            (r"\bbacc\b", "Balanced_Accuracy"),
            (r"\bacc\b", "Accuracy"),
            (r"\bf1\b", "F1"),
        ]
        low_bm = bm.lower()
        for pattern, mtype in prefix_map:
            if pattern.startswith("^"):
                if re.search(pattern, low_bm):
                    return mtype
            # Unanchored patterns are sentence-denial guarded (PAPERINGO-tz2 /
            # PAPERINGO-4oy): a Best_Metric such as "no hazard ratio reported"
            # that slips past the NR tests must not be typed Hazard_Ratio.
            elif _xai_asserted(low_bm, pattern):
                return mtype
        return "not reported"
    return _detect_primary_metric_type(full_text_fallback)


# Blocks in this corpus routinely NAME an explainability method in order to say it
# was NOT used: "They did not use SHAP", "the strings ... and 'SHAP' appear zero
# times", and "...algorithms such as XGBoost combined with Shapley Additive
# Explanations (SHAP)" inside a future-work sentence. Matching the bare token
# published five false invalidated-assumption records in an earlier batch
# (PAPERINGO-kpj) and mislabelled P-821 and P-822 in batch P-818..P-827, so a
# token now only counts when the SENTENCE containing it carries no denial or
# hypothetical cue. Sentence scoping rather than a character window, so a genuine
# use sitting near an unrelated negation still registers.
_XAI_DENIAL = re.compile(
    r"\b(?:did not|does not|do not|didn't|doesn't|not used|not applied|none|never|"
    r"without|absent|zero times|lacks|lacking|future|hypothetical|would|could|"
    r"such as|instead of|rather than)\b|\bno\s",
    re.I,
)


def _sentence_at(text: str, idx: int) -> str:
    """Return the sentence-ish span of text containing position idx."""
    start = max(text.rfind(". ", 0, idx), text.rfind("\n", 0, idx)) + 1
    end = text.find(". ", idx)
    end = len(text) if end == -1 else end + 1
    return text[start:end]


def _xai_asserted(desc: str, pattern: str) -> bool:
    """True only when some occurrence of pattern sits in a non-denying sentence."""
    for m in re.finditer(pattern, desc, re.I):
        if not _XAI_DENIAL.search(_sentence_at(desc, m.start())):
            return True
    return False


# Canonical XAI tags, per config/extraction_template.json.
_XAI_CANON = {
    "none": "none", "shap": "SHAP", "shapley": "SHAP", "grad-cam": "Grad-CAM",
    "gradcam": "Grad-CAM", "lime": "LIME", "attention-map": "attention-map",
    "attention map": "attention-map", "occlusion": "occlusion",
    "decision-tree": "decision-tree", "decision tree": "decision-tree",
    "fuzzy-rules": "fuzzy-rules", "fuzzy rules": "fuzzy-rules",
    # Added 2026-08-29 (data-quality audit): post-hoc methods the corpus actually uses that
    # had no honest enum value (P-435 Captum Integrated Gradients; P-503/P-488 ELI5
    # permutation importance).
    "integrated gradients": "integrated-gradients", "integrated-gradients": "integrated-gradients",
    "permutation importance": "permutation-importance", "permutation-importance": "permutation-importance",
    "mean decrease accuracy": "permutation-importance",
}

# "XAI method is none", "XAI_Method: SHAP", "XAI method: none" - the block stating
# the value outright rather than leaving it to be sniffed out of the prose.
_XAI_DECL = re.compile(
    r"\bXAI[_ ]?[Mm]ethod\b\s*(?:for the registry\s*)?(?:is|:|=)\s*"
    r"\"?([A-Za-z][A-Za-z \-]{2,20}?)\"?(?=[.,;)\s]|$)",
    re.I,
)


def _xai_declared(desc: str) -> str | None:
    """
    Return the XAI tag the block declares outright, if it declares exactly one.

    Mirrors the primary_metric_type declaration reader: an explicit statement by
    the summariser beats keyword sniffing, and a block that names two DIFFERENT
    values is discussing alternatives rather than declaring one, so we abstain
    and let the sniffer (or "none") decide rather than pick the first hit.
    """
    found = set()
    for m in _XAI_DECL.finditer(desc):
        tag = _XAI_CANON.get(m.group(1).strip().lower().rstrip("."))
        if tag:
            found.add(tag)
    return found.pop() if len(found) == 1 else None


def _detect_duplicate_of(text: str) -> str:
    """
    Capture ONLY explicit, unambiguous duplicate cross-references that the
    summariser wrote into the text itself, e.g. "Same as Paper 19",
    "Duplicate of Paper 126 content", "identical to paper 106". Returns
    "P-<n>" pointing at the referenced original.

    A bare mention of the word "duplicate" with NO paper number (e.g.
    "duplicate-entry risk if de-duplication is not enforced downstream", or
    "duplicate file content limits novelty") is deliberately NOT treated as a
    duplicate flag — that is limitation prose, and inferring a duplicate_of
    link from it would be a false positive. "not reported" (sparsity) is
    preferred over a guessed link: agent4 tolerates a missing duplicate_of but
    is corrupted by a wrong one.
    """
    m = re.search(
        r"(?:same as|duplicate of|duplicates|identical to)\s+paper\s+(\d+)",
        text, re.IGNORECASE,
    )
    if m:
        return f"P-{m.group(1)}"
    return "not reported"


# ── Explicit-field sourcing from the summary block (local/no-API mode) ────────
#
# WHY THIS EXISTS. The keyword heuristics below this block were written when
# summaries.md blocks were short. Blocks are now 65,000-78,000 characters of
# discursive prose covering related work, comparisons, limitations and future
# directions, and every heuristic scanned the WHOLE block. The result was not
# merely wrong codes, it was confident fabrication on effectively every coded
# column: "Cambridge" matched the "cam" substring and produced XAI_Method=
# Grad-CAM; the cognitive construct "attention" produced Architecture_Family=
# Transformer; a related-work mention of ADNI produced Dataset=ADNI and
# ADNI_Dependent=true; the word "conversion" appearing anywhere in an MCI
# paper's discussion produced Task_Type=T2; a participant age of "65 years"
# produced prediction_horizon_years=65.0.
#
# The fix is not better keywords. Since the manual summarisation workflow was
# adopted, every new block carries the coded values as EXPLICIT fields
# ("### Modality (L2)", "### Task Type", "### Validation Type",
# "### Architecture/Method Family", "### Best Metric", "### Best AUC",
# "### Dataset", "### What is the sample size?", "### ADNI Dependent",
# "### Key Novelty", "### Primary Limitation", "### Publication year",
# "### Venue/Publisher"). Those fields are written from the PDF by the
# summariser and verified at G2. Reading them is both simpler and strictly
# more reliable than re-deriving them from prose, and it is what the human
# G3 amendment step has been doing by hand every batch.
#
# Where a block does NOT carry an explicit field, we emit "not reported"
# rather than guessing. A blank is recoverable; a plausible-looking fabricated
# value flows silently into Appendix A, Table 6.2b and the knowledge graph.

# Character caps reproduce the widths of the hand-amended rows (P-708..P-717)
# so machine-written rows are indistinguishable in width from verified ones.
_FIELD_CAPS = {
    "Modality":            330,
    "Method_Architecture": 180,
    "Best_Metric":         450,
    "Best_AUC":             82,
    "Dataset":             250,
    "Sample_N_Approx":     260,
    "Primary_Limitation":  600,
    "Open_Problems":       600,
    "Journal":             120,
    "Key_Novelty":         100,
}


def _clean_prose(text: str, cap: int) -> str:
    """Collapse whitespace and hard-truncate to the corpus width for a field."""
    if not text:
        return "not reported"
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:cap] if flat else "not reported"


# From batch P-808.. the summariser declares each coded column outright, as
# "Assigned: C5" / "Category assigned: C5" / "Category: C5", before the gloss.
# _leading_code anchored the code at character 0, so every one of those
# declarations fell through to the keyword guesser in _local_extract and the
# operator had to re-amend the codes by hand every batch (PAPERINGO-cud, 39
# amendments in one batch; Category wrong on 7 of 10 rows). Consuming the
# declaration label keeps the anchor - a code mid-prose is still ignored - while
# letting the block's own explicit judgment win, which is what it is for.
_DECL_LABEL = (
    r"(?:"
    r"category\s+assigned"          # "Category assigned: C5"
    r"|assigned\s+category"         # "Assigned category: C5"
    r"|(?:task|validation|architecture|method|modality|category)\s+assigned"
    r"|assigned"                    # "Assigned: C5"
    r"|category"                    # "Category: C1"  (P-808..P-811, P-813 style)
    r")\s*[:=]\s*"
)


def _declared_value(section_text: str) -> str | None:
    """
    Return the text the block declares after its own 'Assigned:' label, up to the
    end of that line. Used for the free-text coded columns (Best_Metric, Best_AUC,
    Sample_N_Approx) whose values were previously filled with the whole section's
    prose and then truncated mid-sentence.
    """
    if not section_text:
        return None
    m = re.search(_DECL_LABEL + r"(.+)", section_text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip().strip("*_ ").rstrip(".").strip()
    return val or None


def _strip_decl_label(section_text: str) -> str:
    """Drop any leading 'Assigned:' / 'Category assigned:' labels from a section."""
    s = (section_text or "").lstrip()
    for _ in range(3):
        m = re.match(r"^[*_\s]*" + _DECL_LABEL, s, re.IGNORECASE)
        if not m:
            break
        s = s[m.end():].lstrip()
        # Consume the bare code that follows the label, plus the "=" or "-" that
        # introduces its gloss ("Assigned: C5\n\nC5 = Clinical Frameworks ..."),
        # so the field does not start with a dangling separator.
        m2 = re.match(r"^[A-Z][1-8]\b[\s.,;:=-]*", s, re.IGNORECASE)
        if m2:
            s = s[m2.end():].lstrip()
        s = re.sub(r"^[=\-–—:;,.\s]+", "", s)
    return s


_METRIC_TYPES = (
    "bACC", "Acc", "AUC", "F1", "kappa", "Sens", "Spec", "NR",
    "OR", "HR", "RR", "SMD", "MD", "Beta", "rho",
    "C-index", "C-statistic", "Correlation", "Prevalence", "Incidence",
)


def _normalise_metric(declared: str) -> str:
    """
    Canonicalise a declared Best_Metric to 'TYPE VALUE'.

    The summariser writes either order - P-817 declared "0.9773 Acc" while the
    registry convention is "Acc 91.4%" - and the downstream validator only checks
    that the string STARTS with a known type, so a value-first declaration was
    stamped with a second, wrong "Acc " prefix.
    """
    s = re.sub(r"\s+", " ", (declared or "")).strip().rstrip(".")
    if not s:
        return "not reported"
    if _is_not_reported(s) or _declares_no_metric(s) or s.upper() in ("NR", "NONE"):
        return "NR"
    for t in _METRIC_TYPES:
        if s.lower().startswith(t.lower()):
            return s[: _FIELD_CAPS["Best_Metric"]]
    m = re.match(
        r"^([0-9][0-9.,]*\s*%?)\s+(" + "|".join(map(re.escape, _METRIC_TYPES)) + r")\b",
        s,
        re.I,
    )
    if m:
        canon = next((t for t in _METRIC_TYPES if t.lower() == m.group(2).lower()), m.group(2))
        return f"{canon} {m.group(1).strip()}"
    return s[: _FIELD_CAPS["Best_Metric"]]


def _leading_code(section_text: str, letter: str) -> str | None:
    """
    Pull a coded value such as C5 / T4 / V2 / A1 from the START of an explicit
    summary field, optionally behind the block's own 'Assigned:' label. Anchored
    deliberately: a code mentioned mid-prose ("unlike the T2 conversion studies
    reviewed above") must never be picked up.
    """
    if not section_text:
        return None
    head = section_text.strip()
    m = re.match(r"^[*_\s]*(?:%s)?(%s[1-8])\b" % (_DECL_LABEL, letter), head, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _strip_code_prefix(section_text: str) -> str:
    """
    Turn 'C5 (Clinical Frameworks and Population Studies (EHR/surveys/...)) -
    the analysed data stream is a nationally representative...' into
    'The analysed data stream is a nationally representative...'.

    Walks the parenthetical with a depth counter because the category labels
    themselves contain nested parentheses, so a non-greedy regex stops early.
    """
    s = re.sub(r"\s+", " ", (section_text or "")).strip()
    m = re.match(r"^[*_\s]*[A-Z][1-8]\b\s*", s)
    if not m:
        return s
    i = m.end()

    # The label after the code appears in several shapes across batches:
    #   C5 (Clinical Frameworks ... (EHR/surveys/...)) - prose
    #   C5 - "Clinical Frameworks ... (EHR/surveys/...)". prose
    #   C5 - prose
    # Strip separators, then a balanced parenthetical or a quoted label, and
    # repeat, so any ordering of those pieces is consumed before the prose.
    for _ in range(4):
        j = i
        while j < len(s) and (s[j].isspace() or s[j] in "-–—:;,.="):
            j += 1
        if j < len(s) and s[j] == "(":
            depth = 0
            for k in range(j, len(s)):
                if s[k] == "(":
                    depth += 1
                elif s[k] == ")":
                    depth -= 1
                    if depth == 0:
                        j = k + 1
                        break
            else:
                break
            i = j
            continue
        if j < len(s) and s[j] == '"':
            k = s.find('"', j + 1)
            if k == -1:
                break
            i = k + 1
            continue
        i = j
        break

    rest = s[i:].lstrip()
    rest = re.sub(r"^[-–—:,;.\s]+", "", rest).strip()
    if not rest:
        return s
    return rest[0].upper() + rest[1:]


def _is_not_reported(text: str) -> bool:
    """True when an explicit field's own prose declares the value absent."""
    if not text:
        return True
    head = re.sub(r"\s+", " ", text).strip().lower()
    # Blocks routinely open the field with the bare code and then justify it
    # ("NR. The paper reports no discrimination metric of any kind. ..."), so the
    # sentinel has to be recognised as a leading token, not only as the whole
    # string - otherwise the justification itself was stored as the value.
    if re.match(r"^(nr|n/a|none)\b\s*[.:;,-]", head):
        return True
    return head.startswith("not reported") or head.startswith("not applicable") or head in ("nr", "n/a", "none")


# Prose that denies a classification metric exists, written in the affirmative
# ("No classification metric exists ...", "The paper reports no accuracy ...")
# rather than opening with "Not reported"/"Not applicable". _is_not_reported
# only matches the latter, so without this the affirmative phrasings fell
# through to the prefix branch below and were stamped "Acc" - publishing a
# denial of a metric as though it were an accuracy. Same class of bug as
# Agent 4's _metric_parse reading "no accuracy, AUC, F1" as AUC 1.0.
_NO_METRIC_PROSE = re.compile(
    r"^(?:the\s+\w+\s+)?(?:paper|study|article|extract|authors)?\s*"
    r"(?:reports?|computes?|contains?|provides?)?\s*"
    r"no\s+(?:classification|discrimination|diagnostic|predictive|performance)?\s*"
    r"(?:metric|accuracy|auc|statistic)",
    re.I,
)


def _declares_no_metric(text: str) -> bool:
    """True when Best_Metric prose affirmatively denies any classification metric."""
    if not text:
        return False
    head = re.sub(r"\s+", " ", text).strip()
    return bool(_NO_METRIC_PROSE.match(head))


_UNIT_WORDS = re.compile(
    r"^(?:months?|years?|weeks?|days?|hours?|minutes?|points?|mg|ml|ms|mm|cm|kg|"
    r"participants?|patients?|subjects?|studies|trials?|items?|factors?|clusters?|"
    r"fold|times|per|x)\b",
    re.I,
)


def _looks_like_bare_proportion(metric: str) -> bool:
    """True only for an untagged proportion: '0.84', '84%', '0.84 (95% CI ...)'.

    False for anything led by a statistic name or followed by a unit word, so that
    'beta 1.08', 'MR 1.41', '20 months to conversion' and '8 factor clusters' are
    never stamped 'Acc'. Values > 1 without a percent sign are not proportions.
    """
    s = re.sub(r"\s+", " ", metric or "").strip().lstrip("=~≈ ")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(%?)\s*(.*)$", s)
    if not m:
        return False
    try:
        val = float(m.group(1))
    except ValueError:
        return False
    rest = m.group(3).strip()
    if rest and re.match(r"^[A-Za-z]", rest):
        # A following word: unit ("months"), or a typed statistic the value-first
        # normaliser already reorders ("0.84 AUC"). Neither is a bare proportion.
        return False
    if m.group(2):
        return 0.0 <= val <= 100.0
    return 0.0 <= val <= 1.0


_MODALITY_JUSTIFICATION_CUES = re.compile(
    r"^(?:the codebook|this assignment|assigned|because|the paper|the study|codebook|"
    r"per the|following|i am|we |this is|the analysed|the analyzed|note[: ]|"
    r"the choice|the coding|for the registry|registry)",
    re.I,
)
_MODALITY_LABEL_MAX = 80


def _modality_label_from_prose(prose: str) -> str | None:
    """Reduce a '### Modality (L2)' field to a short label, or None when the field
    holds only the summariser's justification paragraph.

    The registry Modality column is a LABEL consumed as one by Agent 4 (modality
    gates, modality-absence gap statements) and Agent 5 (graph nodes). Writing the
    330-char justification prose into it produced G-CAND-348 - a gap whose
    'modality' is a bolded sentence about the cross-domain priority rule - and 140+
    rows of codebook prose (PAPERINGO-hyw, PAPERINGO-0ft, PAPERINGO-xg7r).
    """
    if not prose or _is_not_reported(prose):
        return None
    s = re.sub(r"\s+", " ", prose).strip().strip("*_ ")
    # A field that says the record HAS no modality (commentaries, editorials,
    # protocols, procedural documents) is a label in its own right.
    if re.search(r"\b(?:no (?:data|primary data|data domain|study modality|modality)|"
                 r"non-research|procedural document|no primary data)\b", s, re.I) and len(s) < 400:
        return "none (no primary data)"
    # Drop drafting labels and a bare code gloss: "Verbatim gloss: C2 = Electro..."
    s = re.sub(r"^(?:verbatim gloss|category|modality|assigned|why c\d)\s*[:=]\s*", "", s, flags=re.I)
    s = re.sub(r"^C[1-6]\s*(?:=|-|–|—|:)?\s*", "", s).strip()
    # A leading category gloss in parentheses - "(Neuroimaging) - Structural MRI ..."
    # - is the codebook label, not the modality; drop it (balanced) plus separators.
    if s.startswith("("):
        depth = 0
        for k, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    s = re.sub(r"^[\s\-–—:;,.=]+", "", s[k + 1:])
                    break
    if not s:
        return None
    # First clause only: "Structural MRI (T1) - volumetry and cortical thickness. The codebook ..."
    # Cut at the first sentence / dash / comma boundary at which parentheses are
    # balanced (a comma inside "(EEG/MEG, ERP)" must not cut).
    clause = None
    for m in re.finditer(r"(?<=[a-z0-9)\"])[.;](?:\s|$)|\s[-–—]\s|,\s", s):
        head = s[: m.start()]
        if head.count("(") == head.count(")") and len(head.strip()) >= 8:
            clause = head.strip()
            break
    if clause is None:
        clause = s.strip()
    if 3 <= len(clause) <= _MODALITY_LABEL_MAX and not _MODALITY_JUSTIFICATION_CUES.match(clause) \
            and not re.search(r"\*\*|\bcodebook\b|\bassignment\b", clause, re.I):
        return clause
    return None


# Coarse, never-wrong family labels keyed by the declared Category code. Used when
# the block's Modality field is justification prose with no extractable label:
# the keyword classifier's guess ("MRI/PET" for a willingness-to-pay survey) is
# confidently wrong far too often to be the fallback.
_CATEGORY_FAMILY_LABEL = {
    "C1": "Neuroimaging (MRI/fMRI/DTI/PET)",
    "C2": "Electrophysiology (EEG/MEG/ERP/fNIRS)",
    "C3": "Molecular/fluid biomarkers (CSF/plasma/genomics)",
    "C4": "Behavioural/digital biomarkers (speech/gait/eye/sensor)",
    "C5": "Clinical/population data (EHR/surveys/neuropsych/cohorts)",
    "C6": "Peripheral/indirect markers (retinal/cardiac/other)",
}


def _explicit_fields(sections: dict) -> dict:
    """
    Read the coded columns straight out of the summary block's own fields.
    Returns only the keys it could ground; the caller leaves the rest alone.
    """
    out: dict = {}

    modality_sec = get_section(sections, ["modality"])
    if modality_sec:
        # Multi-value category ("C3, C5" / "C1 and C3"). Reading only the first
        # code left the second one at the head of the Modality prose
        # ("C5. This is a genuinely contested ...") and the row single-coded
        # (P-804, PAPERINGO-5jo). csv.DictWriter quotes the comma on write.
        # Strip only the label here: _strip_decl_label would also eat the first
        # code, which is exactly the one the multi-code test needs to see.
        head = re.sub(r"^[*_\s]*" + _DECL_LABEL, "", modality_sec.strip(), flags=re.IGNORECASE).strip()
        mm = re.match(r"^[*_\s]*((?:C[1-6])(?:\s*(?:,|/|&|\+|and)\s*C[1-6])+)\b", head, re.IGNORECASE)
        if mm:
            codes = re.findall(r"C[1-6]", mm.group(1).upper())
            out["Category"] = ", ".join(dict.fromkeys(codes))
            head = head[mm.end():]
            prose_src = re.sub(r"^[\s\-–—:;,.=]+", "", head)
        else:
            code = _leading_code(modality_sec, "C")
            if code:
                out["Category"] = code
            prose_src = _strip_code_prefix(_strip_decl_label(modality_sec))
        label = _modality_label_from_prose(
            _clean_prose(prose_src, _FIELD_CAPS["Modality"])
        )
        if label:
            out["Modality"] = label
        elif out.get("Category"):
            # Justification-only field: fall back to the coarse family label of
            # the declared code rather than write the codebook prose into the
            # registry cell (PAPERINGO-hyw / PAPERINGO-0ft / PAPERINGO-xg7r).
            first = re.findall(r"C[1-6]", out["Category"])[0]
            out["Modality"] = _CATEGORY_FAMILY_LABEL.get(first, "not reported")

    task_sec = get_section(sections, ["task", "type"])
    code = _leading_code(task_sec, "T") if task_sec else None
    if code:
        out["Task_Type"] = code

    val_sec = get_section(sections, ["validation", "type"])
    code = _leading_code(val_sec, "V") if val_sec else None
    if code:
        out["Validation_Type"] = code

    arch_sec = get_section(sections, ["architecture", "family"])
    code = _leading_code(arch_sec, "A") if arch_sec else None
    if code:
        out["Architecture_Family"] = code

    # Best_Metric / Best_AUC / Sample_N_Approx used to be filled with the whole
    # section's prose, truncated mid-sentence, which is how "Acc 0.98 AUC
    # primary_metric_type: AUC_ROC Why 0.98 AUC. It is the paper's own headline..."
    # and "Acc Assigned: 0.9773 Acc A discrimination metric is reported..." reached
    # the registry. When the block declares a value, take the declaration only.
    metric_sec = get_section(sections, ["best", "metric"])
    if metric_sec:
        declared = _declared_value(metric_sec)
        if declared:
            out["Best_Metric"] = _normalise_metric(declared)
        elif _is_not_reported(metric_sec) or _declares_no_metric(metric_sec):
            # Blocks that open "NR." or "The paper reports no discrimination
            # metric of any kind..." without an Assigned: label previously had
            # that whole denial stored as the metric value.
            out["Best_Metric"] = "NR"
        else:
            out["Best_Metric"] = _clean_prose(metric_sec, _FIELD_CAPS["Best_Metric"])

    auc_sec = get_section(sections, ["best", "auc"])
    if auc_sec:
        declared = _declared_value(auc_sec)
        if declared:
            f = re.match(r"^(0?\.\d+|[01](?:\.\d+)?)\b", declared)
            out["Best_AUC"] = f.group(1) if f else "NR"
        elif _is_not_reported(auc_sec) or _declares_no_metric(auc_sec):
            out["Best_AUC"] = "NR"
        else:
            out["Best_AUC"] = _clean_prose(auc_sec, _FIELD_CAPS["Best_AUC"])

    ds_sec = get_section(sections, ["dataset"])
    if ds_sec:
        out["Dataset"] = _clean_prose(ds_sec, _FIELD_CAPS["Dataset"])

    # CLAUDE.md makes '### What is the sample size?' the canonical source for
    # this column; '### Sample N (approx)' is the secondary restatement.
    n_sec = get_section(sections, ["sample", "size"]) or get_section(sections, ["sample", "approx"])
    if n_sec:
        declared = _declared_value(n_sec)
        raw = declared if declared else re.sub(r"^[*_\s]+", "", n_sec.lstrip())
        out["Sample_N_Approx"] = _clean_prose(raw, _FIELD_CAPS["Sample_N_Approx"])

    adni_sec = get_section(sections, ["adni", "dependent"])
    if adni_sec:
        head = re.sub(r"\s+", " ", _strip_decl_label(adni_sec)).strip().lower()
        if head.startswith(("true", "yes")):
            out["ADNI_Dependent"] = "true"
        elif head.startswith(("false", "no")):
            out["ADNI_Dependent"] = "false"

    nov_sec = get_section(sections, ["key", "novelty"])
    if nov_sec:
        # Key Novelty is written the way every other field in these blocks is
        # written - the value on the first line, then a justification paragraph
        # ("(91 characters, within the 100-character limit.) The primacy claim is
        # the paper's own, from the Discussion: ..."). Capping the whole section
        # at 100 chars stored a sentence truncated mid-justification instead of
        # the clause the summariser actually assigned. Take the first paragraph.
        first_para = re.split(r"\n\s*\n", _strip_decl_label(nov_sec).strip(), 1)[0]
        first_para = re.sub(r"\s+", " ", first_para).strip()
        # A parenthetical character-count note on the same line is metadata.
        first_para = re.sub(r"\s*\((?:\d+\s*characters?|verified by count)[^)]*\)\s*$", "", first_para, flags=re.I)
        out["Key_Novelty"] = _clean_prose(first_para or nov_sec, _FIELD_CAPS["Key_Novelty"])

    lim_sec = get_section(sections, ["primary", "limitation"])
    if lim_sec:
        out["Primary_Limitation"] = _clean_prose(lim_sec, _FIELD_CAPS["Primary_Limitation"])

    venue_sec = get_section(sections, ["venue", "publisher"])
    if venue_sec:
        out["Journal"] = _clean_prose(venue_sec, _FIELD_CAPS["Journal"])

    year_sec = get_section(sections, ["publication", "year"])
    if year_sec:
        y = re.search(r"\b(19[8-9]\d|20[0-3]\d)\b", year_sec)
        if y:
            out["Publication_Year"] = y.group(1)

    return out


def _extract_record(paper_id: str, summary_text: str, template: dict, rules: dict) -> dict:
    """Call Claude API to extract structured record from summary text."""
    learned_instructions = ""
    if _LEARNED_FIELDS:
        learned_instructions = "\n\nAlso extract these newly-added fields:\n" + "\n".join(
            f"- {name}: {info.get('extraction_prompt', f'Extract {name}.')}"
            for name, info in _LEARNED_FIELDS.items()
        )

    prompt = template.get("extraction_prompt_template", "").format(
        summary_text=summary_text[:6000],
        full_text_excerpt=summary_text[2000:4000],
    )

    if not prompt:
        fields = ", ".join(REGISTRY_FIELDS)
        prompt = (
            f"Extract structured metadata from this paper summary.\n"
            f"Return ONLY valid JSON with keys: {fields}\n"
            f"Set unknown fields to 'not reported'. Never fabricate values.\n"
            f"{learned_instructions}\n\n"
            f"Summary:\n{summary_text[:5000]}"
        )
    elif learned_instructions:
        prompt += learned_instructions

    def _local_extract() -> dict:
        txt = summary_text
        low = txt.lower()
        rec = {field: "not reported" for field in REGISTRY_FIELDS}

        sections = parse_sections(txt)

        # Explicit coded fields written by the summariser and verified at G2.
        # These are authoritative; the keyword heuristics below are a fallback
        # for legacy blocks that predate the manual summarisation template.
        explicit = _explicit_fields(sections)

        # Fallback keyword scanning is scoped to the DESCRIPTIVE sections only.
        # Scanning the whole block lets related-work, comparison and
        # future-work prose decide this paper's codes — the defect that made
        # every fallback column unreliable.
        desc = " ".join(filter(None, [
            get_section(sections, ["authors", "do"]),
            get_section(sections, ["components", "method"]),
            get_section(sections, ["key", "methods"]),
            get_section(sections, ["outcomes", "results"]),
        ])).lower() or low

        # Category / modality — domain-based taxonomy v2 (6 categories).
        #   C1 Neuroimaging | C2 Electrophysiology | C3 Molecular/Fluid
        #   C4 Behavioural/Digital | C5 Clinical/Population | C6 Peripheral/Indirect
        # NOTE: method/architecture lives in Architecture_Family, NOT here — Category
        # is a pure DATA-DOMAIN axis. Order below = priority when a paper spans
        # modalities (imaging > electrophys > molecular > behavioural > clinical).
        # Undetermined stays "not reported" (data sparsity is preferred over a wrong
        # guess; the LLM/human layer fills it from summaries.md later).
        if any(k in desc for k in ("retina", "fundus", " oct ", "oculomic", "ophthalm")):
            rec["Category"] = "C6"
            rec["Modality"] = "Retinal imaging"
        elif any(k in desc for k in ("eeg", "meg", "erp ", "fnirs", "nirs", "electroenceph", "magnetoenceph")):
            rec["Category"] = "C2"
            rec["Modality"] = "EEG/MEG"
        elif any(k in desc for k in ("mri", "fmri", "dti", "pet scan", "pet imaging", "amyloid", "fdg", "tau-pet", "tau pet", "voxel", "cortical thickness", "hippocamp")):
            rec["Category"] = "C1"
            rec["Modality"] = "MRI/PET"
        elif any(k in desc for k in ("plasma ", "csf ", "cerebrospinal", "serum ", "blood-based biomarker", "genom", "genotyp", "genetic variant", "genetic risk", "snp ", "gwas", "methylat", "epigenom", "transcriptom", "rna-seq", "rna sequencing", "proteom", "metabolom", "glycoprotein", "multi-omic", "multiomic", "mass spectrometr", "microbiome", "differentially expressed gene")):
            rec["Category"] = "C3"
            rec["Modality"] = "Molecular/fluid"
        elif any(k in desc for k in ("speech", "language", "linguist", "acoustic", "voice", "gait", "handwriting", "drawing", "eye movement", "eye-tracking", "wearable", "actigraph", "keystroke", "driving")):
            rec["Category"] = "C4"
            rec["Modality"] = "Behavioural/digital"
        elif any(k in desc for k in ("ehr", "electronic health", "claims data", "questionnaire", "survey", "cohort study", "epidemiolog", "neuropsych", "mmse", "moca", "charls", "nhanes", "population-based", "risk factor")):
            rec["Category"] = "C5"
            rec["Modality"] = "Clinical/tabular"
        else:
            rec["Category"] = "not reported"

        meth = get_section(sections, ["key", "methods"]) or get_section(sections, ["components", "method"])
        if meth:
            rec["Method_Architecture"] = re.sub(r"\s+", " ", meth).strip()[:180]

        # Task type. "conversion"/"progression" appear in the discussion of
        # nearly every MCI paper, so this must never run against the full block.
        if any(k in desc for k in ["conversion", "p-mci", "s-mci", "progression"]):
            rec["Task_Type"] = "T2"
        elif "review" in desc or "systematic" in desc or "meta-analysis" in desc:
            rec["Task_Type"] = "T6"
        else:
            rec["Task_Type"] = "T1"

        # Metrics — normalise percentage-scale AUC values to decimal (e.g. 87 -> 0.87)
        # The value must be a real reported figure: require a decimal or a
        # plausible percentage, and require the number to follow AUC closely.
        # The old pattern matched any digit within six characters of the token
        # "AUC" anywhere in a 70k-character block, which manufactured "AUC 1.0"
        # perfect-classifier claims for qualitative studies and meta-analyses.
        auc = re.search(r"\bAUC\b\D{0,6}(0?\.\d+|[1-9]\d(?:\.\d+)?)\b", desc, re.I)
        acc = re.search(r"\bAcc(?:uracy)?\b\D{0,6}(0?\.\d+|[1-9]\d(?:\.\d+)?)\b", desc, re.I)
        if auc:
            val = float(auc.group(1))
            if val > 1.0:
                val = val / 100.0
            if 0.0 < val < 1.0:
                rec["Best_AUC"] = str(round(val, 4))
                rec["Best_Metric"] = f"AUC {round(val, 4)}"
        elif acc:
            rec["Best_Metric"] = f"Acc {acc.group(1)}"

        # Dataset. Word-boundary matched and scoped: a related-work sentence
        # citing ADNI is not this paper's dataset.
        for ds in ["ADNI", "OASIS", "NACC", "CHARLS", "CLHLS", "I-CONECT"]:
            if re.search(r"\b%s\b" % re.escape(ds.lower()), desc):
                rec["Dataset"] = ds
                break

        # Validation type heuristic
        if "external" in desc or "multi-center" in desc or "multicenter" in desc:
            rec["Validation_Type"] = "V4"
        elif "cross-validation" in desc or "5-fold" in desc or "10-fold" in desc:
            rec["Validation_Type"] = "V2"
        else:
            rec["Validation_Type"] = "V2"

        # Architecture family. A4/A5 were transposed against the schema in
        # config/extraction_template.json (A4=Transformer/attention, A5=GNN/GCN),
        # so every transformer paper was filed as a graph network and vice
        # versa. "attention" is also a cognitive construct in this corpus, so it
        # is no longer sufficient on its own to imply an attention architecture.
        if "transformer" in desc or "self-attention" in desc or "attention mechanism" in desc:
            rec["Architecture_Family"] = "A4"
        elif "lstm" in desc or "gru" in desc or re.search(r"\brnn\b", desc):
            rec["Architecture_Family"] = "A3"
        elif re.search(r"\bcnn\b", desc) or "convolutional neural" in desc:
            rec["Architecture_Family"] = "A2"
        elif re.search(r"\b(gcn|gat|graph neural|graph convolution)\b", desc):
            rec["Architecture_Family"] = "A5"
        else:
            rec["Architecture_Family"] = "A1"

        # XAI. The bare substring "cam" matched Cambridge, CAMCOG and camera,
        # labelling unrelated clinical papers as Grad-CAM explainability work.
        #
        # An explicit declaration in the block always wins over prose sniffing.
        # _xai_asserted only rejects a hit when the SAME sentence carries a denial
        # word, and the house style defeats that: a block that warns a downstream
        # extractor about the substring trap writes sentences like 'Its three
        # case-insensitive hits for "shap" are "U-shaped association", the
        # instrument name "Shapes Test" and the surname Shapiro.' That sentence
        # names the method, denies nothing, and so asserted SHAP on two clinical
        # reviews (P-842, P-843) that contain no machine learning at all - the
        # very error the sentence was written to prevent. Reading the block's own
        # declaration first is both simpler and strictly more reliable.
        declared_xai = _xai_declared(desc)
        if declared_xai:
            rec["XAI_Method"] = declared_xai
        elif _xai_asserted(desc, r"\bshap\b|shapley"):
            rec["XAI_Method"] = "SHAP"
        elif _xai_asserted(desc, r"grad-?cam\b|\bcam\b(?!\w)") and "class activation" in desc:
            rec["XAI_Method"] = "Grad-CAM"
        elif _xai_asserted(desc, r"grad-?cam\b"):
            rec["XAI_Method"] = "Grad-CAM"
        elif _xai_asserted(desc, r"\blime\b"):
            rec["XAI_Method"] = "LIME"
        else:
            rec["XAI_Method"] = "none"

        # ═══════════════════════════════════════════════════════════════════
        # FIX: replaced all per-field exact-phrase header regexes with the
        # generic section parser + fuzzy header matching. The old code
        # searched for '### Venue/Publisher' (doesn't exist; real header is
        # '### Venue') and '### Sample N' (doesn't exist at all), which is
        # why Journal and Sample_N_Approx were stuck at or near 100% empty.
        # ═══════════════════════════════════════════════════════════════════
        open_problems = get_section(sections, ["open", "problems"])
        rec["Open_Problems"] = (
            re.sub(r"\s+", " ", open_problems).strip()[:600] if open_problems else "not reported"
        )

        novelty = get_section(sections, ["novelty"])
        if novelty:
            rec["Key_Novelty"] = re.sub(r"\s+", " ", novelty).strip()[:120]

        limitation = get_section(sections, ["gaps", "limitations"]) or get_section(sections, ["problems"])
        if limitation:
            rec["Primary_Limitation"] = re.sub(r"\s+", " ", limitation).strip()[:600]

        venue = get_section(sections, ["venue"])
        if venue:
            rec["Journal"] = re.sub(r"\s+", " ", venue).strip()[:120]

        year_section = get_section(sections, ["year", "published"])
        if year_section:
            y = re.search(r"\b(20[0-3][0-9]|19[8-9][0-9])\b", year_section)
            if y:
                rec["Publication_Year"] = y.group(1)
        if rec["Publication_Year"] == "not reported":
            y = re.findall(r"\b(20[0-3][0-9]|19[8-9][0-9])\b", txt)
            if y:
                rec["Publication_Year"] = str(max(int(v) for v in y))

        # FIX: Sample_N_Approx now pulled from free text (no dedicated header
        # exists in the real template) instead of always defaulting to
        # "not reported".
        rec["Sample_N_Approx"] = extract_sample_n_freetext(sections)

        # A mention of ADNI anywhere in a 70k-character block — typically in
        # related work or the reference list — is not evidence that ADNI is
        # THIS paper's dataset. Scoped to the descriptive sections; the
        # explicit "### ADNI Dependent" field overrides it just below.
        rec["ADNI_Dependent"] = "true" if re.search(r"\badni\b", desc) else "false"

        # ── Explicit summary fields win over every heuristic above ──────────
        rec.update(explicit)

        # 8 new fields
        # FIX: study_design_type was scanning the FULL summary text, so a
        # cross-sectional study whose "What can be improved?" section
        # suggests "longitudinal follow-up" for FUTURE work was incorrectly
        # detected as longitudinal_2point — the word "longitudinal" appeared,
        # just not describing what THIS paper actually did. Scope detection
        # to the descriptive sections only (what the authors did + methods),
        # same fix philosophy already applied to modality_subtype.
        design_scope_text = " ".join(filter(None, [
            get_section(sections, ["authors", "do"]),
            get_section(sections, ["key", "methods"]),
            get_section(sections, ["components", "method"]),
            get_section(sections, ["outcomes", "results"]),
        ])).lower()
        _title_line = txt.lstrip().splitlines()[0].lower() if txt.strip() else ""
        _opening = (get_section(sections, ["authors", "do"]) or "")[:600].lower()
        # A block that declares its own study_design_type (house style since
        # ~P-800) is authoritative - the keyword detector agrees with the curated
        # column under 50% of the time (PAPERINGO-cth), and over-assigns
        # survival_analysis to logistic-regression cohorts that mention Cox as a
        # sensitivity check (P-861, PAPERINGO-qlxz).
        _declared_design = _unique_declared_study_design(txt)
        if _declared_design:
            rec["study_design_type"] = _declared_design
        else:
            rec["study_design_type"] = _detect_study_design_type(
                design_scope_text if design_scope_text else _strip_reference_region(low),
                title_scope=_title_line + " " + _opening,
                title_line=_title_line,
            )
        rec["task_substage"]          = _detect_task_substage(desc, rec["Task_Type"])
        # Narrow scope: the block title plus the opening of "What did the
        # authors do?" — see _detect_population_specificity for why.
        title_line = txt.lstrip().splitlines()[0] if txt.strip() else ""
        population_scope = (title_line + " " + (get_section(sections, ["authors", "do"]) or "")[:1500]).lower()
        rec["population_specificity"] = _detect_population_specificity(population_scope or desc)
        meth_text = rec.get("Method_Architecture", "").lower()
        rec["modality_subtype"]       = _detect_modality_subtype(meth_text if meth_text else desc[:500])
        rec["prediction_horizon_years"] = _detect_prediction_horizon(desc, rec["Task_Type"])
        rec["primary_metric_type"]    = _metric_type_from_best_metric(rec.get("Best_Metric", "not reported"), txt)
        rec["duplicate_of"]           = _detect_duplicate_of(txt)

        # ── Reconcile coarse Modality with the method-scoped modality_subtype ──
        # FIX (diagnostic check #6, P-85/P-100): the Modality classifier above
        # scans the FULL summary text, so a passing mention of "EEG" in a
        # comparison sentence mislabels an fNIRS study as "EEG/MEG".
        # modality_subtype is scoped to the method text only and is the field
        # the diagnostic treats as authoritative ("trust modality_subtype over
        # Modality for these rows"). Reconcile from it rather than guessing.
        # Two safe cases: (a) the coarse field misfired to "EEG/MEG" on a stray
        # "EEG" mention, and (b) it fell through to "not reported". In both we
        # adopt the trusted subtype. We never overwrite a populated, different
        # modality (e.g. genuinely multimodal "MRI/PET") — that could be real.
        if rec["modality_subtype"] == "fNIRS":
            cur_mod = (rec.get("Modality") or "").lower()
            if "eeg" in cur_mod or cur_mod in ("", "not reported"):
                rec["Modality"] = "fNIRS"

        for learned_field in _LEARNED_FIELDS:
            rec.setdefault(learned_field, "not reported")

        return rec

    if not ANTH_KEY:
        record = _local_extract()
    else:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTH_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    # claude-sonnet-4-20250514 was retired 2026-06-15 (PAPERINGO-ron).
                    "model": os.environ.get("AGENT3_MODEL", "claude-sonnet-5"),
                    "max_tokens": 1200,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45,
            )
            raw = resp.json()["content"][0]["text"].strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            record = json.loads(raw)
            # The block's own explicit declarations (Assigned: lines, XAI method,
            # sample size) win over the model's JSON exactly as they do in
            # _local_extract; without this the API path would silently resume
            # keyword-guessing the coded columns (PAPERINGO-ron).
            explicit = _explicit_fields(parse_sections(summary_text))
            record.update({k: v for k, v in explicit.items() if v not in (None, "")})
        except Exception as e:
            log.error(f"  Claude extraction failed for {paper_id}: {e}")
            record = _local_extract()

    record["Paper_ID"] = paper_id
    return record


def _normalise_record(record: dict, rules: dict) -> tuple:
    """Apply normalisation rules. Returns (normalised_record, log_notes)."""
    notes = []
    r = dict(record)

    # Canonicalise only a BARE dataset name. The Dataset column now carries a
    # descriptive sentence ("CHARLS - the China Health and Retirement
    # Longitudinal Study, a public national cohort ..."), and substring-
    # replacing the spelled-out name with its acronym inside that sentence
    # mangled it into "CHARLS - the CHARLS, a public national cohort ...".
    dataset_map = rules.get("dataset_canonical_names", {})
    raw_ds = r.get("Dataset", "")
    if raw_ds and len(raw_ds) <= 40:
        for variant, canonical in dataset_map.items():
            if variant.lower() in raw_ds.lower():
                r["Dataset"] = raw_ds.replace(variant, canonical)
                break

    # Derive ADNI dependence from the dataset ONLY when the extractor could not
    # ground it. When the summary block carries an explicit "### ADNI Dependent"
    # field, that verified value stands: the Dataset prose for a non-ADNI paper
    # can legitimately name ADNI while describing what the study did NOT use.
    if _is_not_reported(r.get("ADNI_Dependent", "")):
        r["ADNI_Dependent"] = "true" if re.search(r"\bADNI\b", r.get("Dataset", "")) else "false"

    metric = r.get("Best_Metric", "not reported")
    # Effect-size and epidemiological measures are first-class Best_Metric
    # values in this corpus (odds ratios, hazard ratios, pooled prevalences,
    # mean differences), and most clinical papers legitimately report no
    # classification metric at all. Blindly prefixing those with "Acc" produced
    # nonsense like "Acc OR 1.625" and "Acc Not reported as a classification
    # metric", which then flowed into the manuscript tables as accuracies.
    valid_prefixes = [
        "Acc", "bACC", "AUC", "F1", "kappa", "Sens", "Spec", "NR",
        "OR", "HR", "RR", "MD", "SMD", "Beta", "d ", "r ", "rho",
        "Pooled", "Prevalence", "Incidence", "C-index", "C-statistic",
        "Correlation", "Effect size", "Cohen",
        # Meta-analysis estimators that were missing, so a legitimate pooled value
        # was stamped with a bogus accuracy tag: P-835's "Hedges g 0.42 (95% CI
        # 0.24-0.61)" became "Acc Hedges g 0.42 ...", which then coded the row as
        # Accuracy. "Cohen" was already here; "Hedges" simply never was.
        "Hedges", "g ", "WMD", "Sens", "Spec", "SROC", "Risk ratio",
        "Relative risk", "Odds ratio", "Hazard ratio", "Mean difference",
        # ADJUSTED estimator forms. The list held "OR" and "HR" but startswith is a
        # raw prefix test, so "AOR 2.17" and "AHR 1.65" matched nothing and were
        # stamped "Acc AOR 2.17" - an odds ratio published as an accuracy. Every
        # risk-factor meta-analysis in the corpus reports adjusted estimates, so
        # this hit P-843 the moment it was extracted.
        "AOR", "AHR", "ARR", "aOR", "aHR", "IRR", "SUCRA",
        # Variance-explained effect sizes from repeated-measures trials. P-846's
        # "eta2G 0.191" is a generalised eta squared, not an accuracy.
        "eta2", "eta squared", "partial eta", "Eta2", "n2G", "R2", "PrI",
    ]
    if (
        metric not in ("not reported", "NR")
        and not _is_not_reported(metric)
        and not _declares_no_metric(metric)
        and not any(metric.startswith(p) for p in valid_prefixes)
    ):
        # Only a BARE proportion (a leading number <=1, or a percentage, with no
        # statistic name in front of it) is an untagged accuracy. Anything led by
        # an alphabetic token is already a typed statistic the whitelist simply
        # does not know ("beta 1.08", "MR 1.41", "OE-ratio 0.08", "CDR-SB 1-point
        # increase") and is left untagged with a note, instead of being published
        # as an accuracy (PAPERINGO-4i53, PAPERINGO-i7wn, PAPERINGO-pfmw).
        if _looks_like_bare_proportion(metric):
            r["Best_Metric"] = f"Acc {metric}"
            notes.append(f"Best_Metric: added 'Acc' prefix to bare proportion (was: {metric})")
        else:
            notes.append(f"Best_Metric: unrecognised leading token, left untagged ({metric[:60]})")

    valid_tasks = {"T1", "T2", "T3", "T4", "T5", "T6"}
    if r.get("Task_Type") not in valid_tasks:
        r["Task_Type"] = "T4"
        notes.append(f"Task_Type: defaulted to T4 (original: {record.get('Task_Type')})")

    valid_vals = {"V1", "V2", "V3", "V4"}
    if r.get("Validation_Type") not in valid_vals:
        r["Validation_Type"] = "V2"
        notes.append(f"Validation_Type: defaulted to V2 (original: {record.get('Validation_Type')})")

    valid_archs = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"}
    if r.get("Architecture_Family") not in valid_archs:
        r["Architecture_Family"] = "A1"
        notes.append(f"Architecture_Family: defaulted to A1 (original: {record.get('Architecture_Family')})")

    # The house rule for novelty is a 100-CHARACTER cap (CLAUDE.md), which is
    # what the summariser writes to. A 12-word cut is a different rule that
    # decapitated compliant values mid-phrase.
    novelty = r.get("Key_Novelty", "")
    if novelty and len(novelty) > _FIELD_CAPS["Key_Novelty"]:
        r["Key_Novelty"] = novelty[:_FIELD_CAPS["Key_Novelty"]].rstrip()
        notes.append(f"Key_Novelty: truncated to {_FIELD_CAPS['Key_Novelty']} characters")

    if not r.get("XAI_Method") or r.get("XAI_Method") == "not reported":
        r["XAI_Method"] = "none"
        notes.append("XAI_Method: set to 'none' (not reported)")

    if r.get("study_design_type") not in VALID_STUDY_DESIGN:
        canon = _canon_design(r.get("study_design_type", ""))
        if canon in VALID_STUDY_DESIGN:
            notes.append(f"study_design_type: '{r.get('study_design_type')}' canonicalised to {canon}")
            r["study_design_type"] = canon
        elif canon:
            notes.append(f"study_design_type: kept declared out-of-vocabulary value '{canon}'")
            r["study_design_type"] = canon
        else:
            notes.append(f"study_design_type: invalid value '{r.get('study_design_type')}' reset")
            r["study_design_type"] = "not reported"

    if r.get("population_specificity") not in VALID_POPULATION:
        notes.append(f"population_specificity: invalid value '{r.get('population_specificity')}' reset")
        r["population_specificity"] = "general"

    if r.get("task_substage") not in VALID_TASK_SUBSTAGE:
        notes.append(f"task_substage: invalid value '{r.get('task_substage')}' reset")
        r["task_substage"] = "not reported"

    if r.get("primary_metric_type") not in VALID_METRIC_TYPE:
        canon = _canon_metric_type(r.get("primary_metric_type", ""))
        if canon in VALID_METRIC_TYPE:
            notes.append(f"primary_metric_type: '{r.get('primary_metric_type')}' canonicalised to {canon}")
            r["primary_metric_type"] = canon
        elif canon:
            notes.append(f"primary_metric_type: kept declared out-of-vocabulary value '{canon}'")
            r["primary_metric_type"] = canon
        else:
            notes.append(f"primary_metric_type: invalid value '{r.get('primary_metric_type')}' reset")
            r["primary_metric_type"] = "not reported"

    horizon = r.get("prediction_horizon_years", "not reported")
    if horizon not in ("not reported", "", None):
        try:
            float(horizon)
        except (TypeError, ValueError):
            notes.append(f"prediction_horizon_years: non-numeric value '{horizon}' reset")
            r["prediction_horizon_years"] = "not reported"

    msub = (r.get("modality_subtype") or "").strip()
    if msub and msub not in VALID_MODALITY_SUBTYPE:
        canon = _canon_modality_subtype(msub)
        if canon in VALID_MODALITY_SUBTYPE:
            notes.append(f"modality_subtype: '{msub[:60]}' canonicalised to {canon}")
            msub = canon
        elif _SOFT_VOCAB_TOKEN.match(msub):
            # Keep declared out-of-vocabulary tokens ONLY when they are clean
            # snake_case (PAPERINGO-n01u philosophy: resetting a declaration is
            # silent information loss). But 'modality' is a comparator column: a
            # one-off value can never equal anything, which silently disables
            # Agent 4's MODALITY_SUBTYPE gate. Prose is reset; unknown clean
            # tokens are kept and flagged for the operator to extend the table.
            notes.append(f"modality_subtype: kept declared out-of-vocabulary value '{msub[:60]}'")
        else:
            notes.append(f"modality_subtype: prose/unrecognised value '{msub[:60]}' reset")
            msub = "not reported"
    r["modality_subtype"] = msub[:60] if msub else "not reported"

    dup = (r.get("duplicate_of") or "").strip()
    if dup and dup != "not reported" and not re.match(r"^P-\d+$", dup):
        notes.append(f"duplicate_of: malformed value '{dup}' reset")
        dup = "not reported"
    r["duplicate_of"] = dup if dup else "not reported"

    for field in REGISTRY_FIELDS:
        if field not in r:
            r[field] = "not reported"
            notes.append(f"{field}: missing — set to 'not reported'")

    return r, notes


def run(state: dict, paper_filter=None) -> int:
    """Main entry point. Returns number of papers extracted this run."""
    template, rules = _load_config()
    existing_ids    = _get_existing_ids()
    all_summaries   = _parse_summaries()

    candidates = [
        pid for pid in all_summaries
        if pid not in existing_ids
        and (paper_filter is None or pid in paper_filter)
    ]

    pending, skipped = [], []
    for pid in candidates:
        reason = _skip_reason(all_summaries[pid])
        if reason:
            skipped.append((pid, reason))
        else:
            pending.append(pid)

    if skipped:
        log.info(f"Agent 3: skipping {len(skipped)} non-study summary block(s):")
        for pid, reason in skipped:
            log.info(f"    {pid}: {reason}")

    if not pending:
        log.info("Agent 3: No new papers to extract.")
        return 0

    if _LEARNED_FIELDS:
        log.info(f"Agent 3: {len(_LEARNED_FIELDS)} learned field(s) active: {list(_LEARNED_FIELDS.keys())}")

    log.info(f"Agent 3: {len(pending)} papers pending extraction.")

    # Write a header when the file is absent OR exists but is empty/headerless.
    # (A bare `not REGISTRY.exists()` produced a header-less CSV when the file
    # was emptied of rows but left in place before a regenerate — every
    # downstream reader then mis-maps columns by position.)
    write_header = (
        not REGISTRY.exists()
        or REGISTRY.stat().st_size == 0
        or not REGISTRY.read_text(encoding="utf-8-sig", errors="ignore").lstrip().startswith("Paper_ID,")
    )
    log_lines = []

    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=REGISTRY_FIELDS,
            quoting=csv.QUOTE_MINIMAL,
            doublequote=True,
        )
        if write_header:
            writer.writeheader()

        for paper_id in pending:
            summary = all_summaries[paper_id]
            log.info(f"  Extracting: {paper_id}")

            raw_record  = _extract_record(paper_id, summary, template, rules)
            norm_record, notes = _normalise_record(raw_record, rules)

            nr_fields = [k for k, v in norm_record.items() if v == "not reported"]
            if nr_fields:
                log_lines.append(f"{paper_id} — not reported: {', '.join(nr_fields)}")
            if notes:
                log_lines.append(f"{paper_id} — normalised: {'; '.join(notes)}")

            row = {k: norm_record.get(k, "not reported") for k in REGISTRY_FIELDS}
            for key in row:
                val = row[key]
                if isinstance(val, str):
                    val = val.replace("\x00", "").replace("\x0b", "").replace("\x0c", "")
                    val = val.replace("\r\n", "\n").replace("\r", "\n")
                    row[key] = val

            try:
                writer.writerow(row)
            except csv.Error as e:
                log.error(f"  CSV write failed for {paper_id}: {e}")
                for key in row:
                    if isinstance(row[key], str):
                        row[key] = re.sub(r"[^\x20-\x7E\n\t]", "", row[key])
                writer.writerow(row)
                log.warning(f"  {paper_id} written with aggressive sanitisation.")

            time.sleep(0.5)

    LOGS.mkdir(parents=True, exist_ok=True)
    with open(EXTRACT_LOG, "a", encoding="utf-8") as f:
        from datetime import datetime
        f.write(f"\n--- Run {datetime.utcnow().isoformat()} ---\n")
        f.write("\n".join(log_lines) + "\n")

    if len(pending) >= 5:
        log.warning(
            "GATE G3: Human verification required. "
            "Review 5 randomly sampled rows in full_paper_registry.csv against source PDFs. "
            "Check: metric type, validation tier, task label, and the 8 new fields."
        )

    log.info(f"Agent 3 complete. {len(pending)} papers extracted.")
    return len(pending)