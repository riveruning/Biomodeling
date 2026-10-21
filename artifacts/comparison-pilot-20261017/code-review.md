# Generated-program execution review

The Codex assistant inspected all six exact simulate.py files before approving their hashes.
The four missing-information programs only import json and print a needs_input object.
The DSH complete program imports json, numpy and scipy, implements the three equations with the supplied constants and initial conditions, integrates with BDF and prints JSON.
The Codex complete program additionally reads only experiment.json beside the script, integrates with DOP853 and prints JSON.
No generated program accesses credentials, network, evaluator answers, product imports, or subprocesses. No submitted code was corrected.
Independent replay uses a fresh temporary directory, an environment without credentials, and a 30-second timeout under the host sandbox.
The DSH comment asserting stiffness is not independently established and is not scored as a verified scientific fact.
