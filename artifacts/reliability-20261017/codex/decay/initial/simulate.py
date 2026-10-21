"""Evaluate the supplied first-order decay model at experiment.json times."""

import json
from pathlib import Path

import numpy as np


def main():
    experiment_path = Path(__file__).resolve().parent / "experiment.json"
    with experiment_path.open(encoding="utf-8") as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)):
        raise ValueError("experiment times must be a finite one-dimensional array")

    k = 0.4  # min^-1, as required by dA/dt = -k*A with t in minutes
    A_initial = 2.0  # dimensionless, at t = 0
    # Exact solution of dA/dt = -k*A, avoiding numerical integration error.
    A = A_initial * np.exp(-k * times)
    print(json.dumps({"times": times.tolist(), "values": {"A": A.tolist()}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
