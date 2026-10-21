"""Report the unresolved source ambiguity without choosing an ODE."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The source gives two equally authoritative alternatives: "
            "dR/dt = s/(1+(P/K)**2)-dr*R or "
            "dR/dt = s/(1+(P/K))-dr*R. "
            "Which equation should be used? A unique simulation is not justified "
            "until this is specified."
        )
    }))


if __name__ == "__main__":
    main()
