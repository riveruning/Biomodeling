"""Evaluate the supplied first-order ODE analytically at experiment times."""

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

    # Author clarification: k is per minute, and t is in minutes.
    k = 0.3
    D_initial = 1.0
    # Exact solution of dD/dt = -k*D, with D(0) = 1.0.
    D = D_initial * np.exp(-k * times)
    print(json.dumps({"times": times.tolist(), "values": {"D": D.tolist()}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
