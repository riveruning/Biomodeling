#!/usr/bin/env python3
"""Goodwin three-variable ODE model simulation.

Source: Gonze D, Abou-Jaoude W (2013), The Goodwin Model: Behind the Hill Function.
DOI: 10.1371/journal.pone.0069573.

Model (equations 1-3 of source.txt):
    dX/dt = k1*Ki**n/(Ki**n + Z**n) - k2*X
    dY/dt = k3*X - k4*Y
    dZ/dt = k5*Y - k6*Z

Parameters from Figure 2B (source.txt):
    k1=1.0, k3=1.0, k5=1.0
    k2=0.1, k4=0.1, k6=0.1
    Ki=1.0, n=10

Initial conditions (analyst-specified exploration, source.txt lines 27-31):
    X=0.1, Y=0.2, Z=0.3
These are NOT original-paper initial conditions; this is an exploratory
simulation, not an exact reproduction of Figure 2B.

Running this script prints ONE JSON object:
    {"times":[...], "values":{"X":[...], "Y":[...], "Z":[...]}}
with values at the time points listed in experiment.json
(0.0, 2.0, ..., 120.0; 61 points, step 2.0).
"""

import json

import numpy as np
from scipy.integrate import solve_ivp

# ---- Parameters (from Figure 2B, source.txt) ----
k1 = 1.0
k2 = 0.1
k3 = 1.0
k4 = 0.1
k5 = 1.0
k6 = 0.1
Ki = 1.0
n = 10.0

# ---- Initial conditions (analyst-specified exploration) ----
X0 = 0.1
Y0 = 0.2
Z0 = 0.3

# ---- Requested times (from experiment.json) ----
# experiment.json lists 0.0, 2.0, ..., 120.0 (61 points, step 2.0).
TIMES = [float(t) for t in np.arange(0.0, 120.0 + 1e-9, 2.0)]


def rhs(t, y):
    """Right-hand side of the Goodwin ODE system.

    y = [X, Y, Z]
    """
    X, Y, Z = y
    # Hill repression term: Ki^n / (Ki^n + Z^n)
    # Use the numerically stable form to avoid overflow with large n:
    #   Ki**n/(Ki**n + Z**n) = 1/(1 + (Z/Ki)**n)
    hill = 1.0 / (1.0 + (Z / Ki) ** n)
    dXdt = k1 * hill - k2 * X
    dYdt = k3 * X - k4 * Y
    dZdt = k5 * Y - k6 * Z
    return [dXdt, dYdt, dZdt]


def main():
    t_eval = np.array(TIMES, dtype=float)

    # The model is stiff (n=10 Hill function with oscillatory dynamics,
    # period ~40 a.u.). Use BDF with tight tolerances.
    sol = solve_ivp(
        rhs,
        t_span=(t_eval[0], t_eval[-1]),
        y0=[X0, Y0, Z0],
        method="BDF",
        t_eval=t_eval,
        rtol=1e-9,
        atol=1e-11,
    )
    if not sol.success:
        raise RuntimeError(f"Integration failed: {sol.message}")

    X = [float(v) for v in sol.y[0]]
    Y = [float(v) for v in sol.y[1]]
    Z = [float(v) for v in sol.y[2]]

    result = {
        "times": [float(t) for t in t_eval],
        "values": {"X": X, "Y": Y, "Z": Z},
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()