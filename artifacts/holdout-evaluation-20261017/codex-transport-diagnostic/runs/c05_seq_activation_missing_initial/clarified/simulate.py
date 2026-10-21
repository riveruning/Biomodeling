"""Simulate the supplied reversible binding model; time in min, states in a.u.

The authoritative clarification supplies Es(0) = 0.5, with Es denoting E*.
"""

import json

import numpy as np
from scipy.integrate import solve_ivp


TIMES = [
    0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8,
    2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8,
    4.0, 4.2, 4.4, 4.6, 4.8, 5.0, 5.2, 5.4, 5.6, 5.8,
    6.0, 6.2, 6.4, 6.6, 6.8, 7.0, 7.2, 7.4, 7.6, 7.8, 8.0,
]
k1 = 0.75
k2 = 0.2


def rhs(t, y):
    E, S, Es = y
    binding = k1 * E * S - k2 * Es
    return [-binding, -binding, binding]


def main():
    solution = solve_ivp(
        rhs,
        (TIMES[0], TIMES[-1]),
        [5.0, 2.0, 0.5],
        t_eval=np.asarray(TIMES, dtype=float),
        method="DOP853",
        rtol=1e-10,
        atol=1e-12,
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    if solution.y.shape != (3, len(TIMES)) or not np.isfinite(solution.y).all():
        raise RuntimeError("Solver returned incomplete or nonfinite concentrations")
    print(json.dumps({
        "times": TIMES,
        "values": {
            name: solution.y[index].tolist()
            for index, name in enumerate(("E", "S", "Es"))
        },
    }, allow_nan=False))


if __name__ == "__main__":
    main()
