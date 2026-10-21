import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp


def main():
    with Path(__file__).with_name("experiment.json").open() as handle:
        times = np.asarray(json.load(handle)["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)) or np.any(times < 0):
        raise ValueError("Times must be a finite, nonnegative one-dimensional array.")

    V = 0.9
    K = 0.7
    S_initial = 2.0

    def rhs(t, state):
        S = state[0]
        return [-V * S / (K + S)]

    if times.size == 0:
        values = []
    elif np.max(times) == 0:
        values = [S_initial] * len(times)
    else:
        evaluation_times, inverse = np.unique(times, return_inverse=True)
        solution = solve_ivp(
            rhs,
            (0.0, float(evaluation_times[-1])),
            [S_initial],
            t_eval=evaluation_times,
            method="DOP853",
            rtol=1e-10,
            atol=1e-12,
        )
        if not solution.success:
            raise RuntimeError(solution.message)
        values = solution.y[0, inverse].tolist()
    print(json.dumps({"times": times.tolist(), "values": {"S": values}}, allow_nan=False))


if __name__ == "__main__":
    main()
