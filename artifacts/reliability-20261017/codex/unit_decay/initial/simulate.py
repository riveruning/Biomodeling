"""Report the unresolved source unit conflict without selecting an interpretation."""

import json


def main():
    print(json.dumps({
        "status": "needs_input",
        "reason": (
            "The source gives k = 0.3 but labels it per second in the parameter "
            "table and per minute in the equally authoritative equation legend. "
            "With t in minutes, these imply k = 18 per minute and k = 0.3 per "
            "minute, respectively. Is k = 0.3 per second or 0.3 per minute? "
            "Simulation requires resolution of this conflict."
        )
    }))


if __name__ == "__main__":
    main()
