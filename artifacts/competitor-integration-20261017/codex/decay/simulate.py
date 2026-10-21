import json
from pathlib import Path

import roadrunner


def main():
    model = roadrunner.RoadRunner(str(Path(__file__).with_name("model.xml")))
    model.integrator.relative_tolerance = 1e-12
    model.integrator.absolute_tolerance = 1e-14
    result = model.simulate(0, 10, 21, selections=["time", "[P]"])
    print(json.dumps({
        "times": result[:, 0].tolist(),
        "values": {"P": result[:, 1].tolist()},
    }, allow_nan=False))


if __name__ == "__main__":
    main()
