"""Extract a corpus paper's source PDF to text (cached) and print identity checks.

    PYTHONIOENCODING=utf-8 python pdf_text.py P-651 [P-665 ...]

For each paper prints: the PDF map entry (path, DOI, title, method), the summaries.md header title, the
registry duplicate_of flag, and the first 500 characters of page 1, so the reader can confirm the PDF is the paper
the block describes. Writes <this folder>/pdf/<P-id>.txt with '=== page N ===' markers. Read-only on the repository.
Known wrong map entries (the map points at another paper): P-112, P-114, P-115, P-117, P-174.
"""
import csv, json, re, sys
from pathlib import Path

REPO = Path("C:/Users/aryas/Downloads/PAPERINGO")
HERE = Path(__file__).resolve().parent
OUT = HERE / "pdf"
OUT.mkdir(exist_ok=True)
MAP = json.loads((REPO / "data/paper_pdf_map.json").read_text(encoding="utf-8"))
REG = {}
with open(REPO / "data/full_paper_registry.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        REG[r["Paper_ID"]] = r
HEADERS = {}
with open(REPO / "summaries.md", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"## Paper (\d+):\s*(.*)", line)
        if m:
            HEADERS.setdefault(f"P-{m.group(1)}", []).append(m.group(2).strip())


def extract(pid, path):
    import fitz
    doc = fitz.open(path)
    parts = [f"=== page {i + 1} ===\n{pg.get_text()}" for i, pg in enumerate(doc)]
    return "\n".join(parts)


for arg in sys.argv[1:]:
    m = re.fullmatch(r"(?:[Pp]-?)?(\d+)", arg)
    pid = f"P-{m.group(1)}"
    e = MAP.get(pid)
    print(f"##### {pid}")
    print("summaries header(s):", HEADERS.get(pid, ["(no block)"]))
    print("registry duplicate_of:", (REG.get(pid) or {}).get("duplicate_of"), "| dataset:", (REG.get(pid) or {}).get("Dataset", "")[:160])
    if pid in {"P-112", "P-114", "P-115", "P-117", "P-174"}:
        print("WARNING: known wrong map entry; locate the right PDF by the block's file name / DOI")
    if not e:
        print("no map entry; search downloads/ for the file named in the block")
        continue
    print("map:", {k: e.get(k) for k in ("path", "doi", "title", "method", "verified")})
    p = REPO / e["path"]
    t = OUT / f"{pid}.txt"
    if not t.exists():
        if not p.exists():
            print("PDF NOT FOUND at map path")
            continue
        t.write_text(extract(pid, p), encoding="utf-8")
    txt = t.read_text(encoding="utf-8")
    print("text:", t, f"({len(txt)} chars, {txt.count('=== page ')} pages)")
    print("page 1 start:", re.sub(r"\s+", " ", txt[:700]))
    print()
