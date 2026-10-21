"""Exact solution of the supplied two-state model; time is in minutes."""

import json
from pathlib import Path

import numpy as np


def main():
    experiment_path = Path(__file__).resolve().parent / "experiment.json"
    with experiment_path.open(encoding="utf-8") as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)):
        raise ValueError("experiment.json times must be a finite one-dimensional array")

    kf = 0.4
    kr = 0.2
    A_initial = 1.0
    B_initial = 0.25  # Explicit superseding author clarification.

    # d(A+B)/dt = 0. With initial conditions at t=0, A relaxes
    # exponentially to kr*(A_initial+B_initial)/(kf+kr).
    total = A_initial + B_initial
    A_equilibrium = kr * total / (kf + kr)
    A = A_equilibrium + (A_initial - A_equilibrium) * np.exp(
        -(kf + kr) * times
    )
    B = total - A
    print(json.dumps({"times": times.tolist(),
                      "values": {"A": A.tolist(), "B": B.tolist()}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
