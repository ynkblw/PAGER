# Baut PAGER als Windows-Anwendung (Ordner dist\PAGER mit PAGER.exe, dazu eine einzelne PAGER.exe).
# Voraussetzung: uv (https://docs.astral.sh/uv/) – installiert Python 3.12 bei Bedarf selbst.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync --python 3.12
uv run pytest -q
uv run python scripts/build.py --onefile --package
