import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp


def main():
    with Path(__file__).with_name("experiment.json").open() as handle:
        experiment = json.load(handle)
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)) or np.any(times < 0):
        raise ValueError("Experiment times must be a finite, nonnegative one-dimensional array.")

    s, K, dr, kp, dp = 1.0, 1.0, 0.4, 0.7, 0.3

    def rhs(t, state):
        R, P = state
        return [s / (1 + (P / K) ** 2) - dr * R, kp * R - dp * P]

    if times.size == 0:
        values = np.empty((2, 0))
    elif np.max(times) == 0:
        values = np.repeat(np.array([[0.2], [0.1]]), times.size, axis=1)
    else:
        evaluation_times, inverse = np.unique(times, return_inverse=True)
        result = solve_ivp(
            rhs, (0.0, float(np.max(times))), [0.2, 0.1],
            t_eval=evaluation_times, method="DOP853", rtol=1e-10, atol=1e-12,
        )
        if not result.success:
            raise RuntimeError(result.message)
        values = result.y[:, inverse]
    print(json.dumps({
        "times": times.tolist(),
        "values": {"R": values[0].tolist(), "P": values[1].tolist()},
    }, allow_nan=False))


if __name__ == "__main__":
    main()
