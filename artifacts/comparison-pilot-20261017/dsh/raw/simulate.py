import json

# Execution is unsupported: source.txt contains the three ODEs and the
# Fig. 2B parameter set only as [IMAGE_FORMULA ...] placeholders, and gives
# no initial conditions for X, Y, Z. TASK.md forbids reconstructing the
# image equations from prior knowledge, so no runnable model can be built.

print(
    json.dumps(
        {
            "status": "needs_input",
            "reason": "The ODE equations (1)-(3), the Hill function, and the "
            "Fig. 2B parameter values in source.txt are image formulas "
            "([IMAGE_FORMULA: ...e004-e017]) and are not available as text; "
            "no initial conditions X(0), Y(0), Z(0) are given. Need: (1) the "
            "ASCII form of the three ODEs, (2) the ASCII Hill repression "
            "function with its threshold symbol, (3) the exact parameter "
            "values for the Fig. 2B run, (4) the Hill coefficient value used, "
            "and (5) the initial conditions for X, Y and Z.",
        }
    )
)