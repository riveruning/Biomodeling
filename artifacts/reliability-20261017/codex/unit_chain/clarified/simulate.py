"""Analytical solution of the supplied constant-coefficient ODE model."""

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

    # Time and both rate constants use minutes, per the author correction
    # and dimensional consistency of the V equation.
    k1 = 0.2
    k2 = 0.15
    U_initial = 1.0
    V_initial = 0.1
    U = U_initial * np.exp(-k1 * times)
    # Integrating-factor solution, with expm1 for accuracy near t = 0.
    V = np.exp(-k2 * times) * (
        V_initial
        + k1 * U_initial * (-np.expm1(-(k1 - k2) * times)) / (k1 - k2)
    )
    print(json.dumps({"times": times.tolist(),
                      "values": {"U": U.tolist(), "V": V.tolist()}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
