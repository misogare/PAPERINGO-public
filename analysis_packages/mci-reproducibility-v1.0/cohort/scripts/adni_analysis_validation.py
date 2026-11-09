"""Synthetic-only numerical checks for the independent ADNI analyses.

No ADNI archives or participant records are read. Importing the analysis modules
only defines their functions; their main routines are not executed. Output is
an aggregate numerical-validation JSON, never simulated participant rows.

Run: .venv/Scripts/python.exe -X utf8 scripts/adni_analysis_validation.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from statsmodels.duration.hazard_regression import PHReg


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "adni_independent_2026-11-02"
VERSION = "1.0"
SEED = 2026110201


def load_analysis(filename):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {"script": str(path.relative_to(ROOT)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "version": module.VERSION}


def generator(rates):
    a, b, c, d = rates
    return np.array([[-a-b, a, b], [c, -c-d, d], [0., 0., 0.]])


def check_transition_matrices(g128):
    rng = np.random.default_rng(SEED)
    random_rates = np.exp(rng.uniform(np.log(1e-6), np.log(2.), (160, 4)))
    special_rates = np.array([[.12, .025, .08, .16], [1e-6]*4,
                              [2.]*4, [1e-6, 2., 2., 1e-6]])
    rate_sets = np.vstack([special_rates, random_rates])
    intervals = np.array([0., 1e-8, 1/365.25, .25, .5, 1., 2., 4., 8., 40.])
    max_error, min_probability, max_row_sum_error = 0., 1., 0.
    for rates in rate_sets:
        actual = g128.transition_probabilities(np.log(rates), intervals)
        expected = np.stack([expm(generator(rates)*t) for t in intervals])
        max_error = max(max_error, float(np.max(np.abs(actual-expected))))
        min_probability = min(min_probability, float(actual.min()))
        max_row_sum_error = max(max_row_sum_error,
                                float(np.max(np.abs(actual.sum(axis=2)-1))))
    tolerance = 2e-12
    assert max_error < tolerance, ("matrix exponential", max_error)
    assert min_probability >= -tolerance, ("negative probability", min_probability)
    assert max_row_sum_error < tolerance, ("row sums", max_row_sum_error)
    return {"passed": True, "random_rate_seed": SEED,
            "rate_sets": len(rate_sets), "intervals_per_rate_set": len(intervals),
            "maximum_absolute_difference_from_scipy_expm": max_error,
            "minimum_probability": min_probability,
            "maximum_row_sum_error": max_row_sum_error, "tolerance": tolerance}


def check_ctmc_recovery(g128):
    rng = np.random.default_rng(SEED+1)
    n = 20000
    rates = np.array([.12, .025, .08, .16])
    visits = np.array([0., .5, 1., 2., 3., 4., 6., 8.])
    states = np.empty((n, len(visits)), dtype=int)
    states[:, 0] = np.repeat([0, 1], n//2)
    q = generator(rates)
    for j, delta in enumerate(np.diff(visits), start=1):
        p = expm(q*delta)
        uniform = rng.random(n)
        cumulative = np.cumsum(p[states[:, j-1]], axis=1)
        states[:, j] = np.minimum((uniform[:, None] > cumulative).sum(axis=1), 2)
    series = [(visits, row+1) for row in states]
    panel = g128.panel_arrays(series)
    fitted, _ = g128.fit_ctmc(panel, multi_start=True)
    actual = np.array(list(fitted["rates_per_year"].values()))
    relative_error = (actual-rates)/rates
    # Scientific finite-simulation tolerance, not a claim of exact recovery.
    tolerance = .15
    assert fitted["success"], "Known-Q simulation failed to converge"
    assert not fitted["boundary_rates"], fitted["boundary_rates"]
    assert np.max(np.abs(relative_error)) < tolerance, relative_error.tolist()
    return {"passed": True, "seed": SEED+1, "synthetic_subjects": n,
            "initial_state_counts": {"CN": n//2, "MCI": n//2},
            "fixed_visit_years": visits.tolist(),
            "simulator": "Independent scipy.linalg.expm panel sampler",
            "first_dementia_absorbing": True,
            "true_rates_per_year": dict(zip(fitted["rates_per_year"], rates)),
            "fitted_rates_per_year": fitted["rates_per_year"],
            "relative_rate_errors": dict(zip(fitted["rates_per_year"], relative_error)),
            "maximum_absolute_relative_error": float(np.max(np.abs(relative_error))),
            "relative_error_tolerance": tolerance,
            "model_converged": fitted["success"],
            "limitations": "One homogeneous correctly specified synthetic generator; validates implementation, not ADNI model assumptions."}


def check_cox(g101):
    cases = []
    for seed in (SEED+2, SEED+3, SEED+4):
        rng = np.random.default_rng(seed)
        n = 900
        x = np.column_stack([rng.integers(0, 2, n), rng.normal(size=n),
                             rng.integers(0, 2, n)])
        strata = rng.integers(0, 3, n)
        truth = np.array([.55, -.35, .2])
        hazard = np.array([.08, .14, .2])[strata]*np.exp(x@truth)
        failure = rng.exponential(1/hazard)
        censoring = np.minimum(rng.exponential(1/.035, n), 5.)
        event = (failure <= censoring).astype(int)
        # Quarter-year rounding deliberately creates event/censor ties.
        duration = np.round(np.minimum(failure, censoring)*4)/4
        integer_weights = rng.integers(0, 5, n)
        ix = np.repeat(np.arange(n), integer_weights)
        unweighted = g101.cox_fit(x, duration, event, strata)
        reference = PHReg(duration, x, status=event, strata=strata,
                          ties="breslow").fit(disp=0).params
        weighted = g101.cox_fit(x, duration, event, strata, integer_weights)
        replicated = g101.cox_fit(x[ix], duration[ix], event[ix], strata[ix])
        replicated_reference = PHReg(duration[ix], x[ix], status=event[ix],
                                     strata=strata[ix], ties="breslow").fit(disp=0).params
        scaled = g101.cox_fit(x, duration, event, strata, integer_weights*3.7)
        errors = {
            "unweighted_vs_statsmodels": float(np.max(np.abs(unweighted-reference))),
            "integer_weights_vs_replicated_rows": float(np.max(np.abs(weighted-replicated))),
            "integer_weights_vs_statsmodels_replicated": float(np.max(np.abs(weighted-replicated_reference))),
            "global_weight_scale_invariance": float(np.max(np.abs(weighted-scaled))),
        }
        tolerance = 2e-6
        assert max(errors.values()) < tolerance, errors
        cases.append({"seed": seed, "synthetic_subjects": n,
                      "events": int(event.sum()), "strata": 3,
                      "zero_weight_subjects": int((integer_weights == 0).sum()),
                      "replicated_rows": len(ix), "rounding_years": .25,
                      "maximum_absolute_coefficient_differences": errors,
                      "tolerance": tolerance})
    return {"passed": True, "tie_method": "Breslow", "cases": cases,
            "scope": "Point-estimate likelihood checks; these do not validate causal identification, censoring assumptions, or bootstrap interval coverage."}


def main():
    g101, source101 = load_analysis("gap101_independent.py")
    g128, source128 = load_analysis("gap128_independent.py")
    result = {"version": VERSION,
              "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "scope": "Synthetic-only independent implementation validation; no patient data read or exported; no human gate changed.",
              "analyses": [source101, source128],
              "ctmc_transition_matrix": check_transition_matrices(g128),
              "ctmc_known_generator_recovery": check_ctmc_recovery(g128),
              "cox_likelihood_equivalence": check_cox(g101)}
    result["all_checks_passed"] = True
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "synthetic_validation.json"
    destination.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n",
                           encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))
    print("Aggregate synthetic validation written to", destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
