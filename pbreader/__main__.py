"""Точка входа для `python -m pbreader`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
