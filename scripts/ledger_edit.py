"""Edit one ledger record in place without re-serialising the whole file.

The five ledgers are CRLF JSON with mixed escaping (records appended textually over months), so a
json.load/json.dump round-trip rewrites every line. This module locates the object for a given
"id" in the raw text, parses just that object, applies a function to it, and writes the object back
at the same indentation (ASCII-escaped unless the original record already carried raw non-ASCII).

    from ledger_edit import edit_record
    edit_record("data/consensus.json", "VF-18", lambda r: r.update(...))

CLI (small edits):
    PYTHONIOENCODING=utf-8 python scripts/ledger_edit.py data/consensus.json VF-18 --set-json '{"note": "..."}'
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find_record_span(text, record_id):
    m = re.search(r'\r?\n([ \t]*)"id":\s*"%s"' % re.escape(record_id), text)
    if not m:
        raise KeyError(record_id)
    target = m.end()
    # single forward scan with a brace stack; string state tracked so braces inside values are ignored
    stack = []
    in_str = False
    k = 0
    n = len(text)
    while k < n:
        c = text[k]
        if in_str:
            if c == "\\":
                k += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                stack.append(k)
            elif c == "}":
                start = stack.pop()
                if start < target <= k:
                    return start, k + 1
        k += 1
    raise ValueError("unbalanced braces for " + record_id)


def edit_record(path, record_id, fn):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    raw = path.read_bytes().decode("utf-8")
    start, end = _find_record_span(raw, record_id)
    snippet = raw[start:end]
    obj = json.loads(snippet)
    assert obj.get("id") == record_id, (obj.get("id"), record_id)
    before = json.dumps(obj, sort_keys=True)
    fn(obj)
    if json.dumps(obj, sort_keys=True) == before:
        return False
    # indentation of the record's own lines
    m = re.search(r"\r?\n([ \t]*)\"", snippet)
    inner = m.group(1) if m else "  "
    base = inner[:-2] if len(inner) >= 2 else ""
    eol = "\r\n" if "\r\n" in raw else "\n"
    ascii_only = all(ord(c) < 128 for c in snippet)
    dumped = json.dumps(obj, indent=2, ensure_ascii=ascii_only)
    lines = dumped.split("\n")
    new = lines[0] + eol + eol.join(base + l for l in lines[1:])
    raw2 = raw[:start] + new + raw[end:]
    json.loads(raw2)  # whole file must still parse
    path.write_bytes(raw2.encode("utf-8"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("record_id")
    ap.add_argument("--set-json", required=True, help="JSON object whose keys are set on the record")
    a = ap.parse_args()
    patch = json.loads(a.set_json)
    changed = edit_record(a.path, a.record_id, lambda r: r.update(patch))
    print("changed" if changed else "no change", a.record_id)


if __name__ == "__main__":
    main()
