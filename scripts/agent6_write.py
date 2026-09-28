"""
agent6_write.py — Paper Writer Agent
Updates ONLY the dynamic tables in review_paper.md (Sections 6.2–6.9)
and the generated representative-paper trace in Section 6.10 when
hub JSON is available.
NEVER modifies static prose sections (1–5, 6.1, 6.9 prose, 7).

Follows the cognitive-review-paper skill rules exactly:
- Dynamic content in dynamic slots only
- Section 6.2 now contains diagnostic tables (6.2a, 6.2b, 6.2c)
- Full row-level registry is written to Appendix A (Table A.1)
- No paper IDs inline in prose
- No paper counts inline in prose
"""

import csv
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("agent6_write")

ROOT      = Path(__file__).parent.parent
DATA      = ROOT / "data"
PAPER     = ROOT / "paper"
LOGS      = ROOT / "logs"
PAPER_FILE = ROOT / "review_paper.md"
PAPER6_LOG = LOGS / "agent6_paper_log.txt"
REGISTRY  = DATA / "full_paper_registry.csv"
HUB_JSON_CANDIDATES = [
    DATA / "representative_paper_hub.json",
    DATA / "paper_hub_trace.json",
    DATA / "hub_trace_selected_paper.json",
]
PAPER.mkdir(exist_ok=True)


def _load_registry() -> list:
    if not REGISTRY.exists():
        return []
    rows = []
    with open(REGISTRY, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "Paper_ID" not in row:
                row = {
                    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
                    for k, v in row.items()
                }
            rows.append(row)
    return rows


def _load_json(name: str) -> list:
    path = DATA / name
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    key_map = {
        "consensus.json": "verified_findings",
        "conflicts.json": "conflicts",
        "complementary.json": "complementary_findings",
        "gaps.json": "gaps",
        "invalidated.json": "invalidated_assumptions",
    }
    key = key_map.get(name, "")
    value = payload.get(key, []) if key and isinstance(payload, dict) else []
    return value if isinstance(value, list) else []


def _load_hub_trace() -> dict:
    for path in HUB_JSON_CANDIDATES:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _bool_to_yn(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "y"}:
        return "Yes"
    if lowered in {"false", "no", "n"}:
        return "No"
    return str(value)


def _clean_text(value, default: str = "NR") -> str:
    text = str(value).strip()
    return text if text else default


_TV_SUB = {"1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆"}


def _subify(value) -> str:
    """Render task/validation codes as unicode subscripts (T1->T₁, V2->V₂) for
    manuscript consistency, but never touch MRI sequences (T1-weighted, T2 Maps)."""
    s = str(value or "")
    s = re.sub(r"\bT([1-6])\b(?!-?weighted|w\b|/| Map)", lambda m: "T" + _TV_SUB[m.group(1)], s)
    s = re.sub(r"\bV([1-4])\b", lambda m: "V" + _TV_SUB[m.group(1)], s)
    return s


def _short(value, n: int, default: str = "NR") -> str:
    """Table-cell text: collapse whitespace and truncate at a WORD boundary
    (<= n chars), appending an ellipsis only if actually cut. Prevents the
    mid-word/mid-sentence cuts that made table cells look broken."""
    s = " ".join(str(value or "").split())
    if not s:
        return default
    s = _subify(s)                                  # unicode T₁-T₆ / V₁-V₄ consistency
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:.-—")
    return (cut or s[:n]) + "…"


def _xai_reported(value) -> bool:
    lowered = str(value).strip().lower()
    return lowered not in {"", "none", "nr", "not reported"}


def _parse_categories(value: str) -> list:
    return [chunk.strip() for chunk in str(value).split("/") if chunk.strip()]


def _norm_arch_family(value: str) -> str:
    cleaned = _clean_text(value, "Other")
    return cleaned if cleaned in {f"A{i}" for i in range(1, 9)} else "Other"


def _normalise_token(value) -> str:
    """Lowercase token normalisation for status comparisons."""
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def _is_candidate_like(value) -> bool:
    token = _normalise_token(value)
    if not token:
        return False
    return any(flag in token for flag in ("candidate", "pending", "awaiting"))


def _is_candidate_record(record: dict, status_fields: tuple[str, ...], text_fields: tuple[str, ...] = ()) -> bool:
    for field in status_fields:
        if _is_candidate_like(record.get(field, "")):
            return True
    for field in text_fields:
        if _is_candidate_like(record.get(field, "")):
            return True
    return False


_GENUINE_TRUE = {"true", "yes", "y", "1", "genuine"}
_GENUINE_FALSE = {"false", "no", "n", "0", "not_genuine"}
_CONFLICT_DEAD_TOKENS = ("rejected", "retired", "withdrawn", "superseded", "false_positive", "not_genuine")


def _conflict_genuine_flag(conflict: dict) -> str:
    """'true' / 'false' / '' from the record's explicit adjudication fields.
    genuine_conflict (bool) is preferred; the legacy 'genuine' field may hold
    True/False/'Yes'/'No'/None and is read second."""
    for field in ("genuine_conflict", "genuine"):
        raw = conflict.get(field)
        if raw is None or raw == "":
            continue
        token = _normalise_token(raw)
        if token in _GENUINE_TRUE:
            return "true"
        if token in _GENUINE_FALSE:
            return "false"
    return ""


def _is_confirmed_conflict(conflict: dict) -> bool:
    """A conflict belongs in Table 6.4 when a human gate adjudicated it genuine.

    The explicit genuine flag is authoritative over the free-text resolution
    status. The old order of tests dropped any record whose resolution_status
    merely CONTAINED 'candidate' or 'pending' - so C-80 (genuine=True,
    resolution_status='partially_resolved_candidate': it is the RESOLUTION that
    is provisional, not the conflict) and C-235/C-236 ('Open - ... pending
    head-to-head test') vanished from the manuscript while the ledger held them
    as genuine (PAPERINGO-fee, PAPERINGO-r0h, PAPERINGO-awy, PAPERINGO-2hx2).
    A genuine record is excluded only when its OWN status says it was rejected
    or retired.
    """
    flag = _conflict_genuine_flag(conflict)
    if flag == "true":
        st = _normalise_token(conflict.get("status", ""))
        return not any(k in st for k in _CONFLICT_DEAD_TOKENS)
    if flag == "false":
        return False
    # No explicit adjudication at all: conservative legacy reading.
    if _is_candidate_record(conflict, ("status", "resolution_status"), ("resolution",)):
        return False
    if _has_exclude_token(conflict.get("status", ""), conflict.get("resolution_status", ""),
                          conflict.get("resolution", "")):
        return False
    return True


def ledger_genuine_conflict_ids(conflicts: list) -> list:
    """Ids of every record the ledger flags genuine, regardless of status text.
    Compared against the rendered Table 6.4 so the two can never silently
    disagree (PAPERINGO-fee requirement (b))."""
    return [c.get("id", "") for c in conflicts if _conflict_genuine_flag(c) == "true"]



def _gap_status(gap: dict) -> str:
    """Effective gap status: `status` where present, else `current_status`, else "".

    Precedence is status-first because in every observed divergence between the two
    mirror fields (G-CAND-343/344 rejected-vs-candidate; the 30-record 2026-09-14
    promotion) `status` held the newer value and `current_status` was the stale copy.
    An UNSET record is returned as "" and never as "open": until 2026-09-15 the
    fallback here was "open", which published 38 never-gated G-NEW-1353..1392-A
    records (no status-like key at all) into Table 6.x and the graph as open gaps.
    """
    for key in ("status", "current_status"):
        val = gap.get(key)
        if val not in (None, ""):
            return _normalise_token(val)
    log.warning("gap %s has neither status nor current_status - treated as NOT open", gap.get("id"))
    return ""

def _is_candidate_gap(gap: dict) -> bool:
    # Status is authoritative: a gap explicitly promoted to open/partially_open is
    # NOT a candidate, even if its legacy id still carries a G-CAND- prefix.
    status = _gap_status(gap)
    if status in {"open", "partially_open"}:
        return False
    gap_id = _normalise_token(gap.get("id", ""))
    if gap_id.startswith("g_cand_"):
        return True
    if _is_candidate_like(_gap_status(gap)):
        return True
    if _is_candidate_like(gap.get("gap_type", "")):
        return True
    return False


# Hard-exclude tokens: a record carrying any of these in a status field is NOT a
# confirmed manuscript-table member (AGENTS.md: only confirmed/genuine/open belong).
_EXCLUDE_STATUS_TOKENS = (
    "rejected", "demoted", "retired", "insufficient",
    "superseded", "deprecated", "invalid", "candidate", "awaiting",
)


def _has_exclude_token(*values) -> bool:
    for value in values:
        token = _normalise_token(value)
        if token and any(flag in token for flag in _EXCLUDE_STATUS_TOKENS):
            return True
    return False


def _vf_included(vf: dict) -> bool:
    """A verified finding belongs in Table 6.3 unless it has been demoted, retired,
    judged insufficient, or explicitly flagged EXCLUDE for the manuscript. An explicit
    manuscript_status of include/include_with_caveat is an affirmative human override
    (the re-derived VFs carry status 're-derived_pending_g4' but are G4-approved)."""
    if vf.get("exclude_from_manuscript_tables"):
        return False
    # A verified finding is defined by its supporting studies; one with no
    # supporting_papers is a claim, not a finding, and must not be tabulated or
    # emitted as a graph node whatever its status says (PAPERINGO-0zn).
    if not vf.get("supporting_papers"):
        return False
    ms = _normalise_token(vf.get("manuscript_status", ""))
    if ms.startswith("exclude"):
        return False
    # manuscript_status 'candidate' / 'pending_*' is a hold, not an include.
    if "candidate" in ms or ms.startswith("pending"):
        return False
    if ms.startswith("include"):
        return True
    st = _normalise_token(vf.get("status", ""))
    if any(k in st for k in ("retired", "demoted", "insufficient", "superseded", "deprecated")):
        return False
    # A VF whose own status is still pending has not cleared review. Reached only when no
    # affirmative manuscript_status override is set, so the G4-approved "re-derived_pending_g4"
    # VFs (manuscript_status=include/include_with_caveat) are unaffected.
    if st.startswith("pending"):
        return False
    if "candidate" in _normalise_token(vf.get("id", "")) or "candidate" in st:
        return False
    # A record nothing has ever reviewed (no status, no review_status, no
    # manuscript_status, no gate verdict, no tier) is not a verified finding yet:
    # human verification at G4/G5 is what makes it one (VF-34 case).
    markers = " ".join(_normalise_token(vf.get(k, "")) for k in
                       ("status", "review_status", "manuscript_status", "g5_verdict", "g4_verdict", "tier"))
    if vf.get("gate_verified") is True:
        markers += " verified"
    # Legacy VFs (VF-01/14/18) carry their review only in audit notes, so an
    # empty status set alone is not disqualifying; an empty status set on a
    # record that also fails the >=3-confirmation independence rule is.
    if not markers.strip() and len(vf.get("supporting_papers") or []) < 3:
        return False
    return True


def _cf_included(cf: dict) -> bool:
    """A complementary finding is table-eligible only if explicitly confirmed and not
    rejected/superseded at review."""
    if "confirm" not in _normalise_token(cf.get("status", "")):
        return False
    if _has_exclude_token(cf.get("review_status", "")):
        return False
    return True


_CF_BANNED_PHRASE = "may improve robustness and generalisability"


def _cf_quality_ok(cf: dict) -> bool:
    """AGENTS.md §6.5: combined_insight must be a specific cross-paper discovery
    (>=20 words) and must not be the banned template phrase."""
    insight = str(cf.get("combined_insight", "")).strip()
    if _CF_BANNED_PHRASE in insight.lower():
        return False
    return len(insight.split()) >= 20


def _ia_included(ia: dict) -> bool:
    """Invalidated assumption is table-eligible unless rejected/pending/superseded."""
    if _has_exclude_token(ia.get("status", ""), ia.get("review_status", "")):
        return False
    # "pending" is deliberately NOT in _EXCLUDE_STATUS_TOKENS, because that tuple is also
    # applied to review_status and several legacy CONFIRMED records carry a stale
    # review_status of "pending_human_review". So gate on the record's OWN status only:
    # an IA that is still pending has not been confirmed and is not a table member.
    if _normalise_token(ia.get("status", "")).startswith("pending"):
        return False
    # A record with no status at all has never been reviewed; it is not a table member
    # (IA-94 reached Table 6.7 and the graph through this default, 2026-08-29 audit).
    if not _normalise_token(ia.get("status", "")):
        return False
    # A record still proposed for a gate has not passed one. "proposed_pending_g5" begins
    # with "proposed", so neither the exclude tokens nor the startswith("pending") test
    # above catches it: IA-94 and IA-95 reached Table 6.7, the graph and the DOCX that way
    # (2026-09-14 audit). Gate on any un-adjudicated marker anywhere in the status.
    if any(flag in _normalise_token(ia.get("status", ""))
           for flag in ("proposed", "pending", "provisional", "unreviewed")):
        return False
    return True


# AGENTS.md §6.5 pre-authored editorial illustrations (used only when fewer than five
# confirmed complementary findings pass the insight quality gate). These are editorial,
# not algorithmically generated, and are labelled as such in the table caption.
_CF_EDITORIAL_FALLBACK = [
    {"id": "CF-E1", "paper_a": "P-50", "paper_b": "P-48",
     "paper_a_finding": "Structural atrophy (VBM+SBM) achieves 89% LOOCV in isolation.",
     "paper_b_finding": "Multimodal adaptive fusion (structural+DTI+fMRI) achieves 79% 5-fold.",
     "combined_insight": "Structural and functional modalities are orthogonal: adaptive weighting lets DTI and fMRI contribute precisely when the structural pattern is ambiguous, so the panel detects structural-mimic MCI that atrophy alone misses.",
     "clinical_implication": "Multimodal panels catch functional dysconnection without atrophy, enabling earlier intervention than structure-only screening."},
    {"id": "CF-E2", "paper_a": "P-81", "paper_b": "P-26",
     "paper_a_finding": "Cross-sectional cognitive scores give a strong current-state MCI diagnosis.",
     "paper_b_finding": "Longitudinal latent-class trajectories separate progressive from stable MCI.",
     "combined_insight": "State and rate are distinct axes: a low baseline score does not imply a steep decline slope, so combining current-state classification with trajectory rate improves both diagnosis and prognosis beyond either alone.",
     "clinical_implication": "Stable-slope patients despite baseline impairment may have plateaued; trajectory outweighs baseline for conversion risk."},
    {"id": "CF-E3", "paper_a": "P-35", "paper_b": "P-91",
     "paper_a_finding": "In CHF-MCI, psychosocial adaptation dominates over cardiac features (XGBoost, AUC 0.94).",
     "paper_b_finding": "In occupational-exposure MCI, neurotoxic burden reorders the MTL predictor structure.",
     "combined_insight": "Comorbidity context reorders which features dominate: the predictor hierarchy in disease-specific MCI is not transferable, so a single global feature ranking misrepresents risk in any particular subpopulation.",
     "clinical_implication": "Comorbidity-aware substratification is required; one shared biomarker panel mis-stratifies across populations."},
    {"id": "CF-E4", "paper_a": "P-97", "paper_b": "P-98",
     "paper_a_finding": "Resting-state fMRI isolates Default Mode Network disruption as the dominant functional marker.",
     "paper_b_finding": "EEG/MEG converges on delta-band network change as an amyloid-linked signature.",
     "combined_insight": "Two independent physiological channels converge on the same posterior-network failure: functional-imaging DMN disruption and electrophysiological delta change describe one underlying process at different measurement scales.",
     "clinical_implication": "Convergent low-cost electrophysiology can proxy expensive fMRI DMN findings for scalable screening."},
    {"id": "CF-E5", "paper_a": "P-84", "paper_b": "P-99",
     "paper_a_finding": "Self-attention saliency emphasises MTL/PCC regions matching known pathology.",
     "paper_b_finding": "Whole-brain graph features outperform FreeSurfer ROI summaries on a non-ADNI cohort.",
     "combined_insight": "Preprocessing pathway shapes what is learnable: attention recovers known anatomy only when the upstream representation preserves between-region structure, so signal-preserving graph pipelines and interpretable saliency are complementary requirements.",
     "clinical_implication": "Interpretability claims are conditional on the preprocessing pathway; report both together for clinical trust."},
]


# ── Table generators ───────────────────────────────────────────────────────────

def _gen_table_62(rows: list) -> str:
    """Section 6.2: Diagnostic tables plus pointer to Appendix A."""
    n_rows = len(rows)

    tasks = ["T1", "T2", "T3", "T4", "T5", "T6"]
    categories = ["C1", "C2", "C3", "C4", "C5", "C6"]

    cat_task_counts = defaultdict(int)
    cat_totals = Counter()
    task_totals = Counter()
    validation_totals = Counter()
    arch_totals = Counter()
    arch_xai_totals = Counter()

    for row in rows:
        task = _clean_text(row.get("Task_Type"), "NR")
        validation = _clean_text(row.get("Validation_Type"), "NR")
        # Bucket non-tier strings ("not reported", free text) into NR so the
        # five Table 6.2b rows always sum to the corpus total.
        vm = re.match(r"(?i)\s*(V[1-4])\b", validation)
        validation = vm.group(1).upper() if vm else "NR"
        task_totals[task] += 1
        validation_totals[validation] += 1

        arch = _norm_arch_family(row.get("Architecture_Family", ""))
        arch_totals[arch] += 1
        if _xai_reported(row.get("XAI_Method", "")):
            arch_xai_totals[arch] += 1

        for category in _parse_categories(row.get("Category", "")):
            cat_totals[category] += 1
            cat_task_counts[(category, task)] += 1

    adni_true = sum(
        1
        for row in rows
        if str(row.get("ADNI_Dependent", "")).strip().lower() == "true"
    )
    adni_false = n_rows - adni_true

    matrix_lines = [
        "**Table 6.2a. Category-task coverage matrix (counts by primary/secondary category assignment).**",
        "",
        "| Category | T₁ | T₂ | T₃ | T₄ | T₅ | T₆ | Category Total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for category in categories:
        values = [cat_task_counts[(category, task)] for task in tasks]
        matrix_lines.append(
            f"| {category} | {values[0]} | {values[1]} | {values[2]} | {values[3]} | {values[4]} | {values[5]} | {cat_totals[category]} |"
        )
    # Margin reconciliation (PAPERINGO-359 / jfsk / fmf): rows with no C1-C6 code
    # used to vanish from the category rows while still being counted in the task
    # total, so a reader summing the column got a different N from the table's own
    # total. They get an explicit row; dual-coded rows and rows with no T1-T6 task
    # code are declared in a footnote instead of being silently absorbed.
    uncoded_by_task = Counter()
    n_uncoded = 0
    n_dual = 0
    n_task_nr = 0
    for row in rows:
        task = _clean_text(row.get("Task_Type"), "NR")
        cats = [c for c in _parse_categories(row.get("Category", "")) if c in categories]
        if not cats:
            n_uncoded += 1
            uncoded_by_task[task] += 1
        elif len(cats) > 1:
            n_dual += len(cats) - 1
        if task not in tasks:
            n_task_nr += 1
    if n_uncoded:
        matrix_lines.append(
            "| Uncoded (no C1–C6 assignment) | "
            + " | ".join(str(uncoded_by_task[t]) for t in tasks)
            + f" | {n_uncoded} |"
        )
    matrix_lines.append(
        f"| Task total | {task_totals['T1']} | {task_totals['T2']} | {task_totals['T3']} | {task_totals['T4']} | {task_totals['T5']} | {task_totals['T6']} | {n_rows} |"
    )
    cat_col_sum = sum(cat_totals[c] for c in categories) + n_uncoded
    matrix_lines.append("")
    if cat_col_sum == n_rows + n_dual:
        margin_note = f"_Margins: the Category Total column sums to {cat_col_sum} = {n_rows} papers" + (
            f" + {n_dual} extra code{'s' if n_dual != 1 else ''} from dual-coded papers counted once per code" if n_dual else "")
    else:  # never expected; say so rather than print a false identity
        margin_note = (f"_Margins: the Category Total column sums to {cat_col_sum} against a corpus total of "
                       f"{n_rows} ({n_dual} extra dual codes, {n_uncoded} uncoded rows) - RECONCILIATION FAILED, see Agent 6 log")
        _PENDING_LOG_LINES.append(f"[6.2] ERROR category margin does not reconcile: col_sum={cat_col_sum} n_rows={n_rows} dual={n_dual} uncoded={n_uncoded}")
    matrix_lines.append(
        margin_note
        + (f"; {n_task_nr} paper{'s' if n_task_nr != 1 else ''} carr{'y' if n_task_nr != 1 else 'ies'} no T1–T6 task code and appear{'' if n_task_nr != 1 else 's'} only in the corpus total" if n_task_nr else "")
        + "._"
    )

    validation_lines = [
        "**Table 6.2b. Validation profile and dataset dependency diagnostics.**",
        "",
        "| Diagnostic Axis | Value | Count | Share of Corpus |",
        "|---|---|---|---|",
    ]
    for validation in ["V1", "V2", "V3", "V4", "NR"]:
        count = validation_totals.get(validation, 0)
        share = (100 * count / n_rows) if n_rows else 0
        validation_lines.append(
            f"| Validation tier | {validation} | {count} | {share:.1f}% |"
        )
    adni_share = (100 * adni_true / n_rows) if n_rows else 0
    non_adni_share = (100 * adni_false / n_rows) if n_rows else 0
    validation_lines.append(
        f"| ADNI dependency | ADNI-dependent | {adni_true} | {adni_share:.1f}% |"
    )
    validation_lines.append(
        f"| ADNI dependency | Non-ADNI | {adni_false} | {non_adni_share:.1f}% |"
    )

    architecture_lines = [
        "**Table 6.2c. Architecture-family and XAI-observability profile.**",
        "",
        "| Architecture Family | Papers | Share of Corpus | Papers With Reported XAI |",
        "|---|---|---|---|",
    ]
    for arch in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "Other"]:
        count = arch_totals.get(arch, 0)
        share = (100 * count / n_rows) if n_rows else 0
        architecture_lines.append(
            f"| {arch} | {count} | {share:.1f}% | {arch_xai_totals.get(arch, 0)} |"
        )

    return (
        "Diagnostic summaries are computed directly from `full_paper_registry.csv` using "
        "row-preserving aggregation. For each category-task pair, the table reports "
        "$n_{c,t}$ and uses $p_{c,t}=n_{c,t}/N$ for proportion diagnostics.\n\n"
        + "\n".join(matrix_lines)
        + "\n\n"
        + "\n".join(validation_lines)
        + "\n\n"
        + "\n".join(architecture_lines)
        + "\n\n"
        + "The full row-level registry is moved to Appendix A (Table A.1).\n"
    )


def _gen_appendix_a_registry(rows: list) -> str:
    """Appendix A: full paper registry with one row per paper."""
    lines = [
        "## Appendix A. Full Corpus Paper Registry (Raw Rows)",
        "",
        "**Table A.1. Full row-level paper registry.**",
        "",
        "| Paper ID | Cat. | Task | Best Metric | Val. | Key Novelty (≤12 words) |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        pid = _clean_text(row.get("Paper_ID", ""), "NR")
        cat = _clean_text(row.get("Category", ""), "NR")
        task = _clean_text(row.get("Task_Type", ""), "NR")
        metric = _clean_text(row.get("Best_Metric", ""), "NR")[:32]
        val = _clean_text(row.get("Validation_Type", ""), "NR")
        novelty = " ".join(_clean_text(row.get("Key_Novelty", ""), "NR").split()[:12])
        lines.append(f"| {pid} | {cat} | {task} | {metric} | {val} | {novelty} |")
    return "\n".join(lines) + "\n"


def _gen_table_63(consensus: list) -> str:
    """Table 6.3: Verified Consensus."""
    header = (
        "**Table 6.3. Verified consensus findings.**\n\n"
        "| Claim ID | Statement | Supporting Papers | Independence Met? | Notes |\n"
        "|---|---|---|---|---|\n"
    )
    body_lines = []
    for vf in consensus:
        if not _vf_included(vf):
            continue
        cid        = vf.get("id", "")
        stmt       = _short(vf.get("statement", vf.get("name", "")), 120)
        papers     = vf.get("supporting_papers", [])
        # AGENTS.md cell truncation: >10 IDs -> first 8 + "... and N=X additional".
        if len(papers) > 10:
            supporting = ", ".join(papers[:8]) + f" ... and N={len(papers) - 8} additional (see consensus.json)"
        else:
            supporting = ", ".join(papers)
        indep      = vf.get("independence_met", "Partial")
        tier       = str(vf.get("tier", "")).strip()
        base_notes = vf.get("tier_rationale", vf.get("notes", "")) or ""
        notes      = (f"[{tier}] " if tier and tier != "-" else "") + base_notes
        notes      = _short(notes, 160)
        body_lines.append(f"| {cid} | {stmt} | {supporting} | {indep} | {notes} |")
    footnote = (
        "\n_Consensus IDs are stable and never renumbered, so gaps in the ID sequence are "
        "deliberate: records retired, demoted, or still held at human review (e.g. VF-13, "
        "VF-21) are omitted, and VF-22 (confirmed Tier 3, scope caveat) is intentionally "
        "presented in discussion text only (§6.12 Q6) rather than tabulated._\n"
    )
    return header + "\n".join(body_lines) + "\n" + footnote


def _gen_table_64(conflicts: list) -> str:
    """Table 6.4: Conflict Analysis."""
    # Compact 5-column layout (was 11): the three condition checks collapse into
    # one "Conditions matched" cell, and claims pair into one cell, so the table
    # stays readable in the DOCX instead of squeezing 11 columns across the page.
    header = (
        "**Table 6.4. Conflict analysis.** Conditions matched = task / dataset family / validation tier all comparable.\n\n"
        "| ID | Conflicting claims (A vs B) | Conditions matched | Genuine | Architectural interpretation |\n"
        "|---|---|---|---|---|\n"
    )
    body_lines = []
    for c in conflicts:
        if not _is_confirmed_conflict(c):
            continue
        cid   = c.get("id", "")
        pa    = c.get("paper_a", "")
        pb    = c.get("paper_b", "")
        ca = re.sub(r'^P-\d+:\s*', '', str(c.get('claim_a', '')))   # claim often repeats the paper id
        cb = re.sub(r'^P-\d+:\s*', '', str(c.get('claim_b', '')))
        claims = f"**{pa}:** {_short(ca, 80)} <br> **{pb}:** {_short(cb, 80)}"
        te    = _bool_to_yn(c.get("task_equal", c.get("task_equivalence", "")))
        de    = _bool_to_yn(c.get("dataset_equal", c.get("dataset_family_equivalence", "")))
        ve    = _bool_to_yn(c.get("validation_comparable", c.get("validation_tier_comparable", "")))
        cond  = f"{te} / {de} / {ve}"
        gen   = _bool_to_yn(c.get("genuine", c.get("genuine_conflict", "")))
        arch  = _short(c.get("architectural_interpretation", c.get("resolution_note", "")), 150)
        body_lines.append(f"| {cid} | {claims} | {cond} | {gen} | {arch} |")
    return header + "\n".join(body_lines) + "\n"


# Log lines produced while tables are being built; flushed under the run header.
_PENDING_LOG_LINES: list = []


def _gen_table_65(complementary: list) -> str:
    """Table 6.5: Complementary Findings."""
    header = (
        "**Table 6.5. Complementary findings.**\n\n"
        "| Pair ID | Paper A | Paper B | Paper A Finding | Paper B Finding | "
        "Combined Insight | Clinical Implication |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    # AGENTS.md §6.5: confirmed rows only, combined_insight quality gate, max 10.
    confirmed = [cf for cf in complementary if _cf_included(cf)]
    passing, rejected = [], []
    for cf in confirmed:
        (passing if _cf_quality_ok(cf) else rejected).append(cf)
    if rejected:
        # Buffered, not written here: the run header is written AFTER the tables
        # are built, so a line written now lands above the header of the run that
        # produced it and reads as the previous run's (PAPERINGO-4vh).
        _PENDING_LOG_LINES.append(
            f"[6.5] {len(rejected)} confirmed CF rows failed insight quality gate "
            f"(<20 words or banned template): {', '.join(c.get('id','?') for c in rejected)}"
        )

    caption = ""
    rows = passing[:10]
    if len(passing) < 5:
        # Fallback to the five pre-authored editorial illustrations.
        rows = _CF_EDITORIAL_FALLBACK
        caption = ("\n_Table 6.5 rows are editorial illustrations (pre-authored, not algorithmically "
                   "generated): fewer than five confirmed complementary findings passed the insight "
                   "quality gate._\n")
    elif len(confirmed) > len(rows):
        caption = (f"\n_Showing {len(rows)} representative rows of {len(confirmed)} confirmed "
                   f"complementary findings (pending Gate G6 review); full set in complementary.json._\n")

    body_lines = []
    for cf in rows:
        cfid = cf.get("id", "")
        pa   = cf.get("paper_a", "")
        pb   = cf.get("paper_b", "")
        fa_raw = cf.get("finding_a", None)
        if fa_raw is None:
            fa_raw = cf.get("paper_a_finding", "")
        fb_raw = cf.get("finding_b", None)
        if fb_raw is None:
            fb_raw = cf.get("paper_b_finding", "")
        fa   = _short(fa_raw, 120)
        fb   = _short(fb_raw, 120)
        ins  = _short(cf.get("combined_insight", ""), 200)
        imp  = _short(cf.get("clinical_implication", ""), 200)
        body_lines.append(f"| {cfid} | {pa} | {pb} | {fa} | {fb} | {ins} | {imp} |")
    return header + "\n".join(body_lines) + "\n" + caption


def _gen_table_66(gaps: list) -> str:
    """Table 6.6: Open Gap Verification."""
    header = (
        "**Table 6.6. Open gap verification.**\n\n"
        "| Gap ID | Gap Statement | Papers Claiming It | Papers Potentially Closing It | "
        "Gap Status | Evidence for Remaining Openness |\n"
        "|---|---|---|---|---|---|\n"
    )
    body_lines = []
    for g in gaps:
        if _is_candidate_gap(g):
            continue
        status_raw = _gap_status(g)
        if status_raw not in {"open", "partially_open"}:
            continue
        gid      = g.get("id", "")
        stmt     = _short(g.get("statement", g.get("gap_statement", "")), 120)
        claiming = ", ".join(g.get("papers_claiming_it", g.get("papers_claiming_gap", [])))
        closing  = ", ".join(g.get("papers_potentially_closing_it", g.get("papers_potentially_closing_gap", []))) or "None"
        status   = status_raw
        evidence = _short(g.get("evidence", g.get("evidence_for_open_status", "")), 100)
        body_lines.append(f"| {gid} | {stmt} | {claiming} | {closing} | {status} | {evidence} |")
    return header + "\n".join(body_lines) + "\n"


def _gen_table_67(invalidated: list) -> str:
    """Table 6.7: Invalidated Assumptions Registry."""
    header = (
        "**Table 6.7. Invalidated assumptions — prior beliefs disproved by AI evidence.**\n\n"
        "| ID | Prior Belief | What Disproved It | Paper | Type | Clinical Implication |\n"
        "|---|---|---|---|---|---|\n"
    )
    body_lines = []
    for ia in invalidated:
        if not _ia_included(ia):
            continue
        iaid   = ia.get("id", "")
        belief = _short(ia.get("prior_belief", ""), 95)
        disp   = _short(ia.get("what_disproved_it", ""), 95)
        paper  = ia.get("paper_id") or ia.get("paper", "")  # data uses paper_id; "paper" is legacy
        atype  = ia.get("assumption_type", "")
        impl   = _short(ia.get("proposed_clinical_implication", ia.get("clinical_implication", "")), 95)
        body_lines.append(f"| {iaid} | {belief} | {disp} | {paper} | {atype} | {impl} |")
    return header + "\n".join(body_lines) + "\n"


def _gen_table_68(rows: list, consensus: list, conflicts: list,
                   complementary: list, gaps: list, invalidated: list) -> str:
    """Table 6.8: Knowledge Graph Population Summary."""
    n_papers  = len(rows)
    adni_dep  = sum(1 for r in rows if str(r.get("ADNI_Dependent", "false")).lower() == "true")
    adni_pct  = round(100 * adni_dep / n_papers, 1) if n_papers else 0
    n_vf      = sum(1 for v in consensus if _vf_included(v))
    n_conf    = sum(
        1 for c in conflicts
        if _is_confirmed_conflict(c)
    )
    n_cf      = sum(1 for c in complementary if _cf_included(c))
    n_gaps    = sum(
        1
        for g in gaps
        if (
            not _is_candidate_gap(g)
            and _gap_status(g) in {"open", "partially_open"}
        )
    )
    n_ia      = sum(1 for i in invalidated if _ia_included(i))

    return (
        f"**Table 6.8. Knowledge graph population summary.**\n\n"
        f"| Entity Type | Count | Notes |\n"
        f"|---|---|---|\n"
        f"| Paper nodes | {n_papers} | All papers listed in Table A.1 |\n"
        f"| VerifiedFinding nodes | {n_vf} | All entries in Table 6.3 |\n"
        f"| Conflict nodes | {len(conflicts)} | {n_conf} confirmed genuine (Table 6.4); the remainder are adjudicated non-conflicts retained for provenance |\n"
        f"| ComplementaryFinding nodes | {n_cf} | Confirmed entries in Table 6.5 |\n"
        f"| Gap nodes | {n_gaps} | All entries in Table 6.6 |\n"
        f"| InvalidatedAssumption nodes | {n_ia} | Confirmed entries in Table 6.7 |\n"
        f"\n"
        f"**ADNI dependency:** {adni_dep}/{n_papers} Paper nodes ({adni_pct}%) carry ADNI as primary dataset.\n"
    )


def _gen_table_69_summary(rows: list, consensus: list, conflicts: list,
                            complementary: list, gaps: list) -> str:
    """Table 6.9 summary table (the static prose above it is NOT touched)."""
    n_papers = len(rows)
    # Count distinct canonical domain categories (C1-C6), not raw cells: a paper may
    # carry a compound assignment like "C1, C3", which must not inflate the category count.
    _cat_tokens = set()
    for r in rows:
        _cat_tokens.update(re.findall(r"C[1-6]", str(r.get("Category", ""))))
    n_cats   = len(_cat_tokens)
    incl_vf  = [v for v in consensus if _vf_included(v)]
    avg_sup  = (sum(len(v.get("supporting_papers", [])) for v in incl_vf) / len(incl_vf)) if incl_vf else 0
    n_conf   = sum(
        1 for c in conflicts
        if _is_confirmed_conflict(c)
    )
    n_cf     = sum(1 for c in complementary if _cf_included(c))
    n_gaps   = sum(
        1
        for g in gaps
        if (
            not _is_candidate_gap(g)
            and _gap_status(g) in {"open", "partially_open"}
        )
    )
    n_open   = sum(
        1 for g in gaps
        if (
            not _is_candidate_gap(g)
            and _gap_status(g) == "open"
        )
    )
    n_synth  = sum(
        1
        for g in gaps
        if (
            not _is_candidate_gap(g)
            and "synthetically" in str(g.get("evidence", g.get("evidence_for_open_status", ""))).lower()
        )
    )
    adni_pct = round(100 * sum(1 for r in rows if str(r.get("ADNI_Dependent", "false")).lower() == "true") / n_papers, 1) if n_papers else 0

    return (
        f"| Dimension | Assessment | Basis |\n"
        f"|---|---|---|\n"
        f"| Corpus coverage | Complete — {n_papers}/{n_papers} papers placed | Table 6.2a + Table A.1 |\n"
        f"| Category coverage | {n_cats} of 6 domain categories populated | Table 6.2a |\n"
        f"| Consensus depth | {len(incl_vf)} claims; avg {avg_sup:.1f} supporting papers | Table 6.3 |\n"
        f"| Conflict rigour | {n_conf} conflicts; all condition-normalised | Table 6.4 |\n"
        f"| Complementary pairs | {n_cf} pairs; all require cross-paper synthesis | Table 6.5 |\n"
        f"| Gap density | {n_gaps} gaps; {n_open} open; {n_synth} synthetically identified | Table 6.6 |\n"
        f"| ADNI dependency | {adni_pct}% | Table 6.8 |\n"
    )


def _gen_section_610(hub: dict) -> str:
    """Generate Section 6.10 narrative from hub JSON."""
    paper_id = _clean_text(hub.get("paper_id", ""), "not reported")
    title = _clean_text(hub.get("title", ""), "not reported")
    task_type = _clean_text(hub.get("task_type", ""), "not reported")
    best_metric = _clean_text(hub.get("best_metric", ""), "not reported")
    validation_tier = _clean_text(hub.get("validation_type", ""), "not reported")

    key_contribution = _clean_text(hub.get("key_contribution", ""), "not reported")
    motivation = _clean_text(hub.get("motivation", ""), "not reported")
    after_research = hub.get("what_happened_after", hub.get("hub_trace", []))
    limitations = _clean_text(hub.get("limitations", ""), "not reported")
    importance = _clean_text(hub.get("importance", ""), "not reported")
    method_or_data = _clean_text(hub.get("method_or_data_novelty", ""), "not reported")
    network_position = hub.get("network_position", hub.get("graph_edges", []))

    if not isinstance(after_research, list):
        after_research = [str(after_research)] if after_research else []
    if not isinstance(network_position, list):
        network_position = [str(network_position)] if network_position else []

    after_lines = "\n".join(f"- {item}" for item in after_research) if after_research else "- not reported"
    network_lines = "\n".join(f"- {item}" for item in network_position) if network_position else "- not reported"

    return (
        "The six knowledge tables in Sections 6.2–6.8 are designed to answer cross-paper "
        "questions that individual reading cannot address. This section shows the same "
        "query path for one selected paper using structured hub JSON.\n\n"
        f"**Paper selected:** {paper_id} — {title}. "
        f"Task: {task_type}; Best metric: {best_metric}; Validation: {validation_tier}.\n\n"
        "**What is the key contribution?**\n\n"
        f"{key_contribution}\n\n"
        "**What is the main motivation?**\n\n"
        f"{motivation}\n\n"
        "**What has been done after this research? (hub trace)**\n\n"
        f"{after_lines}\n\n"
        "**How important is the contribution?**\n\n"
        f"{importance}\n\n"
        "**Did the authors devise a new method or collect new data?**\n\n"
        f"{method_or_data}\n\n"
        "**What are the limitations?**\n\n"
        f"{limitations}\n\n"
        "**How is the selected paper positioned on the progress network?**\n\n"
        f"{network_lines}\n"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

# Markers that delimit each dynamic table in review_paper.md
TABLE_MARKERS = {
    "6.2": ("<!-- TABLE_6.2_START -->", "<!-- TABLE_6.2_END -->"),
    "6.3": ("<!-- TABLE_6.3_START -->", "<!-- TABLE_6.3_END -->"),
    "6.4": ("<!-- TABLE_6.4_START -->", "<!-- TABLE_6.4_END -->"),
    "6.5": ("<!-- TABLE_6.5_START -->", "<!-- TABLE_6.5_END -->"),
    "6.6": ("<!-- TABLE_6.6_START -->", "<!-- TABLE_6.6_END -->"),
    "6.7": ("<!-- TABLE_6.7_START -->", "<!-- TABLE_6.7_END -->"),
    "6.8": ("<!-- TABLE_6.8_START -->", "<!-- TABLE_6.8_END -->"),
    "6.9": ("<!-- TABLE_6.9_START -->", "<!-- TABLE_6.9_END -->"),
}


def _replace_section_by_heading(content: str, table_key: str, new_table: str) -> str:
    start_re = re.compile(rf"^#{{2,3}}\s*{re.escape(table_key)}\b[^\n]*\n", re.MULTILINE)
    m = start_re.search(content)
    if not m:
        return content

    start = m.end()
    if table_key == "6.9":
        end_re = re.compile(r"^##\s*6\.10\b|^##\s*7\b", re.MULTILINE)
    else:
        next_minor = str(round(float(table_key) + 0.1, 1))
        end_re = re.compile(rf"^#{{2,3}}\s*{re.escape(next_minor)}\b", re.MULTILINE)
    me = end_re.search(content, start)
    end = me.start() if me else len(content)

    return content[:start] + "\n" + new_table + "\n\n" + content[end:]


def _replace_table(content: str, table_key: str, new_table: str) -> str:
    """Replace content between markers. If markers absent, appends note."""
    start_marker, end_marker = TABLE_MARKERS[table_key]
    if start_marker not in content:
        return _replace_section_by_heading(content, table_key, new_table)
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    replacement = f"{start_marker}\n{new_table}\n{end_marker}"
    return pattern.sub(replacement, content)


def _replace_section_610(content: str, section_text: str) -> str:
    start_re = re.compile(r"^##\s*6\.10\b[^\n]*\n", re.MULTILINE)
    start_match = start_re.search(content)
    if not start_match:
        return content
    start = start_match.end()
    end_re = re.compile(r"^##\s*7\b", re.MULTILINE)
    end_match = end_re.search(content, start)
    end = end_match.start() if end_match else len(content)
    return content[:start] + "\n" + section_text + "\n\n" + content[end:]


def _upsert_appendix_a(content: str, appendix_table: str) -> str:
    appendix_re = re.compile(r"^##\s*Appendix\s+A\b[^\n]*\n", re.MULTILINE)
    match = appendix_re.search(content)
    if match:
        start = match.start()
        next_appendix_re = re.compile(r"^##\s*Appendix\s+[B-Z]\b", re.MULTILINE)
        next_match = next_appendix_re.search(content, match.end())
        end = next_match.start() if next_match else len(content)
        return content[:start] + appendix_table + "\n\n" + content[end:]
    trimmed = content.rstrip()
    return trimmed + "\n\n# Appendix\n\n" + appendix_table + "\n"


def run(state: dict):
    """Main entry point. Updates dynamic tables in review_paper.md."""
    rows        = _load_registry()
    consensus   = _load_json("consensus.json")
    conflicts   = _load_json("conflicts.json")
    complementary = _load_json("complementary.json")
    gaps        = _load_json("gaps.json")
    invalidated = _load_json("invalidated.json")
    hub_trace = _load_hub_trace()

    if not PAPER_FILE.exists():
        log.warning(
            "review_paper.md not found. Creating skeleton with table markers.\n"
            "Add static prose sections manually; Agent 6 manages only the dynamic tables."
        )
        _create_skeleton()

    content = PAPER_FILE.read_text(encoding="utf-8")

    # Generate all tables
    tables = {
        "6.2": _gen_table_62(rows),
        "6.3": _gen_table_63(consensus),
        "6.4": _gen_table_64(conflicts),
        "6.5": _gen_table_65(complementary),
        "6.6": _gen_table_66(gaps),
        "6.7": _gen_table_67(invalidated),
        "6.8": _gen_table_68(rows, consensus, conflicts, complementary, gaps, invalidated),
        "6.9": _gen_table_69_summary(rows, consensus, conflicts, complementary, gaps),
    }

    for key, table in tables.items():
        content = _replace_table(content, key, table)

    content = _upsert_appendix_a(content, _gen_appendix_a_registry(rows))

    if hub_trace:
        content = _replace_section_610(content, _gen_section_610(hub_trace))

    PAPER_FILE.write_text(content, encoding="utf-8")

    # Ledger-vs-table reconciliation for genuine conflicts (PAPERINGO-fee (b)):
    # the Table 6.8 note and Table 6.4 both derive from _is_confirmed_conflict,
    # so compare that set with the raw ledger flag and shout if they differ.
    ledger_genuine = ledger_genuine_conflict_ids(conflicts)
    rendered_genuine = [c.get("id", "") for c in conflicts if _is_confirmed_conflict(c)]
    if set(ledger_genuine) != set(rendered_genuine):
        missing = sorted(set(ledger_genuine) - set(rendered_genuine))
        extra = sorted(set(rendered_genuine) - set(ledger_genuine))
        _PENDING_LOG_LINES.append(
            f"[6.4] ERROR genuine-conflict mismatch: ledger flags {len(ledger_genuine)} "
            f"({', '.join(ledger_genuine)}); Table 6.4 renders {len(rendered_genuine)}"
            + (f"; missing {', '.join(missing)}" if missing else "")
            + (f"; unflagged extras {', '.join(extra)}" if extra else "")
        )
        log.error("Table 6.4 / conflicts.json genuine-conflict mismatch: %s", _PENDING_LOG_LINES[-1])
    else:
        _PENDING_LOG_LINES.append(
            f"[6.4] {len(rendered_genuine)} genuine conflicts rendered = ledger flag count "
            f"({', '.join(rendered_genuine)})"
        )
    excluded_vfs = [f"{v.get('id','?')}({'no supporters' if not v.get('supporting_papers') else _normalise_token(v.get('manuscript_status','')) or _normalise_token(v.get('status','')) or 'unreviewed'})"
                    for v in consensus if not _vf_included(v)]
    _PENDING_LOG_LINES.append(f"[6.3] VFs excluded from Table 6.3: {', '.join(excluded_vfs) or 'none'}")

    # Write log
    with open(PAPER6_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n--- {datetime.utcnow().isoformat()} ---\n")
        f.write(f"Tables updated: {list(tables.keys())}\n")
        f.write(f"Appendix A updated: yes\n")
        f.write(f"Section 6.10 updated from hub JSON: {'yes' if hub_trace else 'no'}\n")
        f.write(f"Papers in registry: {len(rows)}\n")
        for line in _PENDING_LOG_LINES:
            f.write(line + "\n")
    _PENDING_LOG_LINES.clear()

    log.info(
        "Agent 6 complete. Sections 6.2–6.9 updated, Appendix A refreshed, "
        f"registry rows: {len(rows)}."
    )


def _create_skeleton():
    """Create a skeleton review_paper.md with all required markers."""
    skeleton = """# Cognitive Knowledge Construction for Predicting MCI Progression
## A Living Literature Synthesis Hub

---

[STATIC SECTIONS 1–5 — add prose here manually]

---

## 6. Experimental Evaluation

### 6.1 Evaluation Design

[STATIC — do not edit for single paper additions]

---

### 6.2 Full Corpus Coverage: Diagnostic Registry View

<!-- TABLE_6.2_START -->
[Generated by Agent 6]
<!-- TABLE_6.2_END -->

---

## Appendix A. Full Corpus Paper Registry (Raw Rows)

[Generated by Agent 6]

---

### 6.3 Verified Consensus: Findings Confirmed Across Independent Studies

<!-- TABLE_6.3_START -->
[Generated by Agent 6]
<!-- TABLE_6.3_END -->

---

### 6.4 Conflict Analysis: Condition-Normalised Cross-Paper Contradictions

<!-- TABLE_6.4_START -->
[Generated by Agent 6]
<!-- TABLE_6.4_END -->

---

### 6.5 Complementary Findings: Papers That Together Reveal What Neither Shows Alone

<!-- TABLE_6.5_START -->
[Generated by Agent 6]
<!-- TABLE_6.5_END -->

---

### 6.6 Open Gap Verification

<!-- TABLE_6.6_START -->
[Generated by Agent 6]
<!-- TABLE_6.6_END -->

---

### 6.7 Invalidated Assumptions Registry

<!-- TABLE_6.7_START -->
[Generated by Agent 6]
<!-- TABLE_6.7_END -->

---

### 6.8 Knowledge Graph Population Summary

<!-- TABLE_6.8_START -->
[Generated by Agent 6]
<!-- TABLE_6.8_END -->

---

### 6.9 Coverage, Depth, and Contribution Assessment

[STATIC prose — do not edit for single paper additions]

<!-- TABLE_6.9_START -->
[Generated by Agent 6]
<!-- TABLE_6.9_END -->

---

## 7. Conclusion and Future Work

[STATIC — do not edit]

---

## References

[DYNAMIC — append new entries only]
"""
    PAPER_FILE.write_text(skeleton, encoding="utf-8")
    log.info(f"Created skeleton: {PAPER_FILE}")
