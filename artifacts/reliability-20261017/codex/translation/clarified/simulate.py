"""Exact solution of the supplied linear ODE model; time is in minutes."""
import json
from pathlib import Path

import numpy as np


def main():
    with Path(__file__).with_name("experiment.json").open() as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)

    s = 0.8
    dm = 0.4
    kp = 0.6
    dp = 0.2
    M0 = 1.0
    P0 = 0.2  # Explicit author clarification.

    M_equilibrium = s / dm
    M = M_equilibrium + (M0 - M_equilibrium) * np.exp(-dm * times)
    P = (
        P0 * np.exp(-dp * times)
        + kp * M_equilibrium * (-np.expm1(-dp * times)) / dp
        + kp * (M0 - M_equilibrium)
        * (np.exp(-dm * times) - np.exp(-dp * times)) / (dp - dm)
    )
    print(json.dumps({"times": times.tolist(), "values": {"M": M.tolist(), "P": P.tolist()}}, allow_nan=False))


if __name__ == "__main__":
    main()
