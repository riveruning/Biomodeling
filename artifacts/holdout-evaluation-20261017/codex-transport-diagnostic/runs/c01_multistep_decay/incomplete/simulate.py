"""Report the unresolved species correspondence; do not simulate a guessed model."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The mechanism names S1 and S2, but the equations, initial "
            "conditions, and reporting requirement name A and B without "
            "specifying their correspondence. Are S1 and S2 aliases for A "
            "and B, respectively? Confirm that mapping or correct the "
            "species names and their associated equations and initial conditions."
        )
    }))


if __name__ == "__main__":
    main()
