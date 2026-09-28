"""
agent1_acquire.py — Acquisition Agent
Downloads papers from ScienceDirect, applies exclusion filters, organises by category.

Requires:
    SD_API_KEY environment variable (ScienceDirect API key)
    ANTHROPIC_API_KEY environment variable
"""

import csv
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("agent1_acquire")

ROOT      = Path(__file__).parent.parent
DOWNLOADS = ROOT / "downloads"
DATA      = ROOT / "data"
LOGS      = ROOT / "logs"

SD_BASE   = "https://api.elsevier.com/content"
SD_KEY    = os.environ.get("SD_API_KEY", "")
ANTH_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Category heuristic keywords (fast pre-classification before Agent 2) ───────
CATEGORY_KEYWORDS = {
    "C1_Neuroimaging": ["MRI", "fMRI", "PET", "DTI", "EEG", "EEG-based", "retinal",
                        "structural", "functional", "neuroimaging", "hippocampal",
                        "cortical", "white matter", "connectivity", "diffusion"],
    "C2_Computational": ["deep learning", "neural network", "CNN", "transformer",
                         "graph neural", "attention", "ensemble", "XGBoost", "BERT",
                         "machine learning", "classification", "model", "algorithm"],
    "C3_Molecular":     ["plasma", "biomarker", "CSF", "blood", "protein", "tau",
                         "amyloid", "GFAP", "NfL", "methylation", "genomic", "omics"],
    "C4_Behavioural":   ["speech", "language", "driving", "gait", "handwriting",
                         "digital", "mobile", "wearable", "sensor", "cognitive test",
                         "facial", "video", "balance", "motor"],
    "C5_Clinical":      ["clinical", "population", "cohort", "survey", "risk factor",
                         "intervention", "treatment", "review", "meta-analysis",
                         "epidemiolog", "longitudinal study"],
}

EXCLUSION_CRITERIA = [
    "animal model",
    "animal study",
    "mouse model",
    "rat model",
    "in vitro",
    "cell line",
    "no human",
    "pharmacological intervention",
    "drug trial",
    "case report",
    "single patient",
    "letter to the editor",
    "editorial",
    "conference abstract only",
]


def _sd_headers():
    return {
        "X-ELS-APIKey": SD_KEY,
        "Accept": "application/json",
    }


def search_sciencedirect(query: str, date_range: tuple, max_results: int = 16000) -> list:
    """Search ScienceDirect and return metadata records."""
    records = []
    start = 0
    count = 25  # results per page

    log.info(f"Searching ScienceDirect: {query}")
    log.info(f"Date range: {date_range[0]}–{date_range[1]}")

    while start < max_results:
        params = {
            "query": query,
            "date": f"{date_range[0]}-{date_range[1]}",
            "start": start,
            "count": count,
            "field": "dc:title,dc:description,prism:doi,prism:publicationName,prism:coverDate,dc:creator",
        }
        try:
            resp = requests.get(
                f"{SD_BASE}/search/sciencedirect",
                headers=_sd_headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("search-results", {}).get("entry", [])
            if not entries:
                break
            records.extend(entries)
            log.info(f"  Retrieved {len(records)} records so far...")
            start += count
            time.sleep(0.5)  # rate limit courtesy
        except requests.RequestException as e:
            log.error(f"ScienceDirect API error at offset {start}: {e}")
            break

    log.info(f"Total records retrieved: {len(records)}")
    return records


def _fast_exclude(abstract: str) -> tuple:
    """Apply simple keyword exclusion. Returns (exclude: bool, reason: str)."""
    ab = abstract.lower()
    for phrase in EXCLUSION_CRITERIA:
        if phrase in ab:
            return True, phrase
    # Exclude if no MCI mention at all
    if "mild cognitive impairment" not in ab and " mci" not in ab:
        return True, "no MCI mention in abstract"
    return False, ""


def _llm_filter(abstract: str, title: str) -> tuple:
    """Use Claude API to classify borderline abstracts. Returns (exclude, reason, confidence)."""
    prompt = f"""You are screening academic papers for inclusion in an MCI progression prediction literature review.

INCLUSION CRITERIA (paper must meet ALL):
1. Studies human participants (not animals, cell lines, or simulations)
2. Addresses MCI diagnosis, detection, progression prediction, or reversion
3. Uses AI, machine learning, or statistical modelling
4. Reports quantitative performance metrics OR provides clinical data analysis
5. Published as a full research article (not editorial, letter, or abstract-only)

Paper title: {title}
Abstract: {abstract}

Return JSON only: {{"include": true/false, "reason": "one sentence", "confidence": 0.0-1.0}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTH_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": os.environ.get("AGENT1_MODEL", "claude-sonnet-5"),  # sonnet-4-20250514 retired 2026-06-15 (PAPERINGO-ron)
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30,
        )
        text = resp.json()["content"][0]["text"].strip()
        # Strip markdown fences if present
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        exclude = not result.get("include", True)
        return exclude, result.get("reason", ""), result.get("confidence", 0.5)
    except Exception as e:
        log.warning(f"LLM filter failed: {e} — defaulting to include")
        return False, "llm_error_defaulted_include", 0.5


def _guess_category(abstract: str) -> str:
    """Fast heuristic category assignment for folder placement."""
    ab = abstract.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw.lower() in ab)
    return max(scores, key=scores.get)


def download_pdf(doi: str, dest_path: Path) -> bool:
    """Attempt to download full-text PDF via ScienceDirect API."""
    if not doi:
        return False
    url = f"{SD_BASE}/article/pii/{doi.replace('/', '%2F')}?httpAccept=application/pdf"
    try:
        resp = requests.get(url, headers=_sd_headers(), timeout=60, stream=True)
        if resp.status_code == 200 and "pdf" in resp.headers.get("Content-Type", ""):
            dest_path.write_bytes(resp.content)
            return True
        log.warning(f"PDF download failed for {doi}: HTTP {resp.status_code}")
        return False
    except Exception as e:
        log.warning(f"PDF download exception for {doi}: {e}")
        return False


def run(state: dict) -> int:
    """Main entry point called by coordinator. Returns number of new papers downloaded."""

    # Load PRD config
    config = {
        "search_query": '("Mild Cognitive Impairment" OR "MCI") AND ("Progression" OR "Prediction" OR "Prognosis")',
        "date_range": ("2019", "2025"),
        "target_corpus_size": 1000,
    }

    records = search_sciencedirect(config["search_query"], config["date_range"])

    metadata_path = DATA / "corpus_metadata.csv"
    borderline_path = LOGS / "borderline_abstracts.txt"

    existing_dois = set()
    if metadata_path.exists():
        with open(metadata_path) as f:
            reader = csv.DictReader(f)
            existing_dois = {row["doi"] for row in reader if row.get("doi")}

    new_papers = 0
    borderline_records = []

    fieldnames = ["paper_id", "doi", "title", "year", "journal",
                  "abstract", "pre_category", "exclude_flag", "exclude_reason", "llm_confidence"]

    write_header = not metadata_path.exists()
    with open(metadata_path, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for record in records:
            doi   = record.get("prism:doi", "")
            title = record.get("dc:title", "")
            abstract = record.get("dc:description", "")
            year  = record.get("prism:coverDate", "")[:4]
            journal = record.get("prism:publicationName", "")

            if not doi or doi in existing_dois:
                continue

            # Fast exclusion
            exclude, reason = _fast_exclude(abstract)
            confidence = 1.0

            if not exclude:
                # LLM filter for inclusion confirmation
                exclude, reason, confidence = _llm_filter(abstract, title)

            if not exclude and confidence < 0.8:
                borderline_records.append({"doi": doi, "title": title, "abstract": abstract,
                                           "reason": reason, "confidence": confidence})

            paper_id = f"P-{state['next_paper_number']}"
            state["next_paper_number"] += 1

            category = _guess_category(abstract) if not exclude else "excluded"

            row = {
                "paper_id": paper_id,
                "doi": doi,
                "title": title,
                "year": year,
                "journal": journal,
                "abstract": abstract[:500],
                "pre_category": category,
                "exclude_flag": str(exclude),
                "exclude_reason": reason,
                "llm_confidence": confidence,
            }
            writer.writerow(row)
            existing_dois.add(doi)

            if not exclude:
                # Download PDF
                cat_folder = DOWNLOADS / category
                cat_folder.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r"[^\w\-]", "_", doi.split("/")[-1])
                pdf_path = cat_folder / f"{paper_id}_{safe_name}.pdf"
                if not pdf_path.exists():
                    success = download_pdf(doi, pdf_path)
                    if success:
                        new_papers += 1
                        log.info(f"Downloaded: {paper_id} ({title[:60]})")
                    else:
                        log.warning(f"Could not download PDF for {paper_id}: {doi}")

    # Write borderline file for human gate G1
    if borderline_records:
        with open(borderline_path, "w", encoding="utf-8") as f:
            f.write(f"BORDERLINE ABSTRACTS FOR HUMAN REVIEW — {len(borderline_records)} records\n")
            f.write("Review each and confirm exclusion/inclusion in corpus_metadata.csv\n\n")
            for rec in borderline_records:
                f.write(f"DOI: {rec['doi']}\n")
                f.write(f"Title: {rec['title']}\n")
                f.write(f"Confidence: {rec['confidence']:.2f} — Reason: {rec['reason']}\n")
                f.write(f"Abstract: {rec['abstract'][:300]}...\n\n{'─'*60}\n\n")

    log.info(f"Agent 1 complete. New PDFs: {new_papers}. Borderline: {len(borderline_records)}.")
    return new_papers
