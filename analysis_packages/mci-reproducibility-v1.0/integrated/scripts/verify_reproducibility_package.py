"""Offline checks of a code/aggregate package; version 1.0.

Needs only the Python standard library. Does not open provider data, contact a
service, refit models, write results or modify the package.
"""
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    manifest=json.loads((ROOT/'MANIFEST.json').read_text(encoding='utf-8'))
    for relative,item in manifest['files'].items():
        path=ROOT/relative
        assert path.is_file(),relative
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'],relative
    checks=[]
    blood=ROOT/'logs/gap_close_2026-11-01/G-102_prevalence.csv'
    if blood.exists():
        rows=list(csv.DictReader(blood.open(encoding='utf-8')))
        assert len(rows)==6
        for row in rows:
            se,sp,p=map(float,(row['sensitivity'],row['specificity'],row['scenario_prevalence']))
            tp,fp,tn,fn=1000*p*se,1000*(1-p)*(1-sp),1000*(1-p)*sp,1000*p*(1-se)
            for key,val in {'tp_per_1000':tp,'fp_per_1000':fp,'tn_per_1000':tn,'fn_per_1000':fn,
                            'ppv':tp/(tp+fp),'npv':tn/(tn+fn),'false_positives_per_true_positive':fp/tp}.items():
                assert abs(float(row[key])-val)<1e-8,(row['paper'],key)
        checks.append('Six published-input Bayes scenarios recomputed')
        scan=list(csv.DictReader((ROOT/'logs/gap_close_2026-11-01/G-109_schedule.csv').open(encoding='utf-8')))
        assert len(scan)==4
        for row in scan:
            expected=list(range(int(row['interval_months']),61,int(row['interval_months'])))
            months=[int(x.strip()) for x in row['followup_months'].split(',')]
            assert months==expected
            assert int(row['followup_scans'])==len(expected)
        checks.append('Four monitoring-burden scenarios recomputed')
    cohorts={
      'ADNI':('logs/adni_independent_2026-11-02/G-128/G-128_results.json',8.91,4.21),
      'NACC':('logs/nacc_independent_2026-11-03/G-128/G-128_NACC_independent_results.json',19.73,10.32)}
    for label,(name,full,masked) in cohorts.items():
        if not (ROOT/name).exists():continue
        data=json.loads((ROOT/name).read_text(encoding='utf-8'))
        assert round(100*data['point']['full']['observed_reversion_cif_4y'],2)==full
        assert round(100*data['point']['four_year_mask']['observed_reversion_cif_4y'],2)==masked
        checks.append(label+' saved first-reversion estimates match the manuscript')
    selection={
      'ADNI':('logs/adni_independent_2026-11-02/G-101/G-101_independent_results.json',{'e4':2.346,'smoke':1.060}),
      'NACC':('logs/nacc_independent_2026-11-03/G-101/G-101_NACC_independent_results.json',{'e4':1.616,'smoke':.974})}
    for label,(name,expected) in selection.items():
        if not (ROOT/name).exists():continue
        data=json.loads((ROOT/name).read_text(encoding='utf-8'))
        for exposure in data['exposures']:
            assert round(exposure['weighted']['winsor_01_99']['hr'],3)==expected[exposure['exposure']]
        checks.append(label+' saved composition-weighted estimates match the manuscript')
    extra=ROOT/'logs/reviewer_revision_2026-11-09/contradiction_threshold_sensitivity.json'
    if extra.exists():
        data=json.loads(extra.read_text(encoding='utf-8'))
        added=sum(r['added_vs_tiered_n'] for r in data['summaries']
                  if r['ceiling_policy']=='manuscript_internal_ge097' and r['threshold_rule']=='0.05')
        assert added==10
        checks.append('Uniform 0.05 screen adds ten pairs, without scientific reclassification')
    assert checks
    print(json.dumps({'version':'1.0','package':manifest['package'],'files_verified':len(manifest['files']),
                      'offline_checks':checks,'production_models_refitted':False,'provider_data_read':False,
                      'all_checks_passed':True},indent=2))


if __name__=='__main__':main()
