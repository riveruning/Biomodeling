"""Simulate the clarified model at exactly the supplied experimental times."""

import json

import numpy as np
from scipy.integrate import solve_ivp


def main():
    # Embedded from experiment.json so the program is self-contained.
    times = [
        0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75,
        2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75,
        4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 5.75,
        6.0, 6.25, 6.5, 6.75, 7.0, 7.25, 7.5, 7.75, 8.0,
    ]
    Vmax = 1.2  # a.u./min
    Km = 0.9    # a.u.
    KI = 1.4    # a.u.

    def rhs(t, y):
        S, I = y
        return [-Vmax * S / (Km * (1.0 + I / KI) + S), 0.0]

    solution = solve_ivp(
        rhs,
        (times[0], times[-1]),
        [3.0, 2.0],
        t_eval=np.asarray(times, dtype=float),
        method="DOP853",
        rtol=1e-10,
        atol=1e-12,
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    if solution.y.shape != (2, len(times)) or not np.isfinite(solution.y).all():
        raise RuntimeError("The solver did not return all requested finite state values.")

    print(json.dumps({
        "times": times,
        "values": {"S": solution.y[0].tolist(), "I": solution.y[1].tolist()},
    }, allow_nan=False))


if __name__ == "__main__":
    main()
