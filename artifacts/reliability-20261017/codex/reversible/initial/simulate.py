"""Report the missing initial condition without inventing a trajectory."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": "Initial B is not reported. What is the initial value of B at the same initial time as A = 1.0?"
    }))


if __name__ == "__main__":
    main()
