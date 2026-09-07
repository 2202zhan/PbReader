"""Точка сборки консольного pbreader.exe — его запускает основной проект."""

import sys

from pbreader.cli import main

if __name__ == "__main__":
    sys.exit(main())
