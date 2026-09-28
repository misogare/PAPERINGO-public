"""
agent4_relational.py — Relational Analysis Agent
Sub-tasks: conflict detection, consensus verification, complementary finding detection,
gap closure checking, invalidated assumption detection.

This implementation is fully local and does not require external LLM APIs
(except for the single optional LLM-cluster gap-synthesis call, which degrades
gracefully to a no-op when ANTHROPIC_API_KEY is absent).
"""

import csv
import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
from itertools import combinations
from pathlib import Path
import copy

log = logging.getLogger("agent4_relational")

try:
    from opentelemetry import trace
except Exception:
    trace = None

ROOT         = Path(__file__).parent.parent
DATA         = ROOT / "data"
CONFIG       = ROOT / "config"
LOGS         = ROOT / "logs"
REGISTRY     = DATA / "full_paper_registry.csv"
SUMMARIES    = DATA / "summaries.md"
SUMMARIES_FALLBACK = ROOT / "summaries.md"
CONFLICTS    = DATA / "conflicts.json"
CONSENSUS    = DATA / "consensus.json"
COMPLEMENTARY       = DATA / "complementary.json"
COMPLEMENTARY_CANDS = DATA / "complementary_candidates.json"
GAPS         = DATA / "gaps.json"
INVALIDATED  = DATA / "invalidated.json"
REL_LOG      = LOGS / "agent4_relational_log.txt"
ASSUMPTION_REG = CONFIG / "assumption_registry.json"
DOMAIN_STOPLIST = CONFIG / "domain_stoplist.json"

_SUMMARY_INDEX_CACHE = None
_SUMMARY_INDEX_SOURCE = None


def _init_tracer():
    """Return a tracer; if SDK/exporter is available, configure OTLP gRPC when needed."""
    if trace is None:
        return None

    try:
        provider = trace.get_tracer_provider()
        provider_name = provider.__class__.__name__
        if provider_name == "ProxyTracerProvider":
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            endpoint = os.getenv("OTEL_EXPORTER_OTLP_GRPC_ENDPOINT", "localhost:4317")
            insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").strip().lower() in {"1", "true", "yes", "on"}

            span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
            tracer_provider = TracerProvider()
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)
            log.info("Agent 4 tracing enabled (OTLP gRPC endpoint=%s)", endpoint)
    except Exception as e:
        log.warning("Agent 4 tracing setup skipped: %s", e)

    try:
        return trace.get_tracer("agent4_relational")
    except Exception:
        return None


TRACER = _init_tracer()


@contextmanager
def traced_span(name: str, attributes: dict | None = None):
    if TRACER is None:
        yield None
        return

    with TRACER.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                if v is None:
                    continue
                span.set_attribute(k, str(v))
        yield span

# ── JSON file helpers ──────────────────────────────────────────────────────────

def _load_json(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return {}

def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_domain_stoplist() -> set:
    """Load domain-specific stop tokens to exclude from overlap scoring."""
    if DOMAIN_STOPLIST.exists():
        try:
            data = json.loads(DOMAIN_STOPLIST.read_text(encoding="utf-8"))
            return set(data.get("stop_tokens", []))
        except Exception as e:
            log.warning(f"Failed to load domain stoplist: {e}. Using default.")
    # Fallback stop-list
    return {
        "cognitive", "decline", "mci", "assessment", "diagnosis",
        "biomarker", "research", "whether", "study", "results",
        "method", "approach", "performance", "data", "model",
    }


def _compute_file_hash(path: Path) -> str:
    """Compute MD5 hash of file for duplicate detection."""
    if not path.exists():
        return ""
    try:
        hash_md5 = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        log.warning(f"Failed to hash {path}: {e}")
        return ""


def _extract_records(payload, key: str) -> list:
    if isinstance(payload, dict):
        recs = payload.get(key, [])
        return recs if isinstance(recs, list) else []
    if isinstance(payload, list):
        return payload
    return []


def _inject_records(payload, key: str, records: list):
    if isinstance(payload, dict):
        payload[key] = records
        return payload
    return records


def _save_json_append_safe(path: Path, key: str, in_memory_records: list):
    """Concurrency-safe save for APPEND-ONLY record sets (e.g. invalidated_assumptions).

    Agent 4 loads the file at the start of a run and holds it in memory for the
    whole batch, then blindly overwrites it at the end. If another process (e.g. a
    human/agent G5 correction) writes the same file between our load and our save,
    the plain write_text below would silently discard those edits (lost update).

    To prevent that, we RE-READ the file at save time, keep EVERY record already on
    disk (preserving any concurrent correction / re-grounding), and append only the
    records whose id is genuinely new this run. Existing records are never
    overwritten from our stale in-memory snapshot. This matches the invalidated-
    assumption flow, which only ever appends new (paper, assumption) records and
    never mutates existing ones in place.
    """
    disk_payload = _load_json(path)
    disk_records = _extract_records(disk_payload, key)
    disk_ids = {r.get("id") for r in disk_records if isinstance(r, dict)}
    merged = list(disk_records)
    added = 0
    for r in in_memory_records:
        if isinstance(r, dict) and r.get("id") not in disk_ids:
            merged.append(r)
            added += 1
    out_payload = disk_payload if isinstance(disk_payload, dict) else {}
    out_payload = _inject_records(out_payload, key, merged)
    _save_json(path, out_payload)
    return added


def _save_gaps_merge_safe(path: Path, key: str, records: list, base_by_id: dict):
    """Concurrency-safe save for gaps.json, which (unlike invalidated_assumptions)
    is NOT pure-append: Agent 4 updates existing gap records' closure status in the
    same run. A blind overwrite would lose a concurrent editor's edits (e.g. a
    human/G5 gap confirmation) made between our load and our save, and a plain
    append-only merge would instead lose OUR own in-run closure updates.

    So we do a THREE-WAY, field-level merge per record id:
      base    = the record as we loaded it at run start (base_by_id),
      current = our in-memory record now (base + our own updates this run),
      disk    = the record re-read from disk now (base + any concurrent edit).
    For each field: if we changed it this run (current != base) our value wins
    (Agent 4's closure update); otherwise the disk value wins (preserving a
    concurrent edit). Records we never touched are taken from disk, and records
    added concurrently on disk that we never saw are kept. If BOTH sides changed
    the same field, our run wins (documented, not silently dropped).
    """
    disk_payload = _load_json(path)
    disk_by_id = {}
    for r in _extract_records(disk_payload, key):
        if isinstance(r, dict) and r.get("id"):
            disk_by_id.setdefault(r["id"], r)
    merged, seen = [], set()
    for cur in records:
        if not isinstance(cur, dict) or not cur.get("id"):
            merged.append(cur)
            continue
        rid = cur["id"]
        seen.add(rid)
        base = base_by_id.get(rid)
        disk = disk_by_id.get(rid)
        if base is None or disk is None:
            merged.append(cur)  # new this run (or no disk copy) -> our version
            continue
        out = dict(disk)  # start from disk (preserves concurrent edits)
        for f, v in cur.items():
            if f not in base or base.get(f) != v:
                out[f] = v  # we changed/added this field this run -> ours wins
        merged.append(out)
    for rid, disk in disk_by_id.items():
        if rid not in seen:
            merged.append(disk)  # concurrently-added record we never saw
    out_payload = disk_payload if isinstance(disk_payload, dict) else {}
    out_payload = _inject_records(out_payload, key, merged)
    if isinstance(out_payload, dict):
        _refresh_gaps_summary(out_payload, merged)
    _save_json(path, out_payload)
    return len(merged)

def _next_id(records: list, prefix: str) -> str:
    nums = [int(r["id"].replace(prefix, "")) for r in records if r.get("id", "").startswith(prefix) and r["id"].replace(prefix, "").isdigit()]
    return f"{prefix}{(max(nums) + 1) if nums else 1:02d}"


def _safe_iso_today() -> str:
    return date.today().isoformat()


def _normalise_gap_status(raw: str) -> str:
    s = (raw or "open").strip().lower().replace(" ", "_")
    if s in {"open", "partially_open", "closed", "candidate"}:
        return s
    return "open"


def _get_gap_status(gap: dict) -> str:
    if "current_status" in gap:
        return _normalise_gap_status(gap.get("current_status", "open"))
    return _normalise_gap_status(gap.get("status", "open"))


def _set_gap_status(gap: dict, status: str):
    value = _normalise_gap_status(status)
    if "current_status" in gap:
        gap["current_status"] = value
    else:
        gap["status"] = value


def _get_gap_claiming_key(gap: dict) -> str:
    if "papers_claiming_gap" in gap:
        return "papers_claiming_gap"
    return "papers_claiming_it"


def _get_gap_closing_key(gap: dict) -> str:
    if "papers_potentially_closing_gap" in gap:
        return "papers_potentially_closing_gap"
    return "papers_potentially_closing_it"


def _refresh_conflicts_summary(payload: dict, conflicts: list):
    if not isinstance(payload, dict):
        return
    summary = payload.setdefault("summary", {})
    summary["total_conflicts"] = len(conflicts)
    genuine = 0
    partial = 0
    not_genuine = 0
    for c in conflicts:
        g = c.get("genuine_conflict", c.get("genuine", False))
        if isinstance(g, bool):
            if g:
                genuine += 1
            else:
                not_genuine += 1
        elif str(g).strip().lower() == "partial":
            partial += 1
        elif str(g).strip().lower() in {"yes", "true"}:
            genuine += 1
        else:
            not_genuine += 1
    summary["genuine_conflicts"] = genuine
    summary["partial_conflicts"] = partial
    summary["not_genuine_conflicts"] = not_genuine
    summary["last_updated"] = _safe_iso_today()


def _refresh_gaps_summary(payload: dict, gaps: list):
    if not isinstance(payload, dict):
        return
    summary = payload.setdefault("summary", {})
    summary["total_gaps_all"] = len(gaps)
    open_gaps = 0
    partial_gaps = 0
    closed_gaps = 0
    candidate_gaps = 0
    curated_open = 0
    curated_closed = 0
    # Presented in thesis/review: curated + limitation-derived only.
    review_open_gaps = 0
    review_partial_gaps = 0
    review_closed_gaps = 0
    review_candidate_gaps = 0
    for g in gaps:
        status = _get_gap_status(g)
        tier = _normalise_text(g.get("tier", "")) or _infer_gap_tier(g)
        if status == "open":
            open_gaps += 1
        elif status == "partially_open":
            partial_gaps += 1
        elif status == "closed":
            closed_gaps += 1
        elif status == "candidate":
            candidate_gaps += 1

        if tier == "curated":
            if status == "closed":
                curated_closed += 1
            else:
                curated_open += 1

        if tier in {"curated", "derived"}:
            if status == "open":
                review_open_gaps += 1
            elif status == "partially_open":
                review_partial_gaps += 1
            elif status == "closed":
                review_closed_gaps += 1
            elif status == "candidate":
                review_candidate_gaps += 1

    # Tier-filtered reporting for manuscript-facing summaries.
    summary["curated_open_gaps"] = curated_open
    summary["curated_closed_gaps"] = curated_closed
    summary["total_gaps"] = curated_open + curated_closed
    summary["open_gaps"] = curated_open
    if curated_closed > 0 or "closed_gaps" in summary:
        summary["closed_gaps"] = curated_closed

    # Keep all-tier counts for auditing and diagnostics.
    summary["all_open_gaps"] = open_gaps
    summary["all_partially_open_gaps"] = partial_gaps
    summary["all_candidate_gaps"] = candidate_gaps
    if closed_gaps > 0 or "all_closed_gaps" in summary:
        summary["all_closed_gaps"] = closed_gaps
    summary["review_open_gaps"] = review_open_gaps
    summary["review_partially_open_gaps"] = review_partial_gaps
    summary["review_candidate_gaps"] = review_candidate_gaps
    if review_closed_gaps > 0 or "review_closed_gaps" in summary:
        summary["review_closed_gaps"] = review_closed_gaps
    summary["last_updated"] = _safe_iso_today()


# ── Registry loading ───────────────────────────────────────────────────────────

def _load_registry() -> list:
    if not REGISTRY.exists():
        return []
    rows = []
    with open(REGISTRY, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize BOM-prefixed headers such as "\ufeffPaper_ID".
            normalized = {
                (k.lstrip("\ufeff") if isinstance(k, str) else k): v
                for k, v in row.items()
            }
            rows.append(normalized)
    return rows

def _registry_by_id(rows: list) -> dict:
    by_id = {}
    for r in rows:
        pid = r.get("Paper_ID") or r.get("paper_id")
        if not pid:
            continue
        if "Paper_ID" not in r:
            r["Paper_ID"] = pid
        by_id[pid] = r
    return by_id


# ── Sub-task 4A: Conflict Detection ───────────────────────────────────────────

TIER_ORDER = {"V1": 1, "V2": 2, "V3": 3, "V4": 4}
ALL_TASK_TYPES = ["T1", "T2", "T3", "T4", "T5", "T6"]

# Population-specificity values considered "general" (no disease restriction).
# FIX (Issue 1): the previous gate in _check_conflicts() tested for the
# literal substring "disease_specific" inside the population_specificity
# value, but agent3's actual schema values are bare disease codes ("T2DM",
# "sarcopenia", "CHF", "PD", "ESRD", "chronic_pain", "occupational") which
# never contain that substring -- so the gate silently never fired for any
# real value. This constant defines the actual "no restriction" values so the
# gate can correctly distinguish general-population papers from
# disease-specific ones, and from each other.
GENERAL_POPULATION_VALUES = {"general", "not reported", "not_reported", ""}

L2_MODALITY_NODES = [
    "Neuroimaging",
    "Electrophysiology",
    "Ocular",
    "LanguageBehavior",
    "Molecular",
    "ClinicalEHR",
]

_MODALITY_KEYWORDS = {
    "Neuroimaging": {"mri", "fmri", "pet", "dti", "tractography", "freesurfer", "neuroimaging"},
    "Electrophysiology": {"eeg", "meg", "fnirs", "coherence", "psd", "wavelet"},
    "Ocular": {"retinal", "retina", "fundus", "oct", "eye", "eye-tracking", "ocul"},
    "LanguageBehavior": {"speech", "language", "video", "handwriting", "driving", "cognitive", "behavior", "behaviour"},
    "Molecular": {"omics", "transcriptomic", "gene", "genetic", "plasma", "csf", "amyloid", "tau", "immun"},
    "ClinicalEHR": {"ehr", "clinical", "survey", "demographic", "score", "questionnaire", "functional"},
}

_OUT_OF_SCOPE_HINTS = {
    "phase 3", "randomised placebo", "randomized placebo", "rct", "clinical trial",
    "regulatory", "dsm", "icd", "consensus workshop", "qaly", "health economics",
    "clinical pharmacology", "pharmacological", "drug", "genomics consortium",
}

_LEGACY_COMBINATORIAL_POINTER_RE = re.compile(
    r"^Missing cross-combination for T[1-6]:\s*P-\d+\s+method with\s+P-\d+\s+modality\s+\(or inverse\)$",
    flags=re.IGNORECASE,
)

_CONSENSUS_DEFAULTS = {
    "required_modality_family": "any",
    "required_design": "any",
    "min_task_hits": 5,
}

_CONSENSUS_REQUIREMENTS_OVERRIDES = {
    "VF-03": {
        "required_modality_family": "any",
        "required_design": "longitudinal",
    },
    "VF-04": {
        "required_modality_family": "functional_mri",
        "required_design": "any",
    },
    "VF-08": {
        "required_modality_family": "graph_nn",
        "required_design": "any",
    },
    "VF-13": {
        "required_modality_family": "retinal",
        "required_design": "any",
    },
}


def _registry_paper_ids(all_rows: list) -> set:
    return {r.get("Paper_ID", "") for r in all_rows if r.get("Paper_ID")}


def _normalise_text(text: str) -> str:
    return (text or "").strip().lower()


def _similarity_ratio(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta.intersection(tb))
    union = len(ta.union(tb))
    return inter / max(1, union)


def _is_out_of_scope_gap(gap_statement: str, closure_criteria: str) -> tuple[bool, str]:
    combined = _normalise_text(f"{gap_statement} {closure_criteria}")
    hits = sorted([h for h in _OUT_OF_SCOPE_HINTS if h in combined])
    if hits:
        return True, f"closure requires non-AI/ML dependency ({', '.join(hits[:4])})"
    return False, ""


def _is_conflict_pair(paper_a: str, paper_b: str, conflicts: list) -> bool:
    target = frozenset([paper_a, paper_b])
    for c in conflicts:
        pair = frozenset([c.get("paper_a", ""), c.get("paper_b", "")])
        if pair == target:
            return True
    return False


def _find_semantic_gap_match(existing_gaps: list, new_statement: str) -> dict | None:
    best = None
    best_score = 0.0
    for gap in existing_gaps:
        score = _similarity_ratio(gap.get("gap_statement", ""), new_statement)
        if score > best_score:
            best_score = score
            best = gap
    if best is not None and best_score >= 0.35:
        return best
    return None


def _get_related_l2_nodes(modality_text: str) -> set:
    text = _normalise_text(modality_text)
    nodes = set()
    for node, kws in _MODALITY_KEYWORDS.items():
        if any(k in text for k in kws):
            nodes.add(node)
    return nodes


def _modalities_related_but_different(mod_a: str, mod_b: str) -> bool:
    a = _normalise_text(mod_a)
    b = _normalise_text(mod_b)
    if not a or not b or a == b:
        return False
    na = _get_related_l2_nodes(a)
    nb = _get_related_l2_nodes(b)
    return bool(na.intersection(nb))


def _combination_exists(rows: list, method_arch: str, modality: str, task_type: str) -> bool:
    m = _normalise_text(method_arch)
    mod = _normalise_text(modality)
    task = (task_type or "").strip()
    for r in rows:
        if (r.get("Task_Type", "").strip() != task):
            continue
        r_method = _normalise_text(r.get("Method_Architecture", ""))
        r_mod = _normalise_text(r.get("Modality", ""))
        if m and m in r_method and mod and mod in r_mod:
            return True
    return False


def _next_gap_candidate_id(gaps: list) -> str:
    nums = []
    for g in gaps:
        gid = g.get("id", "")
        if gid.startswith("G-CAND-"):
            tail = gid.replace("G-CAND-", "")
            if tail.isdigit():
                nums.append(int(tail))
    return f"G-CAND-{(max(nums) + 1) if nums else 1:02d}"


def _is_noisy_legacy_combinatorial_gap(gap: dict) -> bool:
    gap_type = _normalise_text(str(gap.get("gap_type", "")))
    tier = _normalise_text(str(gap.get("tier", "")))
    if gap_type != "combinatorial" and tier != "combinatorial":
        return False
    # Never prune a human-curated/verified combinatorial gap. A small number of
    # combinatorial gaps were hand-kept by the human (e.g. G-CAND-1127/1122/1117)
    # despite the bare "No published study combines X with Y" template -- they are
    # genuine cross-modal gaps. They carry human_curated/human_evaluation_note;
    # protect them from the noise pruner.
    if gap.get("human_curated") or gap.get("human_evaluation_note"):
        return False

    statement = (gap.get("gap_statement", "") or "").strip()
    if not statement:
        return True

    if _LEGACY_COMBINATORIAL_POINTER_RE.match(statement):
        return True
    # Deprecated per AGENTS.md: bare method×modality pointers carrying no
    # physiological/architectural rationale. The old pruner regex only matched
    # the "Missing cross-combination for T#:" format; the generator later
    # switched to "No published study combines X with Y for T# prediction"
    # (and "No paper combines ..."), which slipped past the pruner and
    # accumulated to ~900+ records. Match those too so they self-clean.
    if re.match(r"^No (?:published study|paper) combines\b", statement, re.IGNORECASE):
        return True
    return False


def _prune_noisy_combinatorial_gaps(gaps: list, log_lines: list) -> tuple[list, int]:
    pruned = []
    removed = 0
    for gap in gaps:
        if _is_noisy_legacy_combinatorial_gap(gap):
            removed += 1
            if removed <= 5:
                log_lines.append(
                    f"cleanup removed_noisy_combinatorial_gap id={gap.get('id', 'NA')}"
                )
            continue
        pruned.append(gap)

    if removed:
        log_lines.append(f"cleanup removed_noisy_combinatorial_total={removed}")
    return pruned, removed


# Boilerplate closure emitted by the (now disabled) single-paper-limitation gap
# generator. It is the precise signature of that false-positive class.
_SINGLE_PAPER_LIMITATION_CLOSURE = "Requires follow-up validated study addressing this limitation"


def _is_single_paper_limitation_gap(gap: dict) -> bool:
    """True if a 'gap' is really just ONE paper's Limitations section reformatted.

    These were auto-derived (tier=derived) with a single claiming paper and the
    boilerplate closure above. A single study's limitation (small N, cross-sectional,
    single-site) is a study weakness, NOT a corpus-level gap, so the operator rejected
    the whole class as false positives (G-CAND-319..335). Human-curated records are
    protected (a few were hand-kept and re-scoped)."""
    if gap.get("human_curated") or gap.get("human_evaluation_note"):
        return False
    if (gap.get("closure_criteria") or "").strip() != _SINGLE_PAPER_LIMITATION_CLOSURE:
        return False
    claiming = gap.get("papers_claiming_gap") or []
    return isinstance(claiming, list) and len(claiming) == 1


def _auto_reject_single_paper_limitation_gaps(gaps: list, log_lines: list) -> int:
    """Auto-reject the single-paper-limitation false-positive class so it stops
    accumulating as 'candidate' and no longer needs manual rejection every batch.
    Records rather than deletes (status -> 'rejected' + reason), preserving the audit
    trail and matching the operator's existing convention for this class. Protects
    human-curated records via _is_single_paper_limitation_gap."""
    rejected = 0
    for gap in gaps:
        # Use the RAW status: "rejected"/"closed" are not canonical statuses (they
        # would normalise to "open"), so only touch genuinely unreviewed records and
        # write current_status="rejected" directly (matching the existing convention).
        raw = (gap.get("current_status") or gap.get("status") or "").strip().lower()
        if raw in {"candidate", "open"} and _is_single_paper_limitation_gap(gap):
            gap["current_status" if "current_status" in gap else "status"] = "rejected"
            gap["rejection_reason"] = (
                "false_positive_single_paper_limitation (auto): a single paper's "
                "limitations section is a study weakness, not a corpus-level gap"
            )
            rejected += 1
            if rejected <= 5:
                log_lines.append(f"cleanup auto_rejected_single_paper_limitation_gap id={gap.get('id', 'NA')}")
    if rejected:
        log_lines.append(f"cleanup auto_rejected_single_paper_limitation_total={rejected}")
    return rejected


def _validate_gap_record_ids(gap: dict, registry_ids: set, log_lines: list):
    for field in ["papers_claiming_gap", "papers_potentially_closing_gap", "identified_in", "closing_paper"]:
        if field not in gap:
            continue
        value = gap.get(field)
        if isinstance(value, list):
            fixed = []
            for pid in value:
                if pid in registry_ids or pid == "UNRESOLVED":
                    fixed.append(pid)
                else:
                    fixed.append("UNRESOLVED")
                    log_lines.append(f"warning unresolved_paper_id field={field} id={pid} gap={gap.get('id', 'NA')}")
            gap[field] = fixed
        elif isinstance(value, str):
            if value not in registry_ids and value != "UNRESOLVED":
                gap[field] = "UNRESOLVED"
                log_lines.append(f"warning unresolved_paper_id field={field} id={value} gap={gap.get('id', 'NA')}")

    for field in ["paper_a", "paper_b", "sole_paper"]:
        if field not in gap:
            continue
        val = gap.get(field)
        if isinstance(val, str) and val and val not in registry_ids and val != "UNRESOLVED":
            gap[field] = "UNRESOLVED"
            log_lines.append(f"warning unresolved_paper_id field={field} id={val} gap={gap.get('id', 'NA')}")


def _append_gap_candidate(gaps: list, candidate: dict, conflicts: list, registry_ids: set, log_lines: list) -> bool:
    out_of_scope, reason = _is_out_of_scope_gap(candidate.get("gap_statement", ""), candidate.get("closure_criteria", ""))
    if out_of_scope:
        log_lines.append(f"{candidate.get('source_paper', 'NA')}: out_of_scope {reason}")
        return False

    ctype = candidate.get("gap_type", "candidate")
    candidate_tier = _normalise_text(candidate.get("tier", "")) or _infer_gap_tier(candidate)

    sem_match = None
    if ctype in {"structural_absence", "underrepresentation"}:
        for g in gaps:
            if (
                g.get("gap_type") == ctype
                and g.get("modality") == candidate.get("modality")
                and g.get("task_type") == candidate.get("task_type")
            ):
                sem_match = g
                break
    elif ctype == "combinatorial":
        ca = candidate.get("paper_a", "")
        cb = candidate.get("paper_b", "")
        pair = frozenset([ca, cb])
        for g in gaps:
            gp = frozenset([g.get("paper_a", ""), g.get("paper_b", "")])
            if g.get("gap_type") == "combinatorial" and gp == pair:
                sem_match = g
                break
    else:
        # Limit semantic merges to the same tier to avoid mixing curated and autogenerated gaps.
        tier_scoped = [g for g in gaps if (_normalise_text(g.get("tier", "")) or _infer_gap_tier(g)) == candidate_tier]
        sem_match = _find_semantic_gap_match(tier_scoped, candidate.get("gap_statement", ""))

    if sem_match is not None:
        key = _get_gap_claiming_key(sem_match)
        sem_match.setdefault(key, [])
        src = candidate.get("source_paper")
        if src and src != "UNRESOLVED" and src not in sem_match[key]:
            sem_match[key].append(src)
        log_lines.append(f"{candidate.get('source_paper', 'NA')}: gap_dedup merged_into={sem_match.get('id', 'NA')}")
        return True  # ✅ Don't create duplicate

    pa = candidate.get("paper_a")
    pb = candidate.get("paper_b")
    if pa and pb and _is_conflict_pair(pa, pb, conflicts):
        log_lines.append(f"{candidate.get('source_paper', 'NA')}: conflict_not_gap pair={pa}/{pb}")
        return False

    record = {
        "id": _next_gap_candidate_id(gaps),
        "gap_statement": candidate.get("gap_statement", "")[:400],
        "papers_claiming_gap": [candidate.get("source_paper", "UNRESOLVED")],
        "papers_potentially_closing_gap": [],
        "current_status": "candidate",
        "evidence_for_open_status": candidate.get("evidence_for_open_status", "Candidate detected by Agent 4"),
        "closure_criteria": candidate.get("closure_criteria", "Human review required"),
        "gap_type": candidate.get("gap_type", "candidate"),
        "tier": candidate_tier,
    }

    for extra_field in ["paper_a", "paper_b", "proposed_combination", "modality", "task_type", "sole_paper"]:
        if candidate.get(extra_field):
            record[extra_field] = candidate[extra_field]

    _validate_gap_record_ids(record, registry_ids, log_lines)
    gaps.append(record)
    return True


def _run_combinatorial_gap_detection(new_paper: dict, all_rows: list, gaps: list, conflicts: list, registry_ids: set, log_lines: list) -> int:
    added = 0
    pid = new_paper.get("Paper_ID", "")
    new_mod = new_paper.get("Modality", "")
    new_method = new_paper.get("Method_Architecture", "")
    new_task = new_paper.get("Task_Type", "")

    if not pid or not new_task:
        return 0

    max_combinatorial_gaps_per_paper = 3

    for other in all_rows:
        if added >= max_combinatorial_gaps_per_paper:
            break

        opid = other.get("Paper_ID", "")
        if not opid or opid == pid:
            continue
        if other.get("Task_Type", "") != new_task:
            continue

        other_mod = other.get("Modality", "")
        nodes_new = _get_related_l2_nodes(new_mod)
        nodes_other = _get_related_l2_nodes(other_mod)

        # Only fire combinatorial gaps for genuinely different L2 modality families.
        if not nodes_new or not nodes_other:
            continue
        if not nodes_new.isdisjoint(nodes_other):
            continue

        combo_exists = _combination_exists(all_rows, new_method, other_mod, new_task) or _combination_exists(
            all_rows, other.get("Method_Architecture", ""), new_mod, new_task
        )
        if combo_exists:
            continue

        paper_a_method = _safe_words(new_method or "unspecified method", 18) or "unspecified method"
        paper_b_modality = _safe_words(other_mod or "unspecified modality", 12) or "unspecified modality"
        statement = (
            f"No published study combines {paper_a_method} ({new_mod}) "
            f"with {paper_b_modality} ({other_mod}) for {new_task} prediction"
        )
        candidate = {
            "source_paper": pid,
            "gap_type": "combinatorial",
            "tier": "combinatorial",
            "gap_statement": statement,
            "evidence_for_open_status": "No registry paper combines method-modality pair for related modalities under same task",
            "closure_criteria": "A study implementing this missing method-modality combination with reproducible validation",
            "paper_a": pid,
            "paper_b": opid,
            "proposed_combination": f"Combine {paper_a_method} ({new_mod}) with {paper_b_modality} ({other_mod})",
        }
        if _append_gap_candidate(gaps, candidate, conflicts, registry_ids, log_lines):
            added += 1

    return added


def _run_synthetic_gap_coverage(all_rows: list, gaps: list, conflicts: list, registry_ids: set, log_lines: list) -> int:
    counts = {(l2, t): 0 for l2 in L2_MODALITY_NODES for t in ALL_TASK_TYPES}
    sole = {}

    for row in all_rows:
        task = (row.get("Task_Type", "") or "").strip()
        if task not in ALL_TASK_TYPES:
            continue
        nodes = _get_related_l2_nodes(row.get("Modality", ""))
        if not nodes:
            nodes = {"ClinicalEHR"}
        for node in nodes:
            key = (node, task)
            counts[key] = counts.get(key, 0) + 1
            sole[key] = row.get("Paper_ID", "UNRESOLVED")

    added = 0
    for node in L2_MODALITY_NODES:
        for task in ALL_TASK_TYPES:
            c = counts.get((node, task), 0)
            if c > 1:
                continue
            if c == 0:
                candidate = {
                    "source_paper": "UNRESOLVED",
                    "gap_type": "structural_absence",
                    "tier": "structural",
                    "gap_statement": f"No papers found for modality-task combination: {node} × {task}",
                    "evidence_for_open_status": "Taxonomy coverage sweep found zero papers",
                    "closure_criteria": "At least one validated paper covering this modality-task pair",
                    "modality": node,
                    "task_type": task,
                }
            else:
                candidate = {
                    "source_paper": sole.get((node, task), "UNRESOLVED"),
                    "gap_type": "underrepresentation",
                    "tier": "structural",
                    "gap_statement": f"Underrepresented modality-task combination: {node} × {task} has only one paper",
                    "evidence_for_open_status": "Taxonomy coverage sweep found a single-paper cell",
                    "closure_criteria": "At least two independent papers covering this modality-task pair",
                    "modality": node,
                    "task_type": task,
                    "sole_paper": sole.get((node, task), "UNRESOLVED"),
                }

            if _append_gap_candidate(gaps, candidate, conflicts, registry_ids, log_lines):
                added += 1
    return added

def _validation_comparable(v1: str, v2: str) -> bool:
    """Within two tier levels = comparable."""
    tier_map = {"V1": 1, "V2": 2, "V3": 3, "V4": 4}
    t1 = tier_map.get(v1, 2)
    t2 = tier_map.get(v2, 2)
    return abs(t1 - t2) <= 2

def _dataset_family(dataset: str) -> str:
    ds = dataset.upper()
    if "ADNI" in ds: return "ADNI"
    if "OASIS" in ds: return "OASIS"
    if "NACC" in ds: return "NACC"
    if "CHARLS" in ds or "CLHLS" in ds: return "population-survey"
    # For single-centre papers, include a coarse country tag so that
    # Chinese hospital cohorts don't get paired with Greek clinics.
    # FIX (Issue 5): "Greek" was the exact example already named in this
    # comment and in the issue report, but GREECE/GREEK were never actually
    # present in the list below -- a Greek single-centre paper fell through
    # to the generic "single-centre" bucket and could still be paired against
    # any other uncategorised-country paper. Adjective forms (e.g. "ITALIAN",
    # "SPANISH") are also added: a noun form is not a substring of its
    # adjective form (e.g. "ITALY" is not contained in "ITALIAN" -- they
    # diverge after the shared "ITAL" prefix), so a paper describing its
    # cohort as "Italian" rather than "Italy" was previously missed for every
    # country in this list except China, which already had both forms. This
    # expansion closes the named gap (Greece) and the same systematic gap for
    # every other entry; it is not verified against a fresh scan of the live
    # registry's Dataset column, so if other single-centre countries appear
    # in the corpus and are still missing here, they need the same
    # two-line treatment once identified against the actual data.
    for country_tag in ["CHINA", "CHINESE", "JAPAN", "JAPANESE", "KOREA", "KOREAN",
                        "IRAN", "IRANIAN", "TURKEY", "TURKISH", "ITALY", "ITALIAN",
                        "SPAIN", "SPANISH", "GERMAN", "UK ", "INDIA", "INDIAN",
                        "RUSSIA", "RUSSIAN", "MOSCOW", "BRAZIL", "BRAZILIAN",
                        "AUSTRALIA", "AUSTRALIAN", "GREECE", "GREEK"]:
        if country_tag in ds:
            return f"single-centre-{country_tag.strip().lower()}"
    return "single-centre"


def _infer_gap_tier(gap: dict) -> str:
    """Classify gap record into curated/derived/combinatorial/structural tiers."""
    tier = _normalise_text(gap.get("tier", ""))
    if tier in {"curated", "derived", "combinatorial", "structural"}:
        return tier

    gap_type = _normalise_text(gap.get("gap_type", ""))
    gid = _normalise_text(gap.get("id", ""))
    statement = _normalise_text(gap.get("gap_statement", ""))

    if gap_type in {"combinatorial"}:
        return "combinatorial"
    if gap_type in {"structural_absence", "underrepresentation"}:
        return "structural"
    if gap_type == "candidate" or statement.startswith("limitation-derived gap") or gid.startswith("g-cand"):
        return "derived"
    return "curated"

# Metrics on a 0-1 discrimination scale. The validation-tier gap thresholds in
# _check_conflicts (0.03-0.15) are calibrated for these and are meaningless on any
# other scale, so only these types are eligible for conflict generation.
PROPORTION_METRICS = {"AUC", "ACC", "F1", "BACC", "MCC", "KAPPA", "C-INDEX"}

# Effect sizes are recognised so they are never silently rescaled or compared
# against a proportion -- "RR 2.31" used to parse as 0.023 and "SMD 1.52" typed as
# ACC (via its "95%CI" suffix) with value 0.0152.
EFFECT_SIZE_METRICS = {"HR", "OR", "RR", "SMD", "COHEN_D", "BETA", "R2"}

# Phrases that MENTION a metric only to say it is absent. 72 of 644 registry rows
# carry one, and a bare substring test typed every one of them as that metric.
_NEGATED_METRIC_RE = re.compile(
    r"\b(?:no|without|not|lack(?:ing)?\s+of|absent|non)\b[^.;]{0,30}?"
    r"\b(?:AUROC|AUC|ACCURACY|ACC|F1)\b",
    re.I,
)

# Sentinels a Best_Metric field opens with when the paper computes no
# classification metric at all. Roughly two thirds of the clinical corpus is in
# this position (qualitative studies, RCTs, meta-analyses, association
# analyses), and their fields go on to NAME the metrics they do not report
# ("no accuracy, AUC, F1, sensitivity or specificity"). Parsing must stop
# before it reaches that list.
_NO_METRIC_PREFIX_RE = re.compile(
    r"^\s*(?:not\s+reported|not\s+applicable|none|nothing|no\s+(?:classification|"
    r"discrimination|predictive|classifier))\b",
    re.I,
)

# Ordered most-specific-first. Each pattern must capture a number ADJACENT to the
# metric token: requiring the number is what stops the bare English word "or"
# matching odds-ratio, and "three-level"/"threshold" matching hazard-ratio.
#
# The (?<![A-Za-z]) guard before each capture stops a digit that belongs to a
# DIFFERENT metric's name being read as this metric's value. Without it the AUC
# pattern matched the literal text "AUC, F1" and captured the 1 of "F1" as an
# AUC of 1.0 - a perfect-classifier claim manufactured out of a sentence saying
# the paper reports no AUC at all. That fed _is_suspect_accuracy (> 0.97) and
# put 15 papers, all of them qualitative studies, trials, reviews and
# association analyses reporting no classification metric whatsoever, on the
# inflated_performance_flags list in conflicts.json.
_METRIC_PATTERNS = [
    ("C-INDEX", r"\bC[-\s]?INDEX\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("AUC",     r"\b(?:AUROC|AUC)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("BACC",    r"\b(?:BACC|BALANCED\s+ACCURACY)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("F1",      r"\bF1(?:[-\s]?SCORE)?\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("MCC",     r"\bMCC\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("KAPPA",   r"\b(?:KAPPA|COHEN'?S\s+KAPPA)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("SMD",     r"\b(?:SMD|STD\.?\s*MEAN\s+DIFF\w*)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("COHEN_D", r"\b(?:COHEN'?S\s*D|HEDGES'?\s*G)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("HR",      r"\b(?:HR|HAZARD\s+RATIO)\b[^0-9\-]{0,10}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("OR",      r"\b(?:OR|ODDS\s+RATIO)\b[^0-9\-]{0,10}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("RR",      r"\b(?:RR|RISK\s+RATIO|RELATIVE\s+RISK)\b[^0-9\-]{0,10}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("R2",      r"\b(?:ADJUSTED\s+)?R\^?2\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    ("BETA",    r"\b(?:BETA|β)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
    # ACC is guarded against a preceding letter or hyphen so anatomical
    # abbreviations such as "LC-ACC" (locus coeruleus to anterior cingulate
    # cortex) are not read as an accuracy.
    ("ACC",     r"(?<![A-Za-z-])(?:ACC|ACCURACY)\b[^0-9\-]{0,15}(?<![A-Za-z0-9.])(-?\d*\.?\d+)"),
]


def _metric_parse(metric: str) -> tuple:
    """Return (type, value) for a Best_Metric string, or ("UNKNOWN", -1.0).

    Type and value are resolved TOGETHER from the same match, so the number
    returned always belongs to the metric that was identified. The previous
    implementation picked type by substring and value by "first number anywhere",
    which disagreed with each other on most non-accuracy rows.
    """
    if _NO_METRIC_PREFIX_RE.match(metric or ""):
        return "UNKNOWN", -1.0
    text = _NEGATED_METRIC_RE.sub(" ", metric or "")
    for mtype, pattern in _METRIC_PATTERNS:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        try:
            val = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if mtype in PROPORTION_METRICS:
            if val > 1.5:            # percent -> decimal, proportions only
                val = val / 100.0
            if not 0.0 <= val <= 1.0:
                continue             # not a usable proportion; keep looking
        return mtype, val
    return "UNKNOWN", -1.0


def _metric_value(metric: str) -> float:
    """Numeric value of a Best_Metric string, or -1.0 when unparseable."""
    return _metric_parse(metric)[1]


def _metric_type(metric: str) -> str:
    """Coarse metric type label for comparability checks."""
    return _metric_parse(metric)[0]


def _modality_family(modality: str, method_arch: str = "") -> str:
    """Map raw modality/method text to a broad comparison family for conflict gating."""
    text = _normalise_text(f"{modality} {method_arch}")

    if any(k in text for k in ["eeg", "meg", "erp", "electrophysiolog", "brainwave", "spectral"]):
        return "eeg_meg"

    if any(k in text for k in ["fmri", "rs-fmri", "resting-state", "functional connectivity", "vmhc", "bold"]):
        return "functional_mri"

    if any(k in text for k in ["structural mri", "smri", "t1", "freesurfer", "vbm", "sbm", "cortical", "hippocamp"]):
        return "structural_mri"

    if any(k in text for k in ["speech", "language", "transcript", "nlp", "linguistic", "voice", "audio"]):
        return "speech_language"

    if any(k in text for k in ["handwriting", "digital", "video", "eye-tracking", "mobile", "wearable", "driving", "behaviour", "behavior", "fnirs", "vr"]):
        return "digital_behaviour"

    if any(k in text for k in ["survey", "questionnaire", "ehr", "demographic", "clinical", "neuropsych", "mmse", "cdr", "charls", "clhls", "informant", "scc"]):
        return "clinical_survey"

    nodes = sorted(_get_related_l2_nodes(modality))
    if nodes:
        return nodes[0]
    return text


# Word-boundary patterns, not substrings: these run against free text where "pet"
# hides inside "competence" and "t1" inside arbitrary identifiers.
_MODALITY_SUBTYPE_BUCKETS = [
    ("structural_mri",  r"structural[_\s-]?mri|smri|\bt1\b|\bvbm\b|\bsbm\b|cortical[_\s-]?thick"
                        r"|hippocamp|gr[ae]y[_\s-]?matter|morphometr|subfield|atrophy"),
    ("diffusion",       r"\bdti\b|\bdki\b|diffusion|fractional[_\s-]?anisotrop|\btract"
                        r"|free[_\s-]?water|\bnoddi\b"),
    ("functional_mri",  r"\bfmri\b|functional[_\s-]?mri|\bbold\b|\balff\b|\breho\b"
                        # 2026-09-04b: bare 'connectivit' here bucketed pure-EEG-connectivity
                        # tokens (EEG_connectivity, EEG_graph_connectivity) as 'multimodal'
                        # -- a false pass against genuine MRI+PET compounds through a gate
                        # meant to BLOCK that pair. fMRI_connectivity still matches via
                        # \bfmri\b, so the term is simply removed.
                        r"|resting[_\s-]?state|\bdmn\b"),
    ("eeg_meg",         r"\beeg\b|\bmeg\b|\berp\b|qeeg|spectral|\bern\b|event[_\s-]?related"
                        r"|electrophysiolog|coherence"),
    ("pet",             r"\bpet\b|\bsuvr\b|amyloid|\bfdg\b|\bpib\b|\btau\b"),
    ("fnirs",           r"\bfnirs\b|\bnirs\b"),
    # 2026-09-04: 'transcript' matched inside 'transcriptomics' (gene-expression
    # studies bucketed as speech_language — a false pass against NLP-transcript
    # studies through a gate meant to BLOCK that pair). Word-boundary guard added.
    ("speech_language", r"speech|acoustic|\bvoice\b|linguistic|language|\btranscripts?\b"),
    ("neuropsych",      r"neuropsych|cognitive[_\s-]?test|\bmmse\b|\bmoca\b|scores?[_\s-]?only"
                        r"|\badas\b"),
    # 2026-09-04c: 'transcriptomic' (word-root) added to fluid -- all 10 registry
    # transcriptomics rows are blood-derived gene expression (verified against the
    # Modality column), so a gene-expression study must gate-compare WITH plasma/
    # serum assays, not sit gate-dead while NLP_text_transcripts matches via
    # \btranscripts?\b. 'transcriptomic' cannot match 'transcripts' (no boundary
    # clash), so the speech branch is unaffected.
    ("fluid",           r"\bcsf\b|plasma|serum|\bblood\b|proteom|metabolom|\bmirna\b"
                        r"|transcriptomic"),
    ("digital",         r"\bgait\b|eye[_\s-]?track|wearable|sensor|handwriting|driving"
                        r"|actigraph|\bvr\b|keystroke"),
    ("genetic",         r"\bapoe\b|genetic|\bsnp\b|polygenic|genom"),
]


def _modality_subtype_bucket(subtype: str) -> str:
    """Map the near-free-text modality_subtype to a coarse comparison bucket.

    Returns "" when the value is missing or unrecognised, which the caller reads as
    "do not gate on this field". The raw column carries 280 distinct values across
    644 registry rows, 240 of them appearing exactly once, so two populated papers
    share a literal value only 4.4% of the time -- exact equality suppressed every
    candidate conflict in the corpus (PAPERINGO-odg).
    """
    text = _normalise_text(subtype or "")
    if not text or text in {"not reported", "not_reported", "nr", "unknown", "none"}:
        return ""
    # Underscore is a \w character, so \b never fires inside "EEG_ERP". Values in this
    # column are overwhelmingly underscore-joined, so flatten separators before matching.
    text = re.sub(r"[_\-]+", " ", text)
    # 2026-09-04b: compound-first guard. Tokens that self-declare as compound
    # (multimodal_MRI, multimodal_MRI_CSF, trimodal_MRI_PET_CSF) must never resolve
    # to a single inner family (or to ""): multimodal_MRI_CSF otherwise buckets as
    # 'fluid' and would pass the gate against a pure-CSF study. A false pass is
    # worse than a suppressed pair (same principle as the transcriptomics fix).
    if re.search(r"\b(?:multimodal|trimodal)\b", text):
        return "multimodal"
    hits = [name for name, pattern in _MODALITY_SUBTYPE_BUCKETS
            if re.search(pattern, text, re.I)]
    if not hits:
        return ""
    return "multimodal" if len(hits) > 1 else hits[0]


def _is_suspect_accuracy(paper: dict) -> bool:
    """Flag potentially inflated performance records for separate tracking.

    Restricted to proportion metrics: ">0.97" only means "implausibly high" on a
    0-1 discrimination scale. An HR of 3.79 or an OR of 4.94 is an ordinary effect
    size, and once the parser stopped mis-scaling those to 0.0379 they would
    otherwise have started tripping this flag.
    """
    mtype, value = _metric_parse(paper.get("Best_Metric", ""))
    return mtype in PROPORTION_METRICS and value > 0.97


def _collect_suspect_accuracy_ids(rows: list) -> list:
    flagged = []
    for row in rows:
        if _is_suspect_accuracy(row):
            pid = row.get("Paper_ID", "")
            if pid:
                flagged.append(pid)
    return sorted(set(flagged))


# ── Complementary-candidate quality detectors ──────────────────────────────────
# These FLAG; they never reject. G4 is a human gate, and the three pre-human
# auto-reject rules proposed after the 2026-07-31 G4 review were measured against
# that batch: they removed 87/87 records, including all four the reviewer had
# judged salvageable and both hand-written good candidates. A "<20 words" floor
# matches everything because the generator's own cap is 14 words, and \d{1,2}
# cannot tell a citation from a result ("AUC 0.87", "n=42", "95% CI"). So each
# detector stamps a reason code and leaves status alone.

_DOC_STRUCTURE_RE = re.compile(
    r"^\s*(?:results?|resulting in|introduction|methods?|discussion|conclusions?"
    r"|abstract|contents lists)\b", re.I)

# A run of >=3 bare 1-2 digit integers is a citation list -- UNLESS a metric token
# is present, in which case the digits are data ("sensitivity 100 yr1 94 67 NPV").
# That guard is what separates this from the rejected \d{1,2} rule.
_INT_RUN_RE = re.compile(r"\b\d{1,2}\b(?:\s+\b\d{1,2}\b){2,}")
_METRIC_TOKEN_RE = re.compile(
    r"AUC|SMD|\bCI\b|\bOR\b|\bHR\b|accuracy|sensitivity|specificity|NPV|PPV"
    r"|prevalence|p\s*[<=]|n\s*=|%|studies|participants|subjects|patients|RCTs"
    r"|yr\d|Hz|fold", re.I)

# 25 chars, not 20: reproducibility, neurotransmitter, thalamocortical and
# neurodegeneration are legitimate domain words and false-positived at 20.
_LOST_SPACING_RE = re.compile(r"\S{25,}|\w+- \w+|classi er")

_GAP_MARKER_RE = re.compile(
    r"whereas|however|neither|in contrast|remains unknown|untested|no study has"
    r"|complement", re.I)

# Function words a sentence cannot end on. Ending here means a hard word-cap cut
# the field mid-phrase, as distinct from an author simply omitting a full stop.
_DANGLING_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "for", "with", "to",
    "by", "at", "from", "as", "that", "which", "while", "than", "is", "are", "was",
    "were", "has", "have", "had", "been", "also", "both", "between", "during",
    "after", "before", "into", "over", "under", "more", "less", "when", "where",
    "these", "this", "their", "its", "such", "including", "versus", "vs",
}


def _flag_candidate_quality(rec: dict) -> list:
    """Machine-readable quality reason codes for a complementary candidate.

    ADVISORY ONLY. Callers must not reject on these -- they route a record to the
    human G4 gate with its defects named, which is what CLAUDE.md's "never skip
    gates" requires.
    """
    flags = []
    insight = str(rec.get("combined_insight", "") or "")
    implication = str(rec.get("clinical_implication", "") or "")
    findings = [str(rec.get("paper_a_finding", "") or ""),
                str(rec.get("paper_b_finding", "") or "")]

    # Copy-paste containment: a synthesis restates a finding, it never embeds it
    # byte-for-byte. Truncation-immune, unlike matching on template tail text.
    head = findings[0][:60].strip()
    if insight.startswith("P-") and head and head in insight:
        flags.append("TEMPLATE_INSIGHT")

    # Anchored on the stem, never the tail: the tail is exactly what the field cap
    # removes, which is why tail-matching missed 29-44 records of the G4 batch.
    if implication.lstrip().startswith("Evaluate fused "):
        flags.append("TEMPLATE_IMPLICATION")

    # Repair-flag only. A missing full stop alone is NOT evidence -- 96% of the
    # curated complementary corpus omits one. Require a dangling function word,
    # which is what a hard word-cap actually leaves behind ("...leaves", "...and").
    for text in [insight, implication] + findings:
        stripped = text.rstrip()
        if not stripped or stripped[-1:] in ".!?":
            continue
        last = re.sub(r"[^A-Za-z-]", "", stripped.split()[-1]).lower()
        if last.endswith("-") or last in _DANGLING_WORDS:
            flags.append("TRUNCATED_FIELD")
            break

    # Failed PDF extraction, which originates upstream in summaries.md rather than
    # here -- flagging it downstream is a symptom report, not a fix.
    for text in findings:
        if not text:
            continue
        if (_DOC_STRUCTURE_RE.search(text)
                or (_INT_RUN_RE.search(text) and not _METRIC_TOKEN_RE.search(text))
                or _LOST_SPACING_RE.search(text)):
            flags.append("RAW_EXTRACT_SOURCE")
            break

    # Positive gate: requiring a good property degrades to "send to a human",
    # whereas enumerating bad ones degrades to "delete".
    if not _GAP_MARKER_RE.search(f"{insight} {implication}"):
        flags.append("NO_GAP_ARTICULATION")

    # A "complementary finding" that never mentions paper_b asserts a relationship
    # while describing one paper (12 such records in the 2026-07-31 batch).
    paper_b = str(rec.get("paper_b", "") or "")
    if paper_b and f"{paper_b} finds" not in insight:
        flags.append("PAPER_B_ABSENT")

    return sorted(set(flags))


def _backfill_legacy_conflict_modality_family(conflicts: list, by_id: dict, log_lines: list) -> int:
    """Backfill modality-family fields for legacy conflicts C-01..C-12."""
    updated = 0
    for conflict in conflicts:
        cid = str(conflict.get("id", "")).strip().upper()
        match = re.match(r"C-(\d+)$", cid)
        if not match:
            continue
        idx = int(match.group(1))
        if idx < 1 or idx > 12:
            continue

        paper_a = by_id.get(conflict.get("paper_a", ""), {})
        paper_b = by_id.get(conflict.get("paper_b", ""), {})
        if not paper_a or not paper_b:
            continue

        fam_a = _modality_family(paper_a.get("Modality", ""), paper_a.get("Method_Architecture", ""))
        fam_b = _modality_family(paper_b.get("Modality", ""), paper_b.get("Method_Architecture", ""))
        inferred_family = fam_a if fam_a == fam_b else f"{fam_a}|{fam_b}"

        current_family = _normalise_text(conflict.get("modality_family", ""))
        if current_family in {"", "unknown", "not_reported", "not reported"}:
            conflict["modality_family"] = inferred_family
            updated += 1

        if not conflict.get("modality_family_equal"):
            conflict["modality_family_equal"] = "Yes" if fam_a == fam_b else "No"

    if updated:
        log_lines.append(f"cleanup legacy_conflict_modality_backfill updated={updated}")
    return updated

def _check_conflicts(new_papers: list, all_papers: list, existing_conflicts: list) -> list:
    # Load learned patches from self-improvement cycle
    PATCHES_PATH = CONFIG / "algorithm_patches.json"
    _learned_patches = []
    if PATCHES_PATH.exists():
        try:
            _learned_patches = json.loads(PATCHES_PATH.read_text()).get("patches", [])
        except Exception:
            pass

    new_conflicts = []
    suspect_seen = set()
    existing_pairs = {
        frozenset([c["paper_a"], c["paper_b"]]) for c in existing_conflicts
    }

    for new in new_papers:
        for existing in all_papers:
            if new["Paper_ID"] == existing["Paper_ID"]:
                continue
            pair = frozenset([new["Paper_ID"], existing["Paper_ID"]])
            if pair in existing_pairs:
                continue
            # At the start of the inner loop, after pair dedup:
            nr_values = {"NR", "nr", "not reported", "not_reported", "unknown", ""}
            # Skip pairs where either paper is flagged a duplicate of another. agent3
            # writes "not reported" as the sparsity default (agent3_extract.py:748) and
            # documents that "agent4 tolerates a missing duplicate_of"; a bare truthiness
            # test here broke that contract, reading the default as a duplicate flag and
            # excluding 97.6% of candidate pairs before any task/dataset/metric check.
            if (new.get("duplicate_of") or "").strip() not in nr_values:
                continue
            if (existing.get("duplicate_of") or "").strip() not in nr_values:
                continue
            if not new.get("Task_Type") or new.get("Task_Type") in nr_values:
                continue
            if not existing.get("Task_Type") or existing.get("Task_Type") in nr_values:
                continue
            if not new.get("Dataset") or new.get("Dataset") in nr_values:
                continue
            if not existing.get("Dataset") or existing.get("Dataset") in nr_values:
                continue
            if not new.get("Modality") or new.get("Modality") in nr_values:
                continue
            if not existing.get("Modality") or existing.get("Modality") in nr_values:
                continue
            if not new.get("Best_Metric") or new.get("Best_Metric") in nr_values:
                continue
            if not existing.get("Best_Metric") or existing.get("Best_Metric") in nr_values:
                continue

            # Condition 1: task equivalence
            if new.get("Task_Type") != existing.get("Task_Type"):
                continue

            # Condition 1b: the two records must be independent evidence. A paper
            # and the letter commenting on it, two analyses of one trial, or two
            # editions of one study cannot conflict with each other, and papers
            # flagged as non-evidence (commentaries, abstract slices) never pair.
            if _ni_excluded_as_evidence(new.get("Paper_ID", "")) or _ni_excluded_as_evidence(existing.get("Paper_ID", "")):
                continue
            if _ni_blocks_pair(new.get("Paper_ID", ""), existing.get("Paper_ID", "")):
                continue

            # Condition 2: dataset family equivalence
            fam_new = _dataset_family(new.get("Dataset", ""))
            fam_old = _dataset_family(existing.get("Dataset", ""))
            if fam_new != fam_old:
                continue
            
            # Condition 3: validation comparability
            if not _validation_comparable(new.get("Validation_Type", "V2"), existing.get("Validation_Type", "V2")):
                continue

            # Condition 4: broad modality family equivalence (avoid cross-instrument pseudo-conflicts)
            mod_new = _modality_family(new.get("Modality", ""), new.get("Method_Architecture", ""))
            mod_old = _modality_family(existing.get("Modality", ""), existing.get("Method_Architecture", ""))
            if mod_new != mod_old:
                continue

            # Gate: study design compatibility
            design_new = new.get("study_design_type", "not reported")
            design_old = existing.get("study_design_type", "not reported")
            if design_new not in nr_values and design_old not in nr_values:
                longitudinal = {"longitudinal_multipoint", "longitudinal_2point", "future_value_forecasting", "survival_analysis"}
                if (design_new in longitudinal) != (design_old in longitudinal):
                    continue  # DESIGN_MISMATCH
            # Gate: task substage
            sub_new = new.get("task_substage", "not reported")
            sub_old = existing.get("task_substage", "not reported")
            if sub_new not in nr_values and sub_old not in nr_values and sub_new != sub_old:
                continue  # TASK_SUBSTAGE_MISMATCH
            # Gate-vacuity guard (2026-09-09, operator G4 verdicts C-243/C-247/C-251):
            # when task_substage is 'not reported' on BOTH sides, the mismatch test
            # above can never fire, so in the T6|review_meta cell any two review
            # cards auto-pass every deterministic gate and are minted as
            # genuine-conflict candidates that a human gate must then untangle.
            # The verdicts require the double-'not reported' skip to become a
            # WARN/block or a mandatory human queue. We BLOCK the pair (skip
            # minting) and log a WARN so the operator can either populate
            # task_substage (intervention family is recoverable from each review's
            # scope) or file a documented-incommensurability record manually.
            if sub_new in nr_values and sub_old in nr_values:
                log.warning(
                    "  task_substage not reported on BOTH sides (%s x %s) - "
                    "pair blocked from conflict generation (gate vacuity); "
                    "populate task_substage or file a documented-incommensurability record",
                    new.get("Paper_ID"), existing.get("Paper_ID")
                )
                continue
            # Gate: population specificity
            # FIX (Issue 1): the previous check tested whether the literal
            # substring "disease_specific" appeared inside pop_new/pop_old.
            # agent3's schema values are bare disease codes ("T2DM",
            # "sarcopenia", "CHF", "PD", "ESRD") that never contain that
            # substring, so ("disease_specific" in pop_new) was always False
            # for both sides, and False != False is always False -- the gate
            # never fired. The corrected version below also adds the second
            # case the issue asked for: two papers that are BOTH
            # disease-specific but for DIFFERENT diseases (e.g. T2DM vs CHF)
            # must not be compared either.
            pop_new = new.get("population_specificity", "general")
            pop_old = existing.get("population_specificity", "general")
            if pop_new not in nr_values and pop_old not in nr_values:
                is_specific_new = pop_new not in GENERAL_POPULATION_VALUES
                is_specific_old = pop_old not in GENERAL_POPULATION_VALUES
                if is_specific_new != is_specific_old:
                    continue  # POPULATION_SPECIFICITY: one general, one disease-specific
                if is_specific_new and is_specific_old and pop_new != pop_old:
                    continue  # POPULATION_SPECIFICITY: different diseases (e.g. T2DM vs CHF)
            # Gate: modality subtype. Compare a normalised BUCKET, not the raw string.
            # The column is near-free-text, so literal equality matched only 4.4% of
            # populated pairs and killed every surviving candidate in the corpus
            # (PAPERINGO-odg). Suppress only when both values resolve to DIFFERENT
            # recognised buckets; unrecognised free text yields "" and falls through
            # to the metric comparability guard rather than silently blocking.
            msub_new = _modality_subtype_bucket(new.get("modality_subtype", ""))
            msub_old = _modality_subtype_bucket(existing.get("modality_subtype", ""))
            if msub_new and msub_old and msub_new != msub_old:
                continue  # MODALITY_SUBTYPE_MISMATCH

            # Performance gap check. Type and value come from ONE parse, so the number
            # always belongs to the metric that was actually identified.
            t_new, v_new = _metric_parse(new.get("Best_Metric", ""))
            t_old, v_old = _metric_parse(existing.get("Best_Metric", ""))
            if v_new < 0 or v_old < 0:
                continue

            # Comparability guard. Both types must be KNOWN, EQUAL, and on the 0-1
            # proportion scale. UNKNOWN was previously treated as compatible with
            # anything, so two unparseable free-text outcome strings (48% of the
            # corpus) were compared numerically. Effect sizes are recognised but
            # excluded here: the tier thresholds below are calibrated for 0-1
            # discrimination metrics and mean nothing on a ratio scale.
            if t_new == "UNKNOWN" or t_old == "UNKNOWN":
                continue
            if t_new != t_old:
                continue
            if t_new not in PROPORTION_METRICS:
                continue
           
            # Additional fix: exclude inflated/suspect accuracy outliers from conflict generation.
            if v_new > 0.97 or v_old > 0.97:
                if v_new > 0.97:
                    suspect_seen.add(new.get("Paper_ID", ""))
                if v_old > 0.97:
                    suspect_seen.add(existing.get("Paper_ID", ""))
                continue
            tier_thresholds = {"V1": 0.15, "V2": 0.08, "V3": 0.05, "V4": 0.03}
            # Use the STRICTER (higher) threshold for the pair to avoid flagging
            # what is likely a validation-tier artifact.
            v_new_tier = new.get("Validation_Type", "V2")
            v_old_tier = existing.get("Validation_Type", "V2")
            threshold = max(
                tier_thresholds.get(v_new_tier, 0.08),
                tier_thresholds.get(v_old_tier, 0.08)
            )
            gap = abs(v_new - v_old)
            if gap < threshold:
                continue

            # Apply learned patch blockers
            patched = False
            for patch in _learned_patches:
                field = patch.get("blocking_field")
                if not field: continue
                val_new = new.get(field, "not reported")
                val_old = existing.get(field, "not reported")
                if val_new not in nr_values and val_old not in nr_values and val_new != val_old:
                    patched = True
                    break
            if patched:
                continue

            conflict = {
                "id": _next_id(existing_conflicts + new_conflicts, "C-"),
                "paper_a": new["Paper_ID"],
                "paper_b": existing["Paper_ID"],
                "claim_a": f"{new['Paper_ID']}: {new.get('Best_Metric', 'NR')} on {new.get('Task_Type')}",
                "claim_b": f"{existing['Paper_ID']}: {existing.get('Best_Metric', 'NR')} on {existing.get('Task_Type')}",
                "task_equal": "Yes",
                "dataset_equal": "Yes",
                "validation_comparable": "Yes",
                "modality_family_equal": "Yes",
                "modality_family": mod_new,
                "genuine": "Yes",
                "resolution": "Pending human review",
                "gap_magnitude": round(gap, 3),
            }
            new_conflicts.append(conflict)
            existing_pairs.add(pair)
            log.info(f"  Conflict flagged: {new['Paper_ID']} vs {existing['Paper_ID']} (gap={gap:.3f})")

    for pid in sorted(p for p in suspect_seen if p):
        log.info(f"  Inflated performance flag: {pid} excluded from conflict generation (metric > 97%)")

    return new_conflicts


# ── Sub-task 4B: Consensus Detection ──────────────────────────────────────────

def _check_consensus(new_paper: dict, all_papers: dict, existing_consensus: list) -> list:
    """Check if new paper confirms any existing consensus claim or creates a new one."""
    updates = []
    # Simple heuristic: papers with same task + architecture family + directional finding
    # Full consensus logic requires LLM; this is the structural check
    for claim in existing_consensus:
        supporting = claim.get("supporting_papers", [])
        if new_paper["Paper_ID"] in supporting:
            continue
        # Check task match
        paper_tasks = [all_papers[pid].get("Task_Type") for pid in supporting if pid in all_papers]
        if new_paper.get("Task_Type") in paper_tasks:
            # Flag for potential addition — human gate G4 will confirm
            updates.append({
                "claim_id": claim["id"],
                "candidate_paper": new_paper["Paper_ID"],
                "reason": f"Same task type {new_paper.get('Task_Type')} and potential confirmation",
            })
    return updates


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "under", "are", "is", "was", "were",
    "will", "across", "among", "between", "within", "against", "using", "use", "used", "study", "paper", "mci",
    "data", "model", "models", "method", "methods", "clinical", "evidence", "analysis", "based", "have", "has",
    "been", "being", "than", "then", "their", "there", "them", "also", "only", "very", "more", "most", "less",
}


@lru_cache(maxsize=200_000)
def _tokenize_cached(text: str) -> frozenset:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (text or "").lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS)


def _tokenize(text: str) -> set:
    """Tokenise to a set of content words.

    Memoised: a corpus-wide run calls this ~860k times over a far smaller number of
    distinct strings, because the same gap statements, findings and limitations are
    re-tokenised once per paper. Profiled at 9.8 s of the 47 s compute budget.

    The cache holds a frozenset and a fresh set is returned each call, so callers keep
    the mutable-set contract they have always had and no caller can corrupt the cache.
    """
    return set(_tokenize_cached(text or ""))


def _safe_words(text: str, max_words: int) -> str:
    words = re.findall(r"[A-Za-z0-9\-]+", text or "")
    return " ".join(words[:max_words])


def _paper_evidence_text(new_paper: dict, summary: str) -> str:
    fields = [
        new_paper.get("Paper_ID", ""),
        new_paper.get("Category", ""),
        new_paper.get("Modality", ""),
        new_paper.get("Method_Architecture", ""),
        new_paper.get("Task_Type", ""),
        new_paper.get("Best_Metric", ""),
        new_paper.get("Validation_Type", ""),
        new_paper.get("Key_Novelty", ""),
        new_paper.get("Primary_Limitation", ""),
        new_paper.get("Architecture_Family", ""),
        new_paper.get("XAI_Method", ""),
        summary,
    ]
    return " ".join(str(x) for x in fields if x)


_SUMMARY_SOURCE_RESOLVED: "tuple[Path, str] | None" = None


def _resolve_summary_source() -> Path | None:
    """Locate summaries.md, resolving the path at most once per process.

    _parse_summary_for_paper calls this on every lookup - about 101,000 times in a
    corpus-wide run - and each call previously cost up to three Path.exists() stats
    plus a Path.resolve(), which on Windows is nt._getfinalpathname. Profiling a
    corpus-wide run measured 202,128 _getfinalpathname calls (7.9 s) and 303,468 stat
    calls (7.1 s), i.e. roughly 15 s spent re-verifying a path that cannot change
    while the process runs.

    A negative result is deliberately NOT cached, so a run started before the file
    exists still picks it up later.
    """
    global _SUMMARY_SOURCE_RESOLVED
    if _SUMMARY_SOURCE_RESOLVED is not None:
        return _SUMMARY_SOURCE_RESOLVED[0]

    source = SUMMARIES if SUMMARIES.exists() else SUMMARIES_FALLBACK
    if not source.exists():
        return None
    _SUMMARY_SOURCE_RESOLVED = (source, str(source.resolve()))
    return source


def _resolved_summary_key() -> str:
    """The cache key for the resolved summaries path, computed with the path itself."""
    return _SUMMARY_SOURCE_RESOLVED[1] if _SUMMARY_SOURCE_RESOLVED else ""


def _build_summary_index(source: Path) -> dict:
    content = source.read_text(encoding="utf-8")
    index = {}
    blocks = re.split(r"(?=^##\s*Paper\s+)", content, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip().startswith("##"):
            continue
        match = re.match(r"##\s*Paper\s+([^:\n]+)\s*:", block.strip(), flags=re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip()
        m = re.match(r"P-(\d+)$", raw, flags=re.IGNORECASE)
        if m:
            pid = f"P-{m.group(1)}"
        else:
            n = re.match(r"(\d+)$", raw)
            pid = f"P-{n.group(1)}" if n else raw
        if pid:
            index[pid] = block.strip()
    return index


def _extract_summary_section(summary_text: str, section_titles: tuple[str, ...]) -> str:
    if not summary_text:
        return ""
    for title in section_titles:
        pattern = rf"###\s*{re.escape(title)}\s*(.+?)(?=\n###\s+|\Z)"
        match = re.search(pattern, summary_text, re.IGNORECASE | re.DOTALL)
        if match:
            text = re.sub(r"\s+", " ", match.group(1)).strip()
            return text
    return ""


def _complementary_evidence_text(paper: dict, summary: str) -> str:
    findings = _extract_summary_section(summary, ("Key findings reported",))
    limits = _extract_summary_section(summary, ("Primary limitation", "Potential gaps or limitations"))
    fields = [
        paper.get("Paper_ID", ""),
        paper.get("Task_Type", ""),
        paper.get("Modality", ""),
        paper.get("Key_Novelty", ""),
        findings,
        limits,
    ]
    return " ".join(str(x) for x in fields if x)


# The marker sets _modality_family tests, named so the consensus matcher can reuse
# them instead of re-deriving a second, silently divergent copy.
_FAMILY_MARKERS = {
    "eeg_meg": {"eeg", "meg", "erp", "electrophysiolog", "brainwave", "spectral"},
    "functional_mri": {"fmri", "rs-fmri", "resting-state", "functional connectivity",
                       "vmhc", "bold"},
    "structural_mri": {"structural mri", "smri", "t1", "freesurfer", "vbm", "sbm",
                       "cortical", "hippocamp"},
    "speech_language": {"speech", "language", "transcript", "nlp", "linguistic",
                        "voice", "audio"},
    "digital_behaviour": {"handwriting", "digital", "video", "eye-tracking", "mobile",
                          "wearable", "driving", "behaviour", "behavior", "fnirs", "vr"},
    "clinical_survey": {"survey", "questionnaire", "ehr", "demographic", "clinical",
                        "neuropsych", "mmse", "cdr", "charls", "clhls", "informant", "scc"},
}

# Exactly the values _modality_family can return from its keyword ladder. A
# required_modality_family outside this set (and outside the explicit branches in
# the matcher) is one the matcher cannot evaluate at all.
_EMITTABLE_FAMILIES = set(_FAMILY_MARKERS)

# Required families the matcher could not judge during a run, surfaced at G4 rather
# than silently swallowed.
_UNJUDGEABLE_FAMILIES: set[str] = set()


def _paper_matches_consensus_modality(paper: dict, required_family: str) -> bool:
    req = _normalise_text(required_family or "any")
    if not req or req == "any":
        return True

    modality = paper.get("Modality", "")
    method = paper.get("Method_Architecture", "")
    text = _normalise_text(f"{modality} {method}")

    if req in {"functional_mri", "fmri", "rs_fmri", "rs-fmri"}:
        markers = {
            "fmri", "rs-fmri", "resting-state", "resting state", "functional mri",
            "functional connectivity", "dmn", "vmhc", "alff", "reho",
        }
        return any(m in text for m in markers)

    if req in {"graph_nn", "graph", "graph_neural", "gnn"}:
        markers = {
            "graph neural", "gnn", "gcn", "gat", "hypergraph", "graph-based", "graph based",
            "graph embedding", "graph attention",
        }
        return any(m in text for m in markers)

    if req in {"retinal", "ocular", "fundus", "oct"}:
        markers = {"retina", "retinal", "fundus", "oct", "ocular", "eye"}
        return any(m in text for m in markers)

    # ── The three families below were UNSATISFIABLE and silently destructive. ──
    # _modality_family can only ever return one of _EMITTABLE_FAMILIES; it has no
    # "multimodal", "clinical_population" or "functional_neuroimaging" branch. The
    # curated consensus records require exactly those three, so `req == family` was
    # False for every paper, every run, and the prune below zeroed VF-02, VF-19,
    # VF-21 and VF-22 on each batch. That is the recurring "curated supporters
    # wiped" defect (PAPERINGO-29c): it was never a data problem and no amount of
    # manual restoration could survive the next run.
    if req == "multimodal":
        # Two or more distinct modality families named together, or an explicit
        # fusion marker. "MRI/PET" and "carotid ultrasound + structural MRI" are
        # both multimodal; a single-modality paper is not.
        fusion = {"multimodal", "multi-modal", "multimodality", "fusion", "combined",
                  "mri/pet", "trimodal", "bimodal"}
        if any(m in text for m in fusion):
            return True
        present = {name for name, markers in _FAMILY_MARKERS.items()
                   if any(m in text for m in markers)}
        return len(present) >= 2

    if req == "clinical_population":
        markers = _FAMILY_MARKERS["clinical_survey"] | {
            "cohort", "population", "epidemiolog", "registry", "community-dwelling",
            "tabular", "risk factor", "case-control",
        }
        return any(m in text for m in markers)

    if req == "functional_neuroimaging":
        markers = _FAMILY_MARKERS["functional_mri"] | {
            "fnirs", "near-infrared", "pet", "fdg", "perfusion", "asl", "spect",
            "metabolic", "activation",
        }
        return any(m in text for m in markers)

    family = _normalise_text(_modality_family(modality, method))
    nodes = {_normalise_text(n) for n in _get_related_l2_nodes(modality)}
    if req == family or req in nodes:
        return True

    # A requirement this matcher cannot express must NOT be read as a mismatch.
    # Returning False here is how an unrecognised family silently deletes curated
    # evidence; the honest answer is "cannot judge", and the safe action for an
    # append-only corpus is to keep the record and let a human decide at G4.
    if req not in _EMITTABLE_FAMILIES:
        _UNJUDGEABLE_FAMILIES.add(req)
        return True
    return False


def _paper_matches_consensus_design(paper: dict, required_design: str, summary_text: str = "") -> bool:
    req = _normalise_text(required_design or "any")
    if not req or req == "any":
        return True

    if req == "longitudinal":
        validation = (paper.get("Validation_Type", "") or "").strip().upper()
        if validation in {"V3", "V4"}:
            return True

        evidence = _normalise_text(_paper_evidence_text(paper, summary_text or ""))
        markers = {
            "longitudinal", "follow-up", "follow up", "trajectory", "timepoint", "time-point",
            "serial", "prospective",
        }
        return any(m in evidence for m in markers)

    return True


def _ensure_consensus_claim_requirements(existing_consensus: list, log_lines: list) -> int:
    changed = 0
    for claim in existing_consensus:
        cid = claim.get("id", "")

        for key, value in _CONSENSUS_DEFAULTS.items():
            current = claim.get(key)
            if current is None or str(current).strip() == "":
                claim[key] = value
                changed += 1

        overrides = _CONSENSUS_REQUIREMENTS_OVERRIDES.get(cid, {})
        for key, value in overrides.items():
            if claim.get(key) != value:
                claim[key] = value
                changed += 1

        try:
            min_hits = int(claim.get("min_task_hits", _CONSENSUS_DEFAULTS["min_task_hits"]))
        except Exception:
            min_hits = _CONSENSUS_DEFAULTS["min_task_hits"]
        if min_hits < 1:
            min_hits = _CONSENSUS_DEFAULTS["min_task_hits"]
        if claim.get("min_task_hits") != min_hits:
            claim["min_task_hits"] = min_hits
            changed += 1

    if changed:
        log_lines.append(f"consensus requirements normalized claims={len(existing_consensus)} field_updates={changed}")
    return changed


def _prune_consensus_support(existing_consensus: list, all_papers: dict, log_lines: list) -> int:
    removed_total = 0
    summary_cache = {}

    for claim in existing_consensus:
        cid = claim.get("id", "")
        required_modality = claim.get("required_modality_family", "any")
        required_design = claim.get("required_design", "any")

        supporting = claim.setdefault("supporting_papers", [])
        kept = []
        removed = 0

        for spid in supporting:
            paper = all_papers.get(spid)
            if not paper:
                removed += 1
                continue

            if not _paper_matches_consensus_modality(paper, required_modality):
                removed += 1
                continue

            summary_text = ""
            if _normalise_text(required_design) == "longitudinal":
                summary_text = summary_cache.get(spid)
                if summary_text is None:
                    summary_text = _parse_summary_for_paper(spid)
                    summary_cache[spid] = summary_text

            if not _paper_matches_consensus_design(paper, required_design, summary_text=summary_text):
                removed += 1
                continue

            kept.append(spid)

        if removed:
            # NON-DESTRUCTIVE. This step used to overwrite supporting_papers with
            # `kept`, deleting human-curated evidence on the strength of a keyword
            # match against the coarse legacy Modality column. Two things made that
            # indefensible: the matcher could not express three of the families the
            # curated records actually require, so those claims were zeroed on EVERY
            # run; and where it can express the family, values like "MRI/PET" still
            # fail a structural_MRI test that the paper plainly satisfies.
            #
            # The re-validation is still worth running - a supporter that no longer
            # matches its claim's stated requirement is a real finding. But it is a
            # finding for a human at G4, not a licence to delete. So the supporters
            # stay, and the questionable ones are queued for review. This is what
            # "append-only, human verification at gates" requires, and it ends the
            # recurring wipe (PAPERINGO-29c) rather than papering over it.
            queued = [p for p in supporting if p not in kept]
            claim["modality_review_queue"] = queued
            claim["evidence_count"] = len(supporting)
            removed_total += removed
            log_lines.append(
                f"consensus_review_queued claim={cid} queued={removed} "
                f"retained={len(supporting)} modality={required_modality} "
                f"design={required_design} papers={','.join(queued)}"
            )
        else:
            claim.pop("modality_review_queue", None)
            claim["evidence_count"] = len(kept)

    return removed_total


# ── Non-independence registry (config/non_independence_registry.json) ────────
# Papers that are NOT independent evidence of one another (same trial, commentary
# on a corpus paper, duplicate edition, overlapping reviews, same research group)
# plus admission flags (conference-abstract slices, commentaries, protocols,
# non-study documents). Consulted at every pair/append decision. Filed under
# PAPERINGO-9u5/and/tz9/t9k/3ik/5ps/84k/trl/z258/d3j/i3h/ha4/9ju/51m/x1k/mb8/1vi6.
NON_INDEPENDENCE = CONFIG / "non_independence_registry.json"
_NI_EXCLUDED_FLAGS = {"commentary_editorial", "conference_abstract", "not_a_study",
                      "trial_protocol_no_results"}
_NI_RANK = {"hard_block": 3, "scope_limited": 2, "single_source": 1, "unconfirmed": 0}


@lru_cache(maxsize=1)
def _load_non_independence() -> dict:
    if not NON_INDEPENDENCE.exists():
        return {"groups": [], "paper_flags": {}}
    try:
        return json.loads(NON_INDEPENDENCE.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - config damage must not kill a run
        log.warning("non_independence_registry.json unreadable (%s); treating as empty", exc)
        return {"groups": [], "paper_flags": {}}


def _ni_flags(pid: str) -> set:
    flags = _load_non_independence().get("paper_flags", {}).get(pid, [])
    return set(flags) if isinstance(flags, list) else set()


def _ni_excluded_as_evidence(pid: str) -> bool:
    """Commentaries, abstract-book slices, protocols and non-study documents never
    count as evidence for a verified finding, a conflict or a complementary pair."""
    return bool(_ni_flags(pid) & _NI_EXCLUDED_FLAGS)


def _ni_relation(pa: str, pb: str, statement: str = ""):
    """Strongest registered non-independence between two papers, or None.
    scope_limited groups escalate to hard_block when the finding statement names
    one of their scope terms."""
    if not pa or not pb or pa == pb:
        return None
    best = None
    stmt = (statement or "").lower()
    for g in _load_non_independence().get("groups", []):
        if g.get("retired"):
            continue
        papers = set(g.get("papers", []))
        if pa in papers and pb in papers:
            enf = g.get("enforcement", "single_source")
            if enf == "scope_limited":
                scopes = [s.lower() for s in g.get("scope", []) if s and s.lower() != "all"]
                if stmt and any(s in stmt for s in scopes):
                    enf = "hard_block"
            if best is None or _NI_RANK.get(enf, 0) > _NI_RANK.get(best[0], 0):
                best = (enf, g)
    return best


def _ni_blocks_pair(pa: str, pb: str, statement: str = "") -> bool:
    rel = _ni_relation(pa, pb, statement)
    return bool(rel) and rel[0] == "hard_block"


# ── Consensus append gate ─────────────────────────────────────────────────────
_REJECT_CUES = re.compile(
    r"\b(?:rejected|reject|removed|pruned|dropped|reversed|struck|excluded|held|not a supporter|"
    r"does not support|counter-evidence|counterevidence)\b", re.I,
)
_PID_RE = re.compile(r"\bP-\d{1,4}\b")
# A1 (classical statistics / SVM / RF / LR) is the validator's DEFAULT family and
# the coding of practically every clinical paper; A8 is the explicit catch-all.
# Neither is evidence that two papers share an approach.
_CATCH_ALL_ARCH = {"a1", "a8", "other", "a8 other", "not reported", ""}

# Words that appear in finding statements as connective or methodological glue.
# They never identify WHAT a finding is about, so they cannot anchor a match.
_ANCHOR_STOP = {
    "authors", "rather", "themselves", "original", "papers", "studies", "reported",
    "within-study", "head-to-head", "corresponding", "comparator", "measure", "measures",
    "matched", "inferred", "static", "separate", "separated", "exists", "stable", "extreme",
    "before", "after", "alongside", "condition", "source", "factor", "factors", "estimate",
    "estimates", "tested", "definition", "substantive", "nuisance", "credible", "trustworthy",
    "implausible", "fragile", "statistically", "biologically", "magnitudes", "scales",
    "classes", "effect", "effects", "outcome", "outcomes", "independent", "carries",
    "assumed", "signal", "status", "scores", "predict", "predicts", "predictive", "strata",
    "stratum", "milder", "increase", "increased", "decrease", "decreased", "preserved",
    "interpreting", "function", "functional", "structural", "cognitive", "cognition",
    "longitudinal", "cross-sectional", "trajectory", "trajectories", "discrimination",
    "deficits", "deficit", "divergence", "corroborated", "subregion", "robust", "signature",
    "recurrent", "modalities", "modality", "parameters", "parameter", "performance", "volume",
    "volumes", "associated", "association", "correlate", "correlates", "correlated",
    "dominates", "moderator", "determinant", "ascertained", "measurement", "measurements",
    "patients", "participants", "subjects", "individuals", "population", "populations",
    "cohort", "cohorts", "clinical", "impairment", "dementia", "alzheimer", "disease",
    "analysis", "analyses", "evidence", "finding", "findings", "results", "result",
    "compared", "comparison", "relative", "versus", "baseline", "follow-up", "higher",
    "lower", "greater", "smaller", "across", "within", "between", "consistent",
    "consistently", "significant", "significantly", "reliable", "reliably", "generalise",
    "generalize", "generalisable", "generalizable", "external", "internal", "validation",
    "validated", "training", "trained", "testing", "tested", "sample", "samples",
    "sensitivity", "specificity", "accuracy", "converters", "conversion", "progression",
    "progressive", "detection", "prediction", "predicting", "classification", "classifier",
    "classifiers", "models", "model", "modelling", "modeling", "methods", "method",
    "approach", "approaches", "features", "feature", "markers", "marker", "biomarkers",
    "biomarker", "outperforms", "outperform", "improves", "improve", "improved",
    "reduction", "reduced", "reduces", "identify", "identified", "identification",
    "quantify", "quantified", "pooled", "meta-analysis", "review", "systematic",
    "intervention", "interventions", "trials", "trial", "randomised", "randomized",
    "controlled", "efficacy", "effectiveness", "heterogeneity", "harmonised", "harmonized",
    "rankings", "unit-free", "cross-intervention", "non-interchangeable",
}
_DF_CACHE: dict = {}


def _corpus_df(all_papers: dict):
    """Document frequency of tokens over the registry's short descriptive fields,
    cached per registry size."""
    key = len(all_papers)
    if key in _DF_CACHE:
        return _DF_CACHE[key]
    df: dict = {}
    for p in all_papers.values():
        toks = _tokenize(" ".join(str(p.get(k, "") or "") for k in
                                  ("Key_Novelty", "Method_Architecture", "Modality", "Best_Metric", "Dataset")))
        for t in toks:
            df[t] = df.get(t, 0) + 1
    _DF_CACHE[key] = (df, max(1, len(all_papers)))
    return _DF_CACHE[key]


def _anchor_terms(claim: dict, all_papers: dict) -> set:
    """The finding statement's DISTINCTIVE content words: rare in the corpus and
    not connective glue. 'depression'/'severity' for VF-27, 'olfactory' for VF-32,
    'compensatory'/'hyperactivation' for VF-21, 'exercise'/'stimulation'/'nutrition'
    for VF-30. A paper must share at least one, or it is not about the finding."""
    df, n = _corpus_df(all_papers)
    limit = max(3, int(0.03 * n))
    return {t for t in _claim_content_terms(claim)
            if t not in _ANCHOR_STOP and df.get(t, 0) <= limit and (len(t) >= 7 or "-" in t)}


def _rejected_supporters(claim: dict) -> set:
    """Every paper a human gate has already rejected, removed or held for this
    finding. Agent 4 used to read only supporting_papers, so P-578 was re-appended
    to VF-02 every corpus-wide run although the record's own note says 'REJECTED
    as VF-02 supporters: P-578' - silently reversing curated gate decisions
    (PAPERINGO-1ei5 / j1uu / c5y). Any sentence in any text field of the record
    that carries a rejection cue contributes the paper ids it names; ids that are
    CURRENT supporters are never treated as rejected."""
    rejected = set(claim.get("rejected_supporters", []) or [])
    supporting = set(claim.get("supporting_papers", []) or [])
    for key, val in claim.items():
        if key in ("supporting_papers", "rejected_supporters", "id", "statement", "finding",
                   "finding_statement") or not val:
            continue
        text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=True, default=str)
        for sent in re.split(r"(?<=[.;\]])\s+|\n", text):
            if _REJECT_CUES.search(sent):
                rejected.update(_PID_RE.findall(sent))
    return rejected - supporting


def _claim_content_terms(claim: dict) -> set:
    text = " ".join(str(claim.get(k, "") or "") for k in ("statement", "finding", "finding_statement", "title"))
    return {t for t in _tokenize(text) if len(t) >= 6}


def _paper_content_terms(paper: dict, summary_text: str) -> set:
    """Terms from the paper's OWN findings, not from the whole 250k-char block
    (which mentions almost every word somewhere)."""
    parts = [
        paper.get("Key_Novelty", ""), paper.get("Method_Architecture", ""),
        paper.get("Modality", ""), paper.get("Best_Metric", ""), paper.get("Dataset", ""),
        _extract_summary_section(summary_text or "", ("Key findings reported",)),
        _extract_summary_section(summary_text or "", ("What did the authors do",)),
    ]
    first_line = (summary_text or "").lstrip().splitlines()[0] if (summary_text or "").strip() else ""
    parts.append(first_line)
    return _tokenize(" ".join(str(p) for p in parts if p))


def _append_consensus_support(new_paper: dict, all_papers: dict, existing_consensus: list,
                              summary_text: str = "", log_lines: list | None = None) -> int:
    """Deterministically append support to existing verified findings.

    Six consecutive batches produced the same failure: 150-600 out-of-batch papers
    appended in bulk to VF-21/25/27/29/30/32, three semantically unrelated findings
    sharing one identical 54-paper block, commentaries counted as intervention
    evidence, and gate-rejected papers re-added. The proximate enablers were
    (a) required_modality_family='any' plus Architecture_Family A8 == A8 - the
    catch-all family matched every clinical paper to every clinical VF - and
    (b) no memory of what a human had rejected. Both are closed here:
      1. flagged non-evidence papers (commentaries, abstract slices, protocols)
         never append;
      2. a paper the record's own notes reject/hold never re-appends;
      3. a paper hard-blocked against an existing supporter by the
         non-independence registry never appends (it is the same evidence);
      4. the paper must share content terms with the finding statement
         (>=2, or >=3 when the claim gates on no modality at all);
      5. A8/Other is not an architecture match.
    """
    added = 0
    pid = new_paper.get("Paper_ID", "")
    if not pid:
        return 0
    if _ni_excluded_as_evidence(pid):
        if log_lines is not None:
            log_lines.append(f"{pid}: not evidence-bearing ({', '.join(sorted(_ni_flags(pid)))}); no consensus appends")
        return 0

    new_task = (new_paper.get("Task_Type", "") or "").strip()
    new_arch = _normalise_text(new_paper.get("Architecture_Family", ""))
    new_metric = _metric_value(new_paper.get("Best_Metric", ""))
    paper_terms = None  # computed lazily

    for claim in existing_consensus:
        supporting = claim.setdefault("supporting_papers", [])
        if pid in supporting:
            continue
        claim_id = claim.get("id", "?")

        if pid in _rejected_supporters(claim):
            continue  # a human already decided this one

        required_modality = claim.get("required_modality_family", "any")
        required_design = claim.get("required_design", "any")
        if not _paper_matches_consensus_modality(new_paper, required_modality):
            continue
        if not _paper_matches_consensus_design(new_paper, required_design, summary_text=summary_text):
            continue

        statement = str(claim.get("statement", "") or claim.get("finding", ""))
        if any(_ni_blocks_pair(pid, spid, statement) for spid in supporting):
            if log_lines is not None:
                log_lines.append(f"{pid}: non-independent of an existing {claim_id} supporter; not appended")
            continue

        claim_terms = _claim_content_terms(claim)
        if claim_terms:
            if paper_terms is None:
                paper_terms = _paper_content_terms(new_paper, summary_text)
            needed = 3 if _normalise_text(required_modality or "any") in ("", "any") else 2
            shared_terms = claim_terms & paper_terms
            if len(shared_terms) < min(needed, len(claim_terms)):
                continue
            anchors = _anchor_terms(claim, all_papers)
            if anchors and not (anchors & paper_terms):
                continue  # shares only glue words with the statement

        try:
            min_task_hits = int(claim.get("min_task_hits", _CONSENSUS_DEFAULTS["min_task_hits"]))
        except Exception:
            min_task_hits = _CONSENSUS_DEFAULTS["min_task_hits"]
        if min_task_hits < 1:
            min_task_hits = _CONSENSUS_DEFAULTS["min_task_hits"]
        # PAPERINGO-wsuf guard: admission is arithmetically impossible when
        # min_task_hits exceeds the current supporter count (a stale threshold
        # outlived its record and permanently closed the finding to growth).
        n_supporters = len(supporting)
        if min_task_hits > max(1, n_supporters):
            min_task_hits = max(1, n_supporters)

        task_hits = 0
        arch_hits = 0
        metric_hits = 0
        directional_hit = 0
        arch_is_specific = bool(new_arch) and new_arch not in _CATCH_ALL_ARCH
        for spid in supporting:
            p = all_papers.get(spid)
            if not p:
                continue
            if new_task and p.get("Task_Type", "") == new_task:
                task_hits += 1
            if arch_is_specific and _normalise_text(p.get("Architecture_Family", "")) == new_arch:
                arch_hits += 1
            pv = _metric_value(p.get("Best_Metric", ""))
            if new_metric >= 0 and pv >= 0 and abs(new_metric - pv) <= 0.08:
                metric_hits += 1
            if not arch_hits and not metric_hits:
                paper_finding = _normalise_text(new_paper.get("Key_Novelty", ""))
                claim_text = _normalise_text(claim.get("statement", ""))
                shared = _tokenize(paper_finding).intersection(_tokenize(claim_text))
                if len(shared) >= 3:
                    directional_hit = 1

        # Require stronger deterministic evidence to avoid count inflation.
        if task_hits >= min_task_hits and (arch_hits >= 1 or metric_hits >= 2 or directional_hit >= 1):
            supporting.append(pid)
            claim["evidence_count"] = len(supporting)
            added += 1

    return added


GAP_RECHECK_TRAIL = LOGS / "gap_recheck_trail.jsonl"
_GAP_TRAIL_CAP = 25
_GAP_DEAD_STATUSES = {"closed", "rejected", "retired", "duplicate", "withdrawn", "superseded"}


def _record_gap_recheck(gap: dict, entry: dict) -> bool:
    """Append one {paper_id, score, terms} re-check to the gap's candidate_relevance
    list (deduplicated, capped at the top _GAP_TRAIL_CAP scores) and to the
    append-only JSONL trail. Returns True when the record changed."""
    trail = gap.get("candidate_relevance")
    if not isinstance(trail, list):
        trail = []
        gap["candidate_relevance"] = trail
    if any(isinstance(e, dict) and e.get("paper_id") == entry.get("paper_id") for e in trail):
        return False
    trail.append(entry)
    if len(trail) > _GAP_TRAIL_CAP:
        trail.sort(key=lambda e: -float(e.get("score", 0) or 0))
        del trail[_GAP_TRAIL_CAP:]
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(GAP_RECHECK_TRAIL, "a", encoding="ascii") as f:
            f.write(json.dumps({"ts": date.today().isoformat(), "gap_id": gap.get("id", ""), **entry},
                               ensure_ascii=True) + "\n")
    except Exception as exc:  # the trail is best-effort
        log.warning("gap recheck trail write failed: %s", exc)
    return True


def _check_gap_closure(new_paper: dict, summary: str, existing_gaps: list) -> list:
    """
    Strict deterministic gap re-check with:
    - High thresholds (overlap >= 5, ratio >= 0.15)
    - Require >= 1 strong marker
    - Domain stop-list filtering
    - Study type gating
    """
    updates = []
    evidence_text = _paper_evidence_text(new_paper, summary)
    evidence_tokens_raw = _tokenize(evidence_text)
    stop_tokens = _load_domain_stoplist()

    strong_markers = {
        "prospective", "multicenter", "multisite", "external", "randomized", "randomised", "trial",
        "benchmark", "harmonisation", "fairness", "calibration", "longitudinal", "guideline",
        "rct", "clinical_trial", "intervention", "causal", "validation", "robust", "independently",
    }
    # Strong-marker gate must run on raw tokens before stop-list filtering.
    marker_hits = sorted(list(evidence_tokens_raw.intersection(strong_markers)))

    # Stop-list filtering is used only for lexical overlap scoring against gap text.
    evidence_tokens = evidence_tokens_raw - stop_tokens

    # CRITICAL: Require at least 1 strong marker to progress further
    if len(marker_hits) < 1:
        log.info("  Gap check via %s: NO STRONG MARKERS — skipping all gaps", new_paper.get("Paper_ID", ""))
        return updates

    for gap in existing_gaps:
        status = _get_gap_status(gap)
        # Rejected/retired gaps used to keep accumulating re-check evidence
        # forever (G-CAND-348 gathered dozens after being rejected at G5).
        if status in _GAP_DEAD_STATUSES:
            continue

        # FIX 2: Check required_study_type gate
        # Check required_study_type gate
        required_study_type = gap.get("required_study_type")
        paper_study_type = new_paper.get("Task_Type", "")
        if gap.get("task_type") is not None and paper_study_type != gap.get("task_type"): 
            continue        
        if required_study_type and required_study_type != "any":
            req_lower = required_study_type.lower()
            
            # SCENARIO A: The Gap explicitly requires an RCT or Clinical Trial
            if req_lower in {"rct", "clinical_trial", "health_economics"}:
                if not any(m in marker_hits for m in ["rct", "randomized", "randomised", "trial", "prospective"]):
                    log.info(
                        "  Gap %s requires %s, but paper %s lacks trial markers — REJECTED",
                        gap.get("id", ""), required_study_type, new_paper.get("Paper_ID", "")
                    )
                    continue
                    
            # SCENARIO B: Standard Task Type comparison (T1 vs T2)
            else:
                # This dictionary translates the CSV's "T2" into "conversion_prediction"
                study_type_mapping = {
                    "T1": "cross-sectional",
                    "T2": "conversion_prediction",
                    "T3": "specialized",
                    "T4": "specialized",
                    "T5": "specialized",
                    "T6": "specialized"
                }
                
                paper_study_str = study_type_mapping.get(paper_study_type, "unknown")
                
                if paper_study_str != required_study_type:
                    log.info(
                        "  Gap %s study-type gate: paper=%s, required=%s — REJECTED",
                        gap.get("id", ""), paper_study_str, required_study_type
                    )
                    continue
        gap_text = f"{gap.get('gap_statement', '')} {gap.get('closure_criteria', '')}"
        gap_tokens = _tokenize(gap_text)
        gap_tokens = gap_tokens - stop_tokens  # Remove stop tokens from gap
        
        if not gap_tokens:
            continue

        overlap = sorted(list(gap_tokens.intersection(evidence_tokens)))
        overlap_count = len(overlap)
        overlap_ratio = overlap_count / max(1, len(gap_tokens))

        # FIX 1: STRICT thresholds — overlap >= 5 AND ratio >= 0.15 AND >= 1 strong marker
        if overlap_count < 5 or overlap_ratio < 0.15:
            log.info(
                "  Gap %s re-check via %s: overlap=%d, ratio=%.3f (THRESHOLD NOT MET: need >=5 and >=0.15)",
                gap.get("id", ""),
                new_paper.get("Paper_ID", ""),
                overlap_count,
                overlap_ratio,
            )
            continue

        # Candidate relevance evidence (NOT closure yet)
        new_status = "candidate"
        if status == "candidate":
            # candidate → partially_open on first strong evidence match
            new_status = "partially_open"
        elif status == "partially_open":
            high_validation = (new_paper.get("Validation_Type", "") == "V4")
            if high_validation and (overlap_count >= 8) and (len(marker_hits) >= 2):
                new_status = "closed"
            else:
                new_status = "partially_open"

        updates.append({
            "gap_id": gap.get("id", ""),
            "paper_id": new_paper.get("Paper_ID", ""),
            "matched_terms": overlap[:12],
            "match_score": round(overlap_ratio, 3),
            "strong_markers": marker_hits,
            "new_status": new_status,
            "evidence_type": "candidate_relevance_evidence",  # FIX 5: renamed from "closure evidence"
        })
        log.info(
            "  Gap %s re-check via %s: score=%.3f, terms=%s, markers=%d, status=%s",
            gap.get("id", ""),
            new_paper.get("Paper_ID", ""),
            overlap_ratio,
            ",".join(overlap[:6]),
            len(marker_hits),
            new_status,
        )

    return updates


def _recheck_conflict_resolution(new_paper: dict, conflicts: list, all_by_id: dict) -> int:
    """Check if new paper provides bridging evidence for existing conflicts."""
    updated = 0
    new_task = new_paper.get("Task_Type", "")
    new_family = _dataset_family(new_paper.get("Dataset", ""))
    new_val = _metric_value(new_paper.get("Best_Metric", ""))
    if new_val < 0:
        return 0

    for conflict in conflicts:
        pa = conflict.get("paper_a")
        pb = conflict.get("paper_b")
        a = all_by_id.get(pa)
        b = all_by_id.get(pb)
        if not a or not b:
            continue

        if new_task != a.get("Task_Type") or new_task != b.get("Task_Type"):
            continue
        if _dataset_family(a.get("Dataset", "")) != _dataset_family(b.get("Dataset", "")):
            continue
        if new_family != _dataset_family(a.get("Dataset", "")):
            continue

        va = _metric_value(a.get("Best_Metric", ""))
        vb = _metric_value(b.get("Best_Metric", ""))
        if va < 0 or vb < 0:
            continue

        lo, hi = (va, vb) if va <= vb else (vb, va)
        near_range = (new_val >= lo - 0.03) and (new_val <= hi + 0.03)
        if not near_range:
            continue

        evidence = conflict.setdefault("resolution_evidence_papers", [])
        pid = new_paper.get("Paper_ID")
        if pid in evidence:
            continue
        evidence.append(pid)
        conflict["resolution_status"] = "partially_resolved_candidate"
        note = conflict.get("resolution_note", "")
        bridge_note = (
            f" Bridging evidence: {pid} reports {new_paper.get('Best_Metric', 'NR')} with comparable task/dataset family."
        )
        if bridge_note.strip() not in note:
            conflict["resolution_note"] = (note + bridge_note).strip()
        updated += 1

    return updated


# ── Sub-task 4C: Complementary Findings ───────────────────────────────────────

_NR_VALUES = {"not reported", "not_reported", "nr", "n/a", "na", "none", ""}

_SCAFFOLD_RE = re.compile(
    r"glyph|decoding convention|pdf extract|prisma figure|search yield|study flow|"
    r"this assignment|codebook|\bassigned\s*:|\bpart [123]\b|the numbers below|"
    r"^\s*note on\b|verbatim gloss|drafting|house style|for the registry|"
    r"reports no findings|no findings of its own|reports no (?:primary )?data|"
    r"^\s*\*\*",
    re.I,
)


def _is_drafting_scaffold(finding: str) -> bool:
    """True when an extracted 'finding' is summariser scaffolding or a denial of
    findings rather than a result the paper reports."""
    text = (finding or "").strip()
    if not text:
        return True
    if _SCAFFOLD_RE.search(text[:240]):
        return True
    words = re.findall(r"[A-Za-z]+", text)
    return len(words) < 5


def _short_modality_label(paper: dict) -> str:
    """A modality LABEL for record text. The registry Modality column sometimes
    holds the summariser's justification paragraph; splicing that into
    clinical_implication produced 'Evaluate fused MRI PET plus The codebook names
    neuropsychological testing ...' (PAPERINGO-p35)."""
    raw = re.sub(r"\s+", " ", str(paper.get("Modality", "") or "")).strip()
    if raw and len(raw) <= 60 and not re.search(r"[.;:!?*]", raw) and raw.lower() not in _NR_VALUES:
        return raw
    fam = _modality_family(raw, paper.get("Method_Architecture", ""))
    if fam and len(fam) <= 40 and not re.search(r"[.;:!?*]", fam):
        return fam.replace("_", " ")
    return "unspecified modality"


def _generate_complementary_insight(paper_a: dict, paper_b: dict, summary_a: str, summary_b: str) -> dict:
    """Deterministic complementary-insight detector (local, no external API)."""
    task_a = (paper_a.get("Task_Type", "") or "").strip()
    task_b = (paper_b.get("Task_Type", "") or "").strip()
    if not task_a or task_a != task_b:
        return {"is_complementary": False}

    # Non-independent pairs (same trial, commentary on the other paper, duplicate
    # edition, overlapping reviews) and non-evidence papers never form a pair.
    pid_a = paper_a.get("Paper_ID", "")
    pid_b = paper_b.get("Paper_ID", "")
    if _ni_excluded_as_evidence(pid_a) or _ni_excluded_as_evidence(pid_b) or _ni_blocks_pair(pid_a, pid_b):
        return {"is_complementary": False}

    # Task substage comparability (PAPERINGO-2qt): the conflict gate already tests
    # it; a five-year NC->MCI incidence study and an MCI->AD converter model are
    # not the same task even when both carry T2.
    sub_a = (paper_a.get("task_substage") or "not reported").strip()
    sub_b = (paper_b.get("task_substage") or "not reported").strip()
    if sub_a not in _NR_VALUES and sub_b not in _NR_VALUES and sub_a != sub_b:
        return {"is_complementary": False}
    # Gate-vacuity guard (2026-09-09, operator G4 verdicts C-243/C-247/C-251,
    # mirrored from _check_conflicts): when task_substage is 'not reported' on
    # BOTH sides the comparability test above cannot fire, so pairs at genuinely
    # different stages under one T-code (e.g. an NC->MCI incidence study and an
    # MCI->AD converter model) could be proposed as complementary. BLOCK the
    # pair and WARN so the operator can populate task_substage or file the
    # finding manually (case-insensitive: registry defaults are lowercase, but
    # 'NR'/'Not Reported' variants must not slip through either).
    if sub_a.lower() in _NR_VALUES and sub_b.lower() in _NR_VALUES:
        log.warning(
            "  task_substage not reported on BOTH sides (%s x %s) - "
            "pair blocked from complementary-finding generation (gate vacuity); "
            "populate task_substage or file the finding manually",
            pid_a, pid_b
        )
        return {"is_complementary": False}

    mod_a = _normalise_text(paper_a.get("Modality", ""))
    mod_b = _normalise_text(paper_b.get("Modality", ""))
    if not mod_a or not mod_b or mod_a == mod_b:
        return {"is_complementary": False}

    # Require papers to be from genuinely different L2 categories (not just
    # different modality strings) -- UNLESS they share the same L2 node but
    # use different architecture families on the same task.
    # FIX (Issue 2): arch_a/arch_b must be computed HERE, before they are
    # used in same_node_but_different_design, not after (the previous version
    # referenced them before their definition further down the function,
    # which raised a NameError on every call that reached this branch). The
    # old code also had a second, now-removed check
    # (`if not nodes_a.isdisjoint(nodes_b) and not same_node_but_different_design`)
    # immediately after this block that was unreachable dead code: by the
    # time execution got there, the first `if` above guarantees
    # nodes_a.isdisjoint(nodes_b) is already True, so
    # `not nodes_a.isdisjoint(nodes_b)` could never be True a second time.
    # The fallback logic is folded into the first gate below instead.
    # An additional guard (`nodes_a and nodes_b`) is added so the fallback
    # cannot fire when BOTH papers have unmappable/empty modality node sets
    # (set() == set() is True, which would otherwise let two papers with no
    # known modality category at all slip through as "complementary").
    nodes_a = _get_related_l2_nodes(mod_a)
    nodes_b = _get_related_l2_nodes(mod_b)
    if not nodes_a or not nodes_b or not nodes_a.isdisjoint(nodes_b):
        arch_a = _normalise_text(paper_a.get("Architecture_Family", ""))
        arch_b = _normalise_text(paper_b.get("Architecture_Family", ""))
        same_node_but_different_design = (
            nodes_a and nodes_b and
            nodes_a == nodes_b and
            arch_a and arch_b and arch_a != arch_b and
            _normalise_text(paper_a.get("Task_Type", "")) == _normalise_text(paper_b.get("Task_Type", ""))
        )
        if not same_node_but_different_design:
            return {"is_complementary": False}

    evidence_a = _complementary_evidence_text(paper_a, summary_a)
    evidence_b = _complementary_evidence_text(paper_b, summary_b)

    tokens_a = _tokenize(evidence_a)
    tokens_b = _tokenize(evidence_b)
    if len(tokens_a) < 8 or len(tokens_b) < 8:
        return {"is_complementary": False}

    inter = len(tokens_a.intersection(tokens_b))
    union = len(tokens_a.union(tokens_b))
    jaccard = inter / max(1, union)

    # Complementary when same task, different L2 modalities, and not near-duplicate evidence text.
    if jaccard > 0.40:
        return {"is_complementary": False}

    findings_a = _extract_summary_section(summary_a, ("Key findings reported",)) or paper_a.get("Key_Novelty", "")
    findings_b = _extract_summary_section(summary_b, ("Key findings reported",)) or paper_b.get("Key_Novelty", "")
    limit_a = _extract_summary_section(summary_a, ("Primary limitation", "Potential gaps or limitations")) or paper_a.get("Primary_Limitation", "")
    limit_b = _extract_summary_section(summary_b, ("Primary limitation", "Potential gaps or limitations")) or paper_b.get("Primary_Limitation", "")

    # NOTE: arch_a/arch_b are re-derived here for an independent, later check
    # (reject if BOTH papers use the identical architecture family) -- this
    # is a separate, legitimate re-use of the same variable names for a
    # different purpose than the same_node_but_different_design gate above,
    # and is left exactly as it already was.
    arch_a = _normalise_text(paper_a.get("Architecture_Family", ""))
    arch_b = _normalise_text(paper_b.get("Architecture_Family", ""))
    if arch_a and arch_b and arch_a == arch_b:
        return {"is_complementary": False}

    bridge_ab = len(_tokenize(limit_a).intersection(_tokenize(findings_b)))
    bridge_ba = len(_tokenize(limit_b).intersection(_tokenize(findings_a)))
    bridge_score = max(bridge_ab, bridge_ba)
    if bridge_score < 2:
        return {"is_complementary": False}

    if _normalise_text(findings_a) == _normalise_text(findings_b):
        return {"is_complementary": False}

    # Drafting apparatus is not a finding. The forensic house style opens sections
    # with glyph-decoding notes, PRISMA-flow commentary and coding justifications;
    # those were being captured as paper_a_finding and spliced into records
    # ("P-831 finds Note on glyph decoding for the numbers below ...")
    # (PAPERINGO-zi5, PAPERINGO-p35). Also refuse a "finding" that is a denial of
    # findings ("The commentary reports no findings of its own").
    if _is_drafting_scaffold(findings_a) or _is_drafting_scaffold(findings_b):
        return {"is_complementary": False}

    novelty_a = _safe_words(findings_a or "not reported", 14)
    novelty_b = _safe_words(findings_b or "not reported", 14)
    label_a = _short_modality_label(paper_a)
    label_b = _short_modality_label(paper_b)
    combined = _safe_words(
        f"{paper_a.get('Paper_ID', 'A')} finds {novelty_a} and reports limitation {limit_a}; "
        f"{paper_b.get('Paper_ID', 'B')} finds {novelty_b} and reports limitation {limit_b}; "
        f"together they indicate a cross modality strategy combining {label_a} and "
        f"{label_b} for {task_a}",
        55,
    )
    implication = _safe_words(
        f"Evaluate fused {label_a} plus {label_b} models under matched validation for {task_a}",
        24,
    )

    return {
        "is_complementary": True,
        "paper_a_finding": novelty_a,
        "paper_b_finding": novelty_b,
        "combined_insight": combined,
        "clinical_implication": implication,
        "score": round((1.0 - jaccard) * (1.0 + 0.1 * bridge_score), 3),
    }


def _find_similar_complementary(candidates: list, paper_a: str, paper_b: str, combined_insight: str) -> bool:
    """Return True when an equivalent or near-duplicate complementary candidate already exists."""
    pair = frozenset([paper_a, paper_b])
    insight_tokens = _tokenize(combined_insight)

    for cand in candidates:
        cand_pair = frozenset([cand.get("paper_a"), cand.get("paper_b")])
        if cand_pair == pair:
            return True

        cand_tokens = _tokenize(cand.get("combined_insight", ""))
        if not cand_tokens or not insight_tokens:
            continue

        overlap = len(cand_tokens.intersection(insight_tokens))
        ratio = overlap / max(1, len(cand_tokens.union(insight_tokens)))
        if ratio >= 0.85:
            return True

    return False


# ── Sub-task 4D: Gap Closure ───────────────────────────────────────────────────

# ── Sub-task 4E: Invalidated Assumption Detection ─────────────────────────────

def _check_invalidated_assumptions(new_paper: dict, summary: str, assumption_reg: dict) -> list:
    """
    Check if new paper's XAI results invalidate any known assumption.
    FIX: Only invalidate if paper's modality matches assumption's modality_required field.
    """
    # Gate 1: Skip review papers and papers without genuine XAI content
    if (new_paper.get("Task_Type", "") or "").strip().upper() == "T6":
        return []  # Review/descriptive papers have no XAI findings of their own

    xai = new_paper.get("XAI_Method", "none").lower().strip()
    if xai in {"none", "not reported", "nr", ""}:
        return []

    # Gate 2: The XAI method must actually be a spatial/feature method, not just mentioned
    valid_xai = {"grad-cam", "shap", "lime", "attention-map", "occlusion", "cart", "fuzzy-rules",
                 "decision-tree", "integrated-gradients", "permutation-importance"}
    if not any(v in xai for v in valid_xai):
        return []
    assumptions = assumption_reg.get("known_assumptions", [])
    # Only check papers with XAI methods
    if new_paper.get("XAI_Method", "none") == "none":
        return []

    text = _normalise_text(summary)
    if not text:
        return []

    contradiction_markers = {
        "contradict", "disprove", "inconsistent", "not support", "fails to support",
        "opposite", "unexpected", "did not", "no association", "inverse",
    }
    has_marker = any(m in text for m in contradiction_markers)
    if not has_marker:
        return []

    results = []
    paper_modality = _normalise_text(new_paper.get("Modality", ""))

    def _modality_aliases(value: str) -> set:
        vals = set()
        for raw in re.split(r"[|,;/]", value or ""):
            v = _normalise_text(raw)
            if not v:
                continue
            vals.add(v)

            if v in {"retinal", "ocular", "fundus", "oct"}:
                vals.update({"retinal", "ocular"})
            if v in {"eeg", "eeg_meg", "electrophysiology", "meg", "erp"}:
                vals.update({"eeg", "eeg_meg", "electrophysiology"})
            if v in {"fmri", "functional_mri", "rs-fmri", "resting-state fmri"}:
                vals.update({"fmri", "functional_mri"})
            if v in {"mri", "structural_mri", "neuroimaging"}:
                vals.update({"mri", "structural_mri", "neuroimaging"})
            if v in {"clinical_survey", "clinicalehr", "ehr", "clinical"}:
                vals.update({"clinical_survey", "clinicalehr", "ehr", "clinical"})
            if v in {"speech_language", "language", "speech"}:
                vals.update({"speech_language", "language", "speech"})
            if v in {"digital_behaviour", "digital_behavior", "digital"}:
                vals.update({"digital_behaviour", "digital_behavior", "digital"})
        return vals

    paper_nodes = {_normalise_text(n) for n in _get_related_l2_nodes(paper_modality)}
    paper_family = _normalise_text(_modality_family(new_paper.get("Modality", ""), new_paper.get("Method_Architecture", "")))
    paper_modality_aliases = _modality_aliases(paper_modality)
    paper_scope = paper_nodes.union(paper_modality_aliases).union({paper_family})
    
    for a in assumptions:
        aid = a.get("id", "")
        if not aid:
            continue

        # FIX 2 (critical): Check modality_required gate
        required_modality = a.get("modality_required", "any")
        required_scope = _modality_aliases(str(required_modality))
        if required_scope and "any" not in required_scope:
            if paper_scope.isdisjoint(required_scope):
                log.info(
                    "  Assumption %s modality gate: paper=%s, required=%s — REJECTED",
                    aid, paper_modality, required_modality
                )
                continue

        belief = _normalise_text(a.get("belief", ""))
        signal = _normalise_text(a.get("disproof_signal", ""))
        if not belief or not signal:
            continue

        belief_hits = sum(1 for t in _tokenize(belief) if t in text)
        signal_hits = sum(1 for t in _tokenize(signal) if t in text)
        def _extract_xai_section(summary: str) -> str:
            """Extract the part of the summary that describes XAI/saliency results."""
            patterns = [
                r"(grad.?cam[^.]{10,200}\.)",
                r"(saliency[^.]{10,200}\.)",
                r"(shap[^.]{10,200}\.)",
                r"(attention.{0,20}map[^.]{10,200}\.)",
                r"(explainab[^.]{10,200}\.)",
            ]
            for pat in patterns:
                m = re.search(pat, summary, re.IGNORECASE)
                if m:
                    return m.group(1)
            return ""
        xai_section = _extract_xai_section(summary)

        
        if belief_hits >= 2 and signal_hits >= 1:
            results.append({
                "assumption_id": aid,
                "prior_belief": a.get("belief", ""),
                "evidence_sentence": _safe_words(xai_section, 28) if xai_section else "XAI evidence not extractable",
                "assumption_type": a.get("assumption_type", "not reported"),
                "paper_modality": paper_modality,
                "required_modality": required_modality,
                "proposed_clinical_implication": "Potentially revise prior assumption; requires Gate G5 clinical confirmation",
                "disproved": True,
            })
            log.info(
                "  Assumption %s invalidated by %s (modality=%s matches required=%s)",
                aid, new_paper.get("Paper_ID", ""), paper_modality, required_modality
            )

    return results


# ── Summary parser ─────────────────────────────────────────────────────────────

def _parse_summary_for_paper(paper_id: str) -> str:
    global _SUMMARY_INDEX_CACHE, _SUMMARY_INDEX_SOURCE
    source = _resolve_summary_source()
    if source is None:
        return ""
    source_key = _resolved_summary_key()

    if _SUMMARY_INDEX_CACHE is None or _SUMMARY_INDEX_SOURCE != source_key:
        _SUMMARY_INDEX_CACHE = _build_summary_index(source)
        _SUMMARY_INDEX_SOURCE = source_key

    if paper_id in _SUMMARY_INDEX_CACHE:
        return _SUMMARY_INDEX_CACHE[paper_id]

    pid_num = paper_id.replace("P-", "")
    alt_id = f"P-{pid_num}" if pid_num.isdigit() else pid_num
    if alt_id in _SUMMARY_INDEX_CACHE:
        return _SUMMARY_INDEX_CACHE[alt_id]

    return ""


def _derive_gap_statement_from_limitation(pid: str, limitation: str, task_type: str, modality: str) -> str:
    """Convert a raw limitation text into a structured gap statement."""
    lim = limitation.strip()
    # If it already reads like a gap statement, use it
    if any(phrase in lim.lower() for phrase in ["no study", "never", "absent", "zero", "lacking"]):
        return lim[:300]
    # Otherwise frame it as a gap
    task_desc = {"T1": "MCI detection", "T2": "MCI conversion prediction",
                 "T3": "multi-class staging", "T4": "biomarker analysis",
                 "T5": "MCI reversion", "T6": "review"}.get(task_type, "MCI prediction")
    return f"{pid} ({modality}, {task_desc}): {lim[:280]}"

def _build_cluster_text(papers: list) -> str:
    parts = []
    for p in papers:
        pid = p.get("Paper_ID", "")
        summary = _parse_summary_for_paper(pid)
        limitation = _extract_summary_section(summary, ("Potential gaps or limitations", "Primary limitation", "Open problems"))
        if not limitation:
            limitation = p.get("Primary_Limitation", "Not reported")
        parts.append(f"Paper {pid} ({p.get('Task_Type', '')}, {p.get('Dataset', '')}): {limitation}")
    return "\n".join(parts)

def _llm_cluster_gap_call(
    modality_group: str,
    cluster_text: str,
    existing_gap_statements: list,
    genuine_gap_examples: list | None = None,
) -> list:
    """
    VESTIGIAL / DEPRECATED PATH. ARCHITECTURE NOTE: the "LLM Actor/Critic" in
    this pipeline is Claude Code operating IN-SESSION -- reading summaries.md +
    the registry and curating gaps/conflicts into the JSON via
    gold_standard_rubric.json, under human review (agents + llm + human). It is
    NOT a programmatic anthropic.Anthropic() API call. Wiring the Actor to an
    ANTHROPIC_API_KEY was the original mistake -- that is why gap synthesis kept
    appearing to "need a key". This function only fires if ANTHROPIC_API_KEY is
    set (normally it is not), so it is a no-op in standard operation and is kept
    only for backward compatibility.

    FIX (Issue 3): genuine_gap_examples is now loaded ONCE at the top of
    run() from CONFIG / "gold_standard_rubric.json" (see run()) and passed in
    explicitly here, rather than this function re-reading the file from disk
    on every call. The previous version computed its path as
    Path(__file__).parent.parent, which equals ROOT (not CONFIG) -- the same
    expression used to define ROOT itself at the top of this module -- so if
    the rubric file actually lives at config/gold_standard_rubric.json as the
    rest of this codebase's convention assumes (see ASSUMPTION_REG,
    DOMAIN_STOPLIST), rubric_path.exists() was always False and few_shot_text
    was silently always empty, with no error or warning raised.
    """
    import anthropic
    client = anthropic.Anthropic()

    few_shot_text = ""
    if genuine_gap_examples:
        examples = [
            f"GOOD EXAMPLE (Genuine Gap):\nStatement: {g.get('gap_statement')}\nReasoning: {g.get('reasoning')}"
            for g in genuine_gap_examples
        ]
        if examples:
            few_shot_text = "\n\nHere are some GOLD STANDARD examples of the depth required:\n\n" + "\n\n".join(examples)

    prompt = f"""
    Analyze these {modality_group} limitations and synthesize ONE OR TWO high-level research gaps.
    DO NOT repeat statements similar to these: {existing_gap_statements}
    {few_shot_text}
    
    Data:
    {cluster_text}
    
    Output JSON list of strings containing just the gap statements.
    """
    try:
        response = client.messages.create(
            model=os.environ.get("AGENT4_MODEL", "claude-haiku-4-5"),  # 3-haiku-20240307 retired 2026-04-19 (PAPERINGO-ron)
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            system="You are a senior AI researcher reviewing limitations in MCI prediction papers."
        )
        text = response.content[0].text
        # Naive extraction - assumes Claude returns a JSON array somewhere in the text
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return []
    except Exception as e:
        log.warning(f"LLM cluster gap call failed: {e}")
        return []


def _is_clean_modality_label(value: str) -> bool:
    """A modality family fit to appear in a gap statement: short, no sentence
    punctuation, no markdown, at most a handful of words."""
    s = (value or "").strip()
    if not s or len(s) > 60 or s.count(" ") > 5:
        return False
    return not re.search(r"[.!?\"*:;]", s)


# Derived structural_absence gaps from registry modality x task crossings
# ("Corpus-wide absence: <modality> modality never applied to <task> ... despite
# appearing in corpus for other tasks"). Rejected as a class by the operator on
# 2026-09-15 (G-CAND-336..355 withdrawn; see logs/pipeline_log.txt 2026-09-15).
# The class is new-evidence-perverse: every new paper adds crossings, so the
# generator emits MORE absences as the corpus grows; two fired on documents with
# no primary data (an editorial, a narrative review); and none carried a genuine
# claiming paper (_append_gap_candidate replaces the computed list with
# [source_paper] = ["corpus-synthesis"], which _validate_gap_record_ids turns
# into ["UNRESOLVED"]). Same precedent as _is_single_paper_limitation_gap for
# G-CAND-319..335. Gates Pattern 1 of _detect_corpus_synthesis_gaps only:
# Pattern 2 (single-site monopoly) is gap_type=methodological, and the LLM
# cluster pattern (source_paper "llm-cluster-synthesis") emits the same
# derived/structural_absence labels from a different provenance and is dormant
# without ANTHROPIC_API_KEY - neither is a registry crossing, so neither is
# gated here. Flip to True only on an explicit operator decision.
EMIT_DERIVED_STRUCTURAL_ABSENCE = False


def _detect_corpus_synthesis_gaps(
    all_rows: list,
    gaps: list,
    log_lines: list,
    genuine_gap_examples: list | None = None,
) -> int:
    """
    Detect gaps that require seeing the full corpus simultaneously.
    These cannot be found by processing one paper at a time.
    Three patterns detected:
    1. Modality-task combination absences (T5 + digital = zero papers)
    2. Repeatedly stated limitations (same specific claim in 3+ papers)
    3. Single-site monopoly (every paper in a modality uses one dataset)

    genuine_gap_examples (FIX Issue 3): few-shot examples from
    config/gold_standard_rubric.json, loaded once in run() and threaded
    through to _llm_cluster_gap_call below, the only place in this file that
    makes a generative LLM call.
    """
    added = 0
    existing_statements = {_normalise_text(g.get("gap_statement","")) for g in gaps}

    # Pattern 1: Modality-task combination absences
    task_modality_counts = {}
    for row in all_rows:
        task = row.get("Task_Type", "")
        mod_family = _modality_family(row.get("Modality",""), row.get("Method_Architecture",""))
        # _modality_family falls back to the RAW registry text when nothing in its
        # keyword ladder matches, so a 511-char bolded coding justification became
        # the "modality" of G-CAND-348 (PAPERINGO-xg7r, PAPERINGO-0ft). Only a
        # label-shaped family can seed an absence gap.
        if not _is_clean_modality_label(mod_family):
            continue
        if task and mod_family:
            key = (task, mod_family)
            task_modality_counts[key] = task_modality_counts.get(key, 0) + 1

    # Modalities that DO appear in the corpus
    all_modalities = set(k[1] for k in task_modality_counts)
    all_tasks = set(k[0] for k in task_modality_counts)

    suppressed_structural_absence = 0
    for task in all_tasks:
        for mod in all_modalities:
            count = task_modality_counts.get((task, mod), 0)
            if count == 0:
                # Check if this modality appears at all in a different task
                modality_exists = any(
                    task_modality_counts.get((t, mod), 0) > 0
                    for t in all_tasks if t != task
                )
                if not modality_exists:
                    continue  # Modality not in corpus at all — not informative
                if not EMIT_DERIVED_STRUCTURAL_ABSENCE:
                    # Class rejected 2026-09-15 (see switch above). Count the
                    # crossing so the run log shows the suppression explicitly
                    # rather than looking like silent under-generation.
                    suppressed_structural_absence += 1
                    continue
                
                task_desc = {"T1": "MCI detection", "T2": "conversion prediction",
                             "T3": "multi-class staging", "T5": "reversion"}.get(task, task)
                stmt = (f"Corpus-wide absence: {mod} modality never applied to "
                        f"{task_desc} ({task}) despite appearing in corpus for other tasks.")
                
                stmt_norm = _normalise_text(stmt)
                sim = _find_semantic_gap_match(gaps, stmt)
                if sim is not None:
                    continue  # Already captured
                if stmt_norm in existing_statements:
                    continue

                # Find papers that use this modality (claiming papers)
                claiming = [r["Paper_ID"] for r in all_rows
                           if _modality_family(r.get("Modality",""), r.get("Method_Architecture","")) == mod][:5]

                candidate = {
                    "source_paper": "corpus-synthesis",
                    "gap_type": "structural_absence",
                    "tier": "derived",
                    "gap_statement": stmt,
                    "papers_claiming_gap": claiming,
                    "papers_potentially_closing_gap": [],
                    "current_status": "open",
                    "evidence_for_open_status": (
                        f"Systematic scan: {len(claiming)} papers use {mod} modality "
                        f"but none apply it to {task} task."
                    ),
                    "closure_criteria": (
                        f"Primary study applying {mod} to {task_desc} ({task}) "
                        f"with N>=50 and AUC on held-out set."
                    ),
                    "modality": mod,
                    "task_type": task,
                }
                registry_ids = {r.get("Paper_ID","") for r in all_rows}
                if _append_gap_candidate(gaps, candidate, [], registry_ids, log_lines):
                    added += 1
                    existing_statements.add(stmt_norm)
                    log_lines.append(f"corpus-synthesis: modality-task absence gap {mod}×{task}")

    if not EMIT_DERIVED_STRUCTURAL_ABSENCE:
        log.info(
            "Agent 4 gap synthesis: EMIT_DERIVED_STRUCTURAL_ABSENCE=False, suppressed %d "
            "derived structural_absence candidate(s) from modality x task crossings "
            "(pre-dedup; class rejected by operator 2026-09-15, G-CAND-336..355).",
            suppressed_structural_absence,
        )
        log_lines.append(
            f"corpus-synthesis: suppressed_derived_structural_absence_total={suppressed_structural_absence}"
        )

    # Pattern 2: Single-site monopoly within a modality
    modality_datasets = {}
    for row in all_rows:
        mod_family = _modality_family(row.get("Modality",""), row.get("Method_Architecture",""))
        ds_family = _dataset_family(row.get("Dataset",""))
        if mod_family not in modality_datasets:
            modality_datasets[mod_family] = set()
        modality_datasets[mod_family].add(ds_family)

    for mod, datasets in modality_datasets.items():
        if len(datasets) == 1 and "single-centre" not in datasets:
            sole_ds = list(datasets)[0]
            count = sum(1 for r in all_rows
                       if _modality_family(r.get("Modality",""), r.get("Method_Architecture","")) == mod)
            if count >= 3:
                stmt = (f"All {count} {mod} papers in this corpus use a single dataset family "
                        f"({sole_ds}). No multi-site or independent-cohort validation exists for this modality.")
                stmt_norm = _normalise_text(stmt)
                if stmt_norm in existing_statements:
                    continue
                sim = _find_semantic_gap_match(gaps, stmt)
                if sim is not None:
                    continue
                claiming = [r["Paper_ID"] for r in all_rows
                           if _modality_family(r.get("Modality",""), r.get("Method_Architecture","")) == mod][:6]
                candidate = {
                    "source_paper": "corpus-synthesis",
                    "gap_type": "methodological",
                    "tier": "derived",
                    "gap_statement": stmt,
                    "papers_claiming_gap": claiming,
                    "papers_potentially_closing_gap": [],
                    "current_status": "open",
                    "evidence_for_open_status": f"Corpus scan: all {mod} papers use {sole_ds} only.",
                    "closure_criteria": f"Multi-site study validating {mod} on >=2 independent cohorts.",
                    "modality": mod,
                    "task_type": "any",
                }
                registry_ids = {r.get("Paper_ID","") for r in all_rows}
                if _append_gap_candidate(gaps, candidate, [], registry_ids, log_lines):
                    added += 1
                    existing_statements.add(stmt_norm)
                    log_lines.append(f"corpus-synthesis: single-site monopoly {mod} → {sole_ds}")

    # After the deterministic absence detection, optionally call LLM for semantic gaps
    ANTH_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if ANTH_KEY and len(all_rows) > 0:
        modality_clusters = {}
        for row in all_rows:
            mod_family = _modality_family(row.get("Modality", ""), row.get("Method_Architecture", ""))
            modality_clusters.setdefault(mod_family, []).append(row)

        for modality_group, papers in modality_clusters.items():
            if len(papers) < 3:
                continue
            # Build compact cluster description using FULL limitation texts
            cluster_text = _build_cluster_text(papers)
            llm_gaps = _llm_cluster_gap_call(
                modality_group, cluster_text, list(existing_statements), genuine_gap_examples
            )
            for gap_stmt in llm_gaps:
                candidate = {
                    "source_paper": "llm-cluster-synthesis",
                    "gap_type": "structural_absence",
                    "tier": "derived",
                    "gap_statement": gap_stmt,
                    "papers_claiming_gap": [p["Paper_ID"] for p in papers[:5]],
                    "papers_potentially_closing_gap": [],
                    "current_status": "open",
                    "evidence_for_open_status": f"LLM cluster analysis of {len(papers)} {modality_group} papers",
                    "closure_criteria": "Human review required at Gate G4",
                    "modality": modality_group,
                    "task_type": "any",
                }
                registry_ids = {r.get("Paper_ID","") for r in all_rows}
                # To prevent errors passing missing list for conflicts:
                if _append_gap_candidate(gaps, candidate, [], registry_ids, log_lines):
                    added += 1

    return added


# ── Main ───────────────────────────────────────────────────────────────────────

def run(state: dict, paper_filter=None) -> dict:
    """Main entry point. Returns counts of updates made."""
    with traced_span("agent4.run", {"paper_filter_count": len(paper_filter) if paper_filter else 0}):
        all_rows      = _load_registry()
        all_by_id     = _registry_by_id(all_rows)
        conflicts_payload = _load_json(CONFLICTS)
        consensus_payload = _load_json(CONSENSUS)
        complementary_payload = _load_json(COMPLEMENTARY)
        cf_candidates_payload = _load_json(COMPLEMENTARY_CANDS)
        gaps_payload = _load_json(GAPS)
        invalidated_payload = _load_json(INVALIDATED)

        conflicts     = _extract_records(conflicts_payload, "conflicts")
        consensus     = _extract_records(consensus_payload, "verified_findings")
        complementary = _extract_records(complementary_payload, "complementary_findings")
        cf_candidates = _extract_records(cf_candidates_payload, "complementary_candidates")
        gaps          = _extract_records(gaps_payload, "gaps")
        # Pristine snapshot of gaps as loaded, for the concurrency-safe 3-way
        # merge at save time (see _save_gaps_merge_safe).
        _gaps_base = {r.get("id"): copy.deepcopy(r) for r in gaps if isinstance(r, dict) and r.get("id")}
        invalidated   = _extract_records(invalidated_payload, "invalidated_assumptions")
        assumption_reg = json.loads(ASSUMPTION_REG.read_text()) if ASSUMPTION_REG.exists() else {}
        registry_ids = _registry_paper_ids(all_rows)

        # FIX (Issue 3): load the gold-standard rubric ONCE here, rather than
        # never (it was previously only read, with a wrong path, inside the
        # nested _llm_cluster_gap_call helper -- see that function's
        # docstring). genuine_gap_examples feeds the one place in this file
        # that actually makes a generative LLM call (_llm_cluster_gap_call,
        # via _detect_corpus_synthesis_gaps below).
        #
        # genuine_conflict_examples is also loaded and kept available, but is
        # deliberately NOT threaded into _check_conflicts(): that function is
        # fully deterministic (threshold/equality checks only, no LLM call),
        # so there is no generative step to inject few-shot examples into. If
        # an LLM-assisted conflict reviewer is added in future, this is
        # already loaded and ready to wire in.
        RUBRIC_PATH = CONFIG / "gold_standard_rubric.json"
        rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8")) if RUBRIC_PATH.exists() else {}
        genuine_gap_examples = rubric.get("genuine_gaps", [])[:5]
        genuine_conflict_examples = rubric.get("genuine_conflicts", [])[:3]  # reserved for future LLM-assisted conflict review

        pre_log_lines = []

        # Step 0: consistency check on existing conflict records.
        # FIX (Issue 4): the previous version DELETED corrupt records
        # outright (losing the audit trail) and used a hardcoded 0.08 gap
        # threshold regardless of validation tier -- which silently deletes
        # genuine V3 conflicts with gap in [0.05, 0.08) and genuine V4
        # conflicts with gap in [0.03, 0.08). This is the exact bug class
        # that previously deleted the genuine P-41 vs P-65 conflict (V4,
        # gap=0.0799, correctly above the V4 threshold of 0.03, but below the
        # old flat 0.08 cutoff). The version of this fix proposed in the
        # issue report reads validation_tier_a/validation_tier_b directly off
        # the conflict record -- but conflict records in this codebase's
        # actual schema do not have those fields; they only have paper_a and
        # paper_b paper-ID references. Reading a non-existent field via
        # .get(..., "V2") would default BOTH sides to "V2" for every record,
        # making max(tier_thresholds["V2"], tier_thresholds["V2"]) == 0.08
        # always -- functionally identical to the original bug it was meant
        # to fix. The corrected version below looks up each paper's actual
        # Validation_Type from the registry via all_by_id[paper_a/paper_b],
        # and auto-fixes corrupt records IN PLACE (rather than deleting them)
        # so the audit trail is preserved.
        _TIER_THRESHOLDS = {"V1": 0.15, "V2": 0.08, "V3": 0.05, "V4": 0.03}

        def _conflict_tier_threshold(c: dict) -> float:
            pa = all_by_id.get(c.get("paper_a", ""), {})
            pb = all_by_id.get(c.get("paper_b", ""), {})
            t_a = _TIER_THRESHOLDS.get(pa.get("Validation_Type", "V2"), 0.08)
            t_b = _TIER_THRESHOLDS.get(pb.get("Validation_Type", "V2"), 0.08)
            return max(t_a, t_b)

        consistency_fixes = 0
        for c in conflicts:
            g1 = str(c.get("genuine", "")).strip().lower()
            g2 = str(c.get("genuine_conflict", "")).strip().lower()

            # Contradictory genuine flags, checked in both directions (the
            # issue named only genuine="No" + genuine_conflict="True", but
            # the inverse contradiction is the same underlying defect).
            if (g1 in ("no", "false") and g2 == "true") or (g1 in ("yes", "true") and g2 == "false"):
                c["genuine"] = "No"
                c["genuine_conflict"] = False
                c["resolution_note"] = (
                    c.get("resolution_note", "") + " [AUTO-FIXED: contradictory genuine flags]"
                ).strip()
                pre_log_lines.append(
                    f"consistency_check auto_fixed id={c.get('id', 'NA')} reason='contradictory genuine flags'"
                )
                consistency_fixes += 1

            # Gap below the tier-appropriate threshold, looked up from the
            # registry rather than a non-existent field on the record itself.
            gap_mag = c.get("gap_magnitude")
            if isinstance(gap_mag, (int, float)):
                threshold = _conflict_tier_threshold(c)
                if gap_mag < threshold:
                    c["genuine"] = "No"
                    c["genuine_conflict"] = False
                    c["resolution_note"] = (
                        c.get("resolution_note", "")
                        + f" [AUTO-FIXED: gap {gap_mag:.3f} below tier threshold {threshold:.3f}]"
                    ).strip()
                    pre_log_lines.append(
                        f"consistency_check auto_fixed id={c.get('id', 'NA')} "
                        f"reason='gap {gap_mag:.3f} < tier_threshold {threshold:.3f}'"
                    )
                    consistency_fixes += 1

        if consistency_fixes:
            log.warning("Step 0 consistency check: auto-fixed %d corrupt conflict record(s)", consistency_fixes)
            pre_log_lines.append(f"consistency_check fixed={consistency_fixes}")

        backfilled_legacy_modality = _backfill_legacy_conflict_modality_family(conflicts, all_by_id, pre_log_lines)
        if backfilled_legacy_modality:
            log.info(
                "Agent 4 cleanup: backfilled modality_family for %d legacy conflict(s).",
                backfilled_legacy_modality,
            )

        _ensure_consensus_claim_requirements(consensus, pre_log_lines)
        pruned_consensus_support = _prune_consensus_support(consensus, all_by_id, pre_log_lines)
        if pruned_consensus_support:
            log.info("Agent 4 cleanup: pruned %d inflated consensus support links.", pruned_consensus_support)

        gaps, removed_noisy = _prune_noisy_combinatorial_gaps(gaps, pre_log_lines)
        if removed_noisy:
            log.info("Agent 4 cleanup: removed %d noisy legacy combinatorial gap(s).", removed_noisy)

        auto_rej_lim = _auto_reject_single_paper_limitation_gaps(gaps, pre_log_lines)
        if auto_rej_lim:
            log.info("Agent 4 cleanup: auto-rejected %d single-paper-limitation false-positive gap(s).", auto_rej_lim)

        # Backfill tiers for legacy records that predate tier support.
        for g in gaps:
            if not _normalise_text(g.get("tier", "")):
                g["tier"] = _infer_gap_tier(g)

        # Determine new papers to process
        if paper_filter:
            new_papers = [r for r in all_rows if r["Paper_ID"] in paper_filter]
        else:
            log.info("No paper_filter provided. Defaulting to maintenance sweep to prevent duplication.")
            new_papers = []
            processed_ids = set()
            for c in conflicts:
                processed_ids.update([c.get("paper_a"), c.get("paper_b")])
                processed_ids.update(c.get("resolution_evidence_papers", []))
            for claim in consensus:
                processed_ids.update(claim.get("supporting_papers", []))
            for g in gaps:
                for pid in g.get(_get_gap_claiming_key(g), []):
                    processed_ids.add(pid)
                for pid in g.get(_get_gap_closing_key(g), []):
                    processed_ids.add(pid)
            for inv in invalidated:
                pid = inv.get("paper_id") or inv.get("paper")
                if pid:
                    processed_ids.add(pid)
            new_papers = [r for r in all_rows if r["Paper_ID"] not in processed_ids]

        if not new_papers:
            log.info("Agent 4: No new papers for relational analysis. Running gap maintenance sweep only.")
            maintenance_logs = list(pre_log_lines)
            with traced_span("agent4.synthetic_gap_coverage"):
                synthetic_added = _run_synthetic_gap_coverage(all_rows, gaps, conflicts, registry_ids, maintenance_logs)
            for g in gaps:
                _validate_gap_record_ids(g, registry_ids, maintenance_logs)

            conflicts_payload = _inject_records(conflicts_payload, "conflicts", conflicts)
            consensus_payload = _inject_records(consensus_payload, "verified_findings", consensus)
            gaps_payload = _inject_records(gaps_payload, "gaps", gaps)
            _refresh_conflicts_summary(conflicts_payload if isinstance(conflicts_payload, dict) else {}, conflicts)
            _refresh_gaps_summary(gaps_payload if isinstance(gaps_payload, dict) else {}, gaps)
            _save_json(CONFLICTS, conflicts_payload)
            _save_json(CONSENSUS, consensus_payload)
            # Concurrency-safe 3-way merge (preserves concurrent G5 gap edits and
            # our own closure updates); replaces blind overwrite.
            _save_gaps_merge_safe(GAPS, "gaps", gaps, _gaps_base)

            if maintenance_logs:
                with open(REL_LOG, "a", encoding="utf-8") as f:
                    from datetime import datetime
                    f.write(f"\n--- Run {datetime.utcnow().isoformat()} ---\n")
                    f.write("\n".join(maintenance_logs) + "\n")

            return {
                "processed": 0,
                "conflicts": 0,
                "consensus": 0,
                "complementary": 0,
                "gaps": synthetic_added,
                "invalidated": 0,
            }

        log.info(f"Agent 4: Processing {len(new_papers)} new papers.")
        counts = {"conflicts": 0, "conflict_resolutions": 0, "consensus": 0, "complementary": 0, "gaps": 0, "invalidated": 0}
        log_lines = list(pre_log_lines)
        suspect_accuracy_ids = _collect_suspect_accuracy_ids(new_papers)
        existing_invalidated_keys = {
            ((inv.get("paper") or inv.get("paper_id") or "").strip(), str(inv.get("assumption_id") or "").strip())
            for inv in invalidated
        }
        for spid in suspect_accuracy_ids:
            log_lines.append(f"{spid}: inflated_performance_flag metric_gt_97 excluded_from_conflict_generation")

        # FIX 4: Detect duplicate-like rows (same method, dataset, results).
        # In explicit paper-filter reruns, keep both rows so forced full resets can cover all requested IDs.
        skip_duplicates = not bool(paper_filter)
        deduped_papers = []
        for new in new_papers:
            pid = new["Paper_ID"]
            modality = new.get("Modality", "")
            method = new.get("Method_Architecture", "")
            dataset = new.get("Dataset", "")
            metric = new.get("Best_Metric", "")

            duplicate_of = None
            for other in all_rows:
                if other["Paper_ID"] == pid:
                    continue
                if (
                    other.get("Modality", "") == modality
                    and other.get("Method_Architecture", "") == method
                    and other.get("Dataset", "") == dataset
                    and other.get("Best_Metric", "") == metric
                    and metric != ""
                ):
                    duplicate_of = other["Paper_ID"]
                    break

            if duplicate_of and skip_duplicates:
                log_lines.append(
                    f"⚠ DUPLICATE DETECTED: {pid} appears identical to {duplicate_of} "
                    f"(same modality/method/dataset/metric). SKIPPING {pid} from analysis."
                )
                log.warning(f"  Duplicate paper: {pid} = {duplicate_of}")
                continue

            if duplicate_of and not skip_duplicates:
                log_lines.append(
                    f"⚠ DUPLICATE DETECTED: {pid} appears identical to {duplicate_of} "
                    f"(same modality/method/dataset/metric). KEEPING {pid} due to explicit paper_filter rerun."
                )
                log.warning(f"  Duplicate paper retained for explicit rerun: {pid} = {duplicate_of}")

            deduped_papers.append(new)

        new_papers = deduped_papers

        log.info(f"Agent 4: After dedup, processing {len(new_papers)} new papers.")

        for new in new_papers:
            pid = new["Paper_ID"]
            log.info(f"  Analysing: {pid}")
            summary = _parse_summary_for_paper(pid)

            with traced_span("agent4.process_paper", {
                "paper_id": pid,
                "task_type": new.get("Task_Type", ""),
                "validation_type": new.get("Validation_Type", ""),
                "dataset": new.get("Dataset", ""),
            }):

                # 4A: Conflicts
                with traced_span("agent4.subtask.conflicts", {"paper_id": pid}):
                    new_confs = _check_conflicts([new], all_rows, conflicts)
                    conflicts.extend(new_confs)
                    counts["conflicts"] += len(new_confs)
                    resolved_hits = _recheck_conflict_resolution(new, conflicts, all_by_id)
                    if resolved_hits:
                        counts["conflict_resolutions"] += resolved_hits
                        log_lines.append(f"{pid}: {resolved_hits} conflict(s) received bridging-resolution evidence")
                    if new_confs:
                        log_lines.append(f"{pid}: {len(new_confs)} conflicts flagged")

                # 4B: Consensus
                with traced_span("agent4.subtask.consensus", {"paper_id": pid}):
                    consensus_added = _append_consensus_support(new, all_by_id, consensus, summary_text=summary, log_lines=log_lines)
                    if consensus_added:
                        counts["consensus"] += consensus_added
                        log_lines.append(f"{pid}: {consensus_added} consensus support append(s)")

                # 4C: Complementary findings
                with traced_span("agent4.subtask.complementary", {"paper_id": pid}):
                    existing_cf_pairs = {
                        frozenset([c.get("paper_a"), c.get("paper_b")])
                        for c in cf_candidates
                    }
                    max_complementary_per_paper = 12
                    added_for_paper = 0
                    # Evaluate against all other papers (deterministic order) to avoid
                    # missing valid complementary pairs because of random sampling.
                    def _paper_sort_key(row: dict):
                        pid_val = str(row.get("Paper_ID", ""))
                        try:
                            return int(pid_val.replace("P-", ""))
                        except Exception:
                            return 10**9

                    other_rows = sorted(all_rows, key=_paper_sort_key)

                    for other in other_rows:
                        if added_for_paper >= max_complementary_per_paper:
                            break

                        if other["Paper_ID"] == pid:
                            continue
            
                        # Prevent duplicate pairs
                        pair = frozenset([pid, other["Paper_ID"]])
                        if pair in existing_cf_pairs:
                            continue

                        other_summary = _parse_summary_for_paper(other["Paper_ID"])
                        result = _generate_complementary_insight(new, other, summary, other_summary)
            
                        if result.get("is_complementary"):
                            similar = _find_similar_complementary(
                                cf_candidates, pid, other["Paper_ID"],
                                result.get("combined_insight", "")
                            )
                            if similar:
                                continue
                
                            candidate = {
                                "id": _next_id(cf_candidates, "CF-cand-"),
                                "paper_a": pid,
                                "paper_b": other["Paper_ID"],
                                "paper_a_finding": result.get("paper_a_finding", ""),
                                "paper_b_finding": result.get("paper_b_finding", ""),
                                "combined_insight": result.get("combined_insight", ""),
                                "clinical_implication": result.get("clinical_implication", ""),
                                "status": "pending_human_review",
                            }
                            # Advisory quality codes for the human G4 reviewer. Never
                            # used to reject: status stays pending_human_review.
                            candidate["quality_flags"] = _flag_candidate_quality(candidate)
                            cf_candidates.append(candidate)
                            counts["complementary"] += 1
                            added_for_paper += 1
                            existing_cf_pairs.add(pair)
                            log_lines.append(f"{pid} × {other['Paper_ID']}: complementary candidate")
        
                # 4D: Gap closure
                with traced_span("agent4.subtask.gaps", {"paper_id": pid}):
                    gap_updates = _check_gap_closure(new, summary, gaps)
                    for upd in gap_updates:
                        for gap in gaps:
                            if gap["id"] == upd["gap_id"]:
                                # PRECISION-FIRST gap false-closure fix:
                                # lexical token overlap + a generic marker (papers
                                # merely sharing words like "longitudinal"/
                                # "validation"/"screening") is far too weak to prove
                                # a paper CLOSES a gap. The previous logic auto-
                                # attached the paper to papers_potentially_closing_gap
                                # AND promoted status (candidate->partially_open->
                                # closed), which falsely closed genuinely-open gaps:
                                # human review of the gold set found G-09 had 10 FP
                                # "closers" and G-04 had 4 -- ALL false positives.
                                # We now ONLY record the paper as candidate-relevance
                                # *text* evidence for human/LLM review; we do NOT add
                                # it to the closing list and do NOT change status.
                                # Real closure happens via human G5 decisions
                                # (apply_copilot_gap_decisions) or the LLM Critic.
                                # Structured, capped trail (PAPERINGO-dpn / vl7): the
                                # old prose append grew evidence_for_open_status without
                                # bound (G-09 held 286 sentences; 87% of a 4 MB file) and
                                # rewrote ~170 gap records every batch with no change of
                                # substance. The curated statement is left alone; the
                                # re-check history lives in candidate_relevance (top-N by
                                # score) and, in full, in logs/gap_recheck_trail.jsonl.
                                _record_gap_recheck(gap, {
                                    "paper_id": upd["paper_id"],
                                    "score": upd["match_score"],
                                    "terms": upd["matched_terms"][:6],
                                })
                    counts["gaps"] += len(gap_updates)

                # Use full summaries section, not the truncated CSV field
                rich_limitation = _extract_summary_section(
                    summary,
                    ("Potential gaps or limitations", "What can be improved", "Open problems")
                ) or (new.get("Primary_Limitation", "") or "").strip()

                if rich_limitation and rich_limitation.lower() not in {"not reported", "nr", ""}:
                    # Only proceed if the limitation is specific enough (not just "small sample size")
                    generic_phrases = {
                        "small sample", "external validation needed", "single site",
                        "future work needed", "limited to", "not investigated"
                    }
                    is_generic = all(
                        phrase in rich_limitation.lower() for phrase in list(generic_phrases)[:2]
                    ) and len(rich_limitation.split()) < 20
                    
                    # DISABLED 2026-07-30: single-paper-limitation gap generation.
                    # Reformatting one paper's Limitations section into a "gap"
                    # (tier=derived, boilerplate closure) yields a study weakness,
                    # NOT a corpus-level gap. The operator rejected the whole class
                    # as false positives (G-CAND-319..335; ~311 accumulated). Same
                    # failure mode as the combinatorial generator disabled below.
                    # Real gaps come from the curated ACTOR Generator-Falsifier
                    # loop + _run_synthetic_gap_coverage; any strays are auto-rejected
                    # each run by _auto_reject_single_paper_limitation_gaps().
                    if False:
                        gap_statement = _derive_gap_statement_from_limitation(
                            pid, rich_limitation, new.get("Task_Type", ""), new.get("Modality", "")
                        )
                        candidate = {
                            "source_paper": pid,
                            "gap_type": "candidate",
                            "tier": "derived",
                            "gap_statement": gap_statement,
                            "papers_claiming_gap": [pid],
                            "evidence_for_open_status": f"Derived from {pid} limitations: {rich_limitation[:200]}",
                            "closure_criteria": "Requires follow-up validated study addressing this limitation",
                            "modality": new.get("Modality", ""),
                            "task_type": new.get("Task_Type", ""),
                        }
                        if _append_gap_candidate(gaps, candidate, conflicts, registry_ids, log_lines):
                            counts["gaps"] += 1

                # DEPRECATED per AGENTS.md ("Must never: emit combinatorial gaps
                # ... unless it includes a robust physiological or architectural
                # reason why"). This generator emitted bare "No published study
                # combines X with Y" pointers (max 3/paper) with no such reason,
                # which exploded to ~1950 records on a full re-analysis. Disabled;
                # the Generator-Falsifier loop + structural coverage sweep cover
                # real gaps, and _prune_noisy_combinatorial_gaps() removes the
                # legacy noise on each run.
                # counts["gaps"] += _run_combinatorial_gap_detection(new, all_rows, gaps, conflicts, registry_ids, log_lines)

                if "gap" in summary.lower() or "future work" in summary.lower() or "limitation" in summary.lower():
                    log_lines.append(f"{pid}: potential gap claim in limitations — review for new Gap node")

                # 4E: Invalidated assumptions
                with traced_span("agent4.subtask.invalidated", {"paper_id": pid}):
                    if summary:
                        invalidation_results = _check_invalidated_assumptions(new, summary, assumption_reg)
                        for result in invalidation_results:
                            paper_key = (pid or "").strip()
                            assumption_key = str(result.get("assumption_id") or "").strip()
                            if (paper_key, assumption_key) in existing_invalidated_keys:
                                log_lines.append(
                                    f"{pid}: invalidated duplicate skipped assumption={result.get('assumption_id')}"
                                )
                                continue
                            inv_record = {
                                "id": _next_id(invalidated, "IA-"),
                                "assumption_id": result.get("assumption_id"),
                                "prior_belief": result.get("prior_belief", ""),
                                "what_disproved_it": result.get("evidence_sentence", ""),
                                "paper": pid,
                                "assumption_type": result.get("assumption_type", ""),
                                "proposed_clinical_implication": result.get("proposed_clinical_implication", ""),
                                "status": "pending_human_review",
                            }
                            invalidated.append(inv_record)
                            existing_invalidated_keys.add((paper_key, assumption_key))
                            counts["invalidated"] += 1
                            log_lines.append(f"{pid}: potential invalidation of {result.get('assumption_id')} — G5 review needed")

        # CORPUS-WIDE SYNTHESIS GAPS — run after all papers processed
        with traced_span("agent4.corpus_synthesis_gaps"):
            synthesis_gaps = _detect_corpus_synthesis_gaps(all_rows, gaps, log_lines, genuine_gap_examples)
            counts["gaps"] += synthesis_gaps

        # Synthetic coverage sweep only on full runs (not targeted paper_filter runs)
        if not paper_filter:
            with traced_span("agent4.synthetic_gap_coverage"):
                counts["gaps"] += _run_synthetic_gap_coverage(all_rows, gaps, conflicts, registry_ids, log_lines)

        # Global paper ID validation for all gap records before save
        for g in gaps:
            _validate_gap_record_ids(g, registry_ids, log_lines)

        # Save all files
        conflicts_payload = _inject_records(conflicts_payload, "conflicts", conflicts)
        consensus_payload = _inject_records(consensus_payload, "verified_findings", consensus)
        cf_candidates_payload = _inject_records(cf_candidates_payload, "complementary_candidates", cf_candidates)

        confirmed_cf = [c for c in cf_candidates if c.get("status") == "confirmed"]
        existing_cf_pairs = {frozenset([c.get("paper_a"), c.get("paper_b")]) for c in complementary}
        for c in confirmed_cf:
            pair = frozenset([c.get("paper_a"), c.get("paper_b")])
            if pair not in existing_cf_pairs:
                # Normalise field names: candidates use paper_a_finding, confirmed use finding_a
                complementary.append({
                    "id": _next_id(complementary, "CF-"),
                    "paper_a": c.get("paper_a"),
                    "paper_b": c.get("paper_b"),
                    "finding_a": c.get("paper_a_finding", ""),
                    "finding_b": c.get("paper_b_finding", ""),
                    "combined_insight": c.get("combined_insight", ""),
                    "clinical_implication": c.get("clinical_implication", ""),
                })
                existing_cf_pairs.add(pair)
                # Mark as processed so it doesn't re-promote next run
                c["status"] = "promoted"
        complementary_payload = _inject_records(complementary_payload, "complementary_findings", complementary)
        gaps_payload = _inject_records(gaps_payload, "gaps", gaps)
        invalidated_payload = _inject_records(invalidated_payload, "invalidated_assumptions", invalidated)

        _refresh_conflicts_summary(conflicts_payload if isinstance(conflicts_payload, dict) else {}, conflicts)
        if isinstance(conflicts_payload, dict):
            # inflated_performance_flags is a CUMULATIVE corpus-wide QA list, but
            # suspect_accuracy_ids is computed over this run's window only. Assigning
            # it directly wiped the whole list whenever a batch happened to contain no
            # >0.97 paper - batch P-728..P-737 dropped 20 genuinely inflated papers
            # (P-1, P-119, P-175, ... ) to an empty list. Union with what is already
            # published so a clean batch adds nothing instead of erasing everything.
            _summary = conflicts_payload.setdefault("summary", {})
            _prior_flags = _summary.get("inflated_performance_flags") or []
            _summary["inflated_performance_flags"] = sorted(
                set(_prior_flags) | set(suspect_accuracy_ids)
            )
        _refresh_gaps_summary(gaps_payload if isinstance(gaps_payload, dict) else {}, gaps)

        _save_json(CONFLICTS, conflicts_payload)
        _save_json(CONSENSUS, consensus_payload)
        _save_json(COMPLEMENTARY_CANDS, cf_candidates_payload)
        _save_json(COMPLEMENTARY, complementary_payload)
        # Concurrency-safe 3-way merge (preserves concurrent G5 gap edits and
        # our own closure updates); replaces blind overwrite.
        _save_gaps_merge_safe(GAPS, "gaps", gaps, _gaps_base)
        # Concurrency-safe: reload + append only new IA records, never clobber
        # existing/edited ones (prevents the lost-update race that wiped G5 corrections).
        _save_json_append_safe(INVALIDATED, "invalidated_assumptions", invalidated)

        # Write log
        with open(REL_LOG, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"\n--- Run {datetime.utcnow().isoformat()} ---\n")
            f.write("\n".join(log_lines) + "\n")

        counts["processed"] = len(new_papers)
        log.info(f"Agent 4 complete. Results: {counts}")
        return counts