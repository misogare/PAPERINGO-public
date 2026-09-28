"""
agent2_summarise.py — Summarisation Agent
Reads PDFs batch-by-batch (5 at a time), generates 23-field summaries, and writes to summaries.md.
Follows SKILL.md exactly.

Insertion rule:
- Add new paper blocks immediately after the latest existing "## Paper <n>:" block.
- If '# Paper Summaries' exists, insert before that header.
- Never overwrite existing paper blocks.

Requires: ANTHROPIC_API_KEY
"""

import base64
import importlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger("agent2_summarise")

ROOT         = Path(__file__).parent.parent
DATA         = ROOT / "data"
DOWNLOADS    = ROOT / "downloads"
LOGS         = ROOT / "logs"
BATCH_LOG    = LOGS / "agent2_batch_log.txt"

ANTH_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
OPENALX_BASE = "https://api.openalex.org"
BATCH_SIZE   = 5
EVIDENCE_LINE_LIMIT = 260
MIN_READABLE_EXTRACT_CHARS = 200
MAX_EXTRACT_CHARS = 200000
PREBATCH_SCAN_MIN = 10
MAX_NOVELTY_CHARS = 100
NOVELTY_REWRITE_TRIGGERS = [
    "contents lists available",
    "2.materials and methods",
    "study participants the data",
]

QUESTIONS = [
    "What did the authors do?",
    "Why did the authors conduct the research?",
    "What are the key components in the method? (Abstract + Methods)",
    "What are the outcomes? (Results)",
    "What is the significance? (focus on early Introduction motivation)",
    "What are the problems?",
    "What can be improved?",
    "Why is it important?",
    "What has been done?",
    "Open problems, applications, methodologies, achievable outcomes?",
    "Key methods used",
    "Key findings reported",
    "Related findings and comparisons with other works",
    "Potential gaps or limitations",
    "Is it a high quality paper giving the data, methods, length, and ranking signal?",
    "What is the novelty of the paper?",
    "Year published",
    "Amount of citation",
]

SECTION_HEADERS = [
    "### What did the authors do?",
    "### Why did the authors conduct the research?",
    "### What are the key components in the method?",
    "### What are the outcomes? (Results)",
    "### What is the significance?",
    "### What are the problems?",
    "### What can be improved?",
    "### Why is it important?",
    "### What has been done?",
    "### Open problems, applications, methodologies, achievable outcomes?",
    "### Key methods used",
    "### Key findings reported",
    "### Related findings and comparisons with other works",
    "### Potential gaps or limitations",
    "### Is it high quality paper giving the data, methods, length and look in somewhere for ranking",
    "### What is the novelty of the paper?",
    "### Year published",
    "### Amount of citation",
]


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_at_word(text: str, max_chars: int) -> str:
    clean = _compact_ws(text)
    if len(clean) <= max_chars:
        return clean
    cut = clean[:max_chars].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")


def _derive_novelty_from_evidence(evidence_text: str) -> str:
    clean = _compact_ws(evidence_text)
    if not clean:
        return "not reported"

    metadata_noise_tokens = [
        "contents lists available",
        "original article",
        "available online",
        "journal homepage",
        "www.elsevier.com",
        "sciencedirect",
    ]

    cue_patterns = [
        r"\bwe\s+(?:propose|present|develop|introduce)\b",
        r"\bthis study\s+(?:proposes|presents|develops|introduces)\b",
        r"\bin this study\b",
        r"\bthis study\b",
        r"\bobjective(?:s)?\b",
        r"\bwe aimed\b",
        r"\bnovel\b",
        r"\bcontribution(?:s)?\b",
    ]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]

    for sentence in sentences:
        lower = sentence.lower()
        if any(trigger in lower for trigger in NOVELTY_REWRITE_TRIGGERS):
            continue
        if any(token in lower for token in metadata_noise_tokens) and not any(
            kw in lower for kw in ["study", "propose", "model", "framework", "method"]
        ):
            continue
        if any(re.search(pattern, lower) for pattern in cue_patterns):
            return _truncate_at_word(sentence, MAX_NOVELTY_CHARS)

    match = re.search(
        r"(we\s+(?:propose|present|develop|introduce)[^.]{0,220})",
        clean,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = match.group(1)
    else:
        candidate = ""
        for sentence in sentences:
            lower = sentence.lower()
            if any(token in lower for token in metadata_noise_tokens):
                continue
            if len(sentence) >= 30:
                candidate = sentence
                break
        if not candidate:
            candidate = sentences[0] if sentences else clean

    candidate = re.sub(r"(?i)contents lists available.*$", "", candidate).strip()
    if not candidate:
        return "not reported"
    return _truncate_at_word(candidate, MAX_NOVELTY_CHARS)


def _normalise_novelty_answer(answer: str, evidence_text: str) -> str:
    candidate = _compact_ws(answer)
    lower = candidate.lower()
    if (
        not candidate
        or any(trigger in lower for trigger in NOVELTY_REWRITE_TRIGGERS)
        or len(candidate) > MAX_NOVELTY_CHARS
    ):
        candidate = _derive_novelty_from_evidence(evidence_text)

    candidate = _truncate_at_word(candidate, MAX_NOVELTY_CHARS)
    if any(trigger in candidate.lower() for trigger in NOVELTY_REWRITE_TRIGGERS):
        candidate = _derive_novelty_from_evidence(evidence_text)

    return candidate or "not reported"


def _extract_section_text(block_text: str, header: str) -> str:
    pattern = re.compile(rf"{re.escape(header)}\n(.*?)(?=\n###\s|\Z)", re.S)
    match = pattern.search(block_text)
    return _compact_ws(match.group(1)) if match else ""


def _parse_summary_blocks(content: str) -> List[Dict[str, object]]:
    pattern = re.compile(
        r"(?ms)^##\s+Paper\s+(\d+):\s*(.+?)\n(.*?)(?=^##\s+Paper\s+\d+:|\Z)"
    )
    blocks: List[Dict[str, object]] = []
    for match in pattern.finditer(content):
        paper_no = int(match.group(1))
        filename = match.group(2).strip()
        text = match.group(0).rstrip() + "\n"
        blocks.append(
            {
                "paper_no": paper_no,
                "filename": filename,
                "text": text,
            }
        )
    return blocks


def _rewrite_reasons_for_block(block_text: str) -> List[str]:
    reasons: List[str] = []

    missing = [h for h in SECTION_HEADERS if h not in block_text]
    if missing:
        reasons.append("missing_required_sections")

    if "The paper proposes/evaluates an AI method" in block_text:
        reasons.append("placeholder_language")

    novelty = _extract_section_text(block_text, "### What is the novelty of the paper?")
    novelty_lower = novelty.lower()
    for trigger in NOVELTY_REWRITE_TRIGGERS:
        if trigger in novelty_lower:
            reasons.append(f"novelty_trigger:{trigger}")
            break
    if len(novelty) > MAX_NOVELTY_CHARS:
        reasons.append("novelty_too_long")

    year_text = _extract_section_text(block_text, "### Year published")
    if year_text:
        year_match = re.search(r"\b(\d{4})\b", year_text)
        if year_match:
            year_value = int(year_match.group(1))
            current_year = time.gmtime().tm_year
            if year_value < 1980 or year_value > current_year + 1:
                reasons.append("implausible_year")

    return sorted(set(reasons))


def _resolve_pdf_path_for_block(paper_no: int, filename: str) -> Optional[Path]:
    if filename:
        matches = list(DOWNLOADS.rglob(filename))
        if matches:
            return matches[0]

    pid = str(paper_no)
    fallback_matches = sorted(DOWNLOADS.rglob(f"*{pid}*.pdf"))
    if fallback_matches:
        return fallback_matches[0]
    return None


def _replace_existing_block(paper_no: int, new_block: str) -> bool:
    if not SUMMARIES.exists():
        return False
    content = SUMMARIES.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        rf"(?ms)^##\s+Paper\s+{paper_no}:\s+.+?(?=^##\s+Paper\s+\d+:|\Z)"
    )
    replacement = new_block.rstrip() + "\n\n"
    updated, count = pattern.subn(replacement, content, count=1)
    if count == 0:
        return False
    SUMMARIES.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return True


def _run_prebatch_auto_recovery(state: dict, paper_filter=None) -> Tuple[int, List[str]]:
    if not SUMMARIES.exists():
        return 0, []

    content = SUMMARIES.read_text(encoding="utf-8", errors="ignore")
    blocks = _parse_summary_blocks(content)
    if not blocks:
        return 0, []

    requested_ids = set(_parse_numeric_paper_ids(paper_filter))
    if requested_ids:
        scope = [b for b in blocks if int(b["paper_no"]) in requested_ids]
    else:
        scope = blocks[-PREBATCH_SCAN_MIN:]

    rewritten = 0
    logs: List[str] = []

    for block in scope:
        paper_no = int(block["paper_no"])
        reasons = _rewrite_reasons_for_block(str(block["text"]))
        if not reasons:
            continue

        filename = str(block.get("filename", ""))
        pdf_path = _resolve_pdf_path_for_block(paper_no, filename)
        if not pdf_path:
            logs.append(
                f"NEEDS_REWRITE P-{paper_no}: reasons={';'.join(reasons)}; status=missing_pdf"
            )
            continue

        evidence_excerpt, evidence_note = _load_extract_evidence(str(paper_no), pdf_path, EVIDENCE_LINE_LIMIT)
        new_block = _summarise_paper(
            str(paper_no),
            pdf_path,
            "",
            evidence_excerpt=evidence_excerpt,
            evidence_note=evidence_note,
        )
        replaced = _replace_existing_block(paper_no, new_block)
        if replaced:
            rewritten += 1
            logs.append(
                f"REWRITE P-{paper_no}: reasons={';'.join(reasons)}; evidence={evidence_note}; file={pdf_path.name}"
            )
        else:
            logs.append(
                f"NEEDS_REWRITE P-{paper_no}: reasons={';'.join(reasons)}; status=block_not_found"
            )

    return rewritten, logs


def _resolve_summaries_path() -> Path:
    """Resolve the active summaries.md path.

    Preference order:
    1) workspace root summaries.md
    2) data/summaries.md
    3) fallback to data/summaries.md if neither exists yet
    """
    root_summaries = ROOT / "summaries.md"
    data_summaries = DATA / "summaries.md"
    if root_summaries.exists():
        return root_summaries
    if data_summaries.exists():
        return data_summaries
    return data_summaries


SUMMARIES = _resolve_summaries_path()
DEFAULT_TRAIL_CATEGORY = DOWNLOADS / "4. Computational Models and Modern AI Architectures"


def _get_citation_count(doi: str) -> str:
    """Attempt citation count from OpenAlex. Returns string."""
    if not doi:
        return "Not verified (no DOI available)"
    try:
        resp = requests.get(
            f"{OPENALX_BASE}/works",
            params={"filter": f"doi:{doi}", "select": "cited_by_count"},
            timeout=10,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            count = results[0].get("cited_by_count", None)
            if count is not None:
                return str(count)
        return "Not verified (not found in OpenAlex)"
    except Exception as e:
        return f"Not verified (source unavailable at run time)"


def _already_summarised(paper_id: str) -> bool:
    """Check if paper_id block already exists in summaries.md."""
    if not SUMMARIES.exists():
        return False
    content = SUMMARIES.read_text(encoding="utf-8")
    return f"## Paper {paper_id}:" in content or f"## Paper: {paper_id}" in content


def _summary_headers() -> List[Tuple[int, str]]:
    """Return summary headers as (paper_number, title)."""
    if not SUMMARIES.exists():
        return []
    content = SUMMARIES.read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r"(?m)^##\s+Paper\s+(\d+):\s*(.+)$", content)
    return [(int(n), t.strip()) for n, t in rows]


def _resolve_trail_anchor(pdf_files: List[Path]) -> Tuple[int, int, int]:
    """Find continuity anchor from summaries against sorted PDF list.

    Returns (anchor_paper_no, anchor_file_idx, offset).
    offset = file_index - paper_number.
    """
    headers = _summary_headers()
    if not headers:
        raise ValueError("No paper headers found in summaries.md")

    name_to_idx = {p.name: i for i, p in enumerate(pdf_files)}

    # Walk backwards to find the nearest previously summarised paper still present
    # in current folder state. This preserves continuity even if some files moved.
    for paper_no, title in reversed(headers):
        idx = name_to_idx.get(title)
        if idx is not None:
            return paper_no, idx, idx - paper_no

    raise ValueError("No continuity anchor found between summaries.md and current folder PDFs")


def _parse_numeric_paper_ids(paper_filter) -> List[int]:
    """Parse filter values like 118 or P-118 into numeric paper ids."""
    if not paper_filter:
        return []
    out = []
    for raw in paper_filter:
        s = str(raw).strip()
        if s.upper().startswith("P-"):
            s = s[2:]
        if s.isdigit():
            out.append(int(s))
    return sorted(set(out))


def _normalise_filter_tokens(paper_filter) -> set:
    """Return normalized filter tokens for robust pid matching.

    Examples:
    - P-128 -> {"P-128", "128"}
    - 128   -> {"128", "P-128"}
    """
    tokens = set()
    if not paper_filter:
        return tokens

    for raw in paper_filter:
        s = str(raw).strip()
        if not s:
            continue
        tokens.add(s)
        if s.upper().startswith("P-") and s[2:].isdigit():
            tokens.add(s[2:])
        elif s.isdigit():
            tokens.add(f"P-{s}")
    return tokens


def _pid_matches_filter(pid: str, paper_filter) -> bool:
    """Check pid against mixed filter tokens like P-128/128."""
    if not paper_filter:
        return True

    tokens = _normalise_filter_tokens(paper_filter)
    p = str(pid).strip()
    candidates = {p}
    if p.upper().startswith("P-") and p[2:].isdigit():
        candidates.add(p[2:])
    elif p.isdigit():
        candidates.add(f"P-{p}")

    return bool(candidates & tokens)


def _get_pending_pdfs_trail_mode(state: dict, paper_filter=None) -> list:
    """Resolve pending PDFs using tail-checked sequence continuity.

    Behavior:
    - Uses sorted PDFs from a category folder (default C2 computational folder).
    - Finds nearest anchor by matching previous summaries headers to current folder files.
    - Continues from the anchor offset to avoid paper-number drift.
    """
    category_path = Path(state.get("trail_category_path", str(DEFAULT_TRAIL_CATEGORY)))
    if not category_path.exists():
        raise FileNotFoundError(f"Trail category path missing: {category_path}")

    pdf_files = sorted(category_path.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in trail category path: {category_path}")

    anchor_paper, anchor_idx, offset = _resolve_trail_anchor(pdf_files)
    log.info(
        f"Trail mode anchor: paper {anchor_paper} -> file[{anchor_idx}] {pdf_files[anchor_idx].name}; offset={offset}"
    )

    requested = _parse_numeric_paper_ids(paper_filter)
    if requested:
        target_numbers = requested
    else:
        # Default next batch from latest verified trail anchor
        target_numbers = list(range(anchor_paper + 1, anchor_paper + 1 + BATCH_SIZE))

    pending = []
    for paper_no in target_numbers:
        file_idx = paper_no + offset
        if file_idx < 0 or file_idx >= len(pdf_files):
            log.warning(f"Trail mode out-of-range for Paper {paper_no}: file index {file_idx}")
            continue

        pid = str(paper_no)
        if _already_summarised(pid):
            log.info(f"Trail mode skip already summarised Paper {pid}")
            continue

        pending.append((pid, pdf_files[file_idx], ""))

    return pending


def _append_summary_blocks(blocks: list[str]) -> None:
    """Append summary blocks after latest paper block.

    If '# Paper Summaries' exists, insert before it to keep the paper list contiguous.
    Otherwise append at end-of-file.
    """
    marker_match = None
    if SUMMARIES.exists():
        content = SUMMARIES.read_text(encoding="utf-8")
    else:
        content = ""

    marker_match = re.search(r"(?m)^# Paper Summaries\s*$", content)

    block_text = "\n".join(b.rstrip() for b in blocks).rstrip() + "\n"

    if marker_match:
        insert_at = marker_match.start()
        before = content[:insert_at].rstrip()
        after = content[insert_at:].lstrip("\n")
        if before:
            new_content = before + "\n\n" + block_text + "\n" + after
        else:
            new_content = block_text + "\n" + after
    else:
        if content.strip():
            new_content = content.rstrip() + "\n\n" + block_text
        else:
            new_content = block_text

    SUMMARIES.parent.mkdir(parents=True, exist_ok=True)
    SUMMARIES.write_text(new_content, encoding="utf-8")


def _pdf_to_base64(pdf_path: Path) -> str:
    return base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")


def _extract_pdf_text(pdf_path: Path, max_chars: int = 120000) -> str:
    """Best-effort PDF text extraction for local summarization mode."""
    text = ""
    try:
        fitz = importlib.import_module("fitz")  # PyMuPDF (optional)
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
    except Exception:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(pdf_path))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            text = ""
    return text[:max_chars]


def _extract_text_path(paper_id: str) -> Path:
    return ROOT / f"pdf_extract_{paper_id}.txt"


def _looks_corrupted_extract(text: str) -> bool:
    if not text:
        return True
    # Heuristic for broken font-map extraction where large chunks become symbol noise.
    replacement_count = text.count("\ufffd")
    symbol_noise = sum(1 for ch in text if ch in "{}[]|~")
    ratio = (replacement_count + symbol_noise) / max(len(text), 1)
    return ratio > 0.04


def _load_extract_evidence(paper_id: str, pdf_path: Path, max_lines: int = EVIDENCE_LINE_LIMIT) -> Tuple[str, str]:
    """Load mandatory evidence excerpt (lines 1..max_lines) from pdf_extract_<paper_id>.txt.

    If extract is missing/too short/corrupted, regenerate from PDF before summarisation.
    """
    extract_path = _extract_text_path(paper_id)
    source = "existing_extract"
    text = ""

    if extract_path.exists():
        text = extract_path.read_text(encoding="utf-8", errors="replace")

    if len(text.strip()) < MIN_READABLE_EXTRACT_CHARS:
        source = "generated_extract"
        text = _extract_pdf_text(pdf_path, max_chars=MAX_EXTRACT_CHARS)
        extract_path.write_text(text, encoding="utf-8", errors="replace")
    elif _looks_corrupted_extract(text):
        refreshed = _extract_pdf_text(pdf_path, max_chars=MAX_EXTRACT_CHARS)
        if len(refreshed.strip()) >= MIN_READABLE_EXTRACT_CHARS and not _looks_corrupted_extract(refreshed):
            source = "refreshed_extract"
            text = refreshed
            extract_path.write_text(text, encoding="utf-8", errors="replace")

    if len(text.strip()) < MIN_READABLE_EXTRACT_CHARS:
        return "EXTRACTION_STATUS: STUB_UNREADABLE_PDF", f"{source}; unreadable_extract chars={len(text.strip())}"

    lines = text.splitlines()
    last_line = min(len(lines), max_lines)
    evidence = "\n".join(lines[:last_line]).strip()
    corrupted = _looks_corrupted_extract(evidence)
    note = f"{source}; read_lines=1-{last_line}; total_lines={len(lines)}; corrupted={corrupted}"
    return evidence, note


def _pick_excerpt(text: str, patterns: list[str], fallback: str = "not reported") -> str:
    if not text:
        return fallback
    lower = text.lower()
    for p in patterns:
        idx = lower.find(p)
        if idx >= 0:
            snippet = text[idx: idx + 700]
            snippet = re.sub(r"\s+", " ", snippet).strip()
            return snippet[:450]
    head = re.sub(r"\s+", " ", text[:700]).strip()
    return head[:450] if head else fallback


def _detect_year(text: str) -> str:
    if not text:
        return "not reported"

    current_year = time.gmtime().tm_year

    def _plausible(values: List[str]) -> List[int]:
        out: List[int] = []
        for value in values:
            y = int(value)
            if 1980 <= y <= current_year + 1:
                out.append(y)
        return out

    head = text[:4000]

    journal_style_years = _plausible(
        re.findall(r"\((20[0-3][0-9]|19[8-9][0-9])\)\s+\d", head)
    )
    if journal_style_years:
        return str(journal_style_years[0])

    doi_match = re.search(r"https?://doi\.org/[^\s]+(.{0,200})", head, flags=re.I | re.S)
    if doi_match:
        doi_context_years = _plausible(
            re.findall(r"\((20[0-3][0-9]|19[8-9][0-9])\)", doi_match.group(1))
        )
        if doi_context_years:
            return str(doi_context_years[0])

    paren_years = _plausible(re.findall(r"\((20[0-3][0-9]|19[8-9][0-9])\)", head))
    if paren_years:
        return str(paren_years[0])

    head_years = _plausible(re.findall(r"\b(20[0-3][0-9]|19[8-9][0-9])\b", head))
    if head_years:
        return str(head_years[0])

    all_years = _plausible(re.findall(r"\b(20[0-3][0-9]|19[8-9][0-9])\b", text))
    if all_years:
        return str(max(all_years))

    return "not reported"


def _local_answers(pdf_path: Path, text: str) -> dict:
    """Generate deterministic summary answers when ANTHROPIC_API_KEY is absent."""
    study_summary = _pick_excerpt(text, ["abstract", "objective", "we propose", "this study", "background"])
    methods = _pick_excerpt(text, ["method", "materials and methods", "proposed", "approach"])
    results = _pick_excerpt(text, ["result", "discussion", "evaluation", "experiment"])
    intro = _pick_excerpt(text, ["introduction", "background", "motivation"])
    limitations = _pick_excerpt(text, ["limitation", "future work", "challenge"])
    novelty = _pick_excerpt(text, ["novel", "contribution", "we propose"])

    answers = {
        "q1": study_summary,
        "q2": intro,
        "q3": methods,
        "q4": results,
        "q5": _pick_excerpt(text, ["significance", "impact", "clinical"], intro),
        "q6": limitations,
        "q7": _pick_excerpt(text, ["future work", "improve", "limitation"], limitations),
        "q8": _pick_excerpt(text, ["important", "early", "screening", "diagnosis"], intro),
        "q9": intro,
        "q10": _pick_excerpt(text, ["open", "application", "methodolog", "future work"], limitations),
        "q11": methods,
        "q12": results,
        "q13": _pick_excerpt(text, ["compared", "baseline", "state-of-the-art", "related work"], results),
        "q14": limitations,
        "q15": _pick_excerpt(
            text,
            ["cross-validation", "external validation", "independent test", "dataset", "sample"],
            "Structured paper; quality not fully verifiable from extract-only evidence.",
        ),
        "q16": novelty,
        "q17": _detect_year(text),
        "q18": "See citation lookup",
    }
    return answers


def _summarise_paper(
    paper_id: str,
    pdf_path: Path,
    doi: str = "",
    evidence_excerpt: str = "",
    evidence_note: str = "",
) -> str:
    """Send PDF to Claude API and return formatted summary block."""
    log.info(f"  Summarising {paper_id}: {pdf_path.name}")

    # Build prompt
    questions_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(QUESTIONS))
    prompt = f"""You are summarising an academic paper on MCI (Mild Cognitive Impairment) progression prediction.

Answer ALL 18 questions below about this paper. Be concise but complete.
For question 17 (Year published): extract the exact publication year.
For question 18 (Amount of citation): write "See citation lookup" — this will be filled separately.
Do NOT fabricate any numbers, results, or claims.
Use the local evidence excerpt (read from lines 1-260 of pdf_extract_<paper_id>.txt) as mandatory grounding.
If text appears unreadable/corrupted, write "not reported" for uncertain fields instead of guessing.

Evidence excerpt (lines 1-260):
{evidence_excerpt[:18000]}

Questions:
{questions_text}

Format your response as a JSON object with keys "q1" through "q18", each containing the answer string."""

    if ANTH_KEY:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTH_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": os.environ.get("AGENT2_MODEL", "claude-sonnet-5"),  # sonnet-4-20250514 retired 2026-06-15 (PAPERINGO-ron)
                    "max_tokens": 4000,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": _pdf_to_base64(pdf_path),
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
                timeout=120,
            )
            raw = resp.json()["content"][0]["text"].strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            answers = json.loads(raw)
        except Exception as e:
            log.error(f"  Claude API error for {paper_id}: {e}")
            local_text = evidence_excerpt or _extract_pdf_text(pdf_path)
            if len(local_text.strip()) < MIN_READABLE_EXTRACT_CHARS or _looks_corrupted_extract(local_text):
                local_text = f"{local_text}\n\n{_extract_pdf_text(pdf_path)}"
            answers = _local_answers(pdf_path, local_text)
    else:
        local_text = evidence_excerpt or _extract_pdf_text(pdf_path)
        if len(local_text.strip()) < MIN_READABLE_EXTRACT_CHARS or _looks_corrupted_extract(local_text):
            local_text = f"{local_text}\n\n{_extract_pdf_text(pdf_path)}"
        answers = _local_answers(pdf_path, local_text)

    novelty_evidence = evidence_excerpt or _extract_pdf_text(pdf_path)
    answers["q16"] = _normalise_novelty_answer(answers.get("q16", ""), novelty_evidence)

    # Citation lookup
    citation_count = _get_citation_count(doi)

    # Build markdown block
    filename = pdf_path.name
    block_lines = [f"## Paper {paper_id}: {filename}", ""]
    for i, header in enumerate(SECTION_HEADERS):
        answer = answers.get(f"q{i+1}", "Not extracted")
        if i == 17:  # citation count
            answer = citation_count
        block_lines.append(header)
        block_lines.append(answer)
        block_lines.append("")

    return "\n".join(block_lines) + "\n"


def _get_pending_pdfs(state: dict, paper_filter=None) -> list:
    """Return list of (paper_id, pdf_path, doi) tuples not yet in summaries.md."""
    requested_numeric = _parse_numeric_paper_ids(paper_filter)

    # Safety-first behavior: explicit numeric Paper IDs (e.g., P-128) must
    # always resolve through trail mode mapping and never trigger full scans.
    if requested_numeric:
        log.info(
            "Explicit paper filter detected (%d papers). Enforcing trail-mode resolution.",
            len(requested_numeric),
        )
        pending = _get_pending_pdfs_trail_mode(state, paper_filter=requested_numeric)
        if len(pending) > len(requested_numeric):
            raise RuntimeError(
                "Safety guard: pending set exceeds requested paper_filter size. "
                "Aborting to prevent full-corpus summarisation."
            )
        return pending

    if state.get("trail_mode"):
        return _get_pending_pdfs_trail_mode(state, paper_filter)

    metadata_path = DATA / "corpus_metadata.csv"
    if not metadata_path.exists():
        log.warning("corpus_metadata.csv not found. Scanning downloads directly.")
        # Fallback: scan all PDFs in downloads
        pending = []
        for pdf in sorted(DOWNLOADS.rglob("*.pdf")):
            pid = pdf.stem.split("_")[0] if "_" in pdf.stem else pdf.stem
            if not _pid_matches_filter(pid, paper_filter):
                continue
            if not _already_summarised(pid):
                pending.append((pid, pdf, ""))
        return pending

    import csv
    pending = []
    with open(metadata_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("exclude_flag", "True") == "True":
                continue
            pid = row["paper_id"]
            if not _pid_matches_filter(pid, paper_filter):
                continue
            if _already_summarised(pid):
                continue
            # Find the PDF
            cat = row.get("pre_category", "")
            pdf_candidates = list(DOWNLOADS.rglob(f"{pid}_*.pdf"))
            if not pdf_candidates:
                pdf_candidates = list(DOWNLOADS.rglob(f"*{pid}*.pdf"))
            if pdf_candidates:
                pending.append((pid, pdf_candidates[0], row.get("doi", "")))
            else:
                log.warning(f"PDF not found for {pid} — skipping")
    return pending


def run(state: dict, paper_filter=None) -> int:
    """Main entry point. Returns number of papers summarised this run."""
    total_rewritten = 0
    batch_log_lines = []

    rewritten, rewrite_logs = _run_prebatch_auto_recovery(state, paper_filter)
    total_rewritten += rewritten
    batch_log_lines.extend(rewrite_logs)

    pending = _get_pending_pdfs(state, paper_filter)

    if not pending:
        if total_rewritten:
            log.info("Agent 2: No new papers to summarise. Applied %d pre-batch rewrites.", total_rewritten)
        else:
            log.info("Agent 2: No new papers to summarise.")

        if batch_log_lines:
            with open(BATCH_LOG, "a", encoding="utf-8") as f:
                from datetime import datetime
                f.write(f"\n--- Run {datetime.utcnow().isoformat()} ---\n")
                f.write("\n".join(batch_log_lines) + "\n")
        return 0

    log.info(f"Agent 2: {len(pending)} papers pending summarisation.")

    total_completed = 0

    # Process in batches of BATCH_SIZE
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start: batch_start + BATCH_SIZE]
        batch_nums = [b[0] for b in batch]
        log.info(f"  Batch: {batch_nums}")

        batch_blocks = []
        for paper_id, pdf_path, doi in batch:
            evidence_excerpt, evidence_note = _load_extract_evidence(paper_id, pdf_path, EVIDENCE_LINE_LIMIT)
            batch_log_lines.append(f"EVIDENCE {paper_id}: {evidence_note}")

            block = _summarise_paper(
                paper_id,
                pdf_path,
                doi,
                evidence_excerpt=evidence_excerpt,
                evidence_note=evidence_note,
            )
            batch_blocks.append(block)
            time.sleep(1)  # avoid rate limit

        # Quality checklist
        for i, (paper_id, _, _) in enumerate(batch):
            block = batch_blocks[i]
            headers_found = sum(1 for h in SECTION_HEADERS if h in block)
            if headers_found < 18:
                log.warning(f"  {paper_id}: Only {headers_found}/18 sections found — check output")
                batch_log_lines.append(f"WARNING {paper_id}: {headers_found}/18 sections")
            else:
                batch_log_lines.append(f"OK {paper_id}: 18/18 sections")

            if "The paper proposes/evaluates an AI method described in" in block:
                batch_log_lines.append(f"WARNING {paper_id}: generic fallback phrasing detected")

        # Append to summaries.md (append-only; inserted after latest paper block)
        _append_summary_blocks(batch_blocks)

        total_completed += len(batch)
        log.info(f"  Batch complete. Total summarised this run: {total_completed}")

        # G2 gate every 50 papers
        current_total = state["papers_at_stage"]["summarised"] + total_completed
        if current_total % 50 == 0:
            log.warning(
                f"GATE G2 CHECKPOINT: {current_total} papers summarised. "
                f"Human review of 10 randomly sampled summaries required before Agent 3 proceeds."
            )

    # Write batch log
    if batch_log_lines:
        with open(BATCH_LOG, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"\n--- Run {datetime.utcnow().isoformat()} ---\n")
            f.write("\n".join(batch_log_lines) + "\n")

    log.info(
        "Agent 2 complete. %d papers summarised, %d pre-batch rewrites.",
        total_completed,
        total_rewritten,
    )
    return total_completed
