#!/usr/bin/env python
"""Corpus-wide duplicate scan that does not depend on a parseable DOI field.

`logs/pipeline_log.txt` records the failure this exists to fix: 393 of 837 summary blocks
exposed no parseable DOI, so a DOI-only duplicate check had a 47 per cent blind spot, and
older blocks carry SYNTHETIC filenames rebuilt from the DOI suffix, so filename matching
missed them too. `summaries_lint`/`registry_lint` catch duplicates *after* a batch is
written (same DOI or same normalised title); this scan decides identity from every signal
the corpus has, including two the lints do not use:

* the PII or PMID embedded in the mapped PDF's filename, and the DOI that identifier owns
  (``logs/pdf_map_doi_audit_truth.json``, built by ``scripts/pdf_map_doi_audit.py``). This is
  what closes the blind spot: 576 of the 670 blocks with no DOI field have an identifier.
* the mapped PDF's real path, against the block head when the header carries a filename.

Signals per paper, all independent of each other:

===================  =========================================================
``doi_field``        the block's own ``### DOI`` / ``### DOI or URL`` value
``doi_prose``        the first DOI in the block body
``map_doi``          ``data/paper_pdf_map.json``'s DOI, de-contaminated where the
                     audit's prefix cache already resolved it
``ident_doi``        the DOI that owns the PII/PMID in the mapped PDF's filename
``identifier``       that PII/PMID itself
``filename``         the mapped PDF's basename
``title``            block head, block title field, map title
===================  =========================================================

Two papers are grouped when they share a DOI, an identifier, a filename, or an exactly
equal normalised title (``duplicate``), or when their titles are a near match
(``near_title`` — a companion paper, not necessarily a duplicate). Each group is reported
with the registry coding of every member, so the disagreement a re-summarised batch
produces is visible, and with every ledger and taxonomy reference to the later members, so
the remediation scope is known before anything is stubbed.

Read-only: it writes only to ``logs/``. Stubbing remains an operator action through
``scripts/dedup_stub.py`` (registry and summaries are batch-session files).

Usage::

    python scripts/corpus_duplicate_scan.py                  # scan, write logs/
    python scripts/corpus_duplicate_scan.py --only-new       # groups outside PAPERINGO-zkiz
    python scripts/corpus_duplicate_scan.py --min-size 3     # only larger clusters
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import io
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUMMARIES = ROOT / "summaries.md"
MAP_PATH = ROOT / "data" / "paper_pdf_map.json"
REGISTRY = ROOT / "data" / "full_paper_registry.csv"
TRUTH_CACHE = ROOT / "logs" / "pdf_map_doi_audit_truth.json"
PREFIX_CACHE = ROOT / "logs" / "pdf_map_doi_audit_prefix.json"
OUT_JSON = ROOT / "logs" / "corpus_duplicate_scan.json"
OUT_MD = ROOT / "logs" / "corpus_duplicate_scan.md"
STUB_PLAN = ROOT / "logs" / "corpus_duplicate_stub_plan.json"

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>|,;\r\n)\]]+", re.I)
FURNITURE = re.compile(r"(abstract|keywords?:?|ios|page|ageing|jpad|brain|key)$", re.I)
TITLE_NEAR = 0.90

# The pairs the 2026-08-29 audit of P-868..P-1097 found (PAPERINGO-zkiz), so the report can
# separate "already known" from "new to this scan".
KNOWN_AUDIT_PAIRS = [
    ("P-877", "P-878"),
    *[(f"P-{981 + i}", f"P-{1018 + i}") for i in range(9)],
    ("P-1019", "P-1019"),  # placeholder kept out of the map below
    *[(f"P-{838 + i}", f"P-{1032 + i}") for i in range(6)],
    *[(f"P-{1000 + i}", f"P-{1038 + i}") for i in range(8)],
    ("P-1008", "P-1047"),
    *[(f"P-{1009 + i}", f"P-{1048 + i}") for i in range(6)],
    ("P-1015", "P-1055"),
    ("P-1016", "P-1056"),
    ("P-1017", "P-1057"),
]
KNOWN_AUDIT_PAIRS = [p for p in KNOWN_AUDIT_PAIRS if p[0] != p[1]]

LEDGERS = (
    ("consensus.json", "verified_findings", ("supporting_papers", "rejected_supporters"), ()),
    ("gaps.json", "gaps", ("papers_claiming_gap", "papers_potentially_closing_gap", "grounding_papers", "related_papers"), ()),
    ("conflicts.json", "conflicts", ("resolution_evidence_papers",), ("paper_a", "paper_b")),
    ("complementary.json", "complementary_findings", (), ("paper_a", "paper_b")),
    ("invalidated.json", "invalidated_assumptions", ("evidence_papers", "supporting_papers"), ("paper_id", "disproving_paper")),
)


def norm_text(value: str | None) -> str:
    """Lower-case alphanumeric form of a title or label."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def norm_doi(value: str | None) -> str:
    """Lower-case DOI with fused page furniture and a leading resolver stripped."""
    doi = (value or "").strip().rstrip(".")
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    out = doi.lower()
    for _ in range(2):
        out = FURNITURE.sub("", out).rstrip(":/-")
    return out


def is_doi(value: str | None) -> bool:
    return bool(value and re.match(r"^10\.\d{4,9}/.{3,}$", value))


def plausible_title(value: str | None) -> str | None:
    """Drop filenames, stubs and placeholders that some headers carry as a title."""
    text = (value or "").strip()
    if len(text) < 20:
        return None
    low = text.lower()
    if low.endswith(".pdf") or low.startswith("pmid_") or re.match(r"^1-s2\.0-", low):
        return None
    if low.startswith("[stub") or low.startswith("not reported") or low.startswith("[non-research"):
        return None
    return text


def identifier_of(path: str | None) -> str | None:
    """The publisher identifier (PII) or PMID embedded in a PDF filename."""
    name = (path or "").split("/")[-1]
    match = re.match(r"1-s2\.0-(S[0-9A-Za-z]+)-main\.pdf$", name)
    if match:
        return "PII:" + match.group(1)
    match = re.match(r"PMID_(\d+)_", name)
    if match:
        return "PMID:" + match.group(1)
    return None


def basename(path: str | None) -> str | None:
    return (path or "").split("/")[-1] or None


def load_blocks() -> dict[str, dict]:
    """Per-paper title candidates and DOI candidates read from summaries.md."""
    text = SUMMARIES.read_bytes().decode("utf-8")
    out: dict[str, dict] = {}
    pattern = re.compile(r"## Paper (\d+):([^\r\n]*)\r\n(.*?)(?=\r\n## Paper \d+:|\Z)", re.S)
    for match in pattern.finditer(text):
        pid, head, body = "P-" + match.group(1), match.group(2).strip(), match.group(3)
        field = re.search(r"### DOI[^\r\n]*\r?\n+([^\r\n]*)", body)
        title_field = re.search(r"### (?:Full paper title|Full title|Title)[^\S\r\n]*\r?\n+([^\r\n]*)", body)
        doi_in_field = DOI_RE.search(field.group(1)) if field else None
        doi_in_prose = next(iter(DOI_RE.findall(body[:4000])), None)
        out[pid] = {
            "head": head,
            "title_field": title_field.group(1).strip() if title_field else None,
            "doi_field": doi_in_field.group(0) if doi_in_field else None,
            "doi_prose": doi_in_prose,
            "stub": bool(re.match(r"^\[(?:STUB-DEDUP|STUB\b|DUPLICATE\b)", head, re.I)),
        }
    return out


def load_registry() -> dict[str, dict]:
    """Coded columns per paper, for the coding-disagreement part of the report."""
    if not REGISTRY.exists():
        return {}
    text = REGISTRY.read_bytes().decode("utf-8")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        pid = (row.get("Paper_ID") or "").strip()
        if pid:
            out[pid] = row
    return out


def load_ledger_refs(pids: set[str]) -> dict[str, list[str]]:
    """Every ledger reference to any of the given papers, as 'file:record:field'."""
    refs: dict[str, list[str]] = collections.defaultdict(list)
    for name, key, list_fields, scalar_fields in LEDGERS:
        path = ROOT / "data" / name
        if not path.exists():
            continue
        data = json.loads(path.read_bytes().decode("utf-8"))
        records = data.get(key) if isinstance(data, dict) and key in data else None
        if records is None:
            records = next((v for v in data.values() if isinstance(v, list)), [])
        for record in records:
            rid = record.get("id") or record.get("assumption_id") or "?"
            for field in list_fields:
                for pid in record.get(field) or []:
                    if pid in pids:
                        refs[pid].append(f"{name}:{rid}:{field}")
            for field in scalar_fields:
                pid = record.get(field)
                if pid in pids:
                    refs[pid].append(f"{name}:{rid}:{field}")
    return refs


def load_taxonomy_refs(pids: set[str]) -> dict[str, list[str]]:
    """Leaf labels the taxonomy HTML attaches to any of the given papers."""
    path = ROOT / "mci_research_top_down_taxonomy.html"
    if not path.exists():
        return {}
    text = path.read_bytes().decode("utf-8", "replace")
    refs: dict[str, list[str]] = collections.defaultdict(list)
    pattern = re.compile(r'\{\s*id:\s*"([^"]+)",\s*label:\s*"((?:[^"\\]|\\.)*)"')
    for pid, label in pattern.findall(text):
        if pid in pids:
            refs[pid].append(label.replace('\\"', '"')[:60])
    return refs


def identity_signals() -> dict[str, dict]:
    """Every identity signal the corpus holds, per paper."""
    blocks = load_blocks()
    pdf_map = json.loads(MAP_PATH.read_bytes().decode("utf-8")) if MAP_PATH.exists() else {}
    truth = json.loads(TRUTH_CACHE.read_bytes().decode("utf-8")) if TRUTH_CACHE.exists() else {}
    prefixes = json.loads(PREFIX_CACHE.read_bytes().decode("utf-8")) if PREFIX_CACHE.exists() else {}

    signals: dict[str, dict] = {}
    for pid, block in blocks.items():
        entry = pdf_map.get(pid) or {}
        path = entry.get("path")
        ident = identifier_of(path)
        ident_doi = truth.get(pid)
        if ident_doi == "ERR":
            ident_doi = None
        map_doi = norm_doi(entry.get("doi"))
        if map_doi and map_doi in prefixes and prefixes[map_doi].get("doi"):
            map_doi = prefixes[map_doi]["doi"]
        titles = [
            t
            for t in (
                plausible_title(block["head"]),
                plausible_title(block["title_field"]),
                plausible_title(entry.get("title")),
            )
            if t
        ]
        dois = {d for d in (norm_doi(block["doi_field"]), norm_doi(block["doi_prose"]), map_doi, norm_doi(ident_doi)) if is_doi(d)}
        signals[pid] = {
            "doi_field": norm_doi(block["doi_field"]) if is_doi(norm_doi(block["doi_field"])) else None,
            "doi_prose": norm_doi(block["doi_prose"]) if is_doi(norm_doi(block["doi_prose"])) else None,
            "map_doi": map_doi if is_doi(map_doi) else None,
            "ident_doi": norm_doi(ident_doi) if is_doi(norm_doi(ident_doi)) else None,
            "dois": sorted(dois),
            "identifier": ident,
            "filename": basename(path),
            "titles": titles,
            "stub": block["stub"],
            "path": path,
        }
    return signals


def _union_find(signals: dict[str, dict], keys: list[str]) -> tuple[dict, dict]:
    """Union-find over the given signal keys, returning components and the evidence per row."""
    parent: dict[str, str] = {pid: pid for pid in signals}
    evidence: dict[str, list[str]] = collections.defaultdict(list)
    index: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for pid, sig in signals.items():
        for kind in keys:
            raw = sig.get("titles") if kind == "title" else sig.get(kind)
            for value in raw if isinstance(raw, list) else [raw]:
                if not value:
                    continue
                if kind == "title":
                    value = norm_text(value)
                    if len(value) < 25:
                        continue
                index[(kind, value)].append(pid)

    def find(pid: str) -> str:
        while parent[pid] != pid:
            parent[pid] = parent[parent[pid]]
            pid = parent[pid]
        return pid

    for (kind, value), members in index.items():
        if len(members) < 2:
            continue
        for member in members:
            evidence[member].append(f"{kind}={value[:70]}")
        first = members[0]
        for other in members[1:]:
            ra, rb = find(first), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    components: dict[str, list[str]] = collections.defaultdict(list)
    for pid in signals:
        components[find(pid)].append(pid)
    return components, evidence


def _title_forms(sig: dict) -> list[str]:
    """Normalised title candidates for a paper, longest first."""
    forms = [norm_text(t) for t in sig["titles"]]
    return sorted({f for f in forms if len(f) >= 25}, key=len, reverse=True)


def _titles_agree(signals: dict[str, dict], members: list[str]) -> float | None:
    """Max title similarity across the members, or None when fewer than two have a title."""
    forms = {pid: _title_forms(signals[pid]) for pid in members}
    present = [pid for pid in members if forms[pid]]
    if len(present) < 2:
        return None
    best = 0.0
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            ratio = difflib.SequenceMatcher(None, forms[a][0], forms[b][0]).ratio()
            if ratio > best:
                best = ratio
    return best


def group(signals: dict[str, dict], skip_near: bool = False) -> list[dict]:
    """Classify multi-row clusters, separating true duplicates from metadata collisions.

    Signals are unioned together, then the *component* is classified. A duplicate needs a
    publisher anchor — the PII/PMID in the mapped PDF's filename, the DOI that identifier
    owns, or the mapped file itself — because two rows sharing a title or a block DOI field
    can just as easily be one row's metadata copied onto another. Within a component that has
    an anchor, title agreement separates the two cases that look identical to a DOI check:
    the same article acquired twice (``duplicate`` — the stub candidate) and two different
    papers whose map entries name the same file (``map_conflict`` — one map row is wrong).
    """
    all_keys = ["identifier", "ident_doi", "filename", "doi_field", "map_doi", "title"]
    components, _ = _union_find(signals, all_keys)
    component_of: dict[str, list[str]] = {}
    for members in components.values():
        for pid in members:
            component_of[pid] = sorted(members)

    # The pair is the unit of evidence: two rows share specific keys, and the component can
    # mix a genuine duplicate pair with a pair whose shared anchor is a map defect.
    index: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for pid, sig in signals.items():
        for kind in all_keys:
            raw = sig.get("titles") if kind == "title" else sig.get(kind)
            for value in raw if isinstance(raw, list) else [raw]:
                if not value:
                    continue
                if kind == "title":
                    value = norm_text(value)
                    if len(value) < 25:
                        continue
                index[(kind, value)].append(pid)

    shared: dict[tuple[str, str], dict[str, list[str]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for (kind, value), members in index.items():
        # A paper can appear twice under one kind (two title forms that normalise alike), so
        # collapse a key's member list before pairing or the scan reports P-x = P-x.
        for i, a in enumerate(sorted(set(members))):
            for b in sorted(set(members))[i + 1 :]:
                shared[(a, b)][kind].append(value)

    anchor_kinds = {"identifier", "ident_doi", "filename"}
    groups = []
    for (a, b), kinds in shared.items():
        similarity = _titles_agree(signals, [a, b])
        anchored = bool(set(kinds) & anchor_kinds)
        stubbed = [pid for pid in (a, b) if signals[pid]["stub"]]
        if stubbed:
            cls = "duplicate_stubbed"
        elif anchored and similarity is not None and similarity >= TITLE_NEAR:
            cls = "duplicate"
        elif anchored and similarity is None:
            # A shared publisher anchor with no comparable title. NOT a duplicate: stubbing
            # on unverifiable identity is the one destructive guess this scan must never
            # make (it mislabelled [STUB - excluded] P-904, a dexmedetomidine review, as a
            # second copy of the MCI network paper P-876, purely because they map to one
            # PDF). Report it, leave it alone.
            cls = "anchor_unverified"
        elif anchored:
            # Two rows claim the same publisher identifier or file but name different papers.
            cls = "map_conflict"
        elif "map_doi" in kinds:
            # No publisher anchor, but the map asserts one article for both rows.
            cls = "map_doi_collision"
        elif "doi_field" in kinds:
            # Only the blocks' own DOI fields agree — the field was copied between them.
            cls = "doi_field_collision"
        else:
            # Only the titles agree, or only a cited DOI. Weakest evidence: a candidate, not a
            # finding, because a generic title can be shared by two genuinely distinct papers.
            cls = "title_only_collision"
        groups.append(
            {
                "members": [a, b],
                "component": component_of[a],
                "class": cls,
                "signals": sorted(kinds),
                "why": {pid: [f"{k}={v[0][:70]}" for k, v in sorted(kinds.items())] for pid in (a, b)},
                "title_similarity": None if similarity is None else round(similarity, 3),
                "anchored": anchored,
                # Both rows lacked a parseable DOI field: a DOI-only duplicate check is
                # structurally blind to this pair, which is the blind spot this scan closes.
                "no_doi_field": not signals[a]["doi_field"] and not signals[b]["doi_field"],
                "stubbed": stubbed,
            }
        )

    # Near-title pass, blocked by a shared leading run of title words so the comparison stays
    # near-linear: 1,413 blocks pairwise with 9 title forms each is ~10^7 SequenceMatcher
    # calls and does not finish. Titles that differ only in a suffix or a subtitle still
    # share their opening words, which is what this keys on.
    member_pairs = set(shared)
    anchors_of = {pid: sig["identifier"] or sig["ident_doi"] or sig["filename"] for pid, sig in signals.items()}
    near = []
    if not skip_near:
        titles = {
            pid: [norm_text(t) for t in signals[pid]["titles"] if len(norm_text(t)) >= 25]
            for pid in signals
        }
        buckets: dict[str, list[str]] = collections.defaultdict(list)
        for pid, title_forms in titles.items():
            for form in title_forms:
                buckets[" ".join(form.split()[:5])].append(pid)
        for members in buckets.values():
            members = sorted(set(members))
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    if (a, b) in member_pairs:
                        continue
                    best = 0.0
                    for ta in titles[a]:
                        for tb in titles[b]:
                            ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
                            if ratio > best:
                                best = ratio
                    if best >= TITLE_NEAR:
                        member_pairs.add((a, b))
                        same_source = anchors_of[a] and anchors_of[a] == anchors_of[b]
                        near.append(
                            {
                                "members": [a, b],
                                "component": component_of.get(a, [a, b]),
                                "class": "duplicate" if same_source else "near_title",
                                "signals": ["near_title"],
                                "title_similarity": round(best, 3),
                                "anchored": bool(same_source),
                                "why": {a: [f"anchor={anchors_of[a]}"], b: [f"anchor={anchors_of[b]}"]},
                                "no_doi_field": not signals[a]["doi_field"] and not signals[b]["doi_field"],
                                "stubbed": [p for p in (a, b) if signals[p]["stub"]],
                            }
                        )
    return groups + near


CLASS_NOTE = {
    "duplicate": "two rows are the same article, acquired twice — stub the later one",
    "duplicate_stubbed": "already remediated (one member carries a [STUB-DEDUP] or [STUB] head)",
    "anchor_unverified": "one PDF/identifier shared, but no comparable title — identity unconfirmed, never auto-stubbed",
    "map_conflict": "two rows claim one PDF/identifier but name different papers — one map row is wrong",
    "map_doi_collision": "no shared PDF identifier, but the map gives both rows one DOI",
    "doi_field_collision": "only the blocks' own DOI fields agree — the field was copied between rows",
    "title_only_collision": "only the titles agree, no shared anchor — a candidate, and a generic title can fake it",
    "near_title": "near-identical titles, different anchors — companion papers rather than duplicates",
}


def render_markdown(payload: dict, signals: dict[str, dict]) -> str:
    """The report, generated from the payload so its numbers cannot drift from the scan."""
    cov = payload["coverage"]
    classes = collections.Counter(g["class"] for g in payload["pairs"])
    lines = [
        "# Corpus duplicate scan",
        "",
        f"Generated {payload['generated']} by `{payload['checker']}`. Read-only: nothing was "
        "stubbed, moved or rewritten. Stubbing stays an operator action through "
        "`scripts/dedup_stub.py`.",
        "",
        "## Why this exists",
        "",
        "`logs/pipeline_log.txt` records that 393 of 837 summary blocks exposed no parseable "
        "DOI, leaving a DOI-only duplicate check with a 47 per cent blind spot; older blocks "
        "also carry synthetic filenames rebuilt from the DOI suffix, so filename matching "
        "missed those too. This scan decides identity from the publisher identifier in the "
        "mapped PDF's name and the DOI that identifier owns (built by "
        "`scripts/pdf_map_doi_audit.py`), independently of the block's DOI field.",
        "",
        "## Coverage",
        "",
        f"- blocks: **{cov['blocks']}**",
        f"- blocks with no parseable DOI field: **{cov['blocks_without_doi_field']}**",
        f"- of those, resolved through the PDF identifier or the map: **{cov['of_those_covered_by_identifier_or_map']}**",
        f"- of those, still no DOI signal at all: **{cov['of_those_with_no_doi_signal_at_all']}**",
        "",
        "## Findings",
        "",
        f"{payload['pair_count']} pairs share an identity signal "
        f"({payload['known_audit_pairs']} already covered by the PAPERINGO-zkiz audit, "
        f"{payload['new_pairs']} new). **The pair is the unit of evidence**; the `component` "
        "field lists the rows transitively involved, which is the remediation scope.",
        "",
        "| class | pairs | what it means |",
        "|---|---:|---|",
    ]
    for name, count in classes.most_common():
        lines.append(f"| `{name}` | {count} | {CLASS_NOTE.get(name, '')} |")
    lines += [
        "",
        f"**Open duplicates: {payload['open_duplicate_pairs']} pairs naming "
        f"{payload['open_duplicate_rows']} rows** — the stub candidates. "
        f"**{payload['invisible_to_a_doi_only_check']} duplicate/map-conflict pairs involve two "
        "rows that both lack a DOI field**, so no DOI-field check could have found them: that "
        "is the blind spot closed here.",
        "",
    ]
    for name in ("duplicate", "map_conflict", "anchor_unverified", "map_doi_collision",
                 "title_only_collision"):
        rows = [g for g in payload["pairs"] if g["class"] == name]
        if not rows:
            continue
        lines += [f"### `{name}` ({len(rows)})", ""]
        for item in rows[:40]:
            a, b = item["members"]
            sim = item["title_similarity"]
            sim_text = "n/a" if sim is None else f"{sim:.2f}"
            lines.append(f"- `{a}` × `{b}` — shared: {', '.join(item['signals'])}; title sim {sim_text}")
            if item.get("coding_disagreement"):
                lines.append(f"  - coding: {item['coding_disagreement']}")
            if item.get("ledger_refs"):
                cited = ", ".join(f"{k} ({len(v)})" for k, v in item["ledger_refs"].items())
                lines.append(f"  - cited in ledgers: {cited}")
        if len(rows) > 40:
            lines.append(f"- … and {len(rows) - 40} more (see `{OUT_JSON.name}`)")
        lines.append("")
    lines += [
        "## Remediation",
        "",
        f"`{STUB_PLAN.name}` is the `scripts/dedup_stub.py` input, regenerated by this scan: "
        f"**{len(payload['stub_plan'])} pair(s)**; the original is the record a prior pass "
        f"already named in `duplicate_of`, else the lowest P-id. "
        f"{len(payload['stub_plan_held'])} component(s) are held back because they also hold a "
        "`map_conflict` pair, so which member is the real paper is undecided. `dedup_stub.py` "
        "is dry-run by default; apply it, then re-run Agents 5-7.",
        "",
        "Only `duplicate` and the `duplicate_stubbed` residue are stub candidates, and stubbing "
        "rewrites `summaries.md` and the registry, so it is a batch-session operator action: "
        "`python scripts/dedup_stub.py --help`. `map_conflict` is not a duplicate at all — one "
        "row's `path` (and usually its `doi`) names another paper's file, which is a "
        "`data/paper_pdf_map.json` repair, per `logs/pdf_map_doi_repair_2026-09-16.md`.",
        "",
        "Before stubbing any pair, re-read both blocks in `summaries.md`: this scan decides "
        "identity, and nothing decides whether the *later* record's coding is the one to keep.",
        "",
    ]
    return "\r\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only-new", action="store_true", help="groups not already in the zkiz audit")
    parser.add_argument("--min-size", type=int, default=2, help="smallest group to report")
    parser.add_argument("--no-near", action="store_true", help="skip the blocked near-title pass")
    parser.add_argument("--json-out", default=str(OUT_JSON))
    parser.add_argument("--md-out", default=str(OUT_MD))
    args = parser.parse_args()

    signals = identity_signals()
    blocks = load_blocks()
    registry = load_registry()

    no_field = [pid for pid, b in blocks.items() if not b["doi_field"]]
    covered = [pid for pid in no_field if signals[pid]["dois"] or signals[pid]["identifier"]]
    no_signal = [pid for pid in no_field if not signals[pid]["dois"] and not signals[pid]["identifier"]]

    groups = group(signals, skip_near=args.no_near)
    known = {frozenset(pair) for pair in KNOWN_AUDIT_PAIRS}
    for item in groups:
        item.setdefault("why", {})
        item.setdefault("no_doi_field", False)
        item["self_conflict"] = {
            pid: {
                "map_doi": signals[pid]["map_doi"],
                "ident_doi": signals[pid]["ident_doi"],
                "doi_field": signals[pid]["doi_field"],
            }
            for pid in item["members"]
            if signals[pid]["ident_doi"]
            and signals[pid]["map_doi"]
            and signals[pid]["ident_doi"] != signals[pid]["map_doi"]
        }
        item["known_audit"] = frozenset(item["members"]) in known or any(
            frozenset(pair) <= frozenset(item["members"]) for pair in known
        )
        item["size"] = len(item["component"])
        item["coded"] = {
            pid: {
                "Category": (registry.get(pid) or {}).get("Category"),
                "Task_Type": (registry.get(pid) or {}).get("Task_Type"),
                "Validation_Type": (registry.get(pid) or {}).get("Validation_Type"),
                "Architecture_Family": (registry.get(pid) or {}).get("Architecture_Family"),
                "duplicate_of": (registry.get(pid) or {}).get("duplicate_of"),
            }
            for pid in item["members"]
        }
        item["evidence"] = {
            pid: {
                "doi": signals[pid]["dois"],
                "identifier": signals[pid]["identifier"],
                "file": signals[pid]["filename"],
                "title": (signals[pid]["titles"] or [None])[0],
            }
            for pid in item["members"]
        }
    groups.sort(key=lambda g: (g["class"], -g["size"], g["members"][0]))

    interesting = {pid for g in groups for pid in g["members"]}
    ledger_refs = load_ledger_refs(interesting)
    taxonomy_refs = load_taxonomy_refs(interesting)
    for item in groups:
        item["ledger_refs"] = {pid: ledger_refs.get(pid, []) for pid in item["members"] if ledger_refs.get(pid)}
        item["taxonomy_refs"] = {pid: taxonomy_refs.get(pid, []) for pid in item["members"] if taxonomy_refs.get(pid)}
        item["coding_disagreement"] = coding_disagreement(item)

    reportable = [g for g in groups if g["size"] >= args.min_size]
    if args.only_new:
        reportable = [g for g in reportable if not g["known_audit"]]

    actionable = [g for g in reportable if g["class"] in ("duplicate", "map_conflict")]
    stub_plan, held = build_stub_plan(reportable)
    duplicate_rows = {pid for g in reportable if g["class"] == "duplicate" for pid in g["members"]}
    payload = {
        "generated": "2026-09-16",
        "checker": "scripts/corpus_duplicate_scan.py",
        "coverage": {
            "blocks": len(blocks),
            "blocks_without_doi_field": len(no_field),
            "of_those_covered_by_identifier_or_map": len(covered),
            "of_those_with_no_doi_signal_at_all": len(no_signal),
            "no_signal_ids": sorted(no_signal)[:50],
        },
        "pairs": reportable,
        "pair_count": len(reportable),
        "by_class": dict(collections.Counter(g["class"] for g in reportable)),
        "known_audit_pairs": sum(1 for g in reportable if g["known_audit"]),
        "new_pairs": sum(1 for g in reportable if not g["known_audit"]),
        "open_duplicate_pairs": sum(1 for g in reportable if g["class"] == "duplicate"),
        "open_duplicate_rows": len(duplicate_rows),
        "invisible_to_a_doi_only_check": sum(1 for g in actionable if g["no_doi_field"]),
        "stub_plan": stub_plan,
        "stub_plan_held": held,
    }
    pathlib.Path(STUB_PLAN).write_bytes(json.dumps(stub_plan, indent=1).encode("utf-8"))
    pathlib.Path(args.json_out).write_text(json.dumps(payload, indent=1, ensure_ascii=True), encoding="utf-8")
    pathlib.Path(args.md_out).write_text(render_markdown(payload, signals), encoding="utf-8", newline="")

    print("=== corpus duplicate scan ===")
    print(f"blocks {len(blocks)} | no DOI field {len(no_field)} | of those covered by the identifier or map {len(covered)} "
          f"| still no DOI signal {len(no_signal)}")
    print(f"pairs {len(reportable)} ({payload['known_audit_pairs']} already in the zkiz audit, "
          f"{payload['new_pairs']} new)")
    print(f"  by class: {payload['by_class']}")
    print(f"  open duplicates: {payload['open_duplicate_pairs']} pairs naming {payload['open_duplicate_rows']} rows")
    print(f"  a DOI-field-only check cannot see: {payload['invisible_to_a_doi_only_check']} "
          f"duplicate/map-conflict pairs")
    for item in reportable:
        tag = "known" if item["known_audit"] else "NEW  "
        kind = {
            "duplicate": "DUP",
            "duplicate_stubbed": "STUB",
            "map_conflict": "MAP",
            "anchor_unverified": "UNV",
            "map_doi_collision": "MDOI",
            "doi_field_collision": "FDOI",
            "title_only_collision": "TTL",
            "near_title": "NEAR",
        }[item["class"]]
        sim = item.get("title_similarity")
        print(f"  {tag} {kind:<4} {item['size']:>2} {' '.join(item['members'])[:84]:<84} "
              f"{','.join(item['signals'])[:34]:<34} sim={sim}")
        if item["coding_disagreement"]:
            print(f"       coding disagreement: {item['coding_disagreement'][:150]}")
        if item["ledger_refs"]:
            print(f"       cited in ledgers: { {k: len(v) for k, v in item['ledger_refs'].items()} }")
    print(f"  stub plan: {len(stub_plan)} pair(s) -> {STUB_PLAN.name}; {len(held)} component(s) held")
    for item in held:
        print(f"    held {item['component']}: shares a map_conflict pair {item['map_conflicts']}")
    print(f"\nwrote {args.json_out}\nwrote {args.md_out}")
    return 0


def _recorded_duplicate_directions() -> dict[str, str]:
    """pid -> the original it is recorded as a duplicate of (`duplicate_of`).

    Where a prior stubbing pass already recorded a direction, that is a fact and it
    outranks this scan's lowest-P-id heuristic — the two disagree on P-147/P-309, where
    the registry says P-147 duplicates P-309.
    """
    out: dict[str, str] = {}
    if not REGISTRY.exists():
        return out
    for row in csv.DictReader(REGISTRY.read_bytes().decode("utf-8", errors="replace").splitlines()):
        pid = (row.get("Paper_ID") or "").strip()
        target = (row.get("duplicate_of") or "").strip()
        if pid and target.startswith("P-") and target[2:].isdigit():
            out[pid] = target
    return out


def build_stub_plan(reportable: list[dict]) -> tuple[list[dict], list[dict]]:
    """The `dedup_stub.py` input: one {'dup','original'} per duplicate component.

    A component that also holds a `map_conflict` pair is held back, not stubbed: inside it two
    rows claim one PDF while naming different papers, so which row is the real paper is
    undecided and stubbing the wrong member would propagate through the ledgers. Within a
    component the original is the record a prior pass already named, else the lowest P-id.
    """
    duplicates = [g for g in reportable if g["class"] == "duplicate"]
    components: dict[frozenset, set[str]] = collections.defaultdict(set)
    for item in duplicates:
        components[frozenset(item["component"])].update(item["members"])
    recorded = _recorded_duplicate_directions()

    plan, held = [], []
    for component, members in sorted(components.items(), key=lambda kv: sorted(kv[1])):
        ids = sorted(members, key=lambda s: int(s[2:]))
        conflicts = [
            g["members"]
            for g in reportable
            if g["class"] == "map_conflict" and set(g["members"]) & set(ids)
        ]
        if conflicts:
            held.append({"component": ids, "map_conflicts": conflicts[:4]})
            continue
        recorded_here = {p: t for p, t in recorded.items() if p in ids and t in ids}
        if recorded_here:
            # A recorded direction fixes the whole component's original, not just the row
            # it names: the registry saying P-147 duplicates P-309 means P-309 is the
            # original and P-147 the stub, whatever the P-id order suggests.
            originals = set(recorded_here.values())
            for dup in ids:
                if dup in originals:
                    continue
                original = recorded_here.get(dup) or min(originals, key=lambda s: int(s[2:]))
                plan.append({
                    "dup": dup, "original": original,
                    "basis": ("registry duplicate_of" if dup in recorded_here
                              else "registry duplicate_of (original fixed)"),
                })
        else:
            for dup in ids[1:]:
                plan.append({"dup": dup, "original": ids[0], "basis": "lowest P-id"})
    return plan, held


def coding_disagreement(item: dict) -> str:
    """Which coded columns the members disagree on — the reproducibility signal."""
    columns = ("Category", "Task_Type", "Validation_Type", "Architecture_Family")
    out = []
    for column in columns:
        values = {pid: (item["coded"][pid] or {}).get(column) for pid in item["members"]}
        distinct = {v for v in values.values() if v and v != "NA"}
        if len(distinct) > 1:
            out.append(f"{column} differs ({', '.join(f'{p}={v}' for p, v in values.items())})")
    return "; ".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
