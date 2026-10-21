"""Report the unresolved equation choice without selecting a model."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The source supplies two equally authoritative alternatives and does "
            "not specify which was used. Which equation was used: "
            "dX/dt = r*X*(1-X/K) or dX/dt = r*X*(1-X)/K?"
        ),
    }))


if __name__ == "__main__":
    main()
