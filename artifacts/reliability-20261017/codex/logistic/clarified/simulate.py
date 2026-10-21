"""Evaluate the author-confirmed logistic ODE at experiment.json times."""

import json
from pathlib import Path

import numpy as np
from scipy.special import expit


def main():
    experiment_path = Path(__file__).resolve().parent / "experiment.json"
    with experiment_path.open(encoding="utf-8") as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)):
        raise ValueError("experiment times must be a finite one-dimensional array")

    # t is in minutes; X(0) = 0.2. The clarification selects
    # dX/dt = r*X*(1-X/K), with r = 0.8 min^-1 and K = 2.0.
    r = 0.8
    K = 2.0
    X_initial = 0.2
    # Exact logistic solution, evaluated with a stable sigmoid.
    X = K * expit(r * times + np.log(X_initial / (K - X_initial)))
    print(json.dumps({"times": times.tolist(), "values": {"X": X.tolist()}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
