<#
.SYNOPSIS
    Собирает PbReader в программу и, если найден Inno Setup, в установщик.

.DESCRIPTION
    Запускать на Windows — той же разрядности, что и целевые аппараты.
    Кроссплатформенной сборки у PyInstaller нет: он кладёт в сборку
    интерпретатор и библиотеки ТОЙ системы, где его запустили, поэтому
    PbReader.exe можно собрать только в Windows.

    На выходе:
        dist\PbReader\                     — программа целиком (можно копировать как есть)
        dist\PbReader-<версия>-setup.exe   — установщик, если стоит Inno Setup

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>

[CmdletBinding()]
param(
    # Пропустить сборку установщика, оставить только каталог программы.
    [switch]$NoInstaller,
    # Не пересоздавать виртуальное окружение, если оно уже есть.
    [switch]$Reuse
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectDir ".build-venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "== PbReader: сборка ==" -ForegroundColor Cyan
Set-Location $ProjectDir

if ((Test-Path $VenvDir) -and -not $Reuse) {
    Write-Host "Удаляю прежнее окружение сборки"
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvDir)) {
    Write-Host "Создаю окружение сборки"
    python -m venv $VenvDir
}

Write-Host "Ставлю зависимости"
& $Python -m pip install --quiet --upgrade pip
# Windows-дополнения обязательны: без pywin32 не будет ни печати, ни лотков.
& $Python -m pip install --quiet ".[windows]" pyinstaller

Write-Host "Рисую значок"
& $Python (Join-Path $PSScriptRoot "make_icon.py")

$Version = (& $Python -c "import pbreader; print(pbreader.__version__)").Trim()
Write-Host "Версия: $Version"

Write-Host "Собираю программу"
Remove-Item -Recurse -Force (Join-Path $ProjectDir "dist\PbReader") -ErrorAction SilentlyContinue
& $Python -m PyInstaller (Join-Path $PSScriptRoot "pbreader.spec") --noconfirm --clean

$Built = Join-Path $ProjectDir "dist\PbReader\PbReader.exe"
if (-not (Test-Path $Built)) { throw "Сборка не создала $Built" }

Write-Host "Проверяю собранное"
& (Join-Path $ProjectDir "dist\PbReader\pbreader.exe") --version
# Заодно убеждаемся, что драйверы отвечают: это самая частая поломка сборки —
# программа запускается, но принтеров «не видит».
& (Join-Path $ProjectDir "dist\PbReader\pbreader.exe") printers

if ($NoInstaller) {
    Write-Host "Готово: dist\PbReader" -ForegroundColor Green
    exit 0
}

$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    Write-Warning "Inno Setup 6 не найден — установщик не собран."
    Write-Warning "Программа лежит в dist\PbReader и работает как есть (скопируйте каталог целиком)."
    Write-Warning "Установщик: поставьте Inno Setup 6 (https://jrsoftware.org/isdl.php) и запустите скрипт снова."
    exit 0
}

Write-Host "Собираю установщик"
$env:PBREADER_VERSION = $Version
& $Iscc (Join-Path $PSScriptRoot "pbreader.iss")

Write-Host "Готово: dist\PbReader-$Version-setup.exe" -ForegroundColor Green
