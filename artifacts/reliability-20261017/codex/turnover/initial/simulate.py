"""Evaluate the supplied linear ODE analytically at experiment.json times."""

import json
from pathlib import Path

import numpy as np


def main():
    experiment_path = Path(__file__).resolve().parent / "experiment.json"
    with experiment_path.open(encoding="utf-8") as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)):
        print(json.dumps({
            "status": "needs_input",
            "reason": "experiment.json times must be a finite one-dimensional array in minutes."
        }))
        return

    s = 1.2
    d = 0.3
    M_initial = 0.5
    # Exact solution of dM/dt = s-d*M, with M(0) = M_initial.
    M = M_initial * np.exp(-d * times) - (s / d) * np.expm1(-d * times)
    print(json.dumps({"times": times.tolist(), "values": {"M": M.tolist()}}, allow_nan=False))


if __name__ == "__main__":
    main()
