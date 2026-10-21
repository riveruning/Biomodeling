"""Report the unresolved inhibitor definition without simulating a guessed model."""

import json


def main():
    reason = (
        "The source does not determine whether I in "
        "dS/dt = -Vmax*S/(Km*(1 + I/KI) + S) denotes the constant free "
        "inhibitor state (I(0) = 2.0 a.u., dI/dt = 0) or includes the "
        "growing enzyme-bound inhibitor fraction. Clarify which definition "
        "applies; if free-plus-bound is intended, supply the equation and "
        "any initial conditions and parameters needed for the bound "
        "contribution, and specify whether reported I is free or total."
    )
    print(json.dumps({"status": "needs_input", "reason": reason}))


if __name__ == "__main__":
    main()
