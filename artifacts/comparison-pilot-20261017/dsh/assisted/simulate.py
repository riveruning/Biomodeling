#!/usr/bin/env python3
"""Deliverable for compare-dsh-assisted.

TASK.md requires a runnable simulation only when the supplied source supports
execution. The supplied source.txt explicitly leaves X0, Y0, and Z0 null and
asks for researcher input, so strict execution is not supported. This script
therefore prints the required needs_input JSON object and nothing else.
"""
import json

print(json.dumps({
    "status": "needs_input",
    "reason": "Initial values for X, Y, and Z are not specified in the supplied source; researcher input for X0, Y0, and Z0 at t=0 is required before the ODE can be integrated."
}))