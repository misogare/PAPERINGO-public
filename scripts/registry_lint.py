#!/usr/bin/env python
"""Gate-G3 lint for data/full_paper_registry.csv rows - model-agnostic schema checker.

Usage:  python scripts/registry_lint.py <first_paper> <last_paper> [--json out.json] [--strict]

Validates each row against the registry schema (enums in agent3_extract.py and
config/extraction_template.json), against its own summary block (coded fields,
sample size, year, ADNI flag) and against the rest of the corpus (duplicate
rows). It runs the same whether the rows were written by agent3, by hand, or
by another model - the 2026-08-29 audit of P-868..P-1097 found 154 distinct
population_specificity values, 176 free-text task_substage values and 23
duplicate papers carried as full rows, none reported by the per-batch G3 notes.

HARD (exit 1 with --strict): code outside its enum (Task/Category/Validation/
Architecture/XAI/ADNI), row/block code disagreement, duplicate of another row,
missing row for an existing non-stub block. SOFT: out-of-vocabulary values in
the extended columns (reported with the alias they would canonicalise to),
Modality/Best_Metric prose, Sample_N disagreement, and Best_AUC provenance
(PAPERINGO-3l8u: a numeric Best_AUC task-labelled in its block only with the
opposite task language to the coded Task_Type - e.g. an "AD vs CN" figure on a
T2 conversion row).
"""
import argparse, collections, csv, io, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import agent3_extract as a3  # noqa: E402

REGISTRY = ROOT / "data" / "full_paper_registry.csv"
SUMMARIES = ROOT / "summaries.md"
NA = {"NA", "N/A", "not reported", "not_reported", "NR", ""}
XAI_OK = {"none", "SHAP", "Grad-CAM", "LIME", "attention-map", "occlusion", "decision-tree", "fuzzy-rules",
          "integrated-gradients", "permutation-importance", "layer-wise-relevance-propagation", "NA", "not reported"}
# How a block may spell the method the row tags (the mention check greps these, not the tag).
XAI_MENTIONS = {
    "Grad-CAM": r"grad-?cam|class activation|\bcam\b heat", "SHAP": r"\bshap\b|shapley",
    "LIME": r"\blime\b",
    # Hyphenated and "-visualisation" spellings are the common ones in the corpus
    # ("CBAM attention-map visualisation (Fig 3)"), and a space-only pattern missed them.
    "attention-map": r"attention[- ]?(?:maps?|heat-?maps?|weights?|scores?|visuali[sz]ation)|\bcbam\b|attention module",
    "occlusion": r"occlusion",
    "decision-tree": r"decision tree|\bcart\b", "fuzzy-rules": r"fuzzy", "integrated-gradients": r"integrated gradients?|captum",
    "permutation-importance": r"permutation importance|eli5|mean decrease",
    "layer-wise-relevance-propagation": r"layer-?wise relevance|\blrp\b|\brelevance propagation",
}
DOI_RX = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]]+")


def _agent3_skips(title, block):
    """True when Agent 3 itself declines to extract this block, so its absence from the
    registry is correct, not a violation. Mirror the extractor's own rule rather than a
    private guess: a hand-rolled '[stub' test flagged 15 legitimate blocks (duplicate
    stubs, a corrigendum, the ADNI Procedures Manual, a publisher brochure) as missing rows.
    Falls back to a header-marker test if agent3 cannot be imported."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from agent3_extract import _skip_reason
        return bool(_skip_reason(block))
    except Exception:
        return bool(re.search(r"(?i)\[stub|\[duplicate|not a research study|^corrigendum", title))


def load_rows():
    with open(REGISTRY, encoding="utf-8", newline="") as f:
        return {r["Paper_ID"]: r for r in csv.DictReader(f)}


def load_blocks():
    raw = SUMMARIES.read_bytes()
    hdr = re.compile(rb"^## Paper (\d+):([^\r\n]*)", re.M)
    pos = sorted([(int(m.group(1)), m.start(), m.group(2).decode("utf-8", "replace").strip()) for m in hdr.finditer(raw)], key=lambda t: t[1])
    out = {}
    for i, (pid, off, title) in enumerate(pos):
        end = pos[i + 1][1] if i + 1 < len(pos) else len(raw)
        out[pid] = (title, raw[off:end].decode("utf-8", "replace"))
    return out


def section(block, title):
    m = re.search(r"^###\s*" + re.escape(title) + r"\s*\n(.*?)(?=^###\s|\Z)", block, re.M | re.S)
    return m.group(1).strip() if m else ""


def head_code(block, title, rx):
    first = (section(block, title).splitlines() or [""])[0]
    m = re.search(rx, first)
    return m.group(0) if m else None


_MSUB_VOCAB_CACHE = None
_MSUB_ALIAS_CACHE = None

def _modality_rules():
    """The modality_subtype_rules section of config/extraction_normalisation_rules.json
    (added by the 2026-09-04 repair of the P-1048+ free-text drift), loaded once.
    Empty dict when absent, so the check degrades to a no-op rather than erroring."""
    global _MSUB_VOCAB_CACHE, _MSUB_ALIAS_CACHE
    if _MSUB_VOCAB_CACHE is None:
        cfg_path = ROOT / "config" / "extraction_normalisation_rules.json"
        try:
            rules = json.loads(cfg_path.read_text(encoding="utf-8"))
            msub = rules.get("modality_subtype_rules", {})
        except Exception:
            msub = {}
        _MSUB_VOCAB_CACHE = set(msub.get("canonical_vocabulary", []))
        _MSUB_ALIAS_CACHE = msub.get("aliases_applied_2026_09_04", {})
    return _MSUB_VOCAB_CACHE, _MSUB_ALIAS_CACHE

def _modality_vocab():
    return _modality_rules()[0]

def _modality_subtype_alias(value: str):
    """Canonical target when the value is a documented alias, else None."""
    return _modality_rules()[1].get(value)

# ── Best_AUC provenance (PAPERINGO-3l8u) ─────────────────────────────────────
# Ground truth: P-570/P-574 carried T1/detection-labelled AUCs on T2 conversion rows
# ("AUC 0.864 (MCI vs NC...)" / "AUC 0.688 ... AD from CN"), which manufactured a
# false conflict candidate in the 2026-09-04 Actor/Critic sweep. The deterministic
# signature: every block clause that pairs the row's Best_AUC value with an explicit
# task label uses the OPPOSITE task language to the coded Task_Type. Clauses without
# task language near the value are silent (most summaries never label the value);
# hybrid wording ("conversion-classification") counts for both. Soft severity: the
# signal depends on summary prose conventions, not schema enums.
_AUC_CONV_RX = re.compile(
    r"\bpMCI\b|\bsMCI\b|conversion|convert\w*|converters?\b|"
    r"\b(?:NC|CN|MCI|EMCI|LMCI|HC|SCD)\s*(?:to|-to-|\u2192)\s*(?:MCI|AD|dementia|EMCI|LMCI)\b|"
    r"progress\w* (?:to|at)|incident (?:alzheimer|dementia)|prognos\w*|"
    r"transition\w* (?:to|risk)|future (?:alzheimer|dementia)|development (?:of|among)|"
    r"follow-?up|predict\w* (?:future|conversion|progression|dementia)|"
    r"conversion model|reversion", re.I)
_AUC_VSC_RX = re.compile(
    r"\b(?:MCI|aMCI|naMCI|AD|dementia)\s*(?:vs\.?|versus|from|and)\s*"
    r"(?:HC|NC|CN|normal controls?|healthy controls?|controls?|cognitively normal)\b"
    r"|\b(?:HC|NC|CN|normal controls?|healthy controls?|controls?|cognitively normal)\s*(?:vs\.?|versus|from|and)\s*"
    r"(?:MCI|aMCI|naMCI|AD|dementia)\b"
    r"|\b(?:detection|discrimination|distinguish\w*|classification) (?:of|between|for) "
    r"(?:MCI|aMCI|AD)(?: patients?)? (?:and|from|versus) (?:healthy )?(?:controls?|HC|NC|CN)\b"
    r"|\b(?:MCI|AD)\s+ detection\b", re.I)
_AUC_HYBRID_RX = re.compile(r"conversion[- ]classif\w*|classif\w*[- ]conversion", re.I)
_AUC_NUM_RX = re.compile(r"\b(?:0?\.\d{2,4}|1\.00)\b")


def _auc_task_label(text):
    c, v, h = bool(_AUC_CONV_RX.search(text)), bool(_AUC_VSC_RX.search(text)), bool(_AUC_HYBRID_RX.search(text))
    if h or (c and v):
        return 'both'
    if c:
        return 'conv'
    if v:
        return 'vsc'
    return 'none'


def auc_provenance_flag(auc_cell, task, blk):
    """Soft finding when the row's Best_AUC is task-labelled in its block only with
    the opposite task language to the coded Task_Type; '' when clean or unlabelled."""
    m = re.match(r"^(0\.\d+|1(?:\.0+)?)$", auc_cell)
    if not m or task not in {"T1", "T2", "T3"}:
        return ""
    val = round(float(auc_cell), 3)
    labels = set()
    sample = ""
    for line in blk.splitlines():
        if not re.search(r"\bAUCs?\b", line, re.I):
            continue
        line = line.strip().lstrip("-").strip()
        for part in re.split(r"\s*[;|]\s*|\.\s+", line):
            if not re.search(r"\bAUCs?\b", part, re.I):
                continue
            for x in _AUC_NUM_RX.finditer(part):
                v = float(x.group(0))
                if not (0.5 <= v <= 1.0) or round(v, 3) != val:
                    continue
                # task language must sit within +-70 chars of the value occurrence
                win = part[max(0, x.start() - 70): x.end() + 70]
                lab = _auc_task_label(win)
                if lab != 'none':
                    labels.add(lab)
                    if not sample:
                        sample = win.strip()[:120]
    if not labels:
        return ""
    want_conv = task == "T2"
    if want_conv and labels <= {"vsc"}:
        return f"Best_AUC provenance: T2 row value {auc_cell} task-labelled only as " \
               f"patient-vs-control detection in block ({sample!r})"
    if not want_conv and labels <= {"conv"}:
        return f"Best_AUC provenance: {task} row value {auc_cell} task-labelled only as " \
               f"conversion in block ({sample!r})"
    return ""


def lint_row(pid, r, blocks, dup_index):
    key = f"P-{pid}"
    hard, soft = [], []
    title, blk = blocks.get(pid, ("", ""))
    stub = bool(re.search(r"(?i)\[stub|duplicate|supplement-only|not a research", title))

    def enum(col, ok, multi=False, hard_fail=True):
        v = (r.get(col) or "").strip()
        vals = [x.strip() for x in v.split(",")] if multi else [v]
        for x in vals:
            if x not in ok and x not in NA:
                (hard if hard_fail else soft).append(f"{col} outside enum: {x[:40]!r}")

    enum("Task_Type", {f"T{i}" for i in range(1, 7)})
    enum("Category", {f"C{i}" for i in range(1, 7)}, multi=True)
    enum("Validation_Type", {f"V{i}" for i in range(1, 5)})
    enum("Architecture_Family", {f"A{i}" for i in range(1, 9)})
    enum("XAI_Method", XAI_OK)
    v = (r.get("ADNI_Dependent") or "").strip().lower()
    if v not in {"true", "false", "na", "not reported", ""}:
        hard.append(f"ADNI_Dependent outside enum: {v!r}")
    for col, valid, canon in (("study_design_type", a3.VALID_STUDY_DESIGN, a3._canon_design),
                              ("primary_metric_type", a3.VALID_METRIC_TYPE, a3._canon_metric_type)):
        v = (r.get(col) or "").strip()
        if v and v not in valid and v not in NA:
            c = canon(v)
            soft.append(f"{col} out of vocabulary: {v[:45]!r}" + (f" -> canonical {c}" if c in valid else " (no alias)"))
    v = (r.get("population_specificity") or "").strip()
    if v and v not in a3.VALID_POPULATION:
        soft.append(f"population_specificity out of vocabulary: {v[:45]!r}")
    v = (r.get("task_substage") or "").strip()
    if v and v not in a3.VALID_TASK_SUBSTAGE:
        soft.append(f"task_substage out of vocabulary: {v[:45]!r}")
    v = (r.get("modality_subtype") or "").strip()
    if v and v not in NA and v not in _modality_vocab():
        c = _modality_subtype_alias(v)
        soft.append(f"modality_subtype out of vocabulary: {v[:45]!r}" + (f" -> canonical {c}" if c else " (no alias)"))
    if len(r.get("Modality") or "") > 80:
        soft.append(f"Modality prose ({len(r.get('Modality'))} chars)")
    if re.search(r"\*\*|codebook|assignment", r.get("Modality") or "", re.I):
        soft.append("Modality holds justification text")
    bm = (r.get("Best_Metric") or "").strip()
    if len(bm) > 200:
        soft.append(f"Best_Metric prose ({len(bm)} chars)")
    auc = (r.get("Best_AUC") or "").strip()
    if auc and auc not in NA and not re.match(r"^0?\.\d+$|^1(?:\.0+)?$|^0\.\d+\s*[-/]\s*0\.\d+", auc):
        soft.append(f"Best_AUC not a bare number: {auc[:30]!r}")
    if blk and not stub and auc and auc not in NA:
        prov = auc_provenance_flag(auc, (r.get("Task_Type") or "").strip(), blk)
        if prov:
            soft.append(prov)
    if len(r.get("Journal") or "") > 120:
        soft.append(f"Journal prose ({len(r.get('Journal'))} chars)")
    if blk and not stub:
        for col, sec, rx in (("Task_Type", "Task Type", r"T[1-6]"), ("Validation_Type", "Validation Type", r"V[1-4]"),
                             ("Architecture_Family", "Architecture/Method Family", r"A[1-8]"), ("Category", "Modality (L2)", r"C[1-6]")):
            code = head_code(blk, sec, rx)
            rv = (r.get(col) or "").strip()
            if code and rv not in NA and code not in rv:
                hard.append(f"{col} {rv!r} disagrees with block declaration {code}")
        n_line = re.sub(r"^[*_\s]*assigned\s*[:=]\s*", "", (section(blk, "What is the sample size?").splitlines() or [""])[0], flags=re.I)
        rn = (r.get("Sample_N_Approx") or "").strip()
        nb = re.findall(r"\d[\d,]*", n_line); nr = re.findall(r"\d[\d,]*", rn)
        if nb and nr and nb[0].replace(",", "") != nr[0].replace(",", ""):
            soft.append(f"Sample_N {rn[:30]!r} vs block {n_line[:40]!r}")
        yb = head_code(blk, "Publication year", r"(?:19|20)\d{2}"); yr = (r.get("Publication_Year") or "").strip()
        if yb and yr and yb != yr[:4]:
            hard.append(f"Publication_Year {yr!r} disagrees with block {yb}")
        ab = head_code(blk, "ADNI Dependent", r"(?i)true|false|yes|no"); ar = (r.get("ADNI_Dependent") or "").strip().lower()
        if ab and ar in {"true", "false"} and (ab.lower() in {"true", "yes"}) != (ar == "true"):
            hard.append(f"ADNI_Dependent {ar} disagrees with block {ab}")
        xr = (r.get("XAI_Method") or "").strip()
        if xr not in {"none", "NA", "", "not reported"} and not re.search(XAI_MENTIONS.get(xr, re.escape(xr.split("-")[0])), blk, re.I):
            hard.append(f"XAI_Method {xr!r} never mentioned in the block")
    d = dup_index.get(pid)
    if d and not stub:
        hard.append(f"DUPLICATE of P-{d[0]} ({d[1]})")
    return {"pid": pid, "hard": hard, "soft": soft}


def build_dup_index(blocks):
    """pid -> (original pid, reason) for later blocks sharing a DOI or a title with an earlier one."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    seen_t, seen_d, dup = {}, {}, {}
    for pid in sorted(blocks):
        title, blk = blocks[pid]
        if re.search(r"(?i)\[stub|duplicate", title):
            continue
        nt = norm(title)
        m = DOI_RX.search(section(blk, "DOI or URL"))
        d = m.group(0).lower().rstrip(".") if m else None
        if d and d in seen_d:
            dup[pid] = (seen_d[d], "same DOI")
        elif len(nt) > 25 and nt in seen_t:
            dup[pid] = (seen_t[nt], "same title")
        seen_t.setdefault(nt, pid)
        if d:
            seen_d.setdefault(d, pid)
    return dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("first", type=int); ap.add_argument("last", type=int)
    ap.add_argument("--json"); ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    rows = load_rows(); blocks = load_blocks(); dup_index = build_dup_index(blocks)
    results, missing = [], []
    for pid in range(a.first, a.last + 1):
        key = f"P-{pid}"
        if key in rows:
            results.append(lint_row(pid, rows[key], blocks, dup_index))
        elif pid in blocks and not _agent3_skips(*blocks[pid]):
            missing.append(pid)
    hard_total = sum(len(r["hard"]) for r in results) + len(missing)
    print(f"registry_lint P-{a.first}..P-{a.last}: {len(results)} rows, {sum(1 for r in results if not r['hard'])} without hard violations; "
          f"blocks without a row: {missing or 'none'}")
    tally = collections.Counter()
    for r in results:
        for h in r["hard"]:
            tally["HARD " + re.sub(r"[:(].*", "", h).strip()] += 1
        for s in r["soft"]:
            tally["soft " + re.sub(r"[:(\d].*", "", s).strip()] += 1
    for k, v in tally.most_common():
        print(f"  {v:4d}  {k}")
    vocab = collections.defaultdict(collections.Counter)
    for r in results:
        row = rows[f"P-{r['pid']}"]
        for col in ("study_design_type", "primary_metric_type", "population_specificity", "task_substage"):
            vocab[col][(row.get(col) or "").strip()] += 1
    for col, c in vocab.items():
        print(f"  vocabulary {col}: {len(c)} distinct values in range")
    for r in results:
        if r["hard"]:
            print(f"  P-{r['pid']}: " + " | ".join(r["hard"])[:300])
    if a.json:
        Path(a.json).write_text(json.dumps({"results": results, "missing_rows": missing}, indent=1), encoding="utf-8")
    if a.strict and hard_total:
        print(f"FAIL: {hard_total} hard violations")
        sys.exit(1)
    print("OK" if not hard_total else f"{hard_total} hard violations (advisory mode)")


if __name__ == "__main__":
    main()
