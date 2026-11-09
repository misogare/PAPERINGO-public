"""Read-only numerical screen for reviewer revision, version 1.0.

Separates candidate-generation sensitivity from full-text scientific judgement.
Loads the existing helper definitions without starting telemetry or the pipeline.
Replays current deterministic gates, varying only thresholds and ceiling policy.
Newly screened pairs are NOT adjudicated or added to the research ledger.
"""
import ast
import csv
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'logs/reviewer_revision_2026-11-09'


def helpers():
    path=ROOT/'scripts/agent4_relational.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    keep=[]
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef,ast.Assign,ast.AnnAssign)):
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TRACER' for t in node.targets):
                continue
            keep.append(node)
    # Remove just the two numerical gates in the replay function so that the
    # resulting base set can be screened under each policy in memory.
    class NumericalGates(ast.NodeTransformer):
        def visit_FunctionDef(self,node):
            if node.name!='_check_conflicts':return node
            for sub in ast.walk(node):
                if isinstance(sub,ast.If):
                    test=ast.unparse(sub.test)
                    if test in ('v_new > 0.97 or v_old > 0.97','gap < threshold'):
                        sub.test=ast.Constant(False)
            return node
    module=NumericalGates().visit(ast.Module(body=keep,type_ignores=[]))
    ast.fix_missing_locations(module)
    ns={'__file__':str(path),'__name__':'reviewer_readonly_helpers','TRACER':None,'trace':None}
    exec(compile(module,str(path),'exec'),ns)
    ns['log'].disabled=True
    return ns


def main():
    OUT.mkdir(exist_ok=True)
    h=helpers()
    rows=list(csv.DictReader((ROOT/'data/full_paper_registry.csv').open(encoding='utf-8')))
    rows=[r for r in rows if (r.get('duplicate_of')or'').strip().lower() in ('','not reported') and
          not any(s in r.get('Modality','').lower() for s in ('out-of-scope','out_of_scope'))]
    assert len(rows)==1318
    reg={r['Paper_ID']:r for r in rows}
    ledger=json.loads((ROOT/'data/conflicts.json').read_text(encoding='utf-8'))['conflicts']
    existing={frozenset((r['paper_a'],r['paper_b'])):r for r in ledger}
    # Both loops deduplicate pair order inside the source helper.
    base=h['_check_conflicts'](rows,rows,[])
    tiers={'V1':.15,'V2':.08,'V3':.05,'V4':.03}
    records=[]
    for pair in base:
        a,b=reg[pair['paper_a']],reg[pair['paper_b']]
        typ,va=h['_metric_parse'](a['Best_Metric']);tb,vb=h['_metric_parse'](b['Best_Metric'])
        if typ not in ('ACC','AUC'):continue
        assert typ==tb
        ta,tb=a.get('Validation_Type','V2'),b.get('Validation_Type','V2')
        old=existing.get(frozenset((a['Paper_ID'],b['Paper_ID'])),{})
        records.append({'paper_a':a['Paper_ID'],'paper_b':b['Paper_ID'],'metric':typ,
                        'value_a':va,'value_b':vb,'tier_a':ta,'tier_b':tb,'gap':abs(va-vb),
                        'tiered_threshold':max(tiers.get(ta,.08),tiers.get(tb,.08)),
                        'internal_ge097':any(v>=.97 and t!='V4' for v,t in ((va,ta),(vb,tb))),
                        'source_gt097_anytier':max(va,vb)>.97,
                        'previous_pair_id':old.get('id',''),
                        'previous_disposition':old.get('resolution_status','not_previously_reviewed'),
                        'previous_discrepancy':(old.get('unresolved_discrepancy_2026_09_30')or{}).get('qualifies',False)})
    def selected(row,rule,policy):
        excluded=row['internal_ge097'] if policy=='manuscript_internal_ge097' else row['source_gt097_anytier'] if policy=='source_gt097_anytier' else False
        cutoff=row['tiered_threshold'] if rule=='tiered' else float(rule)
        return not excluded and row['gap']+1e-12>=cutoff
    summaries=[]
    for policy in ('manuscript_internal_ge097','source_gt097_anytier','include_all'):
        for typ in ('ACC','AUC'):
            rr=[r for r in records if r['metric']==typ]
            original=[r for r in rr if selected(r,'tiered',policy)]
            for rule in ('tiered',.03,.05,.08,.15):
                chosen=[r for r in rr if selected(r,rule,policy)]
                added=[r for r in chosen if not selected(r,'tiered',policy)]
                summaries.append({'ceiling_policy':policy,'metric':typ,'threshold_rule':str(rule),
                    'eligible_before_numerical_threshold_n':sum(not (r['internal_ge097'] if policy=='manuscript_internal_ge097' else r['source_gt097_anytier'] if policy=='source_gt097_anytier' else False) for r in rr),
                    'screen_positive_n':len(chosen),'added_vs_tiered_n':len(added),
                    'removed_vs_tiered_n':sum(not selected(r,rule,policy) for r in original),
                    'previously_reviewed_screen_positive_n':sum(bool(r['previous_pair_id']) for r in chosen),
                    'new_screen_positive_not_adjudicated_n':sum(not r['previous_pair_id'] for r in chosen),
                    'added_previously_labelled_compatible_n':sum(r['previous_disposition']=='false_positive' for r in added),
                    'added_previous_disposition_counts':dict(Counter(r['previous_disposition'] for r in added))})
    for name,data in [('contradiction_screen_pairs.csv',records),('contradiction_threshold_sensitivity.csv',summaries)]:
        with (OUT/name).open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    # Existing full-text dispositions are a separate, fixed denominator; no
    # numerical screen can turn a different estimand into a genuine contradiction.
    dispositions=dict(Counter(r.get('resolution_status','') for r in ledger))
    files=['data/full_paper_registry.csv','data/conflicts.json','config/algorithm_patches.json',
           'config/non_independence_registry.json','scripts/agent4_relational.py',str(Path(__file__).relative_to(ROOT))]
    result={'version':'1.0','built_at_utc':datetime.now(timezone.utc).isoformat(),
            'included_publications_n':len(rows),'base_gate_eligible_acc_auc_pairs_n':len(records),
            'existing_dispositions':dispositions,'existing_discrepancies_n':sum((r.get('unresolved_discrepancy_2026_09_30')or{}).get('qualifies') is True for r in ledger),
            'summaries':summaries,
            'limitations':['Registry headline metrics can differ from the task-specific values established in full-text readings.',
                           'This is a numerical candidate-screen sensitivity, not a new full-text adjudication or an estimate of genuine contradictions.',
                           'The current generator excludes >0.97 in any validation tier; manuscript wording used >=0.97 internal-only. Both policies and no exclusion are reported.',
                           'Newly screened pairs have not been labelled compatible or genuinely contradictory.'],
            'input_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files if (ROOT/p).exists()}}
    (OUT/'contradiction_threshold_sensitivity.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('input_sha256','summaries')},indent=2))
    for row in summaries:
        if row['threshold_rule'] in ('tiered','0.05'):print(row)


if __name__=='__main__':main()
