"""Round 19b: recall-oriented candidate generation. Collect all claim atoms from the mining run's journal, then
pair atoms of DIFFERENT papers in the same topic whose directions are opposite (higher/lower, increases/decreases
risk, positive/negative correlation, improves/worsens) and whose quantity+contrast wording overlaps. Writes
atoms.json and opposite_pairs.json (sorted by similarity) for agent judgement."""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
J = Path("C:/Users/aryas/.claude/projects/c--Users-aryas-Downloads-PAPERINGO/ae4da8a7-34c7-41fb-b9af-cbd04b882da9/subagents/workflows/wf_517f7a5d-96c/journal.jsonl")
atoms = []
for line in J.read_text(encoding="utf-8").splitlines():
    e = json.loads(line)
    r = e.get("result")
    if isinstance(r, dict) and "atoms" in r:
        atoms += r["atoms"]
# de-duplicate (the resumed run re-returned cached chunks)
seen, uniq = set(), []
for a in atoms:
    k = (a["paper"], a["quantity"], a["contrast"], a["direction"])
    if k not in seen:
        seen.add(k)
        uniq.append(a)
atoms = uniq
(HERE / "atoms.json").write_text(json.dumps(atoms, ensure_ascii=False, indent=0), encoding="utf-8")
papers = {a["paper"] for a in atoms}
print("atoms", len(atoms), "papers with atoms", len(papers))

OPP = {("higher_in_cases", "lower_in_cases"), ("increases_risk", "decreases_risk"),
       ("positive_correlation", "negative_correlation"), ("improves", "worsens")}
NULLS = {"no_difference", "no_association", "no_effect"}
DIRECTED = {"higher_in_cases": "no_difference", "lower_in_cases": "no_difference", "increases_risk": "no_association",
            "decreases_risk": "no_association", "improves": "no_effect", "worsens": "no_effect"}
STOP = set("the of and in vs versus with for to a an on by from mci patients individuals participants older adults "
           "cognitive impairment mild level levels concentration measured using score scores group groups".split())


def toks(a):
    s = f"{a['quantity']} {a['contrast']}".lower()
    s = re.sub(r"[^a-z0-9\-β]+", " ", s)
    return {w for w in s.split() if len(w) > 2 and w not in STOP}


def jacc(x, y):
    return len(x & y) / max(1, len(x | y))


by = {}
for a in atoms:
    by.setdefault(a["topic"], []).append(a)
pairs = []
for topic, lst in by.items():
    T = [toks(a) for a in lst]
    for i in range(len(lst)):
        for j in range(i + 1, len(lst)):
            a, b = lst[i], lst[j]
            if a["paper"] == b["paper"]:
                continue
            d = (a["direction"], b["direction"])
            kind = None
            if d in OPP or d[::-1] in OPP:
                kind = "opposite"
            elif (DIRECTED.get(d[0]) == d[1] and a["significant"] == "yes") or (DIRECTED.get(d[1]) == d[0] and b["significant"] == "yes"):
                kind = "effect_vs_null"
            if not kind:
                continue
            sim = jacc(T[i], T[j])
            thr = 0.15 if kind == "opposite" else 0.30
            if sim >= thr:
                pairs.append({"kind": kind, "sim": round(sim, 3), "topic": topic, "a": a, "b": b})
pairs.sort(key=lambda p: (p["kind"] != "opposite", -p["sim"]))
# one entry per paper pair (keep the most similar atom pair; remember how many atom pairs support it)
best = {}
for p in pairs:
    k = "|".join(sorted([p["a"]["paper"], p["b"]["paper"]]))
    if k not in best:
        best[k] = {**p, "key": k, "n_atom_pairs": 1}
    else:
        best[k]["n_atom_pairs"] += 1
out = sorted(best.values(), key=lambda p: (p["kind"] != "opposite", -p["sim"]))
(HERE / "opposite_pairs.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
from collections import Counter
print("atom pairs", len(pairs), "| paper pairs", len(out), Counter(p["kind"] for p in out))
print("by topic:", Counter(p["topic"] for p in out).most_common(15))
for p in out[:12]:
    print(p["kind"], p["sim"], p["key"], "|", p["a"]["quantity"][:50], "|", p["a"]["direction"], "vs", p["b"]["direction"])
