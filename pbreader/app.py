"""
Точка входа для оконного PbReader.exe.

Отдельно от cli.py по одной причине: PbReader.exe собирается БЕЗ консоли (иначе
при каждом запуске мигало бы чёрное окно поверх интерфейса киоска), а без
консоли у процесса нет ни stdout, ни stderr. Поэтому здесь запуск без
аргументов означает «открыть окно», а весь вывод уходит в файл журнала — писать
его в никуда бессмысленно, а падать на этом (Python закрывает поток записью в
несуществующий дескриптор) — тем более.

Консольный pbreader.exe остаётся полноценным: его запускает основной проект и
читает stdout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _log_path() -> Path:
    """Журнал рядом с данными пользователя, а не рядом с программой.

    Программа ставится в Program Files, куда обычному пользователю писать
    нельзя, — журнал там просто не создался бы.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME") or Path.home()
    directory = Path(base) / "PbReader"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "pbreader.log"


class _NullStream:
    """Заглушка вместо потоков вывода у окна без консоли."""

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()

    from .cli import main as cli_main

    argv = list(sys.argv[1:] if argv is None else argv)
    # Двойной щелчок по значку — это «открой окно», а не «покажи справку».
    if not argv:
        argv = ["ui"]

    os.environ.setdefault("PBREADER_LOG_FILE", str(_log_path()))
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
