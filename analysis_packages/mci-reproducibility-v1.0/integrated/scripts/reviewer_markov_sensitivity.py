"""Reviewer-requested boundary sensitivity; aggregate outputs only, version 1.0.

Reuses the audited cohort and masking definitions without changing their outputs.
Refits each panel with CN->dementia fixed to zero, with the original positive
lower bound, and freely estimated on [0, 2]/year. The other three rates are
refitted in every case. No bootstrap or regular boundary-parameter test is used.
Observed competing-event detection curves are computed independently of Q.
"""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from scipy.optimize import minimize

import gap128_independent as core
import gap128_nacc_independent as nacc
from nacc_source_audit import load_clinical

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'logs/reviewer_revision_2026-11-09'
NAMES = ['cn_mci', 'cn_dementia', 'mci_cn', 'mci_dementia']


def transition(rates, elapsed):
    # Exact closed form, permitting an exactly zero direct transition rate.
    a, b, c, d = rates
    u, v = a+b, c+d
    mean = -(u+v)/2
    delta = np.sqrt(((u-v)/2)**2+a*c)
    ep, em = np.exp((mean+delta)*elapsed), np.exp((mean-delta)*elapsed)
    C, S = (ep+em)/2, (ep-em)/(2*delta)
    p = np.zeros((len(np.atleast_1d(elapsed)), 3, 3))
    p[:, 0, 0], p[:, 0, 1] = C+S*(-u-mean), S*a
    p[:, 1, 0], p[:, 1, 1] = S*c, C+S*(-v-mean)
    p[:, 0, 2] = 1-p[:, 0, 0]-p[:, 0, 1]
    p[:, 1, 2] = 1-p[:, 1, 0]-p[:, 1, 1]
    p[:, 2, 2] = 1
    return p


def fit(panel, mode, original_rates):
    unique, inverse, subjects, n = panel
    counts = np.bincount(inverse, minlength=len(unique))
    dt = unique[:, 0]
    origins, destinations = unique[:, 1].astype(int)-1, unique[:, 2].astype(int)-1
    fixed = mode == 'fixed_zero'
    ix = [0, 2, 3] if fixed else [0, 1, 2, 3]
    free_direct = mode == 'free_nonnegative'
    bounds = [(0., 2.) if free_direct and j == 1 else (np.log(1e-6), np.log(2.)) for j in ix]

    def unpack(theta):
        r = np.zeros(4)
        r[ix] = np.exp(theta)
        if free_direct:
            r[1] = theta[1]
        return r

    def objective(theta):
        p = transition(unpack(theta), dt)[np.arange(len(dt)), origins, destinations]
        if not np.all(np.isfinite(p)) or p.min() < -1e-9:
            return 1e100
        return float(-counts @ np.log(np.maximum(p, 1e-300)))

    starts = [original_rates, [.04, .003, .05, .10], [.10, .01, .10, .20], [.01, 1e-10, .02, .05]]
    fits = []
    for start in starts:
        theta = np.array([np.clip(start[j] if free_direct and j == 1 else np.log(start[j]), *bounds[k]) for k, j in enumerate(ix)])
        result = minimize(objective, theta, method='L-BFGS-B', bounds=bounds,
                          options={'maxiter': 2000, 'ftol': 1e-13, 'gtol': 1e-7})
        fits.append(result)
    best = min(fits, key=lambda f: f.fun)
    rates = unpack(best.x)
    p4 = transition(rates, np.array([4.]))[0]
    return {'mode': mode, 'success': bool(best.success), 'optimizer_message': str(best.message),
            'negative_log_likelihood': float(best.fun),
            **dict(zip(NAMES, map(float, rates))),
            'cn_occupation_from_mci_4y_pct': float(100*p4[1, 0]),
            'dementia_occupation_from_mci_4y_pct': float(100*p4[1, 2]),
            'mci_dementia_intensity_per_100py': float(100*rates[3]),
            'successful_starts': sum(f.success for f in fits),
            'start_diagnostics': [
                {'success':bool(f.success), 'negative_log_likelihood':float(f.fun),
                 'message':str(f.message), 'rates_per_year':list(map(float,unpack(f.x)))}
                for f in fits]}


def main():
    OUT.mkdir(exist_ok=True)
    # Validate the sensitivity's zero-capable kernel against an independent
    # matrix exponential, including exactly zero and near-zero direct rates.
    max_error = 0.
    for rates in ([.04, 0., .05, .1], [.1, 1e-12, .03, .2], [.1, .01, .1, .2]):
        a,b,c,d = rates
        q = np.array([[-a-b,a,b],[c,-c-d,d],[0.,0.,0.]])
        for t in (.01,.5,4.,8.):
            max_error = max(max_error, float(np.max(np.abs(transition(rates, np.array([t]))[0]-expm(q*t)))))
    assert max_error < 1e-12
    cohorts, counts, sources, _ = core.load_cohorts()
    adni = (cohorts, sources)
    visits, baseline, _, sources = load_clinical(min_age=50, include_telephone=True)
    cohorts, _, _ = nacc.build_cohorts(visits, baseline)
    inputs = {'ADNI': adni, 'NACC': (cohorts, sources)}
    result = {'version':'1.0', 'built_at_utc':datetime.now(timezone.utc).isoformat(),
              'scope':'Point-estimate boundary sensitivity; original bootstrap outputs preserved',
              'kernel_max_absolute_error_vs_expm':max_error, 'rows':[], 'sources':{}}
    original_paths = {'ADNI':'logs/adni_independent_2026-11-02/G-128/G-128_results.json',
                      'NACC':'logs/nacc_independent_2026-11-03/G-128/G-128_NACC_independent_results.json'}
    for label, (cohorts, sources) in inputs.items():
        result['sources'][label] = sources
        original = json.loads((ROOT/original_paths[label]).read_text(encoding='utf-8'))['point']
        for schedule, period in core.SCHEDULES.items():
            mci = [core.mask_visits(t,s,period) for t,s in cohorts['baseline_mci'].values()]
            cn = [core.mask_visits(t,s,period) for t,s in cohorts['baseline_cn'].values()]
            panel = core.panel_arrays(mci+cn)
            detected, _ = core.cumulative_detection(core.observed_first_events(mci,2), core.HORIZONS)
            assert abs(detected['observed_reversion_cif_4y']-original[schedule]['observed_reversion_cif_4y']) < 1e-12
            original_rates = [original[schedule]['rates_per_year'][k] for k in NAMES]
            print(label, schedule, 'original boundary', original[schedule]['boundary_rates'], flush=True)
            for mode in ('original_bound', 'fixed_zero', 'free_nonnegative'):
                row = {'cohort':label,'schedule':schedule,
                       'observed_reversion_cif_4y_pct':100*detected['observed_reversion_cif_4y'],
                       **fit(panel,mode,original_rates)}
                assert row['success'], row
                result['rows'].append(row)
                print(label, schedule, mode, row['cn_dementia'], row['cn_occupation_from_mci_4y_pct'], flush=True)
            # Explain horizon reversal using event/risk ordering, without exporting rows.
            if schedule in ('full','four_year_mask'):
                records = core.observed_first_events(mci,2)
                result.setdefault('detection_diagnostics',{}).setdefault(label,{})[schedule] = {
                    'first_cn_by_8y_n':int(np.sum(records[:,1]==1)),
                    'dementia_before_any_cn_by_8y_n':int(np.sum(records[:,1]==2)),
                    'censored_before_any_event_n':int(np.sum(records[:,1]==0)),
                    'first_cn_after_4y_n':int(np.sum((records[:,1]==1)&(records[:,0]>4))),
                    'observed_reversion_cif_8y_pct':100*detected['observed_reversion_cif_8y']}
    for label,(cohorts,_) in inputs.items():
        full = core.observed_first_events(list(cohorts['baseline_mci'].values()),2)
        masked = core.observed_first_events([core.mask_visits(t,s,4.) for t,s in cohorts['baseline_mci'].values()],2)
        result['detection_diagnostics'][label]['paired_first_event_counts'] = {
            'dementia_in_full_cn_in_mask':int(np.sum((full[:,1]==2)&(masked[:,1]==1))),
            'cn_in_full_dementia_in_mask':int(np.sum((full[:,1]==1)&(masked[:,1]==2))),
            'cn_in_full_censored_in_mask':int(np.sum((full[:,1]==1)&(masked[:,1]==0))),
            'full_cn_by4_mask_cn_after4':int(np.sum((full[:,1]==1)&(full[:,0]<=4)&(masked[:,1]==1)&(masked[:,0]>4)))}
    paths = list(original_paths.values()) + ['scripts/gap128_independent.py','scripts/gap128_nacc_independent.py','scripts/nacc_source_audit.py', str(Path(__file__).relative_to(ROOT))]
    result['input_sha256'] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    (OUT/'markov_boundary_sensitivity.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    with (OUT/'markov_boundary_sensitivity.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(result['rows'][0]));writer.writeheader();writer.writerows(result['rows'])


if __name__ == '__main__':
    main()
