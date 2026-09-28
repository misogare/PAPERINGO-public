#!/usr/bin/env python
"""Gate-G4/G5 lint for the relational ledgers - model-agnostic invariant checker.

Usage:  python scripts/ledger_lint.py [--since YYYY-MM-DD] [--json out.json] [--strict]

Checks every record in data/conflicts.json, data/consensus.json, data/gaps.json,
data/invalidated.json and data/complementary.json against the rules AGENTS.md
states for Agent 4 and the human gates, regardless of who or what wrote the
record. Written after the 2026-08-29 audit of the model-operated batches
(P-868..P-1097), where two conflicts that fail all four deterministic gates were
self-certified genuine, a verified finding was extended seven times onto an
empty supporter list, 22 invalidated assumptions invented their own prior
belief, and 'confirmed_human_*' markers were written by the model.

HARD (exit 1 with --strict):
  conflicts   genuine record whose two registry rows differ on study_design_type,
              task_substage, population_specificity or modality_subtype (when both
              are reported) or on Task_Type; genuine record pairing duplicates or
              non-independent papers
  consensus   published VF (status/manuscript_status not excluded) with < 3
              supporters, a supporter violating required_design, a duplicate copy
              among supporters, or evidence_count != len(supporting_papers)
  invalidated confirmed IA whose assumption_id is not in config/assumption_registry.json
  gaps        neither status nor current_status set; or the two set and disagreeing
  any         record with a gate/confirmation marker that names a delegate or
              says PENDING while status says confirmed  (self-certification)
  any         mandatory field missing (id, status-like field, statement/claims)
SOFT:         summary blocks that disagree with the record lists; free-text status
              vocabularies; gaps whose statement is a single-paper limitation or a
              combinatorial absence with UNRESOLVED claiming papers.
"""
import argparse, collections, csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
NR = {"", "not reported", "not_reported", "nr", "n/a", "na", "none", None}
# NOTE: a string-matching SELF_CERT pattern (delegated|claude|copilot|...) was removed on
# 2026-08-30. It was never referenced, and wiring it in would reintroduce the bug it was
# written for: July/August records legitimately say "delegated evaluator" because the operator
# delegated those gate passes and then reviewed them. Matching that string over-flagged 26
# human-gated VFs. Self-certification is decided by self_certified() below, which scopes the
# test to the 2026-08-26..29 handover window in which gate_events.log records no human event.
CONFIRMED = re.compile(r"(?i)confirm|verified|genuine_confirmed|open_genuine|include")


def records(payload, key):
    if isinstance(payload, dict):
        if key in payload and isinstance(payload[key], list):
            return payload[key]
        for v in payload.values():
            if isinstance(v, list):
                return v
    return payload if isinstance(payload, list) else []


def load(name, key):
    p = DATA / name
    if not p.exists():
        return {}, []
    payload = json.loads(p.read_text(encoding="utf-8"))
    return payload, records(payload, key)


def registry():
    with open(DATA / "full_paper_registry.csv", encoding="utf-8", newline="") as f:
        return {r["Paper_ID"]: r for r in csv.DictReader(f)}


def non_independence():
    p = CONFIG / "non_independence_registry.json"
    if not p.exists():
        return {}, {}
    d = json.loads(p.read_text(encoding="utf-8"))
    pairs = {}
    for g in d.get("groups", []):
        if g.get("enforcement") == "hard_block" and not g.get("retired"):
            ps = g.get("papers", [])
            for a in ps:
                for b in ps:
                    if a != b:
                        pairs[(a, b)] = g.get("id")
    return pairs, d.get("paper_flags", {})


def genuine(c):
    for f in ("genuine_conflict", "genuine"):
        v = c.get(f)
        if v is None or v == "":
            continue
        return str(v).strip().lower() in {"true", "yes", "y", "1", "genuine"}
    return False


STATUS_KEYS = ("status", "review_status", "manuscript_status", "g5_verdict", "g4_verdict", "gate", "resolution_status")


def markers(rec):
    """Only STATUS-LIKE fields (and field names) count as certification markers. Free-text
    notes mention 'delegated evaluator' for the operator-delegated gate passes of July/August
    that a human then reviewed; those are not self-certification. A record certifies itself
    when the confirmation itself says who wrote it ('confirmed_claude_delegated',
    'PENDING HUMAN REVIEW' next to status confirmed) or a field is named after the delegate."""
    return " ".join(str(rec.get(k, "") or "") for k in STATUS_KEYS if rec.get(k)) + " " + \
           " ".join(k for k in rec if re.search(r"(?i)delegated|claude", k))


HANDOVER = {"CF": 194, "IA": 73, "VF": 32, "C": 235}   # first ids written by the delegated model, 2026-08-26..29


def in_handover_window(rid):
    m = re.match(r"(CF|IA|VF|C)-(\d+)", rid or "")
    return bool(m) and int(m.group(2)) >= HANDOVER[m.group(1)]

GATE_EVENTS_FILE = ROOT / "logs" / "gate_events.log"
# A genuine human-actor gate pass must be a coordinator-written event whose `reason` names the
# operator/human as the non-self actor (rule 13). Delegated / state-reconciled / hand-typed events
# (round timestamps, non-monotonic) do not count and are exactly what the 2026-08-29 audit flagged.
_DELEGATED_REASON = re.compile(r"(?i)delegated|state_file_reconciled|procedural pass|hand-typed|self-adjudicat")
_OPERATOR_ACTOR = re.compile(r"(?i)operator|human\(?s?\)? review|human_...review|reviewer")

def _genuine_operator_gate(rid):
    """Return True if logs/gate_events.log holds a coordinator-written 'passed' gate event naming
    rid as an explicitly operator-actor confirmation (non-self actor), dated at/after the record's
    handover window. Mirrors rule 13: the operating model never certifies itself."""
    if not GATE_EVENTS_FILE.exists():
        return False
    prefix = rid.split("-")[0] + "-" + str(int(rid.split("-")[1]))
    ridset = {prefix, rid}
    found = False
    for ln in GATE_EVENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if ev.get("agent") != "coordinator":
            continue
        if ev.get("status") != "passed":
            continue
        reason = str(ev.get("reason", "") or "")
        body = json.dumps(ev, default=str)
        # event must be about a gate we can certify and mention this record
        if "G4" not in str(ev.get("gate")):
            continue
        if not (rid in reason or rid in body or prefix in reason):
            continue
        if _DELEGATED_REASON.search(reason):
            continue
        if not _OPERATOR_ACTOR.search(reason):
            continue
        found = True
        break
    return found


def self_certified(rec):
    """A confirmation the record itself attributes to a delegate/model, or a confirmed
    status sitting next to 'PENDING HUMAN REVIEW', on a record first written in the
    model-operated window. Older records carry 'delegated' in notes from the
    operator-delegated gate passes of July/August that a human reviewed; those are
    reported as soft 'stale marker' findings elsewhere, not as self-certification."""
    if any(k.startswith("selfcert_revert_") for k in rec):
        return False
    rid = rec.get("id", "") or ""
    if not in_handover_window(rid):
        return False
    # A genuine coordinator-written operator-gate event for this record is a real human
    # pass; it honours rule 13 rather than assuming no human gate ever happened.
    if _genuine_operator_gate(rid):
        return False
    status_text = str(rec.get("status", "")) + " " + str(rec.get("review_status", "")) + " " + str(rec.get("manuscript_status", ""))
    return bool(CONFIRMED.search(status_text))


_GAP_DELEGATED_G5 = re.compile(r"(?i)G5 review \(delegated evaluator\s*(2026-08-2[6-9])\)")


def gap_self_certified(g):
    """Gap ids carry no handover prefix, so the window test above never fires for them. A gap
    published as open whose only G5 marker is a note attributing the review to the delegated
    evaluator inside the model-operated window (2026-08-26..29), with no operator marker
    (g5_status / gate_verified / verified_by) beside it, was opened by the model that ran the
    batch (G-349/350/351, found 2026-09-15). Older 'delegated evaluator' notes are outside the
    window and are the operator-delegated passes a human reviewed."""
    if any(k.startswith("selfcert_revert_") for k in g):
        return False
    st = str(g.get("status") or g.get("current_status") or "").lower()
    if st not in ("open", "partially_open"):
        return False
    if any(g.get(k) for k in ("g5_status", "gate_verified", "verified_by")):
        return False
    note = " ".join(str(g.get(k, "") or "") for k in ("human_evaluation_note", "verification", "status_note"))
    return bool(_GAP_DELEGATED_G5.search(note))


def vf_published(vf):
    """Mirror Agent 6's inclusion filter so 'published' means what Table 6.3 renders."""
    try:
        from agent6_write import _vf_included
        return _vf_included(vf)
    except Exception:
        ms = str(vf.get("manuscript_status", "") or "").lower()
        st = str(vf.get("status", "") or "").lower()
        if ms.startswith("exclude") or "candidate" in ms or ms.startswith("pending"):
            return False
        if any(k in st for k in ("retired", "demoted", "insufficient", "superseded", "deprecated", "pending", "candidate")):
            return False
        return len(vf.get("supporting_papers") or []) >= 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="only records whose text mentions a date >= this (YYYY-MM-DD)")
    ap.add_argument("--json"); ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    reg = registry(); ni_pairs, flags = non_independence()
    dup_of = {pid: r.get("duplicate_of") for pid, r in reg.items() if (r.get("duplicate_of") or "").startswith("P-")}
    hard, soft = collections.defaultdict(list), collections.defaultdict(list)

    def in_window(rec):
        if not a.since:
            return True
        return any(d >= a.since for d in re.findall(r"20\d\d-\d\d-\d\d", json.dumps(rec, default=str)))

    # ---- conflicts
    _, conflicts = load("conflicts.json", "conflicts")
    for c in conflicts:
        cid = c.get("id", "?")
        if not in_window(c):
            continue
        if genuine(c) and isinstance(c.get("g4_override"), dict) and c["g4_override"].get("reason"):
            soft[cid].append(f"gates waived by g4_override ({', '.join(c['g4_override'].get('gates_waived', []))}): {c['g4_override']['reason'][:80]}")
        elif genuine(c):
            pa, pb = c.get("paper_a"), c.get("paper_b")
            ra, rb = reg.get(pa, {}), reg.get(pb, {})
            if not ra or not rb:
                hard[cid].append(f"genuine conflict cites a paper with no registry row ({pa}, {pb})")
            else:
                if ra.get("Task_Type") != rb.get("Task_Type"):
                    hard[cid].append(f"Task_Type differs ({ra.get('Task_Type')} vs {rb.get('Task_Type')})")
                for col in ("study_design_type", "task_substage", "population_specificity", "modality_subtype"):
                    va, vb = (ra.get(col) or "").strip(), (rb.get(col) or "").strip()
                    if va.lower() not in NR and vb.lower() not in NR and va != vb:
                        hard[cid].append(f"gate {col}: {va[:30]!r} vs {vb[:30]!r}")
                # Gate-vacuity guard (2026-09-09, operator G4 verdicts C-243/C-247/C-251):
                # a genuine conflict whose pair has task_substage 'not reported' on BOTH
                # sides could only have been minted through the vacuous T6|review_meta
                # cell, where the substage mismatch test cannot fire. The verdicts
                # require the double-'not reported' skip to become a WARN/block or a
                # mandatory human queue; the minting gate in agent4 now blocks such
                # pairs, and any genuine conflict that still shows both sides NR is a
                # gate-vacuity violation (minted without a substantive comparability
                # check or without populating the column).
                sa2, sb2 = (ra.get("task_substage") or "").strip().lower(), (rb.get("task_substage") or "").strip().lower()
                if sa2 in NR and sb2 in NR:
                    hard[cid].append("gate-vacuity: task_substage not reported on BOTH sides (T6|review_meta vacuous cell)")
            if (pa, pb) in ni_pairs:
                hard[cid].append(f"papers are non-independent ({ni_pairs[(pa, pb)]})")
            if pa in dup_of or pb in dup_of:
                hard[cid].append("pairs a duplicate copy of another paper")
            if not c.get("g4_reviewed") and not re.search(r"(?i)g4", markers(c)):
                soft[cid].append("genuine without any G4 marker")
        if self_certified(c):
            hard[cid].append("self-certified: confirmation marker names a delegate/model or says PENDING")
        if not c.get("id") or not (c.get("claim_a") and c.get("claim_b")):
            hard[cid].append("mandatory fields missing (id/claim_a/claim_b)")

    # ---- consensus
    _, vfs = load("consensus.json", "verified_findings")
    for v in vfs:
        vid = v.get("id", "?")
        if not in_window(v):
            continue
        sp = v.get("supporting_papers") or []
        if vf_published(v):
            if len(sp) < 3:
                hard[vid].append(f"published with {len(sp)} supporter(s) (< 3 confirmations)")
            if isinstance(v.get("evidence_count"), int) and v["evidence_count"] != len(sp):
                hard[vid].append(f"evidence_count {v['evidence_count']} != len(supporting_papers) {len(sp)}")
            req = str(v.get("required_design", "any") or "any").lower()
            if req not in ("any", ""):
                bad = [p for p in sp if reg.get(p, {}).get("study_design_type", "") and req not in reg[p]["study_design_type"].lower()
                       and not (req == "longitudinal" and reg[p].get("Validation_Type") in ("V3", "V4"))]
                if bad and len(bad) >= max(1, len(sp) // 2):
                    hard[vid].append(f"{len(bad)}/{len(sp)} supporters violate required_design={req}: {bad[:6]}")
            dups = [p for p in sp if p in dup_of or any(dup_of.get(q) == p for q in sp)]
            if dups:
                hard[vid].append(f"duplicate copies among supporters: {dups[:6]}")
            pairs = [(p, q) for p in sp for q in sp if p < q and (p, q) in ni_pairs]
            if pairs:
                soft[vid].append(f"non-independent supporter pairs: {pairs[:4]}")
            fl = [p for p in sp if set(flags.get(p, [])) & {"commentary_editorial", "conference_abstract", "not_a_study", "trial_protocol_no_results"}]
            if fl:
                hard[vid].append(f"non-evidence papers among supporters: {fl[:6]}")
            founders = set(re.findall(r"\bP-\d{1,4}\b", str(v.get("human_evaluation_note", "")) + str(v.get("notes", ""))[:600]))
            lost = sorted(founders - set(sp) - set(v.get("rejected_supporters") or []))
            if founders and len(lost) >= 3 and len(lost) > len(founders) // 2:
                soft[vid].append(f"papers named as founding evidence in notes are not supporters: {lost[:6]}")
        if self_certified(v):
            hard[vid].append("self-certified: confirmation marker names a delegate/model or says PENDING")
        if not v.get("statement") and not v.get("finding"):
            hard[vid].append("no statement")

    # ---- invalidated
    assumptions = set()
    p = CONFIG / "assumption_registry.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        for k in ("known_assumptions", "assumptions"):
            for x in d.get(k, []) or []:
                if isinstance(x, dict):
                    assumptions.add(x.get("id") or x.get("assumption_id"))
    _, ias = load("invalidated.json", "invalidated_assumptions")
    for ia in ias:
        iid = ia.get("id", "?")
        if not in_window(ia):
            continue
        st = str(ia.get("status", "") or "").lower()
        if "confirm" in st or ia.get("gate_verified") is True:
            aid = ia.get("assumption_id") or ia.get("assumption") if isinstance(ia.get("assumption"), str) and str(ia.get("assumption")).startswith("KA") else ia.get("assumption_id")
            if not aid or aid not in assumptions:
                hard[iid].append(f"confirmed IA with assumption_id {aid!r} not in config/assumption_registry.json")
            pid = ia.get("paper_id") or ia.get("disproving_paper")
            if pid and (reg.get(pid, {}).get("XAI_Method", "").lower() in ("none", "not reported", "")):
                soft[iid].append(f"disproving paper {pid} has no XAI method in the registry")
        if not ia.get("status"):
            hard[iid].append("no status field (Agent 6 includes status-less IAs by default)")
        if self_certified(ia):
            hard[iid].append("self-certified: confirmation marker names a delegate/model or says PENDING")

    # ---- gaps
    _, gaps = load("gaps.json", "gaps")
    for g in gaps:
        gid = g.get("id", "?")
        if not in_window(g):
            continue
        stmt = g.get("gap_statement") or g.get("title") or ""
        if not stmt:
            hard[gid].append("no gap_statement/title")
        # status / current_status are an unenforced mirror pair. Precedence is STATUS-FIRST:
        # in every observed divergence (G-CAND-343/344; the 30-record 2026-09-14 promotion)
        # `status` held the newer value and `current_status` was the stale copy. A record
        # with NEITHER is an error, not a value: agent5/agent6 used to default it to "open",
        # which published 38 never-gated G-NEW-1353..1392-A records as open gaps (2026-09-15).
        s_val, cs_val = g.get("status"), g.get("current_status")
        if s_val in (None, "") and cs_val in (None, ""):
            hard[gid].append("gap has neither status nor current_status (unset is an error; consumers must not default it to open)")
        elif s_val not in (None, "") and cs_val not in (None, "") and str(s_val).lower() != str(cs_val).lower():
            hard[gid].append(f"status/current_status disagree: status={s_val!r} current_status={cs_val!r} (status is authoritative; sync or collapse)")
        st = str(s_val or cs_val or "").lower()
        if st in ("open", "partially_open"):
            claiming = g.get("papers_claiming_gap") or g.get("related_papers") or []
            if "UNRESOLVED" in claiming:
                hard[gid].append("open gap with papers_claiming_gap UNRESOLVED (combinatorial artefact)")
            if any(p in dup_of for p in claiming):
                hard[gid].append(f"open gap anchored on a duplicate copy: {[p for p in claiming if p in dup_of]}")
            if re.search(r"(?i)^corpus-wide absence:.{80,}", stmt) or re.search(r"(?i)\bAND\b.*in the same cohort", stmt):
                soft[gid].append("statement looks combinatorial (modality x task absence / A AND B in one cohort)")
            if re.search(r"(?i)\b(single[- ]cent(er|re)|small[- ]sample|no external validation|is cross-sectional)\b", stmt) and len(claiming) <= 1:
                soft[gid].append("statement is a single-paper limitation, not a corpus gap")
        if self_certified(g) or gap_self_certified(g):
            hard[gid].append("self-certified: confirmation marker names a delegate/model or says PENDING")

    # ---- complementary
    _, cfs = load("complementary.json", "complementary_findings")
    for cf in cfs:
        cid = cf.get("id", "?")
        if not in_window(cf):
            continue
        if "confirm" in str(cf.get("status", "")).lower():
            pa, pb = cf.get("paper_a"), cf.get("paper_b")
            if (pa, pb) in ni_pairs:
                hard[cid].append(f"confirmed pair is non-independent ({ni_pairs[(pa, pb)]})")
            if pa in dup_of or pb in dup_of:
                hard[cid].append("confirmed pair includes a duplicate copy")
            # Gate-vacuity guard (2026-09-09, mirrored from the conflict gate): a
            # confirmed complementary pair whose task_substage is 'not reported'
            # on BOTH sides was proposed through the vacuous cell where the
            # substage comparability test cannot fire. SOFT, not hard: unlike the
            # conflict ledger, confirmed CFs already passed a human G4 gate, so
            # flagging the 18 existing such pairs as hard would re-block the gate
            # on records that were deliberately confirmed. These are surfaced so
            # the operator can populate task_substage and re-derive the insight.
            ra2, rb2 = reg.get(pa, {}), reg.get(pb, {})
            ca2 = (ra2.get("task_substage") or "").strip().lower()
            cb2 = (rb2.get("task_substage") or "").strip().lower()
            if ca2 in NR and cb2 in NR:
                soft[cid].append("gate-vacuity: task_substage not reported on BOTH sides (T6|review_meta vacuous cell)")
            ci = str(cf.get("combined_insight", "") or "")
            if re.match(r"(?i)^P-\d+ finds ", ci) or str(cf.get("clinical_implication", "")).startswith("Evaluate fused "):
                soft[cid].append("template-generated text")
            if self_certified(cf):
                hard[cid].append("self-certified: confirmation marker names a delegate/model or says PENDING")

    n_hard = sum(len(v) for v in hard.values()); n_soft = sum(len(v) for v in soft.values())
    print(f"ledger_lint{(' since ' + a.since) if a.since else ''}: {n_hard} hard violations on {len(hard)} records, {n_soft} soft on {len(soft)}")
    tally = collections.Counter()
    for rid, items in hard.items():
        for i in items:
            tally["HARD " + re.sub(r"[:(\d].*", "", i).strip()] += 1
    for rid, items in soft.items():
        for i in items:
            tally["soft " + re.sub(r"[:(\d].*", "", i).strip()] += 1
    for k, v in tally.most_common():
        print(f"  {v:4d}  {k}")
    for rid in sorted(hard, key=lambda s: (s.split("-")[0], s)):
        print(f"  {rid}: " + " | ".join(hard[rid])[:260])
    if a.json:
        Path(a.json).write_text(json.dumps({"hard": hard, "soft": soft}, indent=1), encoding="utf-8")
    if a.strict and n_hard:
        print(f"FAIL: {n_hard} hard violations")
        sys.exit(1)


if __name__ == "__main__":
    main()
