"""Report the unresolved source contradiction without simulating the model."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "Conflicting kf units: the parameter table specifies 0.18 per minute, "
            "whereas the equations legend specifies 0.18 per hour. The legend's "
            "additional 10.8 per hour instruction agrees with the table. "
            "What is the intended kf in min^-1: 0.18 or 0.003? "
            "Simulation requires this clarification."
        )
    }))


if __name__ == "__main__":
    main()
