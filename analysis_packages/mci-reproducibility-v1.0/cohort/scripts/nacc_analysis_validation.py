"""Synthetic numerical checks for the independent NACC analyses.

No NACC/ADNI records or archives are read. Analysis modules are imported without
executing their main routines. Only aggregate numerical results are exported.

Run: .venv/Scripts/python.exe -X utf8 scripts/nacc_analysis_validation.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from statsmodels.duration.hazard_regression import PHReg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "nacc_independent_2026-11-03"
VERSION = "1.0"
SEEDS = (2026110311, 2026110312, 2026110313)


def load_analysis(filename):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location("validate_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {"script": path.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "version": module.VERSION}


def center_logistic_checks(g101):
    cases = []
    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        n, groups = 2400, 12
        center = rng.integers(0, groups, n)
        x = np.column_stack([rng.integers(0, 2, n), rng.normal(size=n),
                             rng.integers(0, 2, n), rng.normal(size=n)])
        truth = np.array([.65, -.2, .25, .15])
        alpha = rng.normal(0, .7, groups)
        y = rng.binomial(1, expit(x @ truth + alpha[center])).astype(float)
        for weighted in (False, True):
            mult = np.array([0, 2, 1, 0, 1, 3, 1, 0, 2, 1, 1, 0]) if weighted else np.ones(groups, dtype=int)
            frequency = mult[center]
            b, a, probability = g101.center_logistic(x, y, center, frequency)
            z = np.column_stack([x, np.eye(groups)[center]])

            def objective(coef):
                eta = z @ coef
                value = np.dot(frequency, np.logaddexp(0, eta) - y * eta) + 1e-6 * (coef @ coef) / 2
                gradient = z.T @ (frequency * (expit(eta) - y)) + 1e-6 * coef
                return value, gradient

            dense = minimize(objective, np.zeros(z.shape[1]), jac=True,
                             method="BFGS", options={"gtol": 1e-8, "maxiter": 500})
            difference = float(np.max(np.abs(dense.x - np.r_[b, a])))
            gradient = float(np.max(np.abs(objective(dense.x)[1])))
            objective_difference = float(abs(objective(dense.x)[0] - objective(np.r_[b, a])[0]))
            assert difference < 1e-4, (seed, weighted, difference)
            assert gradient < 1e-4, (seed, weighted, gradient)
            selected = y.astype(bool)
            w, limits = g101.entry_weights(probability, selected, frequency, (.01, .99))
            ix = np.repeat(np.arange(n), frequency)
            expanded_weights, _, expected_limits = g101.core.subject_weights(
                probability[ix], selected[ix], (.01, .99))
            replicated_weights = np.repeat(w, frequency[selected])
            weight_error = float(np.max(np.abs(replicated_weights - expanded_weights)))
            cutoff_error = float(np.max(np.abs(np.asarray(limits) - expected_limits)))
            assert weight_error < 1e-12 and cutoff_error < 1e-12
            cases.append({"seed": seed, "synthetic_subjects": n, "centers": groups,
                          "integer_center_frequency_weights": weighted,
                          "zero_frequency_centers": int(np.sum(mult == 0)),
                          "maximum_coefficient_difference_from_independent_dense_zero_start": difference,
                          "objective_difference": objective_difference,
                          "dense_maximum_gradient": gradient,
                          "participant_weight_replication_error": weight_error,
                          "participant_winsor_cutoff_error": cutoff_error,
                          "coefficient_tolerance": 1e-4})
    return {"passed": True, "comparison": "Schur IRLS versus dense penalized BFGS, independently initialized at zero; participant winsorization versus expanded frequency sample",
            "cases": cases}


def crossed_stratum_cox_checks(g101):
    cases = []
    for seed in SEEDS:
        rng = np.random.default_rng(seed + 100)
        n, groups, versions_n = 1800, 8, 3
        center = rng.integers(0, groups, n)
        version = rng.integers(0, versions_n, n)
        strata = center * (versions_n + 1) + version
        x = np.column_stack([rng.integers(0, 2, n), rng.normal(size=n),
                             rng.integers(0, 2, n), rng.integers(0, 2, n)])
        truth = np.array([.55, -.35, .2, -.15])
        hazard = (.06 + .015 * center + .01 * version) * np.exp(x @ truth)
        failure = rng.exponential(1 / hazard)
        censor = np.minimum(rng.exponential(1 / .035, n), 4.)
        event = (failure <= censor).astype(int)
        duration = np.round(np.minimum(failure, censor) * 4) / 4
        coef = g101.core.cox_fit(x, duration, event, strata)
        ref = PHReg(duration, x, status=event, strata=strata, ties="breslow").fit(disp=0).params
        multiplicity = np.array([0, 2, 1, 3, 1, 0, 1, 2])
        frequency = multiplicity[center]
        ix = np.repeat(np.arange(n), frequency)
        weighted = g101.core.cox_fit(x, duration, event, strata, frequency)
        replicated = PHReg(duration[ix], x[ix], status=event[ix], strata=strata[ix],
                           ties="breslow").fit(disp=0).params
        # Add eventless initial-version strata, analogous to a newly collected
        # UDS version with no observed first-dementia events. No version HR is fit.
        extra_n = 320
        extra_center = rng.integers(0, groups, extra_n)
        extra_x = np.column_stack([rng.integers(0, 2, extra_n), rng.normal(size=extra_n),
                                   rng.integers(0, 2, extra_n), rng.integers(0, 2, extra_n)])
        augmented = g101.core.cox_fit(
            np.vstack([x, extra_x]), np.r_[duration, rng.uniform(.1, 1., extra_n)],
            np.r_[event, np.zeros(extra_n)], np.r_[strata, extra_center * (versions_n + 1) + versions_n])
        errors = {"unweighted_vs_statsmodels": float(np.max(np.abs(coef - ref))),
                  "integer_center_frequency_vs_expanded_statsmodels": float(np.max(np.abs(weighted - replicated))),
                  "adding_eventless_version_strata": float(np.max(np.abs(coef - augmented)))}
        assert max(errors.values()) < 2e-6, (seed, errors)
        cases.append({"seed": seed + 100, "synthetic_subjects": n,
                      "baseline_center_n": groups, "event_generating_versions": versions_n,
                      "extra_eventless_version_subjects": extra_n,
                      "zero_frequency_centers": int(np.sum(multiplicity == 0)),
                      "tie_rounding_years": .25, "maximum_coefficient_differences": errors,
                      "coefficient_tolerance": 2e-6})
    return {"passed": True, "tie_method": "Breslow", "stratification": "center crossed with initial version; version is not a coefficient",
            "cases": cases}


def main():
    sys.path.insert(0, str(ROOT / "scripts"))
    g101, provenance101 = load_analysis("gap101_nacc_independent.py")
    g128, provenance128 = load_analysis("gap128_nacc_independent.py")
    old_validation, provenance_validation = load_analysis("adni_analysis_validation.py")
    _, core101 = load_analysis("gap101_independent.py")
    _, core128 = load_analysis("gap128_independent.py")
    g128.core.validate_implementation()
    assert Path(g101.core.cox_fit.__code__.co_filename).resolve() == ROOT / "scripts" / "gap101_independent.py"
    assert Path(g128.core.fit_ctmc.__code__.co_filename).resolve() == ROOT / "scripts" / "gap128_independent.py"
    result = {"version": VERSION,
              "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "scope": "Synthetic numerical implementation validation only; no participant files read or records exported; no scientific or human-gate approval.",
              "analysis_scripts": [provenance101, provenance128],
              "reused_core_scripts": [core101, core128],
              "reused_validation_helpers": provenance_validation,
              "center_selection_optimizer_and_weights": center_logistic_checks(g101),
              "center_version_stratified_cox": crossed_stratum_cox_checks(g101),
              "ctmc_transition_matrix": old_validation.check_transition_matrices(g128.core),
              "ctmc_known_generator_recovery": old_validation.check_ctmc_recovery(g128.core),
              "g128_mask_observed_event_unit_checks": {"passed": True,
                  "method": "Called the actual reused core validate_implementation, including terminal-preserved and grid-only masks, direct-path flags and competing-event calculation"},
              "limitations": "Numerical equivalence and known-model simulation do not establish causal identification, correct clinical classification, non-informative observation/censoring, model specification or bootstrap interval coverage."}
    result["all_checks_passed"] = True
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "synthetic_validation.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"all_checks_passed": True,
                      "selection_cases": len(result["center_selection_optimizer_and_weights"]["cases"]),
                      "cox_cases": len(result["center_version_stratified_cox"]["cases"]),
                      "matrix_maximum_error": result["ctmc_transition_matrix"]["maximum_absolute_difference_from_scipy_expm"],
                      "known_rate_maximum_relative_error": result["ctmc_known_generator_recovery"]["maximum_absolute_relative_error"]}, indent=2))
    print("Wrote aggregate synthetic_validation.json; no patient data accessed.")


if __name__ == "__main__":
    main()
