"""Report the missing initial condition; no simulation is justified."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The initial concentration Es(0) of the complex E* is explicitly "
            "missing from source.txt. What is Es(0) in a.u.?"
        )
    }))


if __name__ == "__main__":
    main()
