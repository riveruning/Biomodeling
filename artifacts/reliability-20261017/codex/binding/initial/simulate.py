"""Report the missing source parameter without inventing a trajectory."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The source does not report ka. What is the value of ka in min^-1? "
            "Numerical trajectories require this parameter."
        ),
    }))


if __name__ == "__main__":
    main()
