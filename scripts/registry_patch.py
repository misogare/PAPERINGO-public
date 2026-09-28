"""Patch single fields of full_paper_registry.csv rows without round-tripping the file.

The registry is CRLF, has embedded newlines inside quoted fields, and must never be rewritten by
csv.DictWriter (it drops embedded newlines and reflows every row). This tool edits the bytes of the
one record named, then re-parses the file and asserts that exactly the intended (row, column) changed.

    PYTHONIOENCODING=utf-8 python scripts/registry_patch.py --set P-27 Publication_Year 2025 --set P-2 Task_Type T1 ...
    PYTHONIOENCODING=utf-8 python scripts/registry_patch.py --from-json edits.json   # [{"pid":..,"col":..,"new":..}, ...]
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / "data" / "full_paper_registry.csv"


def parse(text):
    return list(csv.reader(io.StringIO(text, newline="")))


def quote(v):
    return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',"\r\n') else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs=3, action="append", metavar=("PID", "COL", "NEW"), default=[])
    ap.add_argument("--from-json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    edits = [{"pid": p, "col": c, "new": n} for p, c, n in a.set]
    if a.from_json:
        edits += json.load(open(a.from_json, encoding="utf-8"))
    if not edits:
        sys.exit("nothing to do")
    raw = REG.read_bytes().decode("utf-8")
    assert "\r\n" in raw, "expected CRLF"
    before = parse(raw)
    header = before[0]
    # split into records on a new line that starts a P-ID row
    parts = re.split(r"(?<=\r\n)(?=P-\d+,)", raw)
    head, records = parts[0], parts[1:]
    by_pid = {}
    for i, rec in enumerate(records):
        by_pid.setdefault(rec.split(",", 1)[0], i)
    changed = []
    for e in edits:
        pid, col, new = e["pid"], e["col"], e["new"]
        ci = header.index(col)
        i = by_pid[pid]
        rec = records[i]
        eol = "\r\n" if rec.endswith("\r\n") else ""
        fields = next(csv.reader(io.StringIO(rec.rstrip("\r\n"), newline="")))
        old = fields[ci]
        if old == new:
            print(f"skip {pid}.{col}: already {new!r}")
            continue
        # rebuild the record field-by-field, preserving the original quoting of untouched fields where possible
        pieces = []
        rest = rec.rstrip("\r\n")
        # tokenise the raw record into its raw field strings
        raw_fields = []
        j = 0
        for k, f in enumerate(fields):
            if rest[j:j + 1] == '"':
                q = '"' + f.replace('"', '""') + '"'
                assert rest.startswith(q, j), (pid, k)
                raw_fields.append(q)
                j += len(q)
            else:
                raw_fields.append(f)
                j += len(f)
            if k < len(fields) - 1:
                assert rest[j] == ",", (pid, k)
                j += 1
        assert j == len(rest), (pid, j, len(rest))
        raw_fields[ci] = quote(new)
        records[i] = ",".join(raw_fields) + eol
        changed.append((pid, col, old, new))
    new_raw = head + "".join(records)
    after = parse(new_raw)
    assert len(after) == len(before), "row count changed"
    diffs = []
    for r0, r1 in zip(before, after):
        for k, (x, y) in enumerate(zip(r0, r1)):
            if x != y:
                diffs.append((r0[0], header[k], x, y))
    expected = sorted((p, c, o, n) for p, c, o, n in changed)
    assert sorted(diffs) == expected, f"unexpected field changes: {sorted(set(diffs) - set(expected))[:5]}"
    for p, c, o, n in changed:
        print(f"{p}.{c}: {o!r} -> {n!r}")
    if a.dry_run:
        print("dry run; not written")
        return
    REG.write_bytes(new_raw.encode("utf-8"))
    print(f"written: {len(changed)} field(s) changed, {len(after) - 1} rows intact")


if __name__ == "__main__":
    main()
