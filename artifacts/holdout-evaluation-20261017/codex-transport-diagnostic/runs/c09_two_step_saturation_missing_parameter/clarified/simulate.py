"""Simulate the substrate-inhibition model specified in source.txt."""

import json

import numpy as np
from scipy.integrate import solve_ivp


def main():
    # Literal sampling times from experiment.json, in minutes.
    times = [
        0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8,
        2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8,
        4.0, 4.2, 4.4, 4.6, 4.8, 5.0, 5.2, 5.4, 5.6, 5.8,
        6.0, 6.2, 6.4, 6.6, 6.8, 7.0, 7.2, 7.4, 7.6, 7.8, 8.0,
    ]
    Vmax = 0.9  # a.u. per minute
    Km = 1.5  # a.u.
    KI = 0.8  # a.u.; authoritative clarification in source.txt

    def rhs(t, y):
        S = y[0]
        return [-Vmax * S / (Km + S + S**2 / KI)]

    solution = solve_ivp(
        rhs,
        (times[0], times[-1]),
        [1.0],
        t_eval=np.asarray(times, dtype=float),
        method="DOP853",
        rtol=1e-10,
        atol=1e-12,
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    if solution.y.shape != (1, len(times)) or not np.isfinite(solution.y).all():
        raise RuntimeError("Solver did not return a finite value at every sampling time.")
    print(json.dumps({"times": times, "values": {"S": solution.y[0].tolist()}}, allow_nan=False))


if __name__ == "__main__":
    main()
