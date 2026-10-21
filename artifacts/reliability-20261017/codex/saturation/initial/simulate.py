"""Report the missing source parameter without inventing a trajectory."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "source.txt does not report the dimensionless parameter K in "
            "dS/dt = -V*S/(K+S). Supply its numerical value to simulate S "
            "at experiment.json times; S initially equals 2.0 and V = 0.9 min^-1."
        ),
    }))


if __name__ == "__main__":
    main()
