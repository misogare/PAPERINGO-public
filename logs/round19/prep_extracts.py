"""Round 19: compact per-study finding extracts for claim-atom extraction (read-only on the repository).

For every included study (not a duplicate, not out of scope): title, registry codes, and capped copies of the block
sections that carry results. Written as chunk files of CHUNK studies each, plus index.json."""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path("C:/Users/aryas/Downloads/PAPERINGO")
OUT = Path(__file__).resolve().parent / "extracts"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts"))
import litreview_stats as L  # noqa: E402

CHUNK = 30
CAPS = [("What did the authors do?", 700), ("Dataset", 250), ("What is the sample size?", 300),
        ("What are the outcomes? (Results)", 2200), ("Key findings reported", 1800)]
rows = [r for r in csv.DictReader(open(ROOT / "data/full_paper_registry.csv", encoding="utf-8", newline=""))
        if not L.is_dup(r) and not L.is_oos(r)]
t = (ROOT / "summaries.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
st = sorted(((int(m.group(1)), m.start(), m.group(2).strip()) for m in re.finditer(r"^## Paper (\d+):([^\n]*)", t, re.M)),
            key=lambda x: x[1])
blocks, titles = {}, {}
for i, (n, s, title) in enumerate(st):
    e = st[i + 1][1] if i + 1 < len(st) else len(t)
    if n not in blocks:
        blocks[n], titles[n] = t[s:e], title


def section(b, name):
    m = re.search(r"^###\s*" + re.escape(name) + r"[^\n]*\n(.*?)(?=^###\s|\Z)", b, re.M | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


units = []
for r in sorted(rows, key=lambda r: int(r["Paper_ID"][2:])):
    n = int(r["Paper_ID"][2:])
    b = blocks.get(n, "")
    parts = [f"### {r['Paper_ID']}: {titles.get(n, '')[:200]}",
             f"codes: task {r['Task_Type']} | domain {r['Category']} | design {r['study_design_type']} | population "
             f"{r['population_specificity']} | dataset {r['Dataset'][:120]} | year {r['Publication_Year']}"]
    for name, cap in CAPS:
        s = section(b, name)
        if s:
            parts.append(f"[{name}] {s[:cap]}{' ...' if len(s) > cap else ''}")
    units.append((r["Paper_ID"], "\n".join(parts)))
index = []
for k in range(0, len(units), CHUNK):
    grp = units[k:k + CHUNK]
    name = f"chunk_{k // CHUNK + 1:02d}.md"
    (OUT / name).write_text("\n\n".join(u[1] for u in grp) + "\n", encoding="utf-8")
    index.append({"file": str(OUT / name).replace("\\", "/"), "papers": [u[0] for u in grp]})
(OUT / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
print("studies", len(units), "chunks", len(index), "avg chars/chunk",
      sum(len(u[1]) for u in units) // len(index))
