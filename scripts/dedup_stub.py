#!/usr/bin/env python
"""Stub duplicate papers so the corpus counts each study once.

Usage:  python scripts/dedup_stub.py pairs.json [--apply]
        pairs.json = [{"dup": "P-1018", "original": "P-981"}, ...]

For every pair (default: dry run):
  summaries.md   the duplicate block's header becomes
                 '## Paper N: [STUB-DEDUP-OF-P-orig] <title>' and a one-line notice is
                 inserted under it; the body is kept (append-only, byte-surgical).
  registry       the duplicate row keeps its Paper_ID; Task_Type/Category/
                 Validation_Type/Architecture_Family/XAI_Method -> NA, Best_Metric/
                 Best_AUC -> NA, duplicate_of -> original (patched in place, no CSV
                 round-trip; every other row byte-identical).
  taxonomy       the duplicate's leaf is removed (its original is already placed).
  ledgers        consensus supporting_papers / rejected_supporters, gaps
                 papers_claiming_gap, complementary/conflict paper_a/paper_b: the
                 duplicate id is replaced by the original when the original is
                 absent from that list, otherwise dropped; the change is recorded
                 in a dedup_note field on the record (span-patched, never
                 re-serialised).
Agents 5-7 must be re-run afterwards (graph, tables, taxonomy coverage).
"""
import argparse, csv, io, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTE_KEY = "dedup_note_2026_08_29"   # overridable with --note-key (a re-summarised block gets its own dated note)
sys.path.insert(0, str(ROOT / "scripts"))
SUMMARIES = ROOT / "summaries.md"
REG = ROOT / "data" / "full_paper_registry.csv"
HTML = ROOT / "mci_research_top_down_taxonomy.html"
NA_COLS = ("Task_Type", "Category", "Validation_Type", "Architecture_Family", "XAI_Method", "Best_Metric", "Best_AUC")


def csv_field(v):
    return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',"\r\n') else v


def patch_registry(pairs, apply):
    raw = REG.read_bytes(); text = raw.decode("utf-8")
    header = text.split("\n", 1)[0].rstrip("\r").split(",")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    idx = {c: i for i, c in enumerate(rows[0])}
    changed = 0; out_rows = []
    by_dup = {p["dup"]: p["original"] for p in pairs}
    for r in rows:
        if r and r[0] in by_dup:
            r = list(r)
            for c in NA_COLS:
                if c in idx: r[idx[c]] = "NA"
            if "duplicate_of" in idx: r[idx["duplicate_of"]] = by_dup[r[0]]
            changed += 1
        out_rows.append(r)
    if apply and changed:
        # Rewrite ONLY the changed rows' byte spans: locate each original row line by its Paper_ID prefix.
        new_text = text
        for r in out_rows:
            if r and r[0] in by_dup:
                pid = r[0]
                m = re.search(r"(?m)^" + re.escape(pid) + r",(?:[^\n]|\n(?!P-\d+,))*?(?=\r?\nP-\d+,|\Z)", new_text)
                if not m:
                    print("  !! could not locate row span for", pid); continue
                new_row = ",".join(csv_field(x) for x in r)
                new_text = new_text[:m.start()] + new_row + new_text[m.end():]
        REG.write_bytes(new_text.encode("utf-8"))
    return changed


def patch_summaries(pairs, apply):
    raw = SUMMARIES.read_bytes(); n = 0
    for p in pairs:
        pid = int(p["dup"][2:]); orig = p["original"]
        m = re.search(rb"^## Paper %d: (?!\[STUB)([^\r\n]*)(\r?\n)" % pid, raw, re.M)
        if not m:
            continue
        title = m.group(1); nl = m.group(2)
        notice = (b"> [STUB-DEDUP " + p["dup"].encode() + b" = " + orig.encode() + b": same paper summarised twice; this copy is retained for numbering only, "
                  b"carries NA registry codes, and must not be cited as evidence. Use " + orig.encode() + b".]" + nl + nl)
        new = b"## Paper %d: [STUB-DEDUP-OF-%s] " % (pid, orig.encode()) + title + nl + notice
        raw = raw[:m.start()] + new + raw[m.end():]
        n += 1
    if apply and n:
        SUMMARIES.write_bytes(raw)
    return n


def patch_taxonomy(pairs, apply):
    text = HTML.read_text(encoding="utf-8"); n = 0
    for p in pairs:
        pat = re.compile(r'[ \t]*\{\s*id:\s*"' + re.escape(p["dup"]) + r'",\s*label:\s*"(?:[^"\\]|\\.)*",\s*tags:\s*\[[^\]]*\]\s*\},?[ \t]*\r?\n')
        text, k = pat.subn("", text); n += k
    if apply and n:
        HTML.write_text(text, encoding="utf-8")
    return n


def ledger_changes(pairs, apply):
    """Span-patch list fields in the JSON ledgers via scripts/patchlib.py (never re-serialise)."""
    from patchlib import load_raw, save_raw, replace_value, replace_scalar, add_fields_before_close, verify_parses
    by_dup = {p["dup"]: p["original"] for p in pairs}
    total = 0
    LEDGERS = (
        ("consensus.json", "verified_findings", ("supporting_papers", "rejected_supporters"), ()),
        ("gaps.json", "gaps", ("papers_claiming_gap", "papers_potentially_closing_gap", "grounding_papers", "related_papers"), ()),
        ("conflicts.json", "conflicts", ("resolution_evidence_papers",), ("paper_a", "paper_b")),
        ("complementary.json", "complementary_findings", (), ("paper_a", "paper_b")),
        ("invalidated.json", "invalidated_assumptions", ("evidence_papers", "supporting_papers"), ("paper_id", "disproving_paper")),
    )
    for name, key, list_fields, scalar_fields in LEDGERS:
        path = ROOT / "data" / name
        if not path.exists():
            continue
        raw = load_raw(path); d = json.loads(raw)
        recs = d[key] if isinstance(d, dict) and key in d else [v for v in d.values() if isinstance(v, list)][0]
        for r in recs:
            if not r.get("id"):
                continue
            notes = []; scalars = {}
            for f in list_fields:
                lst = r.get(f)
                if not isinstance(lst, list) or not any(p in by_dup for p in lst):
                    continue
                new = []
                for p in lst:
                    if p in by_dup:
                        o = by_dup[p]
                        if o not in lst and o not in new:
                            new.append(o); notes.append(f"{f}: {p} -> {o}")
                        else:
                            notes.append(f"{f}: {p} dropped (duplicate of {o}, already present)")
                    elif p not in new:
                        new.append(p)
                if apply:
                    raw = replace_value(raw, r["id"], f, new)
                    if f == "supporting_papers" and isinstance(r.get("evidence_count"), int):
                        raw = replace_scalar(raw, r["id"], "evidence_count", len(new))
                total += 1
            for f in scalar_fields:
                v = r.get(f)
                if isinstance(v, str) and v in by_dup:
                    scalars[f] = by_dup[v]; notes.append(f"{f}: {v} -> {by_dup[v]}")
            if scalars:
                pa = scalars.get("paper_a", r.get("paper_a")); pb = scalars.get("paper_b", r.get("paper_b"))
                if pa and pb and pa == pb:
                    notes.append("pair collapsed onto one paper after de-duplication - record no longer a pair")
                    if apply and "status" in r:
                        raw = replace_scalar(raw, r["id"], "status", "rejected_duplicate_pair")
                if apply:
                    for f, v in scalars.items():
                        raw = replace_scalar(raw, r["id"], f, v)
                total += 1
            if notes:
                print(f"  {name} {r.get('id')}: " + "; ".join(notes))
                if apply:
                    raw = add_fields_before_close(raw, r["id"], {NOTE_KEY: "; ".join(notes)})
        if apply:
            verify_parses(raw); save_raw(path, raw)
    return total


def main():
    global NOTE_KEY
    ap = argparse.ArgumentParser(); ap.add_argument("pairs"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledgers-only", action="store_true",
                    help="only re-point ledger references dup -> original (used when a block is re-summarised from its real "
                         "PDF: the old citations referred to the original's paper, the id itself stays live)")
    ap.add_argument("--note-key", default=NOTE_KEY, help="field name for the audit note written into each touched record")
    a = ap.parse_args()
    NOTE_KEY = a.note_key
    pairs = json.loads(Path(a.pairs).read_text(encoding="utf-8"))
    print(f"{'APPLY' if a.apply else 'DRY RUN'}: {len(pairs)} duplicate pairs")
    if not a.ledgers_only:
        print("registry rows to stub:", patch_registry(pairs, a.apply))
        print("summary blocks to stub:", patch_summaries(pairs, a.apply))
        print("taxonomy leaves to remove:", patch_taxonomy(pairs, a.apply))
    print("ledger list edits:", ledger_changes(pairs, a.apply))
    if a.apply:
        print("done - re-run Agents 5, 6, 7 and the three lints")


if __name__ == "__main__":
    main()
