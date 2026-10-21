"""Report the missing required input; do not simulate an unspecified model."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "KI has no numerical value in source.txt. What numerical value of KI "
            "(in a.u.) from the standard characterisation should be used in "
            "dS/dt = -Vmax*S/(Km + S + S^2/KI)? "
            "The supplementary note does not justify dropping the inhibition term."
        ),
    }))


if __name__ == "__main__":
    main()
