"""Report the unresolved source conflict without selecting a model interpretation."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The source gives equally authoritative conflicting units for k1 = 0.2: "
            "per second in the parameter table and per minute in the equation legend. "
            "Time t is in minutes. Is k1 0.2 per second (12 per minute), "
            "or 0.2 per minute? Simulation requires this conflict to be resolved."
        ),
    }))


if __name__ == "__main__":
    main()
