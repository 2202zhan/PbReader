"""Точка сборки оконного PbReader.exe — его запускает человек с рабочего стола."""

import sys

from pbreader.app import main

if __name__ == "__main__":
    sys.exit(main())
