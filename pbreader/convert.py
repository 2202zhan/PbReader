"""
Конвертация офисных форматов в PDF через COM (Word/Excel/PowerPoint).

Перенесено из прежнего print.py: эта часть делала свою работу, менять её незачем.
Исправлены две вещи, из-за которых на киоске оставались висеть процессы:

* приложение закрывается в finally — при ошибке открытия документа прежний код
  выходил, не вызвав Quit(), и невидимый WINWORD.EXE оставался в памяти до
  перезагрузки, удерживая файл;
* PowerPoint больше не поднимается видимым. Раньше стояло Visible = 1 — окно
  PowerPoint всплывало поверх интерфейса киоска ровно в тот момент, когда его
  там быть не должно.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WORD_FORMATS = {".doc", ".docx", ".rtf", ".odt", ".txt"}
EXCEL_FORMATS = {".xls", ".xlsx", ".csv"}
POWERPOINT_FORMATS = {".ppt", ".pptx", ".odp"}
CONVERTIBLE = WORD_FORMATS | EXCEL_FORMATS | POWERPOINT_FORMATS

WD_FORMAT_PDF = 17
PP_FORMAT_PDF = 32
XL_TYPE_PDF = 0


class ConversionError(RuntimeError):
    pass


def needs_conversion(path: str | Path) -> bool:
    return Path(path).suffix.lower() in CONVERTIBLE


def to_pdf(input_file: str | Path, output_dir: str | Path) -> Path:
    """Конвертирует файл в PDF. PDF на входе возвращается как есть."""
    source = Path(input_file)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return source

    if suffix not in CONVERTIBLE:
        raise ConversionError(
            f"Формат {suffix} не поддерживается. Доступны: {', '.join(sorted(CONVERTIBLE | {'.pdf'}))}"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / (source.stem + ".pdf")

    import pythoncom

    pythoncom.CoInitialize()
    try:
        if suffix in WORD_FORMATS:
            _word_to_pdf(source, output)
        elif suffix in EXCEL_FORMATS:
            _excel_to_pdf(source, output)
        else:
            _powerpoint_to_pdf(source, output)
    finally:
        pythoncom.CoUninitialize()

    if not output.exists():
        raise ConversionError(f"Конвертация не создала файл: {output}")
    logger.info("Сконвертировано: %s → %s", source.name, output.name)
    return output


def _word_to_pdf(source: Path, output: Path) -> None:
    import comtypes.client

    word = comtypes.client.CreateObject("Word.Application")
    document = None
    try:
        word.Visible = False
        word.DisplayAlerts = False
        document = word.Documents.Open(str(source.resolve()), ReadOnly=True)
        document.SaveAs(str(output.resolve()), FileFormat=WD_FORMAT_PDF)
    except Exception as exc:
        raise ConversionError(f"Word не смог открыть {source.name}: {exc}") from exc
    finally:
        if document is not None:
            _quiet(document.Close, False)
        _quiet(word.Quit)


def _excel_to_pdf(source: Path, output: Path) -> None:
    import comtypes.client

    excel = comtypes.client.CreateObject("Excel.Application")
    workbook = None
    try:
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = excel.Workbooks.Open(str(source.resolve()), ReadOnly=True)
        workbook.ExportAsFixedFormat(XL_TYPE_PDF, str(output.resolve()))
    except Exception as exc:
        raise ConversionError(f"Excel не смог открыть {source.name}: {exc}") from exc
    finally:
        if workbook is not None:
            _quiet(workbook.Close, False)
        _quiet(excel.Quit)


def _powerpoint_to_pdf(source: Path, output: Path) -> None:
    import comtypes.client

    powerpoint = comtypes.client.CreateObject("PowerPoint.Application")
    presentation = None
    try:
        presentation = powerpoint.Presentations.Open(
            str(source.resolve()), ReadOnly=True, WithWindow=False
        )
        presentation.SaveAs(str(output.resolve()), FileFormat=PP_FORMAT_PDF)
    except Exception as exc:
        raise ConversionError(f"PowerPoint не смог открыть {source.name}: {exc}") from exc
    finally:
        if presentation is not None:
            _quiet(presentation.Close)
        _quiet(powerpoint.Quit)


def _quiet(call, *args) -> None:
    """Закрытие COM-объекта не должно ронять задание поверх настоящей ошибки."""
    try:
        call(*args)
    except Exception as exc:
        logger.debug("Ошибка при закрытии COM-объекта: %s", exc)
