"""Spawn-safe entry point for ``python -m biomodel_agent``."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())