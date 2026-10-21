"""Report the missing initial condition without inventing a trajectory."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": "Initial P is not reported. What is the initial dimensionless activity P at t = 0 minutes?"
    }))


if __name__ == "__main__":
    main()
