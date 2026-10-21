import json
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp

# Figure 2B parameters transcribed in source.txt; arbitrary units.
k1, k3, k5 = 1.0, 1.0, 1.0
k2, k4, k6 = 0.1, 0.1, 0.1
Ki, n = 1.0, 10


def rhs(t, state):
    X, Y, Z = state
    return [k1*Ki**n/(Ki**n + Z**n) - k2*X,
            k3*X - k4*Y,
            k5*Y - k6*Z]


def main():
    times = np.asarray(json.loads(
        Path(__file__).with_name("experiment.json").read_text())["times"],
        dtype=float)
    # Analyst exploration initial values at t=0, not paper initial values.
    initial = [0.1, 0.2, 0.3]
    solution = solve_ivp(rhs, (0.0, float(times[-1])), initial,
                         t_eval=times, method="DOP853",
                         rtol=1e-10, atol=1e-12)
    if not solution.success:
        raise RuntimeError(solution.message)
    print(json.dumps({"times": times.tolist(),
                      "values": {name: solution.y[i].tolist()
                                 for i, name in enumerate(("X", "Y", "Z"))}},
                     allow_nan=False))


if __name__ == "__main__":
    main()
