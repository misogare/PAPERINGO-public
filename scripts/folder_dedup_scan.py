"""Duplicate scan for a downloads folder against the existing corpus.

The positional mapping P = base + glob_index silently collides with papers that
were already summarised out of glob order (Folder 1, indices 247-252 are P-248..P-253).
This script decides "already in corpus?" from four independent signals so that a
single missing metadata field cannot produce a false 'new'.

Usage: python scripts/folder_dedup_scan.py <folder> <start_idx> <end_idx>
"""
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
from PyPDF2 import PdfReader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SUMMARIES = ROOT / "summaries.md"
DOI_RE = r"10\.\d{4,5}/[A-Za-z0-9._;()/\-]{3,50}"


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_corpus():
    text = SUMMARIES.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"(?m)^## Paper (\d+):(.*)$", text)
    owners, titles = {}, []
    for k in range(1, len(parts), 3):
        num, title, body = int(parts[k]), parts[k + 1].strip(), parts[k + 2]
        titles.append((num, norm(title)))
        found = set()
        m = re.search(r"(?m)^### DOI or URL\r?\n(.{0,600}?)(?=^### |\Z)", body, re.S)
        if m:
            found |= set(re.findall("(%s)" % DOI_RE, m.group(1)))
        found |= set(re.findall(r"(?mi)^[-*]\s*\*\*DOI[^*]*\*\*:?\s*(%s)" % DOI_RE, body))
        for d in found:
            owners.setdefault(d.lower().rstrip(".)"), set()).add(num)
    return text, owners, titles


def pdf_head(path, pages=2):
    reader = PdfReader(str(path))
    return "".join((p.extract_text() or "") for p in reader.pages[:pages])


def candidate_title(head):
    """First plausible title line: longest line in the first 30 non-boilerplate lines."""
    skip = ("available online", "elsevier", "contents lists", "journal homepage",
            "all rights reserved", "creativecommons", "https://doi", "sciencedirect")
    lines = [ln.strip() for ln in head[:3000].split("\n")]
    cand = [ln for ln in lines
            if 25 < len(ln) < 200 and not any(s in ln.lower() for s in skip)]
    return cand[0] if cand else ""


def scan(folder, start, end):
    text, owners, titles = load_corpus()
    files = sorted(Path(folder).glob("*.pdf"))
    print("corpus blocks: %d | DOIs indexed: %d | PDFs in folder: %d"
          % (len(titles), len(owners), len(files)))
    rows = []
    for i in range(start, min(end, len(files))):
        f = files[i]
        try:
            head = pdf_head(f)
        except Exception as exc:  # unreadable PDF is a finding, not a skip
            rows.append((i, f.name, "ERROR", str(exc)[:60]))
            continue
        m = re.search(r"(?:doi\.org/|doi:\s*)(%s)" % DOI_RE, head, re.I)
        doi = m.group(1).lower().rstrip(".)") if m else None
        reasons = []
        if doi and doi in owners:
            reasons.append("doi-field=P-%s" % ",P-".join(str(n) for n in sorted(owners[doi])))
        if doi and doi in text.lower() and not reasons:
            reasons.append("doi-in-prose")
        if f.stem.replace("-main", "") in text:
            reasons.append("filename")
        t = norm(candidate_title(head))
        if len(t) > 40:
            for num, ht in titles:
                if len(ht) > 40 and (t[:70] in ht or ht[:70] in t):
                    reasons.append("title=P-%d" % num)
                    break
        rows.append((i, f.name, "DUP" if reasons else "NEW", "; ".join(reasons) or (doi or "no-doi")))
    for r in rows:
        print("%4d  %-6s  %-42s %s" % (r[0], r[2], r[1][:42], r[3]))
    print("\nNEW indices:", [r[0] for r in rows if r[2] == "NEW"])
    return rows


if __name__ == "__main__":
    scan(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
