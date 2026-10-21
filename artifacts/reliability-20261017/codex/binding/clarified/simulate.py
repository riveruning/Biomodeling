"""Simulate the supplied abstract activity model at experiment.json times."""

import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp


def main():
    experiment = json.loads(
        Path(__file__).with_name("experiment.json").read_text(encoding="utf-8")
    )
    times = np.asarray(experiment["times"], dtype=float)
    if times.ndim != 1 or not np.all(np.isfinite(times)) or np.any(times < 0):
        raise ValueError("Experiment times must be a finite nonnegative vector.")

    ka = 0.15
    initial = np.array([1.5, 1.0, 0.0])

    def rhs(t, states):
        A, B, C = states
        rate = ka * A * B
        return [-rate, -rate, rate]

    if times.size == 0:
        values = np.empty((3, 0))
    elif np.max(times) == 0:
        values = np.repeat(initial[:, None], times.size, axis=1)
    else:
        unique_times, inverse = np.unique(times, return_inverse=True)
        solution = solve_ivp(
            rhs,
            (0.0, float(unique_times[-1])),
            initial,
            t_eval=unique_times,
            method="DOP853",
            rtol=1e-10,
            atol=1e-12,
        )
        if not solution.success:
            raise RuntimeError(solution.message)
        values = solution.y[:, inverse]

    print(json.dumps({
        "times": times.tolist(),
        "values": {name: values[i].tolist() for i, name in enumerate(("A", "B", "C"))},
    }, allow_nan=False))


if __name__ == "__main__":
    main()
