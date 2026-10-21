"""Evaluate the specified constant-rate ODE exactly at the supplied times."""

import json

import numpy as np


def main():
    # Minutes, copied exactly from experiment.json.
    times = [
        0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75,
        2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75,
        4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 5.75,
        6.0, 6.25, 6.5, 6.75, 7.0, 7.25, 7.5, 7.75, 8.0,
    ]
    # source.txt's authoritative clarification supplies both rates in min^-1.
    kf = 0.18
    kg = 0.12
    # Exact solution of dM/dt = -(kf + kg)*M, with M(0) = 5.0 a.u.
    M = 5.0 * np.exp(-(kf + kg) * np.asarray(times, dtype=float))
    print(json.dumps({"times": times, "values": {"M": M.tolist()}}, allow_nan=False))


if __name__ == "__main__":
    main()
