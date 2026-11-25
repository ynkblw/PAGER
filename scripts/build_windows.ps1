# Baut PAGER als Windows-Anwendung (Ordner dist\PAGER mit PAGER.exe).
# Voraussetzung: uv (https://docs.astral.sh/uv/) – installiert Python 3.12 bei Bedarf selbst.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync --python 3.12
uv run pytest -q
uv run python scripts/make_icon.py

uv run pyinstaller `
    --noconfirm --clean --windowed `
    --name PAGER `
    --icon build/pager.ico `
    --paths src `
    --add-data "src/pager/resources;pager/resources" `
    src/pager/__main__.py

Write-Host ""
Write-Host "Fertig: dist\PAGER\PAGER.exe" -ForegroundColor Green
Write-Host "Zum Verteilen den Ordner dist\PAGER zippen oder per Softwareverteilung ausrollen."
