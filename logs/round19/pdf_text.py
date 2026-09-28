"""Extract the full text of corpus studies' PDFs (via data/paper_pdf_map.json) with page markers.

    python pdf_text.py P-123 P-456      -> writes r19/pdf/P-123.txt ... and prints path, title, page count
Checks the PDF's first page against the summary block title and warns when fewer than half the title's words appear.
"""
import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path("C:/Users/aryas/Downloads/PAPERINGO")
OUT = Path(__file__).resolve().parent / "pdf"
OUT.mkdir(exist_ok=True)
m = json.loads((ROOT / "data/paper_pdf_map.json").read_text(encoding="utf-8"))
t = (ROOT / "summaries.md").read_bytes().decode("utf-8")
for pid in sys.argv[1:]:
    pid = pid if pid.startswith("P-") else f"P-{pid}"
    e = m.get(pid)
    if not e or not (ROOT / e["path"]).exists():
        print(f"{pid}: NO PDF in the map")
        continue
    doc = fitz.open(str(ROOT / e["path"]))
    text = "\n".join(f"=== page {i + 1} ===\n" + p.get_text() for i, p in enumerate(doc))
    (OUT / f"{pid}.txt").write_text(text, encoding="utf-8")
    hm = re.search(r"^## Paper %s:([^\r\n]*)" % pid[2:], t, re.M)
    title = hm.group(1).strip() if hm else ""
    words = set(re.findall(r"[a-z][a-z0-9\-]{3,}", title.lower()))
    head = re.sub(r"\s+", " ", " ".join(doc[i].get_text() for i in range(min(2, len(doc)))).lower())
    frac = sum(w in head for w in words) / max(1, len(words))
    warn = "" if frac >= 0.5 or title.lower().endswith(".pdf") else f"  WARNING: only {frac:.0%} of the block title's words on the PDF's first pages - check it is the right paper"
    print(f"{pid}: {OUT / (pid + '.txt')} | {len(doc)} pages | block title: {title[:100]}{warn}")
